"""
GUI Agent RPA — Playwright MCP Server + ReAct 纯视觉引擎。

架构（对齐 AI_RPA_pyqt.py）:
  1. ReAct Agent：截图 → 模型观察思考 → 决定下一步 (click/type/press/scroll/wait)
  2. 纯视觉定位：模型直接返回归一化坐标(0-1000)，转换为屏幕绝对坐标
  3. pyautogui 执行
  4. 循环直到模型判定任务完成或达到最大步数
"""
import os
import re
import json
import time
import base64
import io
import socket
import shutil
import threading
import tempfile
import subprocess
from typing import List, Optional, Callable

from PIL import Image

from config import (
    MCP_SERVER_PACKAGES,
    MCP_INITIALIZE_TIMEOUT,
    MCP_SCREENSHOT_WIDTH,
    MCP_SCREENSHOT_HEIGHT,
)


# ============================================================
# MCP Exceptions
# ============================================================

class MCPError(Exception):
    """Base MCP protocol error."""
    def __init__(self, code: int, message: str, data=None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"MCP Error {code}: {message}")

class MCPToolError(MCPError):
    """Tool execution returned isError=true."""
    def __init__(self, tool_name: str, result: dict):
        self.tool_name = tool_name
        super().__init__(-32000, f"Tool '{tool_name}' failed", result)

class MCPTimeoutError(MCPError):
    """Request timeout."""
    def __init__(self, method: str, timeout: float):
        super().__init__(-1, f"Request '{method}' timed out after {timeout}s")

class MCPConnectionError(MCPError):
    """Server connection lost (process died, pipe broken)."""
    def __init__(self, reason: str):
        super().__init__(-2, f"MCP server connection lost: {reason}")

class MCPServerNotFound(MCPError):
    """npx / Node.js not available."""
    def __init__(self):
        super().__init__(-3, "npx not found in PATH. Install Node.js to use Playwright MCP server.")


# ============================================================
# Shared JS Snippets (used by both MCP and CDP paths)
# ============================================================

_SCAN_DOM_JS = r"""
() => {
    window._ai_elements = [];
    const elements = [];
    let i = 0;
    const pageInfo = {
        title: document.title,
        url: window.location.href,
        viewport: { width: window.innerWidth, height: window.innerHeight }
    };

    function collect(root, depth) {
        const selectors = 'input, button, textarea, a, [role="button"], .el-button, .el-input__inner, span, div, li, h1, h2, h3, h4, h5, h6, p, label, form, select, option, img, iframe, section, article, nav, header, footer';
        const nodes = root.querySelectorAll(selectors);
        nodes.forEach(el => {
            const rect = el.getBoundingClientRect();
            if (rect.width > 1 && rect.height > 1 && rect.top < window.innerHeight + 1000) {
                const rawText = [el.innerText, el.value, el.placeholder, el.title, el.getAttribute('name'), el.getAttribute('aria-label'), el.getAttribute('alt')].filter(Boolean).join(' ').trim().substring(0, 150);
                let parentText = "";
                if (el.parentElement) {
                    parentText = (el.parentElement.innerText || "").trim().substring(0, 80);
                }
                elements.push({
                    tag: el.tagName,
                    text: rawText,
                    value: el.value || "",
                    placeholder: el.placeholder || "",
                    title: el.title || "",
                    id: el.id || "",
                    name: el.getAttribute('name') || "",
                    ariaLabel: el.getAttribute('aria-label') || "",
                    className: (el.className || "").substring(0, 80),
                    href: el.href || "",
                    src: el.src || "",
                    type: el.type || el.getAttribute('type') || "",
                    role: el.getAttribute('role') || "",
                    pos: { x: rect.left, y: rect.top, w: rect.width, h: rect.height },
                    depth: depth,
                    parentText: parentText,
                    ai_id: i
                });
                window._ai_elements.push(el);
                i++;
            }
        });

        // Shadow DOM
        root.querySelectorAll('*').forEach(node => {
            if (node.shadowRoot) {
                collect(node.shadowRoot, depth + 1);
            }
        });

        // iframe
        root.querySelectorAll('iframe').forEach(iframe => {
            try {
                const iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
                if (iframeDoc) {
                    const rect = iframe.getBoundingClientRect();
                    elements.push({
                        tag: 'IFRAME',
                        text: 'IFRAME: ' + (iframe.src || 'unknown'),
                        src: iframe.src || "",
                        pos: { x: rect.left, y: rect.top, w: rect.width, h: rect.height },
                        depth: depth,
                        parentText: "",
                        ai_id: i
                    });
                    window._ai_elements.push(iframe);
                    i++;
                    collect(iframeDoc, depth + 1);
                }
            } catch(e) {}
        });
    }

    collect(document, 0);
    return JSON.stringify({ pageInfo: pageInfo, elements: elements });
}
"""


