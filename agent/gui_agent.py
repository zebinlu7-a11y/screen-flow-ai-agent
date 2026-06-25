"""
GUI Agent RPA — 浏览器 MCP (Playwright CDP) + 视觉识别双引擎。

参考 AI_RPA_pyqt.py 架构：
  1. 读码识别：通过 Playwright CDP 注入 JS 扫描 DOM，返回元素 ai_id
  2. 视觉识别：截图后用豆包 Vision 模型定位坐标
  3. 三层执行：JS events → pyautogui → 豆包视觉回退
"""
import os
import re
import json
import time
import base64
import io
import threading
from typing import List, Optional, Dict, Callable
from dataclasses import dataclass, field

from PIL import Image


# ============================================================
# Browser MCP Engine (Playwright CDP)
# ============================================================

class BrowserMCP:
    """Playwright 连接 Edge/Chrome CDP 进行 DOM 操作。"""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._connected = False

    @staticmethod
    def launch_browser(port: int = 9222) -> bool:
        """自动找到 Edge/Chrome 并带 CDP 端口启动。"""
        import subprocess
        paths = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        for p in paths:
            if os.path.exists(p):
                try:
                    subprocess.Popen([p, f"--remote-debugging-port={port}",
                                      "--new-window", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    print(f"[MCP] 已启动浏览器: {p}")
                    time.sleep(2)
                    return True
                except Exception as e:
                    print(f"[MCP] 启动失败: {e}")
        return False

    def connect(self, cdp_url: str = "http://127.0.0.1:9222", auto_launch: bool = True) -> bool:
        """连接到浏览器 CDP 端口。auto_launch=True 时自动启动浏览器。"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("[MCP] Playwright 未安装: pip install playwright && python -m playwright install chromium")
            return False

        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(cdp_url)
            self._context = self._browser.contexts[0]
            self._page = self._context.pages[0] if self._context.pages else None
            self._connected = bool(self._page)
            if self._connected:
                print(f"[MCP] 已连接浏览器: {self._page.title()}")
            return self._connected
        except Exception as e:
            if "ECONNREFUSED" in str(e) or "connect" in str(e).lower():
                if auto_launch and self.launch_browser():
                    print("[MCP] 浏览器已启动，重试连接...")
                    time.sleep(2)
                    return self.connect(cdp_url, auto_launch=False)
            print(f"[MCP] 连接失败 (端口 {cdp_url}): {e}")
            return False

    @property
    def page(self):
        return self._page

    @property
    def connected(self) -> bool:
        return self._connected

    def get_active_page(self):
        """获取最后打开的页面。"""
        if not self._context:
            return None
        pages = self._context.pages
        if pages:
            self._page = pages[-1]
        return self._page

    def scan_dom(self) -> str:
        """注入 JS 扫描 DOM，返回 JSON 字符串 {pageInfo, elements}。"""
        if not self._page:
            return "{}"
        self.get_active_page()
        script = """
        () => {
            window._ai_elements = [];
            const elements = [];
            let i = 0;
            const pageInfo = {
                title: document.title,
                url: window.location.href,
                viewport: { width: window.innerWidth, height: window.innerHeight }
            };
            const selectors = 'input, button, textarea, a, [role="button"], .el-button, .el-input__inner, span, div, li, h1, h2, h3, h4, h5, h6, p, label, form, select, option, img, section, article, nav, header, footer';
            const nodes = document.querySelectorAll(selectors);
            nodes.forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 1 && rect.height > 1 && rect.top < window.innerHeight + 1000) {
                    const text = (el.innerText || el.value || el.placeholder || el.title || "").trim().substring(0, 100);
                    elements.push({
                        tag: el.tagName,
                        text: text,
                        value: el.value || "",
                        placeholder: el.placeholder || "",
                        title: el.title || "",
                        id: el.id || "",
                        className: (el.className || "").substring(0, 80),
                        href: el.href || "",
                        type: el.type || el.getAttribute('type') || "",
                        role: el.getAttribute('role') || "",
                        pos: { x: rect.left, y: rect.top, w: rect.width, h: rect.height },
                        ai_id: i
                    });
                    window._ai_elements.push(el);
                    i++;
                }
            });
            return JSON.stringify({ pageInfo: pageInfo, elements: elements });
        }
        """
        return self._page.evaluate(script)

    def execute_js(self, ai_id: int, action: str, value: str = "") -> str:
        """通过 JS 在页面上执行操作。返回 "OK" / "ELEMENT_LOST" / 坐标JSON。"""
        js = f"""
        (val) => {{
            const el = window._ai_elements[{ai_id}];
            if (!el) return "ELEMENT_LOST";
            el.scrollIntoView({{block: 'center', behavior: 'instant'}});

            if ("{action}" === "click") {{
                const opts = {{ bubbles: true, cancelable: true, view: window, buttons: 1 }};
                el.dispatchEvent(new PointerEvent('pointerdown', opts));
                el.dispatchEvent(new MouseEvent('mousedown', opts));
                el.focus();
                el.dispatchEvent(new MouseEvent('mouseup', opts));
                el.dispatchEvent(new PointerEvent('pointerup', opts));
                el.click();
                return "OK";
            }}
            else if ("{action}" === "fill") {{
                el.focus();
                el.value = val;
                ['input', 'change', 'blur'].forEach(ev => {{
                    el.dispatchEvent(new Event(ev, {{ bubbles: true }}));
                }});
                return "OK";
            }}
            else if ("{action}" === "move") {{
                const r = el.getBoundingClientRect();
                return JSON.stringify({{ x: r.left + r.width/2, y: r.top + r.height/2 }});
            }}
            return "OK";
        }}
        """
        return self._page.evaluate(js, str(value))

    def screenshot_page(self) -> Optional[Image.Image]:
        """对当前页面截图，返回 PIL Image。"""
        if not self._page:
            return None
        buf = io.BytesIO()
        self._page.screenshot(path=buf)
        buf.seek(0)
        return Image.open(buf) if buf.getbuffer().nbytes > 0 else None

    def close(self):
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass


# ============================================================
# Vision Engine (豆包 VL 截图定位)
# ============================================================

VISION_PROMPT = """分析截图，找到与用户指令相关的元素位置，返回精确坐标。

