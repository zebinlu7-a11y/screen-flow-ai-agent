# -*- coding: utf-8 -*-
"""
手机远程控制 PC 服务 — aiohttp HTTP API (轮询模式, 兼容所有手机浏览器)
"""
import os
import io
import re
import json
import time
import shutil
import base64
import asyncio
import threading
from typing import Optional, Callable

import aiohttp
from aiohttp import web

# 安全 harness
from utils.risk_engine import assess_risk, RiskLevel
from utils.audit_store import AuditStore, AuditRecord, sanitize_command
from utils.rollback import get_alternatives, build_alternative_command


class RemoteServer:
    """HTTP API 服务器，运行在独立守护线程中。手机通过轮询获取更新。"""

    def __init__(self, port: int = 8765, audit_store: Optional[AuditStore] = None):
        self._port = port
        self._app = web.Application()
        self._runner = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # 回调
        self.on_command: Optional[Callable[[str], None]] = None
        self.on_cancel: Optional[Callable[[], None]] = None
        self.on_hint: Optional[Callable[[str], None]] = None
        self.on_direct_command: Optional[Callable[[str], None]] = None
        self.on_confirmation_needed: Optional[Callable[[dict], None]] = None

        # 截图缓存
        self._screenshot_lock = threading.Lock()
        self._latest_screenshot: Optional[str] = None
        self._latest_screenshot_time: str = ""

        # 日志缓冲
        self._log_lock = threading.Lock()
        self._log_buffer: list[dict] = []
        self._log_id = 0

        # 最终结果
        self._final_result: Optional[dict] = None
        self._agent_running = False
        # 手机指令去重：3秒内重复指令忽略
        self._last_command = ""
        self._last_command_time = 0.0

        # ── 安全 harness: 确认状态 ──
        self._pending_confirmation: Optional[dict] = None
        self._confirmation_time: float = 0.0
        self._confirmation_timeout: float = 120.0  # 2分钟超时

        # ── 审计日志 ──
        self._audit_store = audit_store
        self._audit_session_id: str = ""

        # ── 任务路由标签 ──
        self._task_type: str = ""

        # PyInstaller 打包后 __file__ 指向临时目录，用 sys._MEIPASS 获取真实路径
        import sys as _sys
        if getattr(_sys, 'frozen', False):
            _base = _sys._MEIPASS
        else:
            _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._html_path = os.path.join(_base, "remote", "phone.html")
        self._setup_routes()

    def _setup_routes(self):
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/api/status", self._handle_status)
        self._app.router.add_get("/api/updates", self._handle_updates)
        self._app.router.add_post("/api/command", self._handle_command)
        self._app.router.add_post("/api/cancel", self._handle_cancel)
        self._app.router.add_post("/api/hint", self._handle_hint)
        self._app.router.add_post("/api/confirm", self._handle_confirm)
        self._app.router.add_post("/api/reject", self._handle_reject)

    async def _handle_index(self, request):
        try:
            with open(self._html_path, "r", encoding="utf-8") as f:
                html = f.read()
        except FileNotFoundError:
            html = "<h1>phone.html not found</h1>"
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    async def _handle_status(self, request):
        return web.json_response({"status": "ok"})

    async def _handle_updates(self, request):
        since = int(request.query.get("since", "0"))
        with self._log_lock:
            new_logs = [l for l in self._log_buffer if l["id"] > since]
            last_id = self._log_buffer[-1]["id"] if self._log_buffer else since
        with self._screenshot_lock:
            img = self._latest_screenshot
            img_time = self._latest_screenshot_time
        result = self._final_result
        self._final_result = None

        # ── 确认超时检查 ──
        if self._pending_confirmation and time.time() - self._confirmation_time > self._confirmation_timeout:
            expired_task = self._pending_confirmation.get("task", "")
            self._add_log(f"⏰ 确认超时，指令已自动取消: {expired_task}")
            self._pending_confirmation = None
            # 审计日志: 超时
            if self._audit_store:
                self._audit_store.record(AuditRecord(
                    command_text=sanitize_command(expired_task),
                    risk_level="high",
                    confirmation_requested=True,
                    confirmation_response="timed_out",
                    execution_path="blocked",
                    session_id=self._audit_session_id,
                ))

        # ── 确定状态 ──
        if self._pending_confirmation:
            state = "confirming"
        elif self._agent_running:
            state = "running"
        else:
            state = "ready"

        return web.json_response({
            "logs": new_logs,
            "last_id": last_id,
            "screenshot": img,
            "img_time": img_time,
            "result": result,
            "state": state,
            "pending_confirmation": self._pending_confirmation,
            "task_type": self._task_type,
        })

    async def _handle_command(self, request):
        try:
            data = await request.json()
            text = data.get("text", "").strip()
            if not text:
                return web.json_response({"ok": True})
            now = time.time()
            # 3秒内重复指令忽略
            if text == self._last_command and now - self._last_command_time < 3.0:
                return web.json_response({"ok": True, "skipped": True})
            self._last_command = text
            self._last_command_time = now
            print(f"[Remote] HTTP收到指令: {text}")
            self._add_log(f"📨 收到指令: {text}")

            if text.lower() in ("取消", "停止", "cancel", "stop"):
                result = "已取消，可以发送新指令。"
                self._add_log(result)
                if self.on_cancel:
                    self.on_cancel()
                self._final_result = {"success": True, "message": result, "elapsed": ""}
                self._agent_running = False
                self._pending_confirmation = None
                return web.json_response({"ok": True, "direct": True, "result": result})

            # —— 设备直达命令（不经过 Agent，直接执行）——
            from utils.direct_commands import try_direct_command
            handled, result = try_direct_command(text)
            if handled:
                self._add_log(result)
                self._final_result = {"success": True, "message": result, "elapsed": ""}
                self._agent_running = False
                if self.on_direct_command:
                    self.on_direct_command(result)
                return web.json_response({"ok": True, "direct": True, "result": result})

            # ── 如果当前有待确认指令，拒绝新指令 ──
            if self._pending_confirmation:
                self._add_log("⚠️ 有待确认指令，请先处理后再发新指令")
                return web.json_response({
                    "ok": False,
                    "error": "请先处理待确认的指令，或等待超时自动取消",
                    "confirming": True,
                })

            # ── ★ 安全 harness: 风险评估 ──
            assessment = assess_risk(text)
            if assessment.needs_confirmation:
                # 支付/转账直接阻止
                if assessment.is_blocked:
                    self._add_log(f"🚫 指令已被安全策略阻止: {assessment.reason}")
                    if self._audit_store:
                        self._audit_store.record(AuditRecord(
                            command_text=sanitize_command(text),
                            risk_level="critical",
                            risk_reason=assessment.reason,
                            risk_category=assessment.category,
                            confirmation_requested=True,
                            confirmation_response="blocked",
                            execution_path="blocked",
                            rollback_hint=assessment.rollback_hint,
                            session_id=self._audit_session_id,
                        ))
                    return web.json_response({
                        "ok": False,
                        "blocked": True,
                        "reason": assessment.reason,
                        "alternative": assessment.alternative,
                    })

                # 高风险 / 严重风险：等待用户确认
                self._confirmation_time = time.time()
                self._pending_confirmation = {
                    "task": text,
                    "risk_level": assessment.level.value,
                    "risk_reason": assessment.reason,
                    "rollback_hint": assessment.rollback_hint,
                    "alternative": assessment.alternative,
                    "alternatives": get_alternatives(assessment.category),
                    "category": assessment.category,
                    "requested_at": time.strftime("%H:%M:%S"),
                }
                self._add_log(f"⚠️ 需要确认: {assessment.reason}")
                print(f"[Remote] 高风险指令需确认: {text} → {assessment.level.value} ({assessment.category})")

                # 审计日志
                if self._audit_store:
                    self._audit_store.record(AuditRecord(
                        command_text=sanitize_command(text),
                        risk_level=assessment.level.value,
                        risk_reason=assessment.reason,
                        risk_category=assessment.category,
                        confirmation_requested=True,
                        confirmation_response="",  # 待用户响应
                        execution_path="",
                        rollback_hint=assessment.rollback_hint,
                        session_id=self._audit_session_id,
                    ))

                # 通知回调（可选，用于桌面端通知）
                if self.on_confirmation_needed:
                    self.on_confirmation_needed(self._pending_confirmation)

                return web.json_response({"ok": True, "confirming": True})

            # SAFE: 直接进入 Agent 执行链
            if self.on_command:
                self.on_command(text)
            self._agent_running = True
            self._final_result = None
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def _handle_cancel(self, request):
        self._add_log("⏹️ 已发送取消信号")
        if self.on_cancel:
            self.on_cancel()
        self._agent_running = False
        self._pending_confirmation = None  # 清除待确认状态
        return web.json_response({"ok": True})

    async def _handle_hint(self, request):
        try:
            data = await request.json()
            text = data.get("text", "").strip()
            if not text:
                return web.json_response({"ok": True})
            self._add_log(f"💡 用户提示: {text}")
            if self.on_hint:
                self.on_hint(text)
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def _handle_confirm(self, request):
        """用户确认执行高风险指令"""
        try:
            data = await request.json()
            confirmed = data.get("confirm", False)
            pending = self._pending_confirmation

            if not pending:
                return web.json_response({"ok": False, "error": "没有待确认的指令"}, status=400)

            # 先清除 pending（防重入）
            task_text = pending["task"]
            risk_level = pending["risk_level"]
            risk_category = pending.get("category", "unknown")
            confirmation_time = self._confirmation_time
            self._pending_confirmation = None

            if confirmed:
                self._add_log(f"✅ 用户确认执行: {task_text}")
                # 审计日志: 确认
                if self._audit_store:
                    self._audit_store.record(AuditRecord(
                        command_text=sanitize_command(task_text),
                        risk_level=risk_level,
                        risk_category=risk_category,
                        confirmation_requested=True,
                        confirmation_response="confirmed",
                        confirmation_latency_ms=(time.time() - confirmation_time) * 1000,
                        execution_path="agent",
                        rollback_hint=pending.get("rollback_hint", ""),
                        session_id=self._audit_session_id,
                    ))
                # 进入正常执行链
                if self.on_command:
                    self.on_command(task_text)
                self._agent_running = True
                self._final_result = None
                return web.json_response({"ok": True, "executing": True})
            else:
                self._add_log(f"❌ 用户拒绝执行: {task_text}")
                return web.json_response({"ok": True, "rejected": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def _handle_reject(self, request):
        """用户拒绝高风险指令，可选替代方案"""
        try:
            data = await request.json()
            alternative_id = data.get("alternative", "")
            pending = self._pending_confirmation

            if not pending:
                return web.json_response({"ok": False, "error": "没有待确认的指令"}, status=400)

            task_text = pending["task"]
            risk_level = pending["risk_level"]
            risk_category = pending.get("category", "unknown")
            confirmation_time = self._confirmation_time
            self._pending_confirmation = None

            self._add_log(f"❌ 用户拒绝: {task_text}"
                          + (f"，选择替代方案: {alternative_id}" if alternative_id else ""))

            # 审计日志: 拒绝
            if self._audit_store:
                self._audit_store.record(AuditRecord(
                    command_text=sanitize_command(task_text),
                    risk_level=risk_level,
                    risk_category=risk_category,
                    confirmation_requested=True,
                    confirmation_response="rejected",
                    confirmation_latency_ms=(time.time() - confirmation_time) * 1000,
                    alternative_accepted=str(alternative_id) if alternative_id else "",
                    execution_path="blocked",
                    rollback_hint=pending.get("rollback_hint", ""),
                    session_id=self._audit_session_id,
                ))

            # 如果用户选择了替代方案，构建替代命令并重新提交
            if alternative_id:
                new_command = build_alternative_command(task_text, risk_category, alternative_id)
                if new_command:
                    self._add_log(f"💡替代方案: {new_command}")
                    # 替代命令走正常安全检查流程（通常会被判为 SAFE）
                    alt_assessment = assess_risk(new_command)
                    if alt_assessment.needs_confirmation:
                        self._add_log("⚠️替代指令仍为高风险，再次请求确认")
                        self._confirmation_time = time.time()
                        self._pending_confirmation = {
                            "task": new_command,
                            "risk_level": alt_assessment.level.value,
                            "risk_reason": alt_assessment.reason,
                            "rollback_hint": alt_assessment.rollback_hint,
                            "alternative": alt_assessment.alternative,
                            "alternatives": get_alternatives(alt_assessment.category),
                            "category": alt_assessment.category,
                            "requested_at": time.strftime("%H:%M:%S"),
                        }
                        return web.json_response({"ok": True, "confirming": True, "alternative_command": new_command})
                    else:
                        # 替代命令安全，直接执行
                        if self.on_command:
                            self.on_command(new_command)
                        self._agent_running = True
                        self._final_result = None
                        return web.json_response({"ok": True, "executing": True,
                                                   "alternative_command": new_command})

            return web.json_response({"ok": True, "rejected": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    def _add_log(self, text: str, ok: Optional[bool] = None):
        with self._log_lock:
            self._log_id += 1
            self._log_buffer.append({"id": self._log_id, "text": text, "ok": ok})
            if len(self._log_buffer) > 100:
                self._log_buffer = self._log_buffer[-50:]

    # ----- 外部 (Qt 主线程) 接口 -----

    def set_screenshot(self, pil_image):
        buf = io.BytesIO()
        w, h = pil_image.size
        if w > 720:
            ratio = 720 / w
            pil_image = pil_image.resize((720, int(h * ratio)))
        pil_image.convert("RGB").save(buf, format="JPEG", quality=50)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        with self._screenshot_lock:
            self._latest_screenshot = b64
            self._latest_screenshot_time = time.strftime("%H:%M:%S")

    def send_progress(self, text: str):
        self._add_log(text)

    def set_task_type(self, task_type: str):
        """设置当前任务类型标签（🆕新任务 / 🔄延续任务）"""
        self._task_type = task_type

    def send_result(self, success: bool, message: str, elapsed: str = "", keep_running: bool = False):
        self._final_result = {"success": success, "message": message, "elapsed": elapsed}
        if not keep_running:
            self._agent_running = False
        self._add_log(message, ok=success)

    def start(self):
        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._runner = web.AppRunner(self._app)
            loop.run_until_complete(self._runner.setup())
            site = web.TCPSite(self._runner, "0.0.0.0", self._port)
            loop.run_until_complete(site.start())
            print(f"[Remote] 服务器已启动: http://0.0.0.0:{self._port}")
            loop.run_forever()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._loop and self._runner:
            try:
                async def _shutdown():
                    await self._runner.cleanup()
                future = asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
                future.result(timeout=3)
            except Exception:
                pass
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)


# ============================================================
# IP / QR / Tunnel
# ============================================================

_ngrok_tunnel = None
_cloudflared_proc = None


def start_ngrok_tunnel(port: int, auth_token: str = "") -> Optional[str]:
    global _ngrok_tunnel
    if os.environ.get("AIRAG_ENABLE_NGROK", "0") != "1":
        return None
    if not auth_token:
        return None
    try:
        from pyngrok import ngrok, conf
        conf.get_default().auth_token = auth_token
        _ngrok_tunnel = ngrok.connect(port, "http")
        url = _ngrok_tunnel.public_url
        print(f"[Ngrok] 隧道已建立: {url}")
        return url
    except Exception as e:
        print(f"[Ngrok] 启动失败: {e}")
        return None


def stop_ngrok_tunnel():
    global _ngrok_tunnel
    if _ngrok_tunnel:
        try:
            from pyngrok import ngrok
            ngrok.disconnect(_ngrok_tunnel.public_url)
        except Exception:
            pass
        _ngrok_tunnel = None


def start_cloudflare_tunnel(port: int) -> Optional[str]:
    """启动 Cloudflare Tunnel (cloudflared), 返回 trycloudflare URL."""
    global _cloudflared_proc
    import subprocess
    try:
        # Find cloudflared binary
        cf = shutil.which("cloudflared")
        if not cf:
            import sys as _sys

            if getattr(_sys, "frozen", False):
                app_dir = os.path.dirname(_sys.executable)
            else:
                app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            for rel in ("cloudflared.exe", os.path.join("bin", "cloudflared.exe"), os.path.join("tools", "cloudflared.exe")):
                candidate = os.path.join(app_dir, rel)
                if os.path.exists(candidate):
                    cf = candidate
                    break
        if not cf:
            # Try winget install path
            import glob as _glob
            base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
            pattern = os.path.join(base, "Cloudflare.cloudflared_*", "cloudflared.exe")
            matches = _glob.glob(pattern)
            cf = matches[0] if matches else None
        if not cf:
            print("[Cloudflare] cloudflared 未找到, 跳过")
            return None

        _cloudflared_proc = subprocess.Popen(
            [cf, "tunnel", "--url", f"http://localhost:{port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        # Parse URL from output (e.g. "https://xxx.trycloudflare.com")
        import time as _time
        deadline = _time.time() + 10
        url = None
        for line in _cloudflared_proc.stdout:
            m = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', line)
            if m:
                url = m.group(0)
                break
            if _time.time() > deadline:
                break
        if url:
            print(f"[Cloudflare] 隧道已建立: {url}")
            return url
        else:
            print("[Cloudflare] 隧道启动超时")
            _cloudflared_proc.terminate()
            _cloudflared_proc = None
            return None
    except Exception as e:
        print(f"[Cloudflare] 启动失败: {e}")
        return None


def stop_cloudflare_tunnel():
    global _cloudflared_proc
    if _cloudflared_proc:
        try:
            _cloudflared_proc.terminate()
            _cloudflared_proc.wait(timeout=3)
        except Exception:
            pass
        _cloudflared_proc = None


def get_local_ips() -> list[str]:
    import socket
    ips = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127."):
                continue
            if ip not in ips:
                ips.append(ip)
        ips.sort(key=lambda x: (not x.startswith("192.168"), x))
    except Exception:
        pass
    return ips


def get_tailscale_ip() -> Optional[str]:
    import subprocess
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def generate_qr_base64(url: str, size: int = 200) -> str:
    import qrcode
    qr = qrcode.QRCode(box_size=4, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def get_connection_urls(port: int, ngrok_token: str = "") -> list[dict]:
    urls = []
    for ip in get_local_ips():
        url = f"http://{ip}:{port}"
        urls.append({"label": f"局域网 ({ip})", "url": url, "qr_base64": generate_qr_base64(url)})
    ts_ip = get_tailscale_ip()
    if ts_ip:
        url = f"http://{ts_ip}:{port}"
        urls.append({"label": f"Tailscale ({ts_ip})", "url": url, "qr_base64": generate_qr_base64(url)})

    # 公网: 优先用配置的固定地址，否则自动 tunnel
    try:
        from config import REMOTE_PUBLIC_URL
    except ImportError:
        REMOTE_PUBLIC_URL = ""
    if REMOTE_PUBLIC_URL:
        urls.append({"label": "公网 (固定)", "url": REMOTE_PUBLIC_URL, "qr_base64": generate_qr_base64(REMOTE_PUBLIC_URL)})
    else:
        tunnel_url = start_cloudflare_tunnel(port)
        if not tunnel_url:
            tunnel_url = start_ngrok_tunnel(port, ngrok_token)
        if tunnel_url:
            urls.append({"label": "公网 (任何网络)", "url": tunnel_url, "qr_base64": generate_qr_base64(tunnel_url)})
    return urls