def _build_execute_js(ai_id: int, action: str, value: str = "") -> str:
    """Build JS for element operation."""
    return r"""(function() {
    const el = window._ai_elements[""" + str(ai_id) + r"""];
    if (!el) return "ELEMENT_LOST";
    el.scrollIntoView({block: 'center', behavior: 'instant'});

    if ("__ACTION__" === "click") {
        const opts = { bubbles: true, cancelable: true, view: window, buttons: 1 };
        el.dispatchEvent(new PointerEvent('pointerdown', opts));
        el.dispatchEvent(new MouseEvent('mousedown', opts));
        el.focus();
        el.dispatchEvent(new MouseEvent('mouseup', opts));
        el.dispatchEvent(new PointerEvent('pointerup', opts));
        el.click();
        return "OK";
    }
    else if ("__ACTION__" === "fill") {
        el.focus();
        el.value = __VALUE__;
        ['input', 'change', 'blur'].forEach(function(ev) {
            el.dispatchEvent(new Event(ev, { bubbles: true }));
        });
        return "OK";
    }
    else if ("__ACTION__" === "move") {
        const r = el.getBoundingClientRect();
        return JSON.stringify({ x: r.left + r.width/2, y: r.top + r.height/2 });
    }
    return "OK";
})()""".replace("__ACTION__", action).replace("__VALUE__", json.dumps(str(value)))


# ============================================================
# Playwright MCP Client (JSON-RPC 2.0 over stdio)
# ============================================================

