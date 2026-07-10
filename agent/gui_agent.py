# -*- coding: utf-8 -*-
"""
GUI Agent RPA — ReAct core decision + Loop Engineer controller.

Architecture:
  1. ReAct core: screenshot + task + operation memory -> next action JSON.
  2. Loop Engineer: observe, execute, detect repeated/no-op actions, recover,
     audit, summarize operation memory, and decide when to stop.
  3. Executor: PyAutoGUI + optional Playwright MCP browser tools.
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

    def __init__(self, headless: bool = False, keep_browser_open: bool = False,
                 cdp_endpoint: str = ""):
        self._process = None
        self._request_id = 0
        self._pending = {}
        self._responses = {}
        self._lock = threading.Lock()
        self._reader_thread = None
        self._stderr_thread = None
        self._running = False
        self._headless = headless
        self._keep_browser_open = keep_browser_open
        self._cdp_endpoint = cdp_endpoint
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
            if self._cdp_endpoint:
                cmd.extend(["--cdp-endpoint", self._cdp_endpoint])
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
            if getattr(self, "_keep_browser_open", False):
                return
            self.call_tool("browser_close", {}, timeout=5)
        except Exception:
            pass

    def shutdown(self):
        self._running = False
        if not getattr(self, "_keep_browser_open", False):
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

    def __init__(self, use_mcp_server: bool = True, keep_browser_open: bool = False):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._connected = False
        self._launched_process = None
        self._mcp_client = None
        self._use_mcp_server = use_mcp_server
        self._mcp_available = False
        self._keep_browser_open = keep_browser_open

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
            cdp_endpoint = ""
            if self._keep_browser_open:
                cdp_endpoint = "http://127.0.0.1:9222"
                if not self._is_port_open(port=9222):
                    if not self._launch_cdp_browser(port=9222):
                        return False
            self._mcp_client = PlaywrightMCPClient(
                headless=False,
                keep_browser_open=self._keep_browser_open,
                cdp_endpoint=cdp_endpoint,
            )
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

    def screenshot_page(self) -> Optional[Image.Image]:
        if self._mcp_available and self._mcp_client:
            return self._mcp_client.screenshot()
        if self._page:
            data = self._page.screenshot(type="png", full_page=False)
            return Image.open(io.BytesIO(data))
        return None

    def extract_page_text(self, max_chars: int = 5000) -> str:
        js = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const links = Array.from(document.querySelectorAll('a'))
    .slice(0, 30)
    .map(a => ({ text: clean(a.innerText || a.textContent), href: a.href }))
    .filter(x => x.text || x.href);
  const main = document.querySelector('main, article, #content, .content, .result, body');
  const text = clean(main ? main.innerText : document.body.innerText);
  return JSON.stringify({
    title: document.title,
    url: location.href,
    text: text.slice(0, 5000),
    links
  });
}
"""
        raw = ""
        if self._mcp_available and self._mcp_client:
            raw = self._mcp_client.evaluate(js)
        elif self._page:
            raw = self._page.evaluate(js)
        if not raw:
            return ""
        try:
            data = json.loads(raw)
            title = data.get("title", "")
            url = data.get("url", "")
            text = data.get("text", "")[:max_chars]
            links = data.get("links", [])[:10]
            link_text = "\n".join(
                f"- {item.get('text', '')[:80]} {item.get('href', '')}"
                for item in links
            )
            return f"TITLE: {title}\nURL: {url}\nTEXT:\n{text}\nLINKS:\n{link_text}".strip()
        except Exception:
            return str(raw)[:max_chars]

    def mark_unavailable(self):
        """Mark the current browser channel as unavailable so ReAct can fall back."""
        self._connected = False
        self._mcp_available = False

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
        if self._launched_process and not self._keep_browser_open:
            try:
                self._launched_process.terminate()
            except Exception:
                pass
        self._connected = False


# ============================================================
# ReAct Agent — 截图观察 -> 思考 -> 行动 -> 循环
# ============================================================

