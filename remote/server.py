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


class RemoteServer:
    """HTTP API 服务器，运行在独立守护线程中。手机通过轮询获取更新。"""

    def __init__(self, port: int = 8765):
        self._port = port
        self._app = web.Application()
        self._runner = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # 回调
        self.on_command: Optional[Callable[[str], None]] = None
        self.on_cancel: Optional[Callable[[], None]] = None

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

        self._html_path = os.path.join(os.path.dirname(__file__), "phone.html")
        self._setup_routes()

    def _setup_routes(self):
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/api/status", self._handle_status)
        self._app.router.add_get("/api/updates", self._handle_updates)
        self._app.router.add_post("/api/command", self._handle_command)
        self._app.router.add_post("/api/cancel", self._handle_cancel)

    async def _handle_index(self, request):
        try:
            with open(self._html_path, "r", encoding="utf-8") as f:
                html = f.read()
        except FileNotFoundError:
            html = "<h1>phone.html not found</h1>"
        return web.Response(text=html, content_type="text/html")

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
        return web.json_response({
            "logs": new_logs,
            "last_id": last_id,
            "screenshot": img,
            "img_time": img_time,
            "result": self._final_result,
            "state": "running" if self._agent_running else "ready",
        })

    async def _handle_command(self, request):
        try:
            data = await request.json()
            text = data.get("text", "").strip()
            print(f"[Remote] HTTP收到指令: {text}")
            if text:
                self._add_log(f"📨 收到指令: {text}")
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
        return web.json_response({"ok": True})

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

    def send_result(self, success: bool, message: str, elapsed: str = ""):
        self._final_result = {"success": success, "message": message, "elapsed": elapsed}
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
    # Try ngrok first, then Cloudflare
    tunnel_url = start_ngrok_tunnel(port, ngrok_token)
    if not tunnel_url:
        tunnel_url = start_cloudflare_tunnel(port)
    if tunnel_url:
        urls.append({"label": "公网 (任何网络)", "url": tunnel_url, "qr_base64": generate_qr_base64(tunnel_url)})
    return urls