class PlaywrightMCPClient:
    """JSON-RPC 2.0 communication with Playwright MCP Server."""

    def __init__(self, headless: bool = False):
        self._process = None
        self._request_id = 0
        self._pending = {}
        self._responses = {}
        self._lock = threading.Lock()
        self._reader_thread = None
        self._stderr_thread = None
        self._running = False
        self._headless = headless
        self._tools = {}
        self._tool_prefix = "browser"
        self._npx = shutil.which("npx")
        if not self._npx:
            self._npx = shutil.which("npx.cmd")

    def _start_server(self) -> bool:
        if not self._npx:
            raise MCPServerNotFound()
        last_error = None
        for package in MCP_SERVER_PACKAGES:
            cmd = [self._npx, "-y", package]
            print(f"[MCP-Client] 启动: {' '.join(cmd)}")
            try:
                self._process = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8",
                    bufsize=1, env={**os.environ},
                )
            except FileNotFoundError:
                raise MCPServerNotFound()
            except Exception as e:
                last_error = e
                continue
            self._running = True
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()
            self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
            self._stderr_thread.start()
            return True
        raise MCPConnectionError(f"Failed to start MCP server: {last_error}")

    def _read_loop(self):
        while self._running:
            try:
                line = self._process.stdout.readline()
            except Exception:
                break
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_id = msg.get("id")
            if msg_id is not None:
                with self._lock:
                    self._responses[msg_id] = msg
                    event = self._pending.pop(msg_id, None)
                if event:
                    event.set()

    def _stderr_loop(self):
        while self._running:
            try:
                line = self._process.stderr.readline()
            except Exception:
                break
            if not line:
                break

    def _send_request(self, method: str, params: dict = None, timeout: float = 30.0) -> dict:
        if not self._process or self._process.poll() is not None:
            raise MCPConnectionError("Server process died")
        with self._lock:
            req_id = self._request_id
            self._request_id += 1
            event = threading.Event()
            self._pending[req_id] = event
        request = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        try:
            self._process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise MCPConnectionError(f"Broken pipe: {e}")
        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(req_id, None)
            raise MCPTimeoutError(method, timeout)
        with self._lock:
            response = self._responses.pop(req_id, None)
        if response is None:
            raise MCPConnectionError("Response disappeared")
        if "error" in response:
            err = response["error"]
            raise MCPError(err.get("code", -1), err.get("message", "Unknown"), err.get("data"))
        return response.get("result", {})

    def _send_notification(self, method: str, params: dict = None):
        if not self._process or self._process.poll() is not None:
            return
        notification = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        try:
            self._process.stdin.write(json.dumps(notification, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def initialize(self, timeout: float = 60.0) -> bool:
        self._start_server()
        init_result = self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "AIRAG", "version": "1.0.0"},
        }, timeout=timeout)
        print(f"[MCP-Client] 已初始化: {init_result.get('serverInfo', {}).get('name', 'unknown')}")
        self._send_notification("notifications/initialized", {})
        tools_result = self._send_request("tools/list", {}, timeout=30)
        for t in tools_result.get("tools", []):
            self._tools[t["name"]] = t
        self._tool_prefix = "browser" if any(name.startswith("browser_") for name in self._tools) else "playwright"
        print(f"[MCP-Client] 可用工具: {list(self._tools.keys())}")
        return True

    def call_tool(self, name: str, arguments: dict = None, timeout: float = 30.0) -> dict:
        mapped = name
        if self._tool_prefix == "browser" and name.startswith("playwright_"):
            mapped = "browser_" + name[len("playwright_"):]
        elif self._tool_prefix == "playwright" and name.startswith("browser_"):
            mapped = "playwright_" + name[len("browser_"):]
        if mapped in self._tools:
            name = mapped
        result = self._send_request("tools/call", {
            "name": name, "arguments": arguments or {},
        }, timeout=timeout)
        if result.get("isError"):
            raise MCPToolError(name, result)
        return result

    def navigate(self, url: str) -> bool:
        self.call_tool("browser_navigate", {"url": url}, timeout=30)
        print(f"[MCP-Client] 已导航: {url}")
        return True

    def evaluate(self, script: str) -> str:
        if self._tool_prefix == "playwright":
            result = self.call_tool("playwright_evaluate", {"script": script}, timeout=15)
        else:
            result = self.call_tool("browser_run_code_unsafe", {
                "code": f"async (page) => {{ return await page.evaluate({json.dumps(script)}); }}"
            }, timeout=15)
        content = result.get("content", [])
        for item in content:
            if item.get("type") == "text":
                return item.get("text", "")
        return str(content) if isinstance(content, str) else str(content)

    def screenshot(self) -> Optional[Image.Image]:
        if self._tool_prefix == "playwright":
            result = self.call_tool("playwright_screenshot", {
                "name": "screenshot",
                "width": MCP_SCREENSHOT_WIDTH,
                "height": MCP_SCREENSHOT_HEIGHT,
            }, timeout=15)
        else:
            result = self.call_tool("browser_take_screenshot", {
                "type": "png", "fullPage": False, "filename": "airag-page.png",
            }, timeout=15)
        content = result.get("content", [])
        for item in content:
            if item.get("type") == "image":
                b64_data = item.get("data", "")
                if b64_data:
                    return Image.open(io.BytesIO(base64.b64decode(b64_data)))
            elif item.get("type") == "text":
                text = item.get("text", "")
                if text.startswith("data:image/"):
                    b64_data = text.split(",", 1)[-1]
                    return Image.open(io.BytesIO(base64.b64decode(b64_data)))
        return None

    def close_browser(self):
        try:
            self.call_tool("browser_close", {}, timeout=5)
        except Exception:
            pass

    def shutdown(self):
        self._running = False
        self.close_browser()
        if self._process and self._process.poll() is None:
            try:
                self._process.stdin.close()
            except Exception:
                pass
            try:
                self._process.terminate()
            except Exception:
                pass
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    self._process.kill()
                except Exception:
                    pass
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2)
        if self._stderr_thread and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=2)
        self._process = None
        self._tools.clear()
        print("[MCP-Client] 已关闭")

    def is_alive(self) -> bool:
        return (self._process is not None and self._process.poll() is None and self._running)