REACT_PROMPT = """你是桌面自动化专家。看截图 → 理解用户意图 → 决定下一步动作。

【用户任务】: {task}

【已执行的历史】:
{history}

请仔细观察当前截图，思考：
1. 当前屏幕上看到了什么？（桌面？浏览器？某个应用？）
2. 用户的意图是什么？（搜索？打开软件？关闭窗口？填写表单？）
3. 任务是否已完成？
4. 如果未完成，下一步应该做什么？

你可以使用的工具 action:
- "click":    鼠标点击元素 → 同时返回 x, y (归一化坐标 0-1000)
- "type":     在当前焦点输入框打字 → text="要输入的文字"
- "press":    按键盘按键 → text="enter" / "ctrl+w" / "alt+f4" / "win+r" / "tab" 等
- "scroll":   滚动 → text="up"/"down" 或数字如 "-8"/"15"（自己做主判断滚多少）
- "wait":     等待 → text="2" (秒)
- "open_url": 在浏览器打开网址 → text="https://www.baidu.com"
              如果需要搜索但没开浏览器，先用 press text="win+r" 打开运行,
              或用 open_url 直接打开搜索引擎

提示:
- 看到桌面想搜索: 用 click 点浏览器图标或 press text="win+r" 输入网址
- 看到浏览器页面: 用 click 点搜索框, 用 type 输入关键词, 用 press text="enter" 搜索
- 看到目标页面: 任务可能已完成, 返回 done=true
- 需要用特定软件: 用 press text="win" 打开开始菜单, 然后 type 输入软件名
- 不确定下一步时: 用 wait 等待页面加载

坐标规则:
- x: 归一化 0-1000 (0=最左, 1000=最右)
- y: 归一化 0-1000 (0=最上, 1000=最下)
- 右上角 ≈ x=980, y=10；中央 ≈ x=500, y=500；任务栏 ≈ y=980

返回 JSON（只输出JSON）:
任务未完成:
{{"done": false, "state": "当前界面状态", "goal": "当前子目标", "strategy": "primary", "attempt": 1, "thought": "当前看到xxx, 下一步应该xxx", "action": "click", "x": 500, "y": 300, "text": ""}}

任务已完成:
{{"done": true, "reason": "任务完成了，当前屏幕显示xxx证明xxx"}}

不确定时先等待:
{{"done": false, "state": "页面可能正在加载", "goal": "等待界面稳定", "strategy": "wait", "attempt": 1, "thought": "等待页面加载后再判断", "action": "wait", "text": "2"}}

重要：不要按文字查找元素，也不要输出 selector 或元素文字定位。只根据截图判断坐标并调用基础工具。
正式动作集：
- click: 左键单击，必须给 x,y
- double: 左键双击，必须给 x,y
- right: 右键单击，必须给 x,y
- move: 移动鼠标，必须给 x,y
- drag: 拖拽，必须给 x,y,x2,y2
- fill: 在坐标处点击后输入文本，必须给 x,y,text
- hotkey: 键盘或快捷键，必须给 text，例如 enter、ctrl+l、alt+f4、win+r
- scroll: 在指定区域滚动，最好给 x,y，text 为 up/down 或直接写数字（负数下滚，正数上滚，根据截图内容判断幅度，如需要滚半页写 -15）
- multi_scroll: 一次性大幅滚动，必须给 text 为次数或方向:次数，例如 down:8
- page_jump: 估算目标页差距后连续翻页，text 为页数差或目标页，例如 9 或 target:10
- page_down: 向下翻页，不需要 text
- page_up: 向上翻页，不需要 text
- wait: 等待，text 为秒数
- screenshot: 重新截图观察，不需要 text
- alt_tab: 切换到上一个窗口，不需要 text
- focus_window: 点击/激活当前目标窗口，必须给 x,y
- open_url: 浏览器打开 URL，text 为 URL
- observe_browser: 检测/连接已有 MCP 或 browser page，并截图观察，不需要 text
- activate_browser: 尝试激活后台或最小化的浏览器窗口，不需要 text
- zoom_out: 缩小页面视图，看更多内容，不需要 text
- zoom_in: 放大页面视图，看清局部内容，不需要 text
- maximize: 最大化/恢复浏览器窗口，不需要 text
- read_page: 读取当前网页可见文本/标题/链接并放入历史，不需要 text

优先输出这些 action 名；type 视为旧版 fill，press 视为旧版 hotkey。
- 如果你完全无法确认目标在哪（截图里找不到相关元素），请返回 ask_user 让用户协助指定，不要随机或胡乱点击坐标。
- ask_user 格式: {{"done": false, "action": "ask_user", "text": "你希望我点击的xxx具体在哪？能描述一下位置或形状吗？"}}"""


