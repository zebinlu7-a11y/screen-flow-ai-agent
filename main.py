"""
AIRAG — 智能截图解析悬浮窗工具 主入口

流程：
  1. 后台监听 Ctrl+D 快捷键 (keyboard 库 → 后台线程)
  2. 弹出全屏截图遮罩 → 用户选择区域 → 截图并发射信号
  3. 弹出追问输入框 → 用户可选输入问题
  4. 图片压缩 + Base64 → LangGraph 流式调用豆包 VL API
  5. 流式结果逐字显示在可拖动的透明悬浮窗上
  6. 保存/加载对话上下文到 JSON，支持多轮对话 + 历史裁剪

系统托盘图标提供：
  - 手动截图入口（右键菜单）
  - 程序状态指示
  - 退出程序
"""

import sys
import os
import json
import time
import threading

# Qt 高 DPI 支持
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

import asyncio
import traceback
from typing import Optional, List

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QTimer, QThread, QRect
from PyQt6.QtGui import QIcon, QAction, QImage
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu

from pynput import keyboard as pynput_keyboard
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage

from config import (
    DEFAULT_HOTKEY, TOGGLE_HOTKEY, OCR_HOTKEY, LEETCODE_HOTKEY, QUIT_HOTKEY,
    CONTEXT_FILE, HTTP_PROXY, RECENT_ROUNDS,
    FULLSCREEN_CAPTURE_PROMPT, CURSOR_STEP,
)
from utils.image_tool import qimage_to_pil, pil_to_base64, compress_image
from utils.context_store import load_context, save_context
from utils.session_memory import (
    clear_session_messages,
    load_session_messages,
    redis_available,
    sync_session_messages,
)
from utils.ocr_tool import ocr_recognize_batch
from utils.speech_worker import get_speech_worker
from utils.user_manager import (
    user_id_from_key, new_conversation, save_conversation,
    load_conversation, list_conversations, delete_conversation,
    get_active_conversation_id, set_active_conversation_id,
    get_last_conversation,
)
from utils.memory_store import (
    load_profile, save_profile, extract_facts_from_conversation,
    merge_facts, build_memory_context,
)
from utils.api_key_manager import get_api_key, set_api_key, set_model, get_model, get_proxy
from agent.graph import build_graph, stream_graph
from agent.llm_client import build_multimodal_message
from gui.capture_window import CaptureWindow
from gui.result_window import ResultWindow
from remote.server import RemoteServer, get_connection_urls, stop_ngrok_tunnel, stop_cloudflare_tunnel


# ============================================================
# Streaming Worker — QThread 中运行 asyncio 流式调用
# ============================================================

class StreamWorker(QThread):
    token_received = pyqtSignal(str)
    stream_finished = pyqtSignal()
    stream_error = pyqtSignal(str)

    def __init__(self, graph, messages: List[BaseMessage],
                 user_text: str, image_base64_list: Optional[List[str]],
                 user_id: str,
                 parent=None):
        super().__init__(parent)
        self._graph = graph
        self._messages = messages
        self._user_text = user_text
        self._image_base64_list = image_base64_list or []
        self._user_id = user_id

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def _stream():
                async for token in stream_graph(
                    graph=self._graph,
                    messages=self._messages,
                    user_text=self._user_text,
                    image_base64_list=self._image_base64_list,
                    user_id=self._user_id,
                ):
                    self.token_received.emit(token)

            loop.run_until_complete(_stream())
            self.stream_finished.emit()
        except Exception as e:
            traceback.print_exc()
            self.stream_error.emit(str(e))
        finally:
            loop.close()


def _is_browser_automation_task(task: str) -> bool:
    text = (task or "").lower()
    keywords = (
        "浏览器", "网页", "网址", "网站", "http://", "https://", "www.",
        "百度", "搜索", "google", "bing", "edge", "chrome", "打开网页",
        "b站", "bilibili", "淘宝", "京东", "知乎", "微博",
    )
    return any(k in text for k in keywords)


class DesktopAgentProcessThread(QThread):
    """Run the GUI automation agent in a separate Python process."""

    progress = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, task: str, use_mcp: bool = False,
                 user_id: str = "default", memory_key: str = "desktop"):
        super().__init__()
        self._task = task
        self._use_mcp = use_mcp
        self._user_id = user_id
        self._memory_key = memory_key
        self._proc = None          # subprocess handle for termination
        self._cancel_file = ""     # signal file to tell agent to stop

    def cancel(self):
        """Terminate the agent subprocess immediately."""
        # Write cancel signal file so agent can exit cleanly between steps
        if self._cancel_file and not os.path.exists(self._cancel_file):
            try:
                with open(self._cancel_file, "w") as f:
                    f.write("cancel")
            except Exception:
                pass
        # Force kill the subprocess
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                time.sleep(0.3)
                if self._proc.poll() is None:
                    self._proc.kill()
            except Exception:
                pass

    def run(self):
        import subprocess
        import tempfile

        try:
            result_file = tempfile.mktemp(suffix=".json", prefix="airag_")
            self._cancel_file = tempfile.mktemp(suffix=".cancel", prefix="airag_cancel_")
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent", "run_gui_agent.py")

            from config import GUI_AGENT_PYTHON
            python = GUI_AGENT_PYTHON or sys.executable

            cmd = [python, script, self._task, "--result", result_file]
            if self._use_mcp:
                cmd.append("--mcp")
                cmd.append("--keep-browser")
            cmd.extend(["--cancel-file", self._cancel_file])
            cmd.extend(["--user-id", self._user_id or "default"])
            cmd.extend(["--memory-key", self._memory_key or "desktop"])

            self.progress.emit(f"启动独立 Agent 进程: {python}")
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUNBUFFERED"] = "1"
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )

            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                if "[进度]" in line:
                    self.progress.emit(line.split("[进度]", 1)[-1].strip())
                elif "[Agent进程]" in line:
                    msg = line.split("[Agent进程]", 1)[-1].strip()
                    if not msg.startswith("完成"):
                        self.progress.emit(msg)
                elif "[MCP]" in line or "[Exec]" in line or "[Analyzer]" in line:
                    self.progress.emit(line)
                elif "[Cancelled]" in line:
                    self.progress.emit("⏹️ 操作已被用户取消 (ESC)")
                else:
                    self.progress.emit(line)

            self._proc.wait()

            # Clean up cancel file
            if self._cancel_file and os.path.exists(self._cancel_file):
                try:
                    os.remove(self._cancel_file)
                except Exception:
                    pass

            time_waited = 0
            while not os.path.exists(result_file) and time_waited < 5:
                self.msleep(200)
                time_waited += 0.2

            if os.path.exists(result_file):
                try:
                    with open(result_file, "r", encoding="utf-8") as f:
                        result = json.load(f)
                except json.JSONDecodeError:
                    result = {"success": False, "message": "结果文件损坏"}
            else:
                canceled = self._proc.returncode != 0
                result = {
                    "success": False,
                    "canceled": canceled,
                    "message": "操作已取消 (ESC)" if canceled else f"进程退出(code={self._proc.returncode})，无结果文件",
                }

            msg = result.get("message", "")
            steps = result.get("steps_done", "")
            if steps:
                msg = f"[{steps}] {msg}"
            elapsed = result.get("elapsed", "")
            if elapsed:
                msg = f"({elapsed}) {msg}"
            self.done.emit(result.get("success", False), msg)

        except Exception as e:
            self.done.emit(False, f"进程异常: {e}")