# ============================================================
# Browser MCP Engine
# ============================================================

class BrowserMCP:
    """Browser automation engine. Prioritizes MCP Server, falls back to CDP."""

    def __init__(self, use_mcp_server: bool = True):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._connected = False
        self._launched_process = None
        self._mcp_client = None
        self._use_mcp_server = use_mcp_server
        self._mcp_available = False

    # ----- CDP fallback -----

    @staticmethod
    def _is_port_open(host: str = "127.0.0.1", port: int = 9222, timeout: float = 0.4) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    @classmethod
    def _wait_for_port(cls, host: str = "127.0.0.1", port: int = 9222, seconds: float = 8.0) -> bool:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if cls._is_port_open(host, port):
                return True
            time.sleep(0.25)
        return False

    def _launch_cdp_browser(self, port: int = 9222) -> bool:
        paths = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        for p in paths:
            if os.path.exists(p):
                try:
                    user_data_dir = os.path.join(tempfile.gettempdir(), f"airag-cdp-profile-{port}")
                    args = [p, f"--remote-debugging-port={port}",
                            f"--user-data-dir={user_data_dir}",
                            "--no-first-run", "--no-default-browser-check",
                            "--new-window", "--start-maximized", "about:blank"]
                    self._launched_process = subprocess.Popen(
                        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    print(f"[MCP-CDP] 已启动浏览器: {p}")
                    return self._wait_for_port(port=port)
                except Exception as e:
                    print(f"[MCP-CDP] 启动失败: {e}")
        return False

    def _connect_cdp_fallback(self, cdp_url: str = "http://127.0.0.1:9222", auto_launch: bool = True) -> bool:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("[MCP-CDP] Playwright 未安装")
            return False
        try:
            if not self._playwright:
                self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(cdp_url)
            self._context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
            self._page = self._context.pages[-1] if self._context.pages else self._context.new_page()
            self._connected = bool(self._page)
            if self._connected:
                print(f"[MCP-CDP] 已连接: {self._page.title()}")
                try:
                    self._page.evaluate("() => { if (!document.fullscreenElement) { moveTo(0,0); if (screen.width && screen.height && (window.outerWidth < screen.width*0.8 || window.outerHeight < screen.height*0.8)) { resizeTo(screen.width, screen.height); } } }")
                except Exception:
                    pass
            return self._connected
        except Exception as e:
            err = str(e)
            if ("ECONNREFUSED" in err or "connect" in err.lower()) and auto_launch:
                if self._launch_cdp_browser():
                    print("[MCP-CDP] 浏览器已启动，重试连接...")
                    return self._connect_cdp_fallback(cdp_url, auto_launch=False)
            print(f"[MCP-CDP] 连接失败: {err}")
            self.close()
            return False

    # ----- MCP connection -----

    def _connect_via_mcp(self) -> bool:
        try:
            self._mcp_client = PlaywrightMCPClient(headless=False)
            self._mcp_client.initialize()
            self._mcp_available = True
            self._connected = True
            print(f"[MCP] 已通过 MCP Server 连接")
            return True
        except MCPServerNotFound as e:
            print(f"[MCP] npx 不可用: {e}")
            return False
        except (MCPTimeoutError, MCPConnectionError, MCPError) as e:
            print(f"[MCP] MCP Server 启动失败: {e}")
            return False
        except Exception as e:
            print(f"[MCP] 初始化异常: {e}")
            return False

    def connect(self, cdp_url: str = "http://127.0.0.1:9222", auto_launch: bool = True) -> bool:
        if self._use_mcp_server:
            if self._connect_via_mcp():
                return True
            print("[MCP] MCP 不可用 -> 回退 CDP...")
        return self._connect_cdp_fallback(cdp_url=cdp_url, auto_launch=auto_launch)

    @property
    def page(self):
        return self._page

    @property
    def connected(self) -> bool:
        return self._connected

    def navigate(self, url: str) -> bool:
        if self._mcp_available and self._mcp_client:
            return self._mcp_client.navigate(url)
        elif self._page:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return True
        return False

    def maximize(self):
        js = "() => { moveTo(0,0); if (screen && screen.width) { resizeTo(screen.width, screen.height); } }"
        if self._mcp_available and self._mcp_client:
            try:
                self._mcp_client.evaluate(js)
            except Exception:
                pass
        elif self._page:
            try:
                self._page.evaluate(js)
            except Exception:
                pass
        import pyautogui
        try:
            time.sleep(0.2)
            pyautogui.hotkey("win", "up")
            time.sleep(0.3)
        except Exception:
            pass

    def close(self):
        if self._mcp_available and self._mcp_client:
            try:
                self._mcp_client.shutdown()
            except Exception:
                pass
            self._mcp_client = None
            self._mcp_available = False
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        if self._launched_process:
            try:
                self._launched_process.terminate()
            except Exception:
                pass
        self._connected = False


# ============================================================
# ReAct Agent — 截图观察 -> 思考 -> 行动 -> 循环
# ============================================================

REACT_PROMPT = """你是桌面自动化专家。看截图 → 思考 → 决定下一步动作，用 ReAct 模式逐步完成任务。

【用户任务】: {task}

【已执行的历史】:
{history}

请仔细观察当前截图，思考：
1. 当前屏幕上看到了什么？
2. 已执行的操作产生了什么效果？
3. 任务是否已完成？
4. 如果未完成，下一步应该做什么？

返回 JSON（只输出JSON）:

如果任务已完成:
{{"done": true, "reason": "任务完成了，因为...(描述当前屏幕状态证明任务完成)"}}

如果还需要继续操作:
{{"done": false, "thought": "描述看到了什么和下一步逻辑", "action": "click", "x": 500, "y": 300, "text": ""}}

action 类型:
- "click": 点击元素 → 同时返回 x, y (归一化坐标 0-1000)
- "type":  在已聚焦的输入框中打字 → 用 text 字段传文字，无需坐标
- "press": 按键盘键 → 用 text 字段传键名 ("enter", "ctrl+w", "alt+f4", "tab" 等)
- "scroll": 滚动 → text="up" 或 "down"
- "wait":  等待加载 → text="2" 表示等2秒

坐标规则（重要！）:
- x: 归一化横坐标 0-1000 (0=最左, 1000=最右)
- y: 归一化纵坐标 0-1000 (0=最上, 1000=最下)
- 例: 屏幕右上角的X按钮 ≈ x=980, y=10
- 例: 屏幕中央 ≈ x=500, y=500
- 例: 左侧标签页 ≈ x≈150, y≈30

如果页面还在加载或不确定下一步，先等待:
{{"done": false, "action": "wait", "text": "2"}}"""


def react_decide(task: str, image: Image.Image, history: List[str],
                 model: str = "doubao-seed-2-0-lite-260428") -> dict:
    """ReAct 决策：截图 + 任务 + 历史 → {done, thought, action, x, y, text}。

    纯视觉方法 — 模型观察截图，自己思考下一步该做什么、点哪里。
    """
    from agent.llm_client import ChatDoubaoVL
    from langchain_core.messages import HumanMessage

    img_w, img_h = image.size
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=90)
    img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    history_text = "\n".join(history[-10:]) if history else "(无 — 这是第一步)"
    prompt_text = REACT_PROMPT.format(task=task, history=history_text)

    content = [
        {"type": "text", "text": prompt_text},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
    ]

    try:
        llm = ChatDoubaoVL(model_name=model)
        response = llm.invoke([HumanMessage(content=content)])
        text = response.content if hasattr(response, 'content') else ""

        # Parse JSON
        m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
        if m:
            text = m.group(1)
        else:
            s, e = text.find("{"), text.rfind("}") + 1
            if s >= 0 and e > s:
                text = text[s:e]
        result = json.loads(text.replace("'", '"'))

        # Normalize coordinates: 0-1000 → pixel
        if not result.get("done") and "x" in result and "y" in result:
            result["x_pixel"] = int(result["x"] * img_w / 1000)
            result["y_pixel"] = int(result["y"] * img_h / 1000)

        print(f"[ReAct] {json.dumps(result, ensure_ascii=False)[:400]}")
        return result
    except Exception as e:
        print(f"[ReAct] 决策失败: {e}")
        return {"done": True, "reason": f"ReAct异常: {e}"}