用户指令: {task}

返回 JSON:
{{
  "found": true,
  "x": 500,
  "y": 300,
  "reason": "简短原因"
}}"""


def vision_locate(task: str, image: Image.Image,
                  model: str = "doubao-seed-2-0-lite-260428") -> Optional[dict]:
    """用豆包 VL 观察截图，返回元素坐标。"""
    from agent.llm_client import ChatDoubaoVL
    from langchain_core.messages import HumanMessage

    # PIL → base64 JPEG
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=80)
    img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    content = [
        {"type": "text", "text": VISION_PROMPT.format(task=task)},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
    ]

    try:
        llm = ChatDoubaoVL(model_name=model)
        response = llm.invoke([HumanMessage(content=content)])
        text = response.content if hasattr(response, 'content') else ""

        # 解析 JSON
        m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
        if m:
            text = m.group(1)
        else:
            s = text.find("{")
            e = text.rfind("}") + 1
            if s >= 0 and e > s:
                text = text[s:e]
        return json.loads(text.replace("'", '"'))
    except Exception as e:
        print(f"[Vision] 识别失败: {e}")
        return None


# ============================================================
# Analyzer — 任务分解
# ============================================================

ANALYZER_PROMPT = """你是桌面自动化专家。分析任务并分解为最小的操作步骤。

重要原则:
1. 如果任务涉及网页/浏览器，第一步必须是打开浏览器（Win+R→输入网址→回车）
2. 每个步骤只能做一个动作
3. 把复杂操作拆细（点击前先确保目标可见）

可用动作: click(点击元素), fill(填写文字), press(按键盘键), scroll(滚动), wait(等待秒数)

press 的常用键: enter, tab, escape, space, backspace, delete, win+r, ctrl+c, ctrl+v
组合键放在 target 字段: 如 action="press" target="win+r"

输出纯 JSON，step_id 从 1 开始:
{{
  "steps": [
    {{"step_id":1, "desc":"打开运行窗口", "action":"press", "target":"win+r", "value":""}},
    {{"step_id":2, "desc":"输入百度网址", "action":"fill", "target":"运行输入框", "value":"www.baidu.com"}},
    {{"step_id":3, "desc":"确认打开", "action":"press", "target":"enter", "value":""}},
    {{"step_id":4, "desc":"等待页面加载", "action":"wait", "target":"", "value":"3"}},
    {{"step_id":5, "desc":"点击搜索框", "action":"click", "target":"搜索框", "value":""}},
    {{"step_id":6, "desc":"输入搜索词", "action":"fill", "target":"搜索框", "value":"Python"}},
    {{"step_id":7, "desc":"搜索", "action":"press", "target":"enter", "value":""}}
  ]
}}