class _RemoteAgentThread(QThread):
    """远程控制多轮 Agent: 子进程常驻, 通过 stdin/stdout 收发多轮任务."""

    progress = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, task: str, remote_server, use_mcp: bool = False,
                 user_id: str = "default", memory_key: str = "remote_desktop"):
        super().__init__()
        self._task = task
        self._use_mcp = use_mcp
        self._remote = remote_server
        self._user_id = user_id
        self._memory_key = memory_key
        self._proc = None
        self._cancel_file = ""
        self._stop_event = threading.Event()
        # 多轮任务队列 (线程安全)
        import queue
        self._task_queue = queue.Queue()

    def send_task(self, task: str):
        """发送新任务给正在运行的 Agent (线程安全)."""
        self._task_queue.put(task)

    def stop_agent(self):
        """停止 Agent."""
        self._stop_event.set()
        self._task_queue.put(None)  # 唤醒等待的线程
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass

    def cancel(self):
        self._stop_event.set()
        if self._cancel_file and not os.path.exists(self._cancel_file):
            try:
                with open(self._cancel_file, "w") as f:
                    f.write("cancel")
            except Exception:
                pass
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass

    def run(self):
        import subprocess as _sp
        import tempfile as _tf
        import time as _time

        self._cancel_file = _tf.mktemp(suffix=".cancel", prefix="airag_cancel_")
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent", "run_gui_agent.py")

        from config import GUI_AGENT_PYTHON
        python = GUI_AGENT_PYTHON or sys.executable

        # 结果保存到项目目录
        save_dir = os.path.dirname(os.path.dirname(os.path.abspath(script)))

        cmd = [python, script, self._task, "--keep-browser", "--stay-open", "--save-dir", save_dir]
        if self._use_mcp:
            cmd.append("--mcp")
        cmd.extend(["--cancel-file", self._cancel_file])
        cmd.extend(["--user-id", self._user_id or "default"])
        cmd.extend(["--memory-key", self._memory_key or "remote_desktop"])

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        self._proc = _sp.Popen(
            cmd, stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env,
        )

        # 读取第一轮结果
        self._read_and_emit_result()

        # 多轮循环: 等待新任务 → 发送 → 读结果
        while not self._stop_event.is_set():
            try:
                next_task = self._task_queue.get(timeout=0.5)
            except Exception:
                # queue.Empty or timeout
                if self._proc.poll() is not None:
                    break  # 子进程已退出
                continue

            if next_task is None:
                break  # stop signal

            # 发送任务到子进程
            try:
                self._proc.stdin.write(next_task + "\n")
                self._proc.stdin.flush()
                self._remote.send_progress(f"📨 收到指令: {next_task}")
            except Exception:
                break

            # 读取结果
            self._read_and_emit_result()

        # 清理: 通知子进程退出
        try:
            self._proc.stdin.write("STOP\n")
            self._proc.stdin.flush()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=3)
        except Exception:
            self._proc.kill()

        if self._cancel_file and os.path.exists(self._cancel_file):
            try:
                os.remove(self._cancel_file)
            except Exception:
                pass

    def _read_and_emit_result(self):
        """从子进程 stdout 读取结果 JSON 直到 READY 标记，然后发射 done 信号。"""
        lines = []
        result = None

        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue

            # 转发进度信息
            for prefix in ["[进度] ", "[Exec] ", "[Analyzer] ", "[ReAct] ", "[Cancelled] ", "[Vision] "]:
                if prefix in line:
                    msg = line.split(prefix, 1)[-1].strip()
                    self._remote.send_progress(msg)
                    self.progress.emit(msg)
                    break
            else:
                if "[Agent进程]" in line:
                    msg = line.split("[Agent进程]", 1)[-1].strip()
                    self._remote.send_progress(msg)
                    self.progress.emit(msg)
                elif "[MCP]" in line:
                    self._remote.send_progress(line)
                    self.progress.emit(line)
                elif line == "READY":
                    # 子进程准备就绪，可以接收下一个任务
                    break
                else:
                    # 可能是结果 JSON
                    lines.append(line)

        # 解析最后一条有效 JSON 作为结果
        for line in reversed(lines):
            try:
                result = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

        if result:
            success = result.get("success", False)
            msg = result.get("message", "")
            steps = result.get("steps_done", "")
            elapsed = result.get("elapsed", "")
            if steps:
                msg = f"[{steps}] {msg}"
            if elapsed:
                msg = f"({elapsed}) {msg}"
            self.done.emit(success, msg)
            if not success or result.get("canceled"):
                self._stop_event.set()


# ============================================================
# Main Agent Controller
# ============================================================