# ============================================================
# Executor helpers
# ============================================================

def _screenshot_desktop() -> Image.Image:
    import pyautogui
    return pyautogui.screenshot()


def _pyautogui_click(x: int, y: int):
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.2
    pyautogui.moveTo(x, y, duration=0.15)
    pyautogui.click()


def _pyautogui_type(text: str):
    import pyautogui
    try:
        import pyperclip
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
    except Exception:
        pyautogui.write(text, interval=0.03)


def _pyautogui_press(key: str):
    import pyautogui
    key = key.strip().lower()
    aliases = {"回车": "enter", "空格": "space", "esc": "escape"}
    key = aliases.get(key, key)
    if "+" in key:
        keys = [aliases.get(k.strip(), k.strip()) for k in key.split("+")]
        pyautogui.hotkey(*keys)
    else:
        pyautogui.press(key)


def _pyautogui_scroll(direction: str):
    import pyautogui
    amount = 3 if direction == "down" else -3
    pyautogui.scroll(amount)


# ============================================================
# Browser task helpers
# ============================================================

def is_browser_task(task: str) -> bool:
    text = (task or "").lower()
    keywords = [
        "浏览器", "网页", "网址", "网站", "http://", "https://", "www.",
        "百度", "搜索", "google", "bing", "edge", "chrome", "打开网页",
        "b站", "bilibili", "淘宝", "京东", "知乎", "微博",
    ]
    return any(k in text for k in keywords)