任务: {task}
输出 JSON:"""


def analyze_task(task: str) -> List[dict]:
    """AI 分解任务为步骤列表。"""
    from agent.llm_client import ChatDoubaoVL
    from langchain_core.messages import HumanMessage

    try:
        print(f"[Analyzer] 正在分析: {task}")
        llm = ChatDoubaoVL(model_name="doubao-seed-2-0-lite-260428")
        response = llm.invoke([HumanMessage(content=ANALYZER_PROMPT.format(task=task))])
        text = response.content if hasattr(response, 'content') else ""
        print(f"[Analyzer] AI 原始输出:\n{text[:800]}")

        # 提取 JSON
        m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
        if m:
            text = m.group(1)
        else:
            s, e = text.find("{"), text.rfind("}") + 1
            if s >= 0 and e > s:
                text = text[s:e]
        data = json.loads(text.replace("'", '"'))
        steps = data.get("steps", [])
        print(f"[Analyzer] 分解为 {len(steps)} 步:")
        for s in steps:
            print(f"  {s.get('step_id', s.get('id', '?'))}. [{s.get('action', '?')}] {s.get('desc', s.get('target', ''))}")
        return steps
    except Exception as e:
        print(f"[Analyzer] 失败: {e}")
        return [{"id": 1, "desc": task, "action": "click", "target": task, "value": ""}]


# ============================================================
# Executor — 双引擎执行
# ============================================================

def _screenshot_desktop() -> Image.Image:
    """截取整个桌面。"""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app:
        screen = app.primaryScreen()
        if screen:
            pixmap = screen.grabWindow(0)
            img = pixmap.toImage()
            buf = io.BytesIO()
            # QImage.save 需要通过 QBuffer
            from PyQt6.QtCore import QBuffer, QByteArray
            ba = QByteArray()
            qbuf = QBuffer(ba)
            qbuf.open(QBuffer.OpenModeFlag.WriteOnly)
            img.save(qbuf, "PNG")
            return Image.open(io.BytesIO(ba.data()))
    import pyautogui
    return pyautogui.screenshot()


def _pyautogui_execute(action: str, x: int, y: int, value: str = ""):
    """用 pyautogui 执行桌面操作。"""
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.2

    pyautogui.moveTo(x, y)
    if action == "click":
        pyautogui.click()
    elif action == "fill":
        pyautogui.click()
        time.sleep(0.1)
        pyautogui.write(value, interval=0.03)
    elif action == "press":
        key = value or "enter"
        try:
            pyautogui.press(key)
        except Exception:
            pass


def execute_step(step: dict, mcp: Optional[BrowserMCP] = None,
                 use_browser: bool = True,
                 progress: Callable = None,
                 hide_window: Callable = None,
                 show_window: Callable = None) -> bool:
    """
    执行单个步骤。双引擎决策：
    1. Playwright MCP (不影响鼠标，无干扰)
    2. 视觉+pyautogui (先隐藏Ai_Flow窗口避免遮挡)
    """
    action = step.get("action", "click")
    target = step.get("target", step.get("desc", ""))
    value = step.get("value", "")
    desc = step.get("desc", target)

    if progress:
        progress(f"▶ {desc}")

    # 特殊动作
    if action == "wait":
        time.sleep(float(value or 1) if value else 1)
        return True
    if action == "press":
        import pyautogui
        key = value or target or "enter"
        key_map = {"回车": "enter", "空格": "space", "tab": "tab", "esc": "escape"}
        pyautogui.press(key_map.get(key, key))
        return True

    # ===== 引擎 1: Playwright MCP（优先，不影响鼠标）=====
    if use_browser and mcp and mcp.connected:
        try:
            dom_json = mcp.scan_dom()
            dom_data = json.loads(dom_json)
            elements = dom_data.get("elements", [])

            best, best_score = None, 0
            for el in elements:
                text = (el.get("text", "") + el.get("placeholder", "") +
                        el.get("title", "") + el.get("id", "")).lower()
                score = sum(1 for w in target.lower().split() if w in text)
                if score > best_score:
                    best_score, best = score, el

            if best and best_score > 0:
                ai_id = best["ai_id"]
                result = mcp.execute_js(ai_id, action, value)
                if result == "OK":
                    print(f"[Exec] Playwright ✅: {desc} (ai_id={ai_id})")
                    return True
                print(f"[Exec] Playwright 失败 → 降级视觉...")
        except Exception as e:
            print(f"[Exec] Playwright 异常: {e}")

    # ===== 引擎 2: 视觉 + pyautogui（隐藏 Ai_Flow 窗口避免遮挡）=====
    if hide_window:
        hide_window()
        time.sleep(0.3)

    try:
        img = _screenshot_desktop()
        if mcp and mcp.connected:
            page_img = mcp.screenshot_page()
            if page_img:
                img = page_img

        pos = vision_locate(desc, img)
        if show_window:
            show_window()

        if pos and pos.get("found"):
            x, y = int(pos.get("x", 0)), int(pos.get("y", 0))
            # 用真实屏幕分辨率做坐标映射（不写死 1920×1080）
            import pyautogui
            real_w, real_h = pyautogui.size()
            img_w, img_h = img.size
            if abs(img_w - real_w) > 10 or abs(img_h - real_h) > 10:
                x = int(x * real_w / img_w)
                y = int(y * real_h / img_h)
            print(f"[Exec] 坐标: AI原始=({pos.get('x')},{pos.get('y')}) 图片={img_w}x{img_h} 屏幕={real_w}x{real_h} → 最终=({x},{y})")
            _pyautogui_execute(action, x, y, value)
            time.sleep(1.5)
            print(f"[Exec] Vision ✅: {desc} ({x},{y})")
            return True
        print(f"[Exec] Vision 未找到: {desc}")
    except Exception as e:
        print(f"[Exec] Vision 异常: {e}")
        if show_window:
            show_window()

    return False


# ============================================================
# Auditor
# ============================================================

def audit_result(task: str, image: Image.Image,
                 model: str = "doubao-seed-2-0-pro-260215") -> dict:
    """截图审计：判断任务是否成功。"""
    from agent.llm_client import ChatDoubaoVL
    from langchain_core.messages import HumanMessage

    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=80)
    img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    prompt = f"""观察当前截图，判断以下任务是否已经成功完成。