class ScreenAIAgent(QObject):
    """主控制器，管理截图→AI→展示的全流程。"""

    def __init__(self, app: QApplication):
        super().__init__()
        self._app = app
        self._thread_id = "default"
        self._hotkey_registered = False

        # 用户系统
        self._user_id = user_id_from_key(get_api_key())
        self._active_conv_id = get_active_conversation_id()
        print(f"[AIRAG] 用户ID: {self._user_id}")

        # 长期记忆
        self._profile = load_profile(self._user_id)
        self._memory_needs_extract = False  # 是否需要提取记忆

        # 加载对话：优先活跃对话，其次最近对话
        loaded_from_conv = False
        if self._active_conv_id:
            conv = load_conversation(self._user_id, self._active_conv_id)
            if conv:
                self._messages = self._conv_to_langchain(conv.get("messages", []))
                self._active_conv_id = conv["id"]
                loaded_from_conv = True
                self._restore_redis_short_term()
                print(f"[AIRAG] 已加载对话: {conv.get('title', '')} ({len(self._messages)} 条)")

        if not loaded_from_conv:
            # 尝试加载最近对话
            last_conv = get_last_conversation(self._user_id)
            if last_conv:
                self._messages = self._conv_to_langchain(last_conv.get("messages", []))
                self._active_conv_id = last_conv["id"]
                set_active_conversation_id(self._user_id, self._active_conv_id)
                self._restore_redis_short_term()
                print(f"[AIRAG] 已加载最近对话: {last_conv.get('title', '')}")
            else:
                # 全新用户
                conv = new_conversation(self._user_id, "新对话")
                self._active_conv_id = conv["id"]
                set_active_conversation_id(self._user_id, self._active_conv_id)
                self._messages = []
                print("[AIRAG] 新建对话")

        # LangGraph
        self._graph = build_graph()
        print("[AIRAG] LangGraph 状态机已就绪")

        # UI 组件 — ResultWindow 常驻显示（内嵌侧边栏）
        self._result_window = ResultWindow()
        self._result_window.follow_up_requested.connect(self._on_follow_up)
        self._result_window.model_changed.connect(self._on_model_changed)
        self._result_window.settings_requested.connect(self._change_api_key)
        self._result_window.mode_changed.connect(self._on_mode_changed)
        self._mode = "operate"
        self._result_window.set_mode(self._mode)
        # 侧边栏：内嵌在 ResultWindow 中
        self._result_window.set_sidebar_listener(
            on_select=self._on_conv_selected,
            on_new=self._on_new_conv,
            on_refresh=self._refresh_sidebar,
            user_id=self._user_id,
        )
        self._refresh_sidebar()
        self._result_window.show()
        self._stream_worker: Optional[StreamWorker] = None
        self._capture_win: Optional[CaptureWindow] = None
        self._browser_warmup_thread: Optional[DesktopAgentProcessThread] = None

        # 本轮输入追踪
        self._last_user_text = ""
        self._last_image_b64_list: List[str] = []
        self._last_capture_rect: Optional[QRect] = None

        # 系统托盘
        self._setup_tray()

        # 注册快捷键
        self._register_hotkey()

        # 后台截图推送(手机远程控制, 1.5s 间隔)
        self._screen_pusher_stop = threading.Event()
        self._screen_pusher_thread = threading.Thread(
            target=self._screen_pusher_loop, daemon=True)
        self._screen_pusher_thread.start()

        # 远程控制服务（手机远程控制 PC）
        self._remote_server: Optional[RemoteServer] = None
        self._init_remote_server()

        # 延迟启动 API Key 检查（等 QApplication 事件循环就绪）
        QTimer.singleShot(500, self._check_api_key)

    def _on_mode_changed(self, mode: str):
        self._mode = "chat" if mode == "chat" else "operate"
        if self._mode == "chat":
            self._result_window.show()
            self._result_window.raise_()
            print("[AIRAG] 已切换到问答模式")
        else:
            print("[AIRAG] 已切换到操作模式")

    # ============================================================
    # System Tray
    # ============================================================

    def _setup_tray(self):
        """创建系统托盘图标和右键菜单。"""
        self._tray = QSystemTrayIcon()
        self._tray.setToolTip("AIRAG 截图助手\nCtrl+D 截图  Ctrl+H 显隐")

        # 使用一个简单的内置图标（没有外部图标文件）
        pixmap = self._create_tray_icon_pixmap()
        icon = QIcon(pixmap)
        self._tray.setIcon(icon)
        self._tray.setVisible(True)

        # 右键菜单
        menu = QMenu()

        capture_action = QAction("📷 立即截图 (Ctrl+D)")
        capture_action.triggered.connect(self._start_capture_flow)
        menu.addAction(capture_action)

        menu.addSeparator()

        apikey_action = QAction("🔑 修改 API Key")
        apikey_action.triggered.connect(self._change_api_key)
        menu.addAction(apikey_action)

        clear_action = QAction("🗑️ 清空对话历史")
        clear_action.triggered.connect(self._clear_history)
        menu.addAction(clear_action)

        menu.addSeparator()

        self._privacy_action = QAction("👁️ 屏幕共享可见 (当前: 隐藏)")
        self._privacy_action.setCheckable(True)
        self._privacy_action.setChecked(False)
        self._privacy_action.triggered.connect(self._toggle_privacy)
        menu.addAction(self._privacy_action)

        menu.addSeparator()

        quit_action = QAction("❌ 退出")
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)

        # 双击托盘图标也可截图
        self._tray.activated.connect(self._on_tray_activated)

        # 启动气泡提示
        self._tray.showMessage(
            "AIRAG 截图助手",
            f"已启动！\n{DEFAULT_HOTKEY.upper()} 截图发送\n{TOGGLE_HOTKEY.upper()} 隐藏/显示\n直接输入文字即可对话",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )

    def _create_tray_icon_pixmap(self):
        """创建简单的系统托盘图标（纯色方块 + 字母 A）。"""
        from PyQt6.QtGui import QPainter, QColor, QFont, QPixmap, QPen, QBrush
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 背景圆
        painter.setBrush(QBrush(QColor(60, 120, 220)))
        painter.setPen(QPen(QColor(40, 80, 180), 2))
        painter.drawRoundedRect(2, 2, 28, 28, 8, 8)

        # 文字 A
        painter.setPen(QColor(255, 255, 255))
        font = QFont("Arial", 16, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(2, 2, 28, 28, Qt.AlignmentFlag.AlignCenter, "A")

        painter.end()
        return pixmap

    def _on_tray_activated(self, reason):
        """双击托盘图标触发截图。"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._start_capture_flow()

    def _toggle_privacy(self, checked: bool):
        """切换防屏幕捕获：checked=True → 会议可见"""
        if checked:
            self._result_window.toggle_privacy(False)
            self._privacy_action.setText("👁️ 屏幕共享可见 (当前: 可见)")
        else:
            self._result_window.toggle_privacy(True)
            self._privacy_action.setText("👁️ 屏幕共享可见 (当前: 隐藏)")

    def _quit_app(self):
        """退出程序 — 保存对话 + 提取记忆 + 停止远程服务。"""
        print("[AIRAG] 正在退出...")
        # 停止截图推送线程
        self._screen_pusher_stop.set()
        # 停止远程控制服务 + 隧道
        try:
            stop_ngrok_tunnel()
        except Exception:
            pass
        try:
            stop_cloudflare_tunnel()
        except Exception:
            pass
        try:
            self._stop_remote_agent()
        except Exception:
            pass
        if self._remote_server:
            try:
                self._remote_server.stop()
            except Exception:
                pass
        # 保存对话
        self._save_current_conv()
        set_active_conversation_id(self._user_id, self._active_conv_id)
        # 后台提取记忆（快速版：最多等2秒）
        self._extract_memory_background()
        try:
            if hasattr(self, '_hotkey_listener') and self._hotkey_listener:
                self._hotkey_listener.stop()
        except Exception:
            pass
        self._tray.hide()
        self._app.quit()

    # ============================================================
    # Hotkey
    # ============================================================

    def _register_hotkey(self):
        """注册全局快捷键 Ctrl+D（截图）和 Ctrl+F（显隐）— 跨平台 pynput。"""
        try:
            # pynput 热键映射：<ctrl>+d, <ctrl>+f
            hotkeys = {
                '<ctrl>+d': self._on_hotkey_triggered,
                '<ctrl>+f': self._on_hotkey_toggle,
                '<ctrl>+r': self._on_ocr_hotkey,
                '<ctrl>+k': self._on_leetcode_hotkey,
                '<ctrl>+y': self._on_speech_hotkey,
                '<ctrl>+g': self._on_gui_agent_hotkey,
                # 鼠标微调
                '<ctrl>+<up>': lambda: self._cursor_move(0, -CURSOR_STEP),
                '<ctrl>+<down>': lambda: self._cursor_move(0, CURSOR_STEP),
                '<ctrl>+<left>': lambda: self._cursor_move(-CURSOR_STEP, 0),
                '<ctrl>+<right>': lambda: self._cursor_move(CURSOR_STEP, 0),
                '<ctrl>+q': self._on_quit_hotkey,
                '<esc>': self._on_esc_hotkey,
            }
            self._hotkey_listener = pynput_keyboard.GlobalHotKeys(
                hotkeys, suppress=False)  # suppress=False 让 Ctrl+Z 等正常传递
            self._hotkey_listener.start()  # 非阻塞，后台线程运行
            self._hotkey_registered = True
            print(f"[AIRAG] [OK] 快捷键注册成功 (pynput)")
            print(f"         Ctrl+D — 截图发送")
            print(f"         Ctrl+R — OCR 文字识别")
            print(f"         Ctrl+K — LeetCode 编程解答")
            print(f"         Ctrl+F — 隐藏/显示窗口")
            print(f"         Ctrl+↑↓←→ — 鼠标微调 ({CURSOR_STEP}px)")
            print(f"         Ctrl+Q — 退出程序")
        except Exception as e:
            self._hotkey_registered = False
            self._hotkey_listener = None
            print(f"[AIRAG] [WARN] 快捷键注册失败: {e}")
            print(f"         pynput 在 Linux 上通常无需管理员权限。")
            print(f"         你也可以通过系统托盘的右键菜单手动截图。")
            self._tray.setToolTip(
                "AIRAG 截图助手\n⚠ 热键未注册\n右键托盘图标手动截图"
            )

    def _on_hotkey_triggered(self, key=None):
        """
        Ctrl+D 回调 — 在 pynput 后台线程中运行。
        QTimer.singleShot(0, ...) 安全切回主线程。
        """
        QTimer.singleShot(0, self._start_capture_flow)

    def _on_hotkey_toggle(self, key=None):
        """
        Ctrl+F 回调 — 切换 ResultWindow 显隐。
        """
        QTimer.singleShot(0, self._toggle_window)

    def _toggle_window(self):
        """切换常驻窗口的显示/隐藏（主线程）。"""
        if self._result_window.isVisible():
            self._result_window.hide()
        else:
            self._result_window.show()
            self._result_window.raise_()

    def _cursor_move(self, dx: int, dy: int):
        """Ctrl+方向键 — 微调鼠标位置（Windows API，任何线程可用）。"""
        import ctypes
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        ctypes.windll.user32.SetCursorPos(pt.x + dx, pt.y + dy)

    def _on_ocr_hotkey(self, key=None):
        """Ctrl+R 回调 — 启动 OCR 截图模式。"""
        QTimer.singleShot(0, self._start_ocr_flow)

    def _start_ocr_flow(self):
        """打开截图遮罩，OCR 模式。"""
        if self._capture_win is not None and self._capture_win.isVisible():
            return
        self._result_window.hide()
        QApplication.processEvents()
        self._capture_win = CaptureWindow()
        self._capture_win.captured.connect(self._on_ocr_captured)
        self._capture_win.destroyed.connect(lambda: self._result_window.show() if not self._result_window.isVisible() else None)
        self._capture_win.showFullScreen()

    def _on_ocr_captured(self, images: list):
        """OCR 截图完成 → 本地识别 → 显示可复制文本。"""
        self._capture_win = None

        if not images:
            return

        # 准备 PIL 图片列表
        pil_images = []
        for img, _ in images:
            pil_images.append(qimage_to_pil(img))

        # 显示加载提示
        existing = self._result_window.get_content()
        self._result_window.set_content(
            existing + "\n\n---\n\n**🔍 OCR 识别中...**\n\n" if existing.strip()
            else "**🔍 OCR 识别中...**\n\n"
        )
        self._result_window.show()
        self._result_window.raise_()

        # 主线程中运行 OCR（PaddleOCR 首次加载较慢）
        QApplication.processEvents()

        try:
            ocr_text = ocr_recognize_batch(pil_images)
        except Exception as e:
            ocr_text = f"❌ OCR 识别失败: {e}"

        # 替换"识别中"为实际结果
        if existing.strip():
            self._result_window.set_content(
                existing + "\n\n---\n\n**🔍 OCR 结果：**\n\n" + ocr_text
            )
        else:
            self._result_window.set_content("**🔍 OCR 结果：**\n\n" + ocr_text)

        self._result_window.show()
        self._result_window.raise_()

    # ============================================================
    # Ctrl+K Leetcode 模式
    # ============================================================

    def _on_leetcode_hotkey(self, key=None):
        """Ctrl+K 回调 — 截图 + LeetCode 格式回答。"""
        QTimer.singleShot(0, self._start_leetcode_flow)

    def _start_leetcode_flow(self):
        """Ctrl+K — 直接全屏截图 → AI 编程解答，无需框选。"""
        self._result_window.hide()
        QApplication.processEvents()

        screen = QApplication.primaryScreen()
        if not screen:
            return

        # 直接截全屏
        pixmap = screen.grabWindow(0)
        image = pixmap.toImage()

        # 压缩 → Base64
        pil_img = qimage_to_pil(image)
        pil_img = compress_image(pil_img)
        image_base64 = pil_to_base64(pil_img)

        # 用 LeetCode 提示词
        user_text = FULLSCREEN_CAPTURE_PROMPT
        self._last_user_text = user_text
        self._last_image_b64_list = [image_base64]

        self._result_window.show()
        self._result_window.raise_()
        self._run_ai_stream(user_text, [image_base64])

    def _on_leetcode_captured(self, images: list):
        pass  # 不再使用，保留兼容

    # ============================================================
    # Step 1: Capture
    # ============================================================

    def _start_capture_flow(self):
        """打开全屏截图遮罩（主线程）。"""
        # 避免重复打开截图窗口
        if self._capture_win is not None and self._capture_win.isVisible():
            return

        # 截图时隐藏悬浮窗，避免它出现在截图中
        self._result_window.hide()
        QApplication.processEvents()

        self._capture_win = CaptureWindow()
        self._capture_win.captured.connect(self._on_image_captured)
        self._capture_win.destroyed.connect(lambda: self._result_window.show() if not self._result_window.isVisible() else None)
        self._capture_win.showFullScreen()

    def _on_image_captured(self, images: list):
        """多框截图完成 → 全部压缩存入 ResultWindow 缩略图区。"""
        self._capture_win = None

        if not images:
            return

        # 用第一个截图的矩形定位窗口
        _, first_rect = images[0]
        self._last_capture_rect = first_rect

        # 每张图压缩 → Base64 → 添加到缩略图
        for img, _ in images:
            pil_img = qimage_to_pil(img)
            pil_img = compress_image(pil_img)
            image_base64 = pil_to_base64(pil_img)
            self._result_window.add_image_thumbnail(image_base64, img)

        # 窗口保持可见 + 自动聚焦输入框
        if not self._result_window.isVisible():
            self._result_window.show()
        self._result_window.raise_()
        self._result_window.focus_input()

    # ============================================================
    # Step 2: AI Stream
    # ============================================================

    def _run_ai_stream(self, user_text: str, image_base64_list: Optional[List[str]] = None):
        """启动 AI 流式处理。支持多图列表。"""
        if image_base64_list is None:
            image_base64_list = []

        has_image = len(image_base64_list) > 0

        # 只有新截图时才重新定位窗口
        if has_image and self._last_capture_rect and not self._last_capture_rect.isEmpty():
            self._result_window.position_near_rect(self._last_capture_rect)
        elif has_image:
            self._result_window._position_bottom_right()

        # 在已有内容后追加用户问题标题
        if user_text.strip():
            img_count = len(image_base64_list)
            if img_count > 1:
                icon = f"🖼️×{img_count}"
            elif img_count == 1:
                icon = "🖼️"
            else:
                icon = "💬"
            existing = self._result_window.get_content()
            separator = "\n\n---\n\n" if existing.strip() else ""
            self._result_window.set_content(
                existing + separator +
                f"**{icon} 你：** {user_text}\n\n"
            )

        self._result_window.start_loading("⏳ AI 正在思考")
        self._result_window.show()
        self._result_window.raise_()

        self._stream_worker = StreamWorker(
            graph=self._graph,
            messages=list(self._messages),  # 传全部历史，检索模块智能选取
            user_text=user_text,
            image_base64_list=image_base64_list,
            user_id=self._user_id,
        )
        self._stream_worker.token_received.connect(self._on_token_received)
        self._stream_worker.stream_finished.connect(self._on_stream_finished)
        self._stream_worker.stream_error.connect(self._on_stream_error)
        self._stream_worker.start()

    def _on_token_received(self, token: str):
        if self._result_window:
            self._result_window.append_text(token)

    def _on_model_changed(self, model_name: str):
        """用户切换模型 — 保存到配置并更新运行时。"""
        set_model(model_name)
        import config
        config.DOUBAO_MODEL_NAME = model_name
        print(f"[AIRAG] 模型已切换: {model_name}")

    def _on_follow_up(self, text: str):
        """用户点击发送 — 收集所有待发送图片 + 文字一起提交 AI。"""
        # 收集所有待发送图片
        self._last_image_b64_list = self._result_window.get_pending_images()
        self._result_window.clear_pending_images()

        # 检查是否来自语音查询（Ctrl+Enter）
        speech_selected = self._result_window.get_speech_selected()
        if speech_selected and text == speech_selected:
            # 语音查询：加精简要回答提示
            text = f"以下是一段语音转文字的内容，请挑重点简要回答其中的问题：\n\n{text}"

        # 如果无文字也无图片，不发送
        if not text.strip() and not self._last_image_b64_list:
            return

        automation_task = self._extract_automation_task(text)
        if self._mode == "operate" and text.strip() and not self._last_image_b64_list:
            automation_task = automation_task or text.strip()

        if automation_task and not self._last_image_b64_list:
            self._result_window.clear_input()
            self._run_desktop_automation(automation_task, original_text=text)
            return

        self._last_user_text = text
        self._result_window.clear_input()
        self._run_ai_stream(text, image_base64_list=self._last_image_b64_list)

    def _extract_automation_task(self, text: str) -> str:
        """Route explicit desktop-operation commands from the main chat box."""
        stripped = (text or "").strip()
        prefixes = ("自动 ", "自动：", "自动:", "操作 ", "操作：", "操作:", "执行 ", "执行：", "执行:", "/agent ")
        for prefix in prefixes:
            if stripped.lower().startswith(prefix.lower()):
                return stripped[len(prefix):].strip()
        return ""

    def _run_desktop_automation(self, task: str, original_text: str = ""):
        if not task:
            return

        if getattr(self, "_agent_thread", None) and self._agent_thread.isRunning():
            self._result_window.append_text("\n\n**桌面助手正在执行上一个任务，请稍后再试。**\n")
            return

        existing = self._result_window.get_content()
        separator = "\n\n---\n\n" if existing.strip() else ""
        shown_text = original_text or f"自动 {task}"
        self._result_window.set_content(
            existing + separator + f"**🖥️ 你：** {shown_text}\n\n**桌面助手：**\n\n**实时进度：**\n"
        )
        self._result_window.start_loading("桌面助手正在操作")
        self._result_window.show()
        self._result_window.raise_()

        self._last_user_text = shown_text
        self._agent_log_lines = []
        use_mcp = _is_browser_automation_task(task)
        mode_text = "浏览器自动化 + 视觉保底" if use_mcp else "纯视觉/鼠标键盘"
        self._result_window.append_text(f"- 模式：{mode_text}\n")
        self._agent_log_lines.append(f"模式：{mode_text}")
        self._agent_thread = DesktopAgentProcessThread(
            task,
            use_mcp=use_mcp,
            user_id=self._user_id,
            memory_key=self._active_conv_id or "desktop",
        )
        self._agent_thread.progress.connect(self._on_desktop_agent_progress)
        self._agent_thread.done.connect(self._on_desktop_agent_done)
        self._agent_thread.start()

    def _cancel_desktop_automation(self):
        """ESC 回调：取消当前操作 / 停止远程 Agent。"""
        thread = getattr(self, "_agent_thread", None)
        if thread and thread.isRunning():
            self._result_window.stop_loading()
            # 断开信号，防止旧线程的 done 信号重复触发
            try:
                thread.progress.disconnect()
                thread.done.disconnect()
            except Exception:
                pass
            # 远程会话也直接终止，不再保留复用
            if hasattr(thread, "stop_agent"):
                thread.stop_agent()
            thread.cancel()
            self._agent_thread = None
            self._release_remote_task_state()

    def _release_remote_task_state(self):
        self._remote_session = False
        self._remote_task_running = False
        self._remote_current_task = ""
        self._remote_result_sent = False
        self._remote_pending_task = None
        if self._remote_server:
            self._remote_server.send_result(False, "操作已取消，可以发送新指令。")

    def _screen_pusher_loop(self):
        """后台线程：持续截图推送给手机。"""
        import pyautogui as _pg
        while not self._screen_pusher_stop.is_set():
            try:
                if self._remote_server:
                    img = _pg.screenshot()
                    self._remote_server.set_screenshot(img)
            except Exception:
                pass
            self._screen_pusher_stop.wait(1.5)

    def _on_desktop_agent_progress(self, msg: str):
        self._agent_log_lines.append(msg)
        if self._result_window:
            self._result_window.append_text(f"- {msg}\n")
        # 转发到手机远程
        if self._remote_server:
            self._remote_server.send_progress(msg)
            self._result_window.show()
            self._result_window.raise_()

    def _on_desktop_agent_done(self, success: bool, msg: str):
        # 清理
        if not getattr(self, "_remote_session", False):
            self._agent_thread = None
        if self._result_window:
            self._result_window.stop_loading()
            status = "完成" if success else "失败"
            self._result_window.append_text(f"\n**{status}：** {msg}\n")

        # 向远程手机发送结果
        if self._remote_server and getattr(self, "_remote_session", False) and not self._remote_result_sent:
            elapsed = ""
            try:
                # 尝试从 msg 提取耗时
                import re as _re
                m = _re.search(r'\((\d+\.\d+)s\)', msg)
                if m:
                    elapsed = m.group(1) + "s"
            except Exception:
                pass
            self._remote_server.send_result(success, msg, elapsed)
            self._remote_result_sent = True

        user_msg = HumanMessage(content=self._last_user_text)
        log_text = "\n".join(self._agent_log_lines[-20:]) if hasattr(self, "_agent_log_lines") else ""
        assistant_content = f"桌面助手执行{'成功' if success else '失败'}：{msg}"
        if log_text:
            assistant_content += f"\n\n执行日志：\n{log_text}"
        assistant_msg = AIMessage(content=assistant_content)
        self._messages.append(user_msg)
        self._messages.append(assistant_msg)
        self._save_current_conv()
        self._last_user_text = ""
        if getattr(self, "_remote_session", False):
            self._remote_task_running = False
            self._remote_current_task = ""
        # 远程会话保持线程不释放, 等下一轮指令复用
        if not getattr(self, "_remote_session", False):
            self._agent_thread = None

    def _on_stream_finished(self):
        full_response = ""
        if self._result_window:
            self._result_window.stop_loading()
            full_response = self._result_window.get_content()

        print(f"[AIRAG] 流式输出完成 ({len(full_response)} 字符)")

        # 保存上下文（支持多图）
        if self._last_user_text or self._last_image_b64_list:
            user_msg = build_multimodal_message(
                self._last_user_text or "请描述图片内容",
                image_base64_list=self._last_image_b64_list,
            )
            self._messages.append(user_msg)

        if full_response.strip():
            ai_msg = AIMessage(content=full_response.strip())
            self._messages.append(ai_msg)

        # 保存到对话 JSON + context_history
        self._save_current_conv()
        print(f"[AIRAG] 对话已保存 ({len(self._messages)} 条消息)")

        self._stream_worker = None
        self._last_image_b64_list = []

    def _on_stream_error(self, error_msg: str):
        print(f"[AIRAG] 流式错误: {error_msg}")
        if self._result_window:
            self._result_window.stop_loading()
            self._result_window.append_text(
                f"\n\n❌ **请求失败**: {error_msg}\n\n"
                f"请检查 API Key 和网络连接。"
            )
        self._stream_worker = None

    def _restore_redis_short_term(self):
        """Prefer Redis short-term memory when it has a warmer recent window."""
        if not redis_available():
            return

        messages = load_session_messages(self._user_id, self._active_conv_id)
        if not messages:
            return

        if len(self._messages) > len(messages):
            self._messages = self._messages[:-len(messages)] + messages
        else:
            self._messages = messages
        print(f"[SessionMemory] restored {len(messages)} recent messages from Redis")

    # ============================================================
    # Conversation Helpers
    # ============================================================

    def _conv_to_langchain(self, raw_messages: list) -> List[BaseMessage]:
        """将对话 JSON 中的消息转为 LangChain 消息。"""
        result = []
        for m in raw_messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "user":
                result.append(HumanMessage(content=content))
            else:
                result.append(AIMessage(content=content))
        return result

    def _messages_to_dicts(self) -> list:
        """将 LangChain 消息列表转为可序列化的 dict 列表。"""
        result = []
        for m in self._messages:
            role = "user" if isinstance(m, HumanMessage) else "assistant"
            content = m.content
            result.append({"role": role, "content": content})
        return result

    def _save_current_conv(self):
        """保存当前对话到 JSON + context_history。"""
        conv = {
            "id": self._active_conv_id,
            "title": "新对话",
            "model": "",
            "messages": self._messages_to_dicts(),
        }
        save_conversation(self._user_id, conv)
        save_context(self._messages, CONTEXT_FILE)
        sync_session_messages(self._user_id, self._active_conv_id, self._messages)

    # ============================================================
    # Sidebar
    # ============================================================

    def _on_quit_hotkey(self, key=None):
        """Ctrl+Q → 退出程序。"""
        QTimer.singleShot(0, self._quit_app)

    def _on_speech_hotkey(self, key=None):
        """Ctrl+Y → 开始/停止语音识别。"""
        QTimer.singleShot(0, self._toggle_speech)

    def _on_esc_hotkey(self, key=None):
        """ESC → 立即终止当前 GUI Agent 操作。"""
        QTimer.singleShot(0, self._cancel_desktop_automation)

    def _on_gui_agent_hotkey(self, key=None):
        """Ctrl+G → GUI Agent 自动化操作。"""
        QTimer.singleShot(0, self._start_gui_agent)

    def _start_gui_agent(self):
        """GUI Agent 独立进程 — 不阻塞 UI，不受 Qt 限制。"""
        from gui.gui_agent_panel import GuiAgentDialog

        dlg = GuiAgentDialog()

        def on_submit(task: str):
            dlg.accept()
            self._run_desktop_automation(task, original_text=f"自动 {task}")

        dlg.task_submitted.connect(on_submit)
        dlg.exec()

    # ============================================================
    # 远程控制 (手机 → PC)
    # ============================================================

    def _init_remote_server(self):
        """启动手机远程控制 HTTP/WebSocket 服务。"""
        try:
            from config import REMOTE_PORT
            port = REMOTE_PORT
        except ImportError:
            port = 8765

        self._remote_pending_task: Optional[str] = None  # 手机发来的待处理指令
        self._remote_current_task = ""
        self._remote_task_running = False
        self._remote_result_sent = False

        self._remote_server = RemoteServer(port=port)
        self._remote_server.on_command = self._on_remote_command
        self._remote_server.on_cancel = self._on_remote_cancel
        self._remote_server.start()

        # 主线程定时检查: 处理手机指令 + 截图推送
        self._remote_check_timer = QTimer()
        self._remote_check_timer.timeout.connect(self._remote_tick)
        self._remote_check_timer.start(1000)  # 每秒检查一次

        # 在悬浮窗显示连接信息
        self._show_remote_connection_info()

    def _remote_tick(self):
        """主线程定时: 检查手机指令 + 推截图."""
        # 处理手机发来的指令
        task = self._remote_pending_task
        if task:
            self._remote_pending_task = None
            self._run_remote_task(task)

        # 截图推送 (仅agent运行时)
        if self._remote_server and getattr(self, "_agent_thread", None) and self._agent_thread.isRunning():
            try:
                import pyautogui
                img = pyautogui.screenshot()
                self._remote_server.set_screenshot(img)
            except Exception:
                pass

    def _run_remote_task(self, task: str):
        """主线程: 执行手机发来的指令."""
        thread = getattr(self, "_agent_thread", None)
        if self._is_remote_stop_command(task):
            self._stop_remote_agent()
            return
        if self._remote_task_running:
            if task == self._remote_current_task:
                self._remote_server.send_progress("任务正在执行，已忽略重复指令。")
                return
            self._remote_server.send_progress("上一条任务还在执行中，请先取消或等待完成。")
            return
        print(f"[Remote] 执行: {task}")
        self._remote_server.send_progress(f"执行: {task}")
        if thread and thread.isRunning() and hasattr(thread, "send_task"):
            self._last_user_text = task
            self._agent_log_lines = []
            thread.send_task(task)
            return
        if thread and thread.isRunning():
            self._remote_server.send_progress("⚠️ 上一个任务还在执行中, 请稍后再试")
            return
        self._start_remote_agent(task)

    def _is_remote_stop_command(self, task: str) -> bool:
        text = (task or "").strip().lower()
        stop_keys = [
            "结束远程", "停止远程", "关闭远程", "退出远程",
            "结束会话", "停止会话", "关闭会话",
            "stop remote", "close remote", "quit remote", "end remote",
        ]
        return any(k in text for k in stop_keys)

    def _start_remote_agent(self, task: str):
        self._remote_session = True
        self._remote_current_task = task
        self._remote_task_running = True
        self._remote_result_sent = False
        self._last_user_text = task
        self._agent_log_lines = []
        self._agent_thread = _RemoteAgentThread(
            task,
            self._remote_server,
            use_mcp=True,
            user_id=self._user_id,
            memory_key=self._active_conv_id or "remote_desktop",
        )
        self._agent_thread.progress.connect(self._on_desktop_agent_progress)
        self._agent_thread.done.connect(self._on_desktop_agent_done)
        self._agent_thread.start()

    def _stop_remote_agent(self):
        thread = getattr(self, "_agent_thread", None)
        if thread and thread.isRunning() and hasattr(thread, "stop_agent"):
            self._remote_server.send_progress("正在结束远程 Agent 进程。")
            thread.stop_agent()
        self._agent_thread = None
        self._release_remote_task_state()

    def _push_remote_screenshot(self):
        """截图推送给远程手机."""
        if not self._remote_server:
            return
        try:
            import pyautogui
            img = pyautogui.screenshot()
            self._remote_server.set_screenshot(img)
        except Exception:
            pass

    def _show_remote_connection_info(self):
        """在悬浮窗/日志中显示远程连接地址和 QR 码。"""
        try:
            from config import REMOTE_PORT
            port = REMOTE_PORT
        except ImportError:
            port = 8765

        # ngrok token: 环境变量 > config > 空(跳过)
        ngrok_token = os.environ.get("NGROK_AUTH_TOKEN", "")
        if not ngrok_token:
            try:
                from config import NGROK_AUTH_TOKEN
                ngrok_token = NGROK_AUTH_TOKEN
            except ImportError:
                pass

        urls = get_connection_urls(port, ngrok_token=ngrok_token)
        if urls:
            lines = ["\n📱 **手机远程控制**\n"]
            for u in urls:
                lines.append(f"- {u['label']}: {u['url']}")
            lines.append(f"\n打开上述地址即可唤醒AI助手远程控制。")
            self._result_window.append_text("\n".join(lines))
            print(f"[Remote] 连接信息: {[(u['label'], u['url']) for u in urls]}")
        else:
            print("[Remote] 未检测到局域网 IP")

    def _on_remote_command(self, task: str):
        """手机发来指令 → 设置标记, 主线程定时检查并处理."""
        if self._remote_task_running:
            if task == self._remote_current_task:
                return
            self._remote_server.send_progress("上一条任务还在执行中，请先取消或等待完成。")
            return
        if self._remote_pending_task == task:
            return
        self._remote_pending_task = task
        self._remote_server.send_progress(f"📨 已收到: {task}")

    def _on_remote_cancel(self):
        """手机发来取消信号。"""
        QTimer.singleShot(0, self._cancel_desktop_automation)

    def _toggle_speech(self):
        """切换语音识别开关。"""
        worker = get_speech_worker()
        if worker.is_running():
            worker.stop()
            try:
                worker.signal.sentence_ready.disconnect(self._on_speech_sentence)
                worker.signal.error_occurred.disconnect(self._on_speech_error)
            except Exception:
                pass
            self._result_window.set_speech_status("")
            self._tray.showMessage("Ai_Flow", "语音识别已停止", QSystemTrayIcon.MessageIcon.Information, 1500)
        else:
            worker.clear()
            worker.signal.sentence_ready.connect(self._on_speech_sentence)
            worker.signal.error_occurred.connect(self._on_speech_error)
            worker.start()
            self._result_window.set_speech_status("🎤 正在听...")
            self._result_window.show()
            self._result_window.raise_()
            self._tray.showMessage("Ai_Flow", "语音识别已启动\nCtrl+Y 停止 | 滚轮选句子 | Ctrl+Enter 提问", QSystemTrayIcon.MessageIcon.Information, 2000)

    def _on_speech_sentence(self, text: str, all_sentences: list):
        """新句子识别完成 → pyqtSignal 已切回主线程，直接更新 UI。"""
        if text:  # 只有非空文本才更新
            self._result_window.set_speech_sentences(all_sentences)

    def _on_speech_error(self, err: str):
        print(f"[Speech] 错误: {err}")
        self._result_window.set_speech_status(f"❌ {err}")

    def _refresh_sidebar(self):
        convs = list_conversations(self._user_id)
        self._result_window.refresh_sidebar(convs, self._active_conv_id)

    def _on_conv_selected(self, conv_id: str):
        """用户点击侧边栏对话 → 切换。"""
        if conv_id == self._active_conv_id:
            return
        # 保存当前对话 + 提取记忆（后台）
        self._save_current_conv()
        self._extract_memory_background()
        # 加载新对话
        conv = load_conversation(self._user_id, conv_id)
        if conv:
            self._messages = self._conv_to_langchain(conv.get("messages", []))
            self._active_conv_id = conv_id
            set_active_conversation_id(self._user_id, conv_id)
            # 恢复显示
            self._result_window.clear_content()
            if self._messages:
                self._restore_conv_display()
            self._refresh_sidebar()
            print(f"[AIRAG] 已切换到: {conv.get('title', '')}")

    def _on_new_conv(self):
        """新建对话。"""
        self._save_current_conv()
        self._extract_memory_background()
        conv = new_conversation(self._user_id, "新对话")
        self._active_conv_id = conv["id"]
        set_active_conversation_id(self._user_id, self._active_conv_id)
        self._messages = []
        self._result_window.clear_content()
        self._result_window.clear_pending_images()
        self._refresh_sidebar()
        print("[AIRAG] 新建对话")

    def _restore_conv_display(self):
        """恢复对话内容到 ResultWindow。"""
        text = ""
        for m in self._messages:
            role = "你" if isinstance(m, HumanMessage) else "AI"
            icon = "💬" if isinstance(m, HumanMessage) else "🤖"
            content = m.content
            if isinstance(content, str):
                text += f"\n\n**{icon} {role}：** {content}"
        if text:
            self._result_window.set_content(text.strip())

    # ============================================================
    # Memory Extraction (Background Thread)
    # ============================================================

    def _extract_memory_background(self):
        """后台提取长期记忆（不阻塞用户）。"""
        if len(self._messages) < 6:  # 对话太短不提取
            return
        # 在 QThread 中运行，不阻塞 UI
        class MemWorker(QThread):
            finished = pyqtSignal(list)
            def __init__(self, messages, user_id):
                super().__init__()
                self._msgs = [{"role": "user" if isinstance(m, HumanMessage) else "assistant",
                               "content": m.content} for m in messages]
                self._uid = user_id
            def run(self):
                facts = extract_facts_from_conversation(self._msgs, self._uid)
                self.finished.emit(facts)

        self._mem_worker = MemWorker(list(self._messages), self._user_id)
        self._mem_worker.finished.connect(self._on_mem_extracted)
        self._mem_worker.start()

    def _on_mem_extracted(self, facts: list):
        """记忆提取完成 → 合并到 profile。"""
        if not facts:
            return
        self._profile = load_profile(self._user_id)
        old_count = len(self._profile.get("facts", []))
        self._profile["facts"] = merge_facts(self._profile.get("facts", []), facts)
        self._profile["stats"]["total_conversations"] += 1
        save_profile(self._user_id, self._profile)
        new_count = len(self._profile.get("facts", []))
        if new_count > old_count:
            print(f"[Memory] 提取了 {new_count - old_count} 条新记忆（共 {new_count} 条）")

    def _extract_memory_on_exit(self):
        """退出时同步提取记忆（等完成再退出）。"""
        if len(self._messages) < 6:
            return
        msgs = [{"role": "user" if isinstance(m, HumanMessage) else "assistant",
                 "content": m.content} for m in self._messages]
        facts = extract_facts_from_conversation(msgs, self._user_id)
        if facts:
            self._profile = load_profile(self._user_id)
            self._profile["facts"] = merge_facts(self._profile.get("facts", []), facts)
            save_profile(self._user_id, self._profile)
            print(f"[Memory] 退出时保存记忆")

    # ============================================================
    # History
    # ============================================================

    def _clear_history(self):
        """清空对话历史（窗口保持显示）。"""
        self._messages = []
        save_context(self._messages, CONTEXT_FILE)
        clear_session_messages(self._user_id, self._active_conv_id)
        print("[AIRAG] 对话历史已清空")
        self._result_window.clear_content()
        self._tray.showMessage(
            "AIRAG",
            "对话历史已清空",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )

    def _check_api_key(self):
        """启动时检查 API Key，没有则弹出设置框。"""
        key = get_api_key()
        if not key:
            self._tray.showMessage(
                "AIRAG",
                "未检测到 API Key，请设置后使用",
                QSystemTrayIcon.MessageIcon.Warning,
                3000,
            )
            QTimer.singleShot(1000, self._change_api_key)

    def _change_api_key(self):
        """弹出设置弹窗（API Key + 代理 + OCR 凭证）。"""
        from gui.api_key_dialog import ApiKeyDialog
        dlg = ApiKeyDialog()
        if dlg.exec() == ApiKeyDialog.DialogCode.Accepted:
            # 重新加载所有配置
            import config
            config.ARK_API_KEY = get_api_key()
            config.HTTP_PROXY = get_proxy()
            config.DOUBAO_MODEL_NAME = get_model()
            print("[AIRAG] 设置已保存")
            self._tray.showMessage(
                "Ai_Flow",
                "设置已保存",
                QSystemTrayIcon.MessageIcon.Information,
                2000,
            )


# ============================================================
# Entry Point
# ============================================================

def main():
    try:
        print()
        print("=" * 58)
        print("   AIRAG - AI Screenshot Assistant")
        print("=" * 58)
        print(f"   {DEFAULT_HOTKEY.upper()} — 截图发送")
        print(f"   {TOGGLE_HOTKEY.upper()} — 隐藏/显示窗口")
        print(f"   直接输入文字即可与 AI 对话")
        print(f"   Model:      doubao-seed-2-0-mini-260428")
        print(f"   记忆:       最近 {RECENT_ROUNDS} 轮对话")
        print("=" * 58)
        print("   [*] Check system tray (bottom-right) for icon")
        print("   [*] Right-click tray icon to capture manually")
        print("   [*] Run as Administrator if hotkey fails")
        print("=" * 58)
    except UnicodeEncodeError:
        pass  # Windows console GBK encoding doesn't support emoji
    print()

    # 设置网络代理（火山引擎 API 需要）
    if HTTP_PROXY:
        import os
        os.environ["HTTP_PROXY"] = HTTP_PROXY
        os.environ["HTTPS_PROXY"] = HTTP_PROXY
        print(f"   Proxy:      {HTTP_PROXY}")
    print()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 检查是否支持系统托盘
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("[AIRAG] [WARN] 系统托盘不可用，请确保桌面环境支持。")

    agent = ScreenAIAgent(app)

    try:
        sys.exit(app.exec())
    finally:
        try:
            if hasattr(agent, '_hotkey_listener') and agent._hotkey_listener:
                agent._hotkey_listener.stop()
        except Exception:
            pass
        print("[AIRAG] 已退出。")


if __name__ == "__main__":
    main()