def browser_bootstrap_steps(task: str) -> List[dict]:
    """Determine URL and search query from task text."""
    text = (task or "").strip()
    lower = text.lower()
    url = ""
    search_query = ""

    m = re.search(r"https?://[^\s，。]+|www\.[^\s，。]+", text, re.I)
    if m:
        url = m.group(0)
        if url.startswith("www."):
            url = "https://" + url

    if not search_query:
        for pat in [
            r"搜索\s*(.+?)(?:然后|点击|进入|并|，|。|$)",
            r"搜\s*(.+?)(?:然后|点击|进入|并|，|。|$)",
            r"(?:search|find)\s+(.+?)(?:then|click|and|,|\.|$)",
        ]:
            m_query = re.search(pat, text, re.I)
            if m_query:
                search_query = m_query.group(1).strip(" ：:，。,. ")
                break

    if not url:
        if "百度" in text or "baidu" in lower:
            url = "https://www.baidu.com"
        elif "google" in lower or "谷歌" in text:
            url = "https://www.google.com"
        elif "bing" in lower or "必应" in text:
            url = "https://www.bing.com"
        elif "bilibili" in lower or "b站" in text:
            url = "https://www.bilibili.com"
        else:
            url = "https://www.baidu.com"

    steps = [
        {"action": "open_url", "url": url, "desc": f"打开 {url}"},
        {"action": "wait", "seconds": 3, "desc": "等待页面加载"},
        {"action": "maximize", "desc": "最大化浏览器窗口"},
    ]
    if search_query:
        steps[0]["search_query"] = search_query
    return steps