任务: {task}

判断标准:
- 如果截图显示任务目标已达成（如页面已打开、文字已输入、按钮已点击后的结果），返回 success=true
- 只有非常明确的任务失败才返回 success=false
- 有疑虑时倾向于 success=true

返回 JSON (不要输出其他内容):
{{"success": true, "reason": "具体看到了什么", "need_human": false}}"""

    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
    ]

    try:
        llm = ChatDoubaoVL(model_name=model)
        response = llm.invoke([HumanMessage(content=content)])
        text = response.content if hasattr(response, 'content') else ""
        m = re.search(r'\{.*\}', text, re.DOTALL)
        return json.loads(m.group().replace("'", '"')) if m else {"success": False, "need_human": True}
    except Exception:
        return {"success": False, "reason": "审计异常", "need_human": True}


# ============================================================
# Main Pipeline
# ============================================================

def run_gui_task(task: str,
                 use_browser: bool = True,  # 默认启用浏览器 MCP
                 steps: List[dict] = None,
                 progress_callback: Callable = None,
                 hide_window: Callable = None,
                 show_window: Callable = None) -> dict:
    """完整 RPA 流水线。"""
    mcp = None
    if use_browser:
        mcp = BrowserMCP()
        mcp.connect()

    # Phase 1: Analyze
    if not steps:
        if progress_callback:
            progress_callback("🔍 分析任务...")
        steps = analyze_task(task)

    if not steps:
        return {"success": False, "message": "任务分析失败"}

    # Phase 2: Execute
    ok = 0
    for i, step in enumerate(steps):
        success = execute_step(step, mcp, use_browser, progress_callback,
                               hide_window, show_window)
        if success:
            ok += 1
        time.sleep(0.5)

    # 给操作留出生效时间
    time.sleep(2)

    # Phase 3: Audit
    if progress_callback:
        progress_callback("🔎 审计中...")
    img = _screenshot_desktop()
    if mcp and mcp.connected:
        page_img = mcp.screenshot_page()
        if page_img:
            img = page_img
    audit = audit_result(task, img)

    if mcp:
        mcp.close()

    return {
        "success": audit.get("success", False),
        "message": audit.get("reason", ""),
        "steps_done": f"{ok}/{len(steps)}",
        "need_human": audit.get("need_human", not audit.get("success")),
    }