def react_decide(task: str, image: Image.Image, history: List[str],
                 operation_context: str = "",
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
    if operation_context:
        history_text = f"{operation_context}\n\n## 本轮已执行历史\n{history_text}"
    prompt_text = REACT_PROMPT.format(task=task, history=history_text)

    prompt_text += """

Additional execution rules:
1. Break the user task into a short plan in thought, and state the current step/status.
2. Use the action history as progress memory. Do not repeat the same open_url for the same URL.
3. For browser/search tasks, do not assume the browser is closed just because the desktop screenshot shows another app. First decide whether to call observe_browser or activate_browser.
4. Use observe_browser to connect to an existing MCP/browser page and inspect it. Use activate_browser when history suggests a browser exists but it may be minimized or behind another window.
5. Use open_url only after you decide a browser/search page is unavailable or a new explicit URL/search engine is needed.
6. For search/research tasks, progress as: check browser state -> get/search results -> identify relevant links -> open/read pages -> call read_page -> summarize collected material -> done=true.
7. If the screenshot conflicts with action history, treat the last successful action as an important clue and try observe_browser, activate_browser, wait, maximize, hotkey, or clicking the visible browser/taskbar before repeating navigation.
8. Do not keep scrolling forever. After at most two scroll actions on a search results page, click a relevant result or call read_page to collect text.
9. If the visible page is too cramped or only shows ads, use zoom_out or maximize before more scrolling.
10. For file/folder/PPT tasks, use desktop tools too: double click folders/files, alt_tab, focus_window, page_down/page_up, hotkey f5/escape, and scroll in the visible slide/page area.
11. For PPT/page navigation, estimate the gap to the target page from the screenshot and history. Use page_jump or multi_scroll instead of moving one page per step. Example: if current page is 1 and target is 10, use page_jump text="9".
12. Use the operation memory context as the current desktop/application state. If the user asks a follow-up like "再翻到第十页", continue from the remembered workflow and current screenshot.
13. Think internally before every action: goal -> current state -> likely next action -> backup action if it fails. Keep the visible thought short; do not expose long chain-of-thought.
14. Return state, goal, strategy, and attempt on every unfinished step. Treat strategy as the method category, e.g. primary_visual, menu_path, keyboard_shortcut, command_palette, search_box, context_menu, window_focus, direct_open, recovery_observe.
15. Try at most one action per strategy before switching when the screen does not change, the action repeats, or the target remains absent. Do not repeat the same coordinate/action more than twice.
16. Two consecutive failed, repeated, or unchanged-screen steps must switch to a different method. Prefer a new strategy category, such as keyboard shortcut, menu path, search box, double click, context menu, page navigation, window focus, or direct URL/open command.
17. If a task is phrased loosely, normalize it to a concrete UI goal before acting. Examples: "recent folder" means try welcome-page recent item first, then File/Open Recent menu, then command palette/search.
18. Do not ask the user immediately after one failed attempt. Try several distinct methods unless the target is genuinely ambiguous or destructive.
19. After five ineffective strategy attempts, return action="ask_user" with a concise failure_reason and ask for permission/help, such as confirming the target window, granting permissions, or pointing to the target.
20. Return JSON. You may include plan/status, but must include done/action/thought or done/reason.
"""

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
        if not result.get("done") and "x2" in result and "y2" in result:
            result["x2_pixel"] = int(result["x2"] * img_w / 1000)
            result["y2_pixel"] = int(result["y2"] * img_h / 1000)

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


def _image_fingerprint(image: Image.Image) -> tuple:
    small = image.convert("L").resize((16, 16))
    return tuple(int(p // 16) for p in small.getdata())


def _fingerprint_distance(a: tuple, b: tuple) -> int:
    if not a or not b or len(a) != len(b):
        return 9999
    return sum(abs(x - y) for x, y in zip(a, b))


def _pyautogui_click(x: int, y: int):
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.2
    pyautogui.moveTo(x, y, duration=0.15)
    pyautogui.click()


def _pyautogui_double_click(x: int, y: int):
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.2
    pyautogui.moveTo(x, y, duration=0.15)
    pyautogui.doubleClick()


def _pyautogui_right_click(x: int, y: int):
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.2
    pyautogui.moveTo(x, y, duration=0.15)
    pyautogui.rightClick()


def _pyautogui_move(x: int, y: int):
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.2
    pyautogui.moveTo(x, y, duration=0.15)


def _pyautogui_drag(x: int, y: int, x2: int, y2: int):
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.2
    pyautogui.moveTo(x, y, duration=0.15)
    pyautogui.dragTo(x2, y2, duration=0.35, button="left")


def _pyautogui_type(text: str):
    import pyautogui
    try:
        import pyperclip
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
    except Exception:
        pyautogui.write(text, interval=0.03)


def _pyautogui_fill(text: str):
    _pyautogui_type(text)


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


def _pyautogui_hotkey(key: str):
    _pyautogui_press(key)


def _pyautogui_scroll(direction: str, x: int = 0, y: int = 0):
    import pyautogui
    direction = (direction or "down").strip().lower()
    if direction.lstrip("-").isdigit():
        amount = int(direction)
    elif direction in ("down", "roll_down"):
        amount = -12
    elif direction in ("long_down", "pagedown", "page_down"):
        amount = -20
    elif direction in ("long_up", "pageup", "page_up"):
        amount = 20
    else:
        amount = 12
    try:
        if x > 0 and y > 0:
            pyautogui.moveTo(x, y, duration=0.1)
        else:
            w, h = pyautogui.size()
            pyautogui.moveTo(w // 2, h // 2, duration=0.1)
    except Exception:
        pass
    print(f"[PyAutoGUI] scroll amount={amount} at=({x},{y})", flush=True)
    pyautogui.scroll(amount)
    _native_mouse_wheel(amount)
    # 键盘兜底：文件夹等不响应模拟滚轮，用 PageDown/PageUp 翻页
    time.sleep(0.15)
    if amount < 0:
        count = max(1, abs(amount) // 10)
        for _ in range(count):
            pyautogui.press("pagedown")
            time.sleep(0.05)
    elif amount > 0:
        count = max(1, amount // 10)
        for _ in range(count):
            pyautogui.press("pageup")
            time.sleep(0.05)


def _pyautogui_page(direction: str, x: int = 0, y: int = 0):
    import pyautogui
    if x > 0 and y > 0:
        try:
            pyautogui.moveTo(x, y, duration=0.1)
            time.sleep(0.05)
        except Exception:
            pass
    pyautogui.press("pagedown" if direction == "down" else "pageup")


def _native_mouse_wheel(amount: int):
    if amount == 0:
        return
    try:
        import ctypes

        wheel_delta = int(amount) * 120
        ctypes.windll.user32.mouse_event(0x0800, 0, 0, wheel_delta, 0)
    except Exception:
        pass


def _parse_count_text(text: str, default: int = 3, limit: int = 20) -> tuple:
    raw = (text or "").strip().lower()
    direction = "down"
    count_text = raw
    if ":" in raw:
        direction, count_text = raw.split(":", 1)
    if "up" in raw or "上" in raw:
        direction = "up"
    try:
        count = abs(int(re.findall(r"-?\d+", count_text)[0]))
    except Exception:
        count = default
    return direction, max(1, min(count, limit))


def _pyautogui_multi_scroll(text: str, x: int = 0, y: int = 0):
    direction, count = _parse_count_text(text, default=5, limit=15)
    for _ in range(count):
        _pyautogui_scroll("long_up" if direction == "up" else "long_down", x, y)
        time.sleep(0.08)


def _pyautogui_page_jump(text: str):
    import pyautogui
    raw = (text or "").strip().lower()
    direction, count = _parse_count_text(raw, default=1, limit=30)
    if raw.startswith("-"):
        direction = "up"
    key = "pageup" if direction == "up" else "pagedown"
    for _ in range(count):
        pyautogui.press(key)
        time.sleep(0.08)


def _task_requests_close(task: str) -> bool:
    text = (task or "").lower()
    close_keys = [
        "关闭", "关掉", "退出", "结束", "停止",
        "close", "quit", "exit", "stop", "shutdown",
    ]
    return any(k in text for k in close_keys)


def _is_close_hotkey(text: str) -> bool:
    key = (text or "").lower().replace(" ", "")
    return key in {"alt+f4", "ctrl+w", "cmd+w", "command+w", "ctrl+q", "cmd+q", "command+q"}


def _open_url_in_browser(url: str, mcp):
    """在浏览器中打开 URL。首次调用时自动连接 MCP。"""
    url = (url or "").strip()
    if not url:
        return
    if not url.startswith("http"):
        url = "https://" + url
    # 延迟连接 MCP: AI 第一次需要浏览器时才连
    if mcp and not mcp.connected:
        mcp.connect()
    if mcp and mcp.connected:
        try:
            mcp.navigate(url)
            print(f"[ReAct] MCP open: {url}")
            return
        except Exception as e:
            print(f"[ReAct] MCP open failed: {e}")
    # 回退
    _pyautogui_press("win+r")
    time.sleep(0.8)
    try:
        import pyperclip
        pyperclip.copy(url)
        import pyautogui
        pyautogui.hotkey("ctrl", "v")
    except Exception:
        import pyautogui
        pyautogui.write(url, interval=0.02)
    time.sleep(0.3)
    _pyautogui_press("enter")
    time.sleep(3)
    import pyautogui
    pyautogui.hotkey("win", "up")
    print(f"[ReAct] Win+R opened: {url}")


# ============================================================
# Browser task helpers
# ============================================================

def is_browser_task(task: str) -> bool:
    """仅判断是否需要预打开浏览器 (有明确 URL 或浏览器关键词)。

    意图识别交给 ReAct Agent — AI 看截图自己决定做什么。
    这里只做最简单的确定性判断，不做关键词猜测。
    """
    text = (task or "").lower()
    # 明确URL
    if re.search(r"https?://|www\.", text):
        return True
    # 明确提到浏览器、网页搜索或资料整理
    browser_keys = [
        "浏览器", "打开网页", "网页", "网址", "链接",
        "搜索", "搜", "整理资料", "资料", "csdn",
        "edge", "chrome", "baidu", "bing", "google",
    ]
    return any(k in text for k in browser_keys)


def observe_browser_page(mcp: BrowserMCP, progress_callback: Callable = None) -> bool:
    """Connect to an existing browser page and verify page observation."""
    if not mcp:
        return False

    if not mcp.connected:
        if progress_callback:
            progress_callback("Detecting an existing browser page...")
        mcp.connect()

    if not mcp.connected:
        return False

    try:
        img = safe_browser_screenshot(mcp, progress_callback)
        if img is None:
            return False
        mcp.maximize()
        time.sleep(0.5)
        if progress_callback:
            progress_callback("Detected an existing browser page; observing it before any navigation.")
        return True
    except Exception as exc:
        print(f"[ReAct] browser observe failed: {exc}")
        return False


def activate_browser_window(mcp: BrowserMCP = None, progress_callback: Callable = None) -> bool:
    """Try to bring an existing browser window forward without navigating."""
    try:
        import pyautogui
        if progress_callback:
            progress_callback("Trying to activate an existing browser window from the desktop.")
        pyautogui.hotkey("alt", "tab")
        time.sleep(0.8)
        pyautogui.hotkey("win", "up")
        time.sleep(0.8)
        if mcp and mcp.connected:
            try:
                mcp.maximize()
            except Exception:
                pass
        return True
    except Exception as exc:
        print(f"[ReAct] browser activation fallback failed: {exc}")
        return False


def safe_browser_screenshot(mcp: BrowserMCP,
                            progress_callback: Callable = None,
                            history: Optional[List[str]] = None) -> Optional[Image.Image]:
    """Take a browser screenshot with one recovery attempt, then let ReAct continue."""
    if not mcp or not mcp.connected:
        return None

    try:
        return mcp.screenshot_page()
    except Exception as exc:
        msg = f"MCP browser screenshot failed: {exc}"
        print(f"[ReAct] {msg}")
        if history is not None:
            history.append(f"[tool_error] {msg}; fallback to desktop screenshot")
        if progress_callback:
            progress_callback("Browser screenshot failed; activating browser and falling back if needed.")

    try:
        activate_browser_window(mcp, progress_callback)
        img = mcp.screenshot_page()
        if img is not None:
            if history is not None:
                history.append("[recovery] browser screenshot recovered after activation")
            return img
    except Exception as exc:
        msg = f"MCP browser screenshot retry failed: {exc}"
        print(f"[ReAct] {msg}")
        if history is not None:
            history.append(f"[tool_error] {msg}; MCP channel marked unavailable")

    try:
        mcp.mark_unavailable()
    except Exception:
        pass
    return None


def choose_recovery_action(action: str, text: str, x: int, y: int, attempts: int, mcp_connected: bool) -> dict:
    """Pick a different desktop-control method after repeated ineffective actions."""
    cycle = max(0, attempts - 1) % 5
    if action in ("click", "focus_window"):
        fallbacks = [
            {"action": "double", "x_pixel": x, "y_pixel": y, "text": "recovery double click", "strategy": "double_click_recovery"},
            {"action": "press", "text": "enter", "strategy": "keyboard_confirm"},
            {"action": "right", "x_pixel": x, "y_pixel": y, "text": "recovery context click", "strategy": "context_menu"},
            {"action": "alt_tab", "text": "recovery alt tab", "strategy": "window_focus"},
            {"action": "screenshot", "text": "recovery observe", "strategy": "observe"},
        ]
    elif action in ("scroll", "page_down", "page_up"):
        fallbacks = [
            {"action": "page_down", "text": "recovery page down", "strategy": "page_navigation"},
            {"action": "read_page" if mcp_connected else "zoom_out", "text": "recovery collect or zoom", "strategy": "collect_or_zoom"},
            {"action": "multi_scroll", "x_pixel": x, "y_pixel": y, "text": "down:5", "strategy": "multi_scroll"},
            {"action": "page_up", "text": "recovery page up", "strategy": "reverse_page_navigation"},
            {"action": "screenshot", "text": "recovery observe", "strategy": "observe"},
        ]
    elif action in ("fill", "type"):
        fallbacks = [
            {"action": "hotkey", "text": "ctrl+a", "strategy": "select_existing_text"},
            {"action": "type", "text": text, "strategy": "retry_text_input"},
            {"action": "press", "text": "enter", "strategy": "keyboard_confirm"},
            {"action": "click", "x_pixel": x, "y_pixel": y, "text": "recovery refocus", "strategy": "refocus_input"},
            {"action": "screenshot", "text": "recovery observe", "strategy": "observe"},
        ]
    else:
        fallbacks = [
            {"action": "wait", "text": "1", "strategy": "wait"},
            {"action": "screenshot", "text": "recovery observe", "strategy": "observe"},
            {"action": "alt_tab", "text": "recovery alt tab", "strategy": "window_focus"},
            {"action": "maximize", "text": "recovery maximize", "strategy": "maximize_window"},
            {"action": "observe_browser" if mcp_connected else "activate_browser", "text": "recovery observe browser", "strategy": "browser_focus"},
        ]
    return fallbacks[cycle]


class LoopController:
    """Engineering controller around the ReAct decision core."""

    def __init__(self, max_recoveries: int = 5):
        self.max_recoveries = max_recoveries
        self.last_action_key = ""
        self.same_action_count = 0
        self.recovery_attempts = 0
        self.forced_failure = ""
        self.last_image_fp = None
        self.unchanged_screen_count = 0
        self.strategy_attempts = []

    def observe_image(self, image: Image.Image):
        current_fp = _image_fingerprint(image)
        if self.last_image_fp is not None and _fingerprint_distance(current_fp, self.last_image_fp) < 80:
            self.unchanged_screen_count += 1
        else:
            self.unchanged_screen_count = 0
        self.last_image_fp = current_fp

    def maybe_recover(self, action: str, text: str, x: int, y: int,
                      x2: int, y2: int, strategy: str, mcp_connected: bool) -> tuple:
        predicted_action_key = f"{action}:{text}:{x}:{y}"
        strategy = strategy or action or "primary"
        if strategy not in self.strategy_attempts:
            self.strategy_attempts.append(strategy)
        if predicted_action_key == self.last_action_key:
            self.same_action_count += 1
        else:
            self.same_action_count = 0

        if self.same_action_count < 1 and self.unchanged_screen_count < 2:
            return action, text, x, y, x2, y2, strategy, len(self.strategy_attempts), ""

        self.recovery_attempts += 1
        if self.recovery_attempts > self.max_recoveries:
            self.forced_failure = (
                f"连续多次操作无效，已尝试 {self.max_recoveries} 种替代方法仍未完成；"
                "请确认目标窗口是否在前台、是否需要权限，或描述目标位置让我继续。"
            )
            return "ask_user", self.forced_failure, x, y, x2, y2, "ask_user", len(self.strategy_attempts), self.forced_failure

        recovery = choose_recovery_action(action, text, x, y, self.recovery_attempts, mcp_connected)
        old_action = action
        old_strategy = strategy
        action = recovery.get("action", action)
        text = recovery.get("text", text)
        strategy = recovery.get("strategy", action)
        if strategy not in self.strategy_attempts:
            self.strategy_attempts.append(strategy)
        x = recovery.get("x_pixel", x)
        y = recovery.get("y_pixel", y)
        x2 = recovery.get("x2_pixel", x2)
        y2 = recovery.get("y2_pixel", y2)
        self.unchanged_screen_count = 0
        note = f"检测到连续重复或画面未变化，自动从 {old_strategy}/{old_action} 切换为 {strategy}/{action}（第 {self.recovery_attempts}/{self.max_recoveries} 种方法）"
        return action, text, x, y, x2, y2, strategy, len(self.strategy_attempts), note

    def record_action(self, action: str, text: str, x: int, y: int):
        self.last_action_key = f"{action}:{text}:{x}:{y}"


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
# Main Pipeline — Loop Engineer around ReAct Core
# ============================================================

MAX_REACT_ITERATIONS = 15
MAX_CONSECUTIVE_SAME_ACTION = 3


def run_gui_task(task: str,
                 use_browser: bool = True,
                 keep_browser_open: bool = False,
                 cancel_file: str = "",
                 progress_callback: Callable = None,
                 hide_window: Callable = None,
                 show_window: Callable = None,
                 shared_mcp: Optional[BrowserMCP] = None,
                 user_id: str = "default",
                 memory_key: str = "desktop") -> dict:
    """Run GUI automation as ReAct core decision + Loop Engineer control."""
    import pyautogui

    mcp = shared_mcp or BrowserMCP(keep_browser_open=keep_browser_open)  # 延迟连接: AI 调用 open_url 时才 connect()

    browser_mode = bool(use_browser and is_browser_task(task))
    operation_context = ""
    try:
        from utils.gui_operation_memory import build_operation_context

        operation_context = build_operation_context(user_id, memory_key, task)
        if operation_context and progress_callback:
            progress_callback("已加载当前操作窗口历史流程")
    except Exception as exc:
        print(f"[GuiOpMemory] load skipped: {exc}")

    # --- Loop Engineer: observe -> ReAct decide -> execute -> recover ---
    history = []
    collected_notes = []
    loop = LoopController(max_recoveries=5)
    start_time = time.time()

    for iteration in range(MAX_REACT_ITERATIONS):
        # ESC cancel check
        if cancel_file and os.path.exists(cancel_file):
            print("[Cancelled] 检测到 ESC 取消信号")
            if mcp and not keep_browser_open:
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

        img = None
        if browser_mode and mcp.connected:
            img = safe_browser_screenshot(mcp, progress_callback, history)
        if img is None:
            img = _screenshot_desktop()
        loop.observe_image(img)

        if show_window:
            show_window()

        # ReAct decision
        decision = react_decide(task, img, history, operation_context=operation_context)

        if decision.get("done"):
            reason = decision.get("reason", "任务已完成")
            if progress_callback:
                progress_callback(f"✅ ReAct 判定完成: {reason}，进入最终审计。")
            break

        # ---- 关键：执行动作前再次检查取消信号 ----
        if cancel_file and os.path.exists(cancel_file):
            print("[Cancelled] 动作执行前检测到 ESC 取消信号，跳过动作")
            if mcp and not keep_browser_open:
                mcp.close()
            return {
                "success": False, "canceled": True,
                "message": "操作已被用户取消 (ESC)",
                "steps_done": f"{len(history)}/{iteration}",
                "need_human": False,
            }

        # Execute action
        action = decision.get("action", "click")
        thought = decision.get("thought", "")
        state = decision.get("state", "")
        goal = decision.get("goal", "")
        strategy = decision.get("strategy", action)
        attempt = decision.get("attempt", 1)
        text = decision.get("text", "")
        x = decision.get("x_pixel", 0)
        y = decision.get("y_pixel", 0)
        x2 = decision.get("x2_pixel", 0)
        y2 = decision.get("y2_pixel", 0)

        action, text, x, y, x2, y2, strategy, attempt, loop_note = loop.maybe_recover(
            action, text, x, y, x2, y2, strategy, bool(mcp and mcp.connected)
        )
        if loop.forced_failure:
            print(f"[LoopController] {loop.forced_failure}")
            if progress_callback:
                progress_callback(f"  ❓ {loop.forced_failure}")
            return {
                "success": False,
                "message": loop.forced_failure,
                "steps_done": f"{len(history)}/{iteration+1}",
                "need_human": True,
                "elapsed": f"{time.time() - start_time:.1f}s",
            }
        if loop_note:
            thought = f"{thought}；{loop_note}"

        recent_actions = [h.split(" → ")[-1].split(" ", 1)[0] for h in history[-2:]]
        if action == "scroll" and recent_actions == ["scroll", "scroll"]:
            if mcp and mcp.connected:
                action = "read_page"
                text = "auto read_page after repeated scrolling"
            else:
                action = "zoom_out"
                text = "auto zoom_out after repeated scrolling"
            thought = f"{thought}；连续滚动后自动切换为 {action}，避免只滚动不收集信息"

        if progress_callback:
            if state or goal or strategy:
                progress_callback(f"  🧭 state={state[:40] or '-'} | goal={goal[:40] or '-'} | strategy={strategy} | attempt={attempt}")
            progress_callback(f"  💭 {thought[:100]}")
            if action == "click":
                progress_callback(f"  🖱️ {action} ({x},{y})")
            elif action == "type":
                progress_callback(f"  ⌨️ {action}: \"{text[:50]}\"")
            elif action == "press":
                progress_callback(f"  🔤 {action}: \"{text}\"")
            elif action in ("page_down", "page_up", "page_jump", "multi_scroll", "screenshot", "alt_tab", "focus_window", "maximize", "zoom_in", "zoom_out"):
                progress_callback(f"  🧭 {action}: {text}")
            elif action in ("observe_browser", "activate_browser"):
                progress_callback(f"  🌐 {action}")
            elif action == "read_page":
                progress_callback("  📄 read_page")
            else:
                progress_callback(f"  ⏳ {action}: {text}")

        if cancel_file and os.path.exists(cancel_file):
            print("[Cancelled] Cancel signal detected before executing action")
            if mcp and not keep_browser_open:
                mcp.close()
            return {
                "success": False,
                "canceled": True,
                "message": "操作已被用户取消 (ESC)",
                "steps_done": f"{len(history)}/{iteration}",
                "need_human": False,
            }

        try:
            if action == "click":
                _pyautogui_click(x, y)
            elif action in ("double", "double_click"):
                _pyautogui_double_click(x, y)
            elif action in ("right", "right_click"):
                _pyautogui_right_click(x, y)
            elif action == "move":
                _pyautogui_move(x, y)
            elif action == "drag":
                _pyautogui_drag(x, y, x2, y2)
            elif action == "fill":
                if x and y:
                    _pyautogui_click(x, y)
                _pyautogui_fill(text)
            elif action == "hotkey":
                if _is_close_hotkey(text) and not _task_requests_close(task):
                    print(f"[ReAct] blocked close hotkey without close intent: {text}")
                    continue
                _pyautogui_hotkey(text)
            elif action == "type":
                _pyautogui_type(text)
            elif action == "press":
                if _is_close_hotkey(text) and not _task_requests_close(task):
                    print(f"[ReAct] blocked close hotkey without close intent: {text}")
                    continue
                _pyautogui_press(text)
            elif action == "scroll":
                _pyautogui_scroll(text or "down", x, y)
            elif action == "multi_scroll":
                _pyautogui_multi_scroll(text or "down:5", x, y)
                text = f"multi_scroll {text or 'down:5'}"
            elif action == "page_jump":
                _pyautogui_page_jump(text or "1")
                text = f"page_jump {text or '1'}"
            elif action == "page_down":
                _pyautogui_page("down", x, y)
                text = "page down"
            elif action == "page_up":
                _pyautogui_page("up", x, y)
                text = "page up"
            elif action == "wait":
                time.sleep(min(float(text or 1), 5))
            elif action == "screenshot":
                time.sleep(0.5)
                text = "screenshot refreshed"
            elif action == "alt_tab":
                _pyautogui_hotkey("alt+tab")
                text = "alt tab"
            elif action == "focus_window":
                _pyautogui_click(x, y)
                text = "window focused"
            elif action == "observe_browser":
                ok = observe_browser_page(mcp, progress_callback)
                text = "browser observed" if ok else "no observable browser page"
            elif action == "activate_browser":
                ok = activate_browser_window(mcp, progress_callback)
                text = "browser activation attempted" if ok else "browser activation failed"
            elif action == "zoom_out":
                _pyautogui_hotkey("ctrl+-")
                text = "zoomed out"
            elif action == "zoom_in":
                import pyautogui
                pyautogui.hotkey("ctrl", "+")
                text = "zoomed in"
            elif action == "maximize":
                if mcp and mcp.connected:
                    mcp.maximize()
                else:
                    _pyautogui_hotkey("win+up")
                text = "window maximized"
            elif action == "read_page":
                if not mcp.connected:
                    mcp.connect()
                note = mcp.extract_page_text() if mcp.connected else ""
                note = note[:5000]
                if note:
                    collected_notes.append(note)
                    history.append(f"[read_page] {note[:1200]}")
                    text = f"read {len(note)} chars"
                else:
                    text = "read_page failed or empty"
            elif action == "open_url":
                action_key = f"{action}:{text}:{x}:{y}"
                if action_key == last_action_key:
                    print(f"[ReAct] skip repeated open_url, wait for page focus/load: {text}")
                    if mcp and mcp.connected:
                        mcp.maximize()
                    time.sleep(2)
                else:
                    _open_url_in_browser(text, mcp)
            elif action == "ask_user":
                # AI 不确定，询问用户 — 直接结束循环返回提示
                ask_msg = text or "当前截图未找到明确目标，请描述你想点击的位置或下一步操作。"
                print(f"[ReAct] ask_user: {ask_msg}")
                history.append(f"[{iteration+1}] {thought} → ask_user: {ask_msg}")
                return {
                    "success": False,
                    "message": f"AI 不确定下一步：{ask_msg}",
                    "steps_done": f"{len(history)}/{iteration+1}",
                    "need_human": True,
                    "elapsed": f"{time.time() - start_time:.1f}s",
                }
            else:
                print(f"[ReAct] 未知动作: {action}")
        except Exception as e:
            print(f"[ReAct] 执行失败: {e}")

        # Record history
        action_desc = f"{action}"
        if action == "click":
            action_desc += f" ({x},{y})"
        elif action in ("type", "press", "scroll", "multi_scroll", "page_jump"):
            action_desc += f" \"{text}\""
        history.append(
            f"[{iteration+1}] state={state or '-'} | goal={goal or '-'} | "
            f"strategy={strategy} | attempt={attempt} | {thought} → {action_desc}"
        )

        time.sleep(1.5)

        # Update action fingerprint for the next ReAct step.
        loop.record_action(action, text, x, y)

    elapsed = time.time() - start_time

    if loop.forced_failure:
        failure_audit = {"success": False, "reason": loop.forced_failure, "need_human": True}
        try:
            from utils.gui_operation_memory import update_operation_memory

            update_operation_memory(user_id, memory_key, task, history, failure_audit)
        except Exception as exc:
            print(f"[GuiOpMemory] update skipped: {exc}")
        return {
            "success": False,
            "message": loop.forced_failure,
            "steps_done": f"{len(history)}/{iteration+1}",
            "need_human": True,
            "elapsed": f"{elapsed:.1f}s",
        }

    # --- Final Audit ---
    if progress_callback:
        progress_callback("🔎 最终审计中...")
    try:
        img = _screenshot_desktop()
        if mcp and mcp.connected:
            page_img = safe_browser_screenshot(mcp, progress_callback, history)
            if page_img:
                img = page_img
        audit = audit_result(task, img)
    except Exception as e:
        audit = {"success": len(history) > 0,
                 "reason": f"已执行 {len(history)} 个动作；审计截图失败: {e}",
                 "need_human": False}

    try:
        from utils.gui_operation_memory import update_operation_memory

        update_operation_memory(user_id, memory_key, task, history, audit)
    except Exception as exc:
        print(f"[GuiOpMemory] update skipped: {exc}")

    # 任务完成后不关闭浏览器，保留页面供用户查看
    # ESC 取消时才关闭 (见上方 cancel_file 检查)

    message = audit.get("reason", "")
    if collected_notes:
        notes = "\n\n---\n\n".join(collected_notes[-3:])
        message = f"{message}\n\n已读取网页资料：\n{notes[:8000]}".strip()

    return {
        "success": audit.get("success", False),
        "message": message,
        "steps_done": f"{len(history)}/{iteration+1}",
        "need_human": audit.get("need_human", False),
        "elapsed": f"{elapsed:.1f}s",
    }