# ============================================================
# Auditor
# ============================================================

def audit_result(task: str, image: Image.Image,
                 model: str = "doubao-seed-2-0-pro-260215") -> dict:
    """Final audit: compare current screen with task goal."""
    from agent.llm_client import ChatDoubaoVL
    from langchain_core.messages import HumanMessage

    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=85)
    img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    prompt = f"""你是验收员。请查看当前屏幕截图，判断以下任务是否已完成。

原始任务: {task}

请检查：
1. 当前屏幕显示了什么？
2. 是否与任务目标一致？
3. 如果未完成，缺少什么？

返回 JSON (只输出JSON):
{{"success": true/false, "reason": "当前屏幕状态与任务目标的对比", "need_human": false/true}}"""

    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
    ]

    try:
        llm = ChatDoubaoVL(model_name=model)
        response = llm.invoke([HumanMessage(content=content)])
        text = response.content if hasattr(response, 'content') else ""
        print(f"[Auditor] {text[:400]}")

        m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
        if m:
            text = m.group(1)
        else:
            m = re.search(r'\{.*\}', text, re.DOTALL)
            if m:
                text = m.group()
        text = text.strip()
        text = re.sub(r',\s*}', '}', text)
        text = re.sub(r',\s*]', ']', text)
        return json.loads(text)
    except Exception as e:
        print(f"[Auditor] 解析失败: {e}")
        return {"success": False, "reason": f"审计异常: {e}", "need_human": True}


# ============================================================
# Main Pipeline — ReAct Loop
# ============================================================

MAX_REACT_ITERATIONS = 15
MAX_CONSECUTIVE_SAME_ACTION = 3


def run_gui_task(task: str,
                 use_browser: bool = True,
                 cancel_file: str = "",
                 progress_callback: Callable = None,
                 hide_window: Callable = None,
                 show_window: Callable = None) -> dict:
    """ReAct RPA pipeline.

    1. If browser task: open URL + maximize (deterministic bootstrap)
    2. ReAct loop: screenshot → AI thinks → act → repeat until done
    3. Final audit
    """
    import pyautogui

    browser_needed = use_browser and is_browser_task(task)
    mcp = None
    if browser_needed:
        if progress_callback:
            progress_callback("检测到浏览器任务，准备连接浏览器自动化...")
        mcp = BrowserMCP()
        mcp.connect()
        if progress_callback:
            progress_callback("正在打开浏览器页面...")
        bootstrap = browser_bootstrap_steps(task)
        for s in bootstrap:
            if s["action"] == "open_url":
                if mcp and mcp.connected:
                    mcp.navigate(s["url"])
                else:
                    _pyautogui_press("win+r")
                    time.sleep(0.8)
                    try:
                        import pyperclip
                        pyperclip.copy(s["url"])
                        pyautogui.hotkey("ctrl", "v")
                    except Exception:
                        pyautogui.write(s["url"], interval=0.02)
                    time.sleep(0.3)
                    _pyautogui_press("enter")
                    time.sleep(3)
                    pyautogui.hotkey("win", "up")
                if progress_callback:
                    progress_callback(f"  已打开: {s['url']}")
            elif s["action"] == "wait":
                time.sleep(s.get("seconds", 2))
            elif s["action"] == "maximize":
                if mcp and mcp.connected:
                    mcp.maximize()
                else:
                    pyautogui.hotkey("win", "up")
                    time.sleep(0.5)
    elif progress_callback:
        progress_callback("非浏览器任务，使用纯视觉/鼠标键盘操作...")

    # --- ReAct Loop ---
    history = []
    last_action_key = ""
    same_action_count = 0
    start_time = time.time()

    for iteration in range(MAX_REACT_ITERATIONS):
        # ESC cancel check
        if cancel_file and os.path.exists(cancel_file):
            print("[Cancelled] 检测到 ESC 取消信号")
            if mcp:
                mcp.close()
            return {
                "success": False, "canceled": True,
                "message": "操作已被用户取消 (ESC)",
                "steps_done": f"{len(history)}/{iteration}",
                "need_human": False,
            }

        if progress_callback:
            progress_callback(f"🔄 ReAct 第{iteration+1}步: 截图观察...")

        # Hide AIRAG window for clean screenshot
        if hide_window:
            hide_window()
            time.sleep(0.2)

        img = _screenshot_desktop()

        if show_window:
            show_window()

        # ReAct decision
        decision = react_decide(task, img, history)

        if decision.get("done"):
            if progress_callback:
                progress_callback(f"✅ ReAct 判定完成: {decision.get('reason', '')}")
            break

        # Execute action
        action = decision.get("action", "click")
        thought = decision.get("thought", "")
        text = decision.get("text", "")
        x = decision.get("x_pixel", 0)
        y = decision.get("y_pixel", 0)

        if progress_callback:
            progress_callback(f"  💭 {thought[:100]}")
            if action == "click":
                progress_callback(f"  🖱️ {action} ({x},{y})")
            elif action == "type":
                progress_callback(f"  ⌨️ {action}: \"{text[:50]}\"")
            elif action == "press":
                progress_callback(f"  🔤 {action}: \"{text}\"")
            else:
                progress_callback(f"  ⏳ {action}: {text}")

        try:
            if action == "click":
                _pyautogui_click(x, y)
            elif action == "type":
                _pyautogui_type(text)
            elif action == "press":
                _pyautogui_press(text)
            elif action == "scroll":
                _pyautogui_scroll(text or "down")
            elif action == "wait":
                time.sleep(min(float(text or 1), 5))
            else:
                print(f"[ReAct] 未知动作: {action}")
        except Exception as e:
            print(f"[ReAct] 执行失败: {e}")

        # Record history
        action_desc = f"{action}"
        if action == "click":
            action_desc += f" ({x},{y})"
        elif action in ("type", "press", "scroll"):
            action_desc += f" \"{text}\""
        history.append(f"[{iteration+1}] {thought} → {action_desc}")
        time.sleep(1.5)

        # Detect stuck loops
        action_key = f"{action}:{text}:{x}:{y}"
        if action_key == last_action_key:
            same_action_count += 1
            if same_action_count >= MAX_CONSECUTIVE_SAME_ACTION:
                print(f"[ReAct] 连续{MAX_CONSECUTIVE_SAME_ACTION}次相同动作，退出循环")
                break
        else:
            same_action_count = 0
            last_action_key = action_key

    elapsed = time.time() - start_time

    # --- Final Audit ---
    if progress_callback:
        progress_callback("🔎 最终审计中...")
    try:
        img = _screenshot_desktop()
        if mcp and mcp.connected:
            page_img = mcp.screenshot_page()
            if page_img:
                img = page_img
        audit = audit_result(task, img)
    except Exception as e:
        audit = {"success": len(history) > 0,
                 "reason": f"已执行 {len(history)} 个动作；审计截图失败: {e}",
                 "need_human": False}

    if mcp:
        mcp.close()

    return {
        "success": audit.get("success", False),
        "message": audit.get("reason", ""),
        "steps_done": f"{len(history)}/{iteration+1}",
        "need_human": audit.get("need_human", False),
        "elapsed": f"{elapsed:.1f}s",
    }
