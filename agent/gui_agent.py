"""
GUI Agent — 三模型协作自动化桌面操作。

架构:
  用户指令 → Analyzer(分析) → Executor(执行) → Auditor(审计)

  Analyzer: pro/lite 模型，任务分解为步骤列表
  Executor: mini 模型，每步截图→AI定位→pyautogui执行
  Auditor:  pro 模型，截图验证最终结果是否成功
"""
import time
import base64
import io
import json
from typing import List, Optional, Dict, Callable
from dataclasses import dataclass, field
from PIL import Image


@dataclass
class ActionStep:
    """单个操作步骤。"""
    step_id: int
    description: str           # 人类可读描述
    action_type: str           # click | type | scroll | wait | screenshot | navigate | press
    target: str                # 操作目标描述（如"搜索框"）
    value: str = ""            # 输入内容（type动作）
    position: tuple = None     # 执行后填充的坐标
    status: str = "pending"    # pending | running | done | failed


@dataclass
class AgentResult:
    """Agent 执行结果。"""
    success: bool
    message: str
    steps: List[ActionStep] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)


# ============================================================
# 1. Analyzer — 任务分解
# ============================================================

ANALYZER_PROMPT = """你是一个桌面自动化专家。用户给你一个任务，你需要把它分解为一步步的桌面操作。

可用操作类型:
- click: 点击屏幕上的某个元素（如按钮、输入框、链接）
- type: 输入文字
- press: 按下键盘按键（如 enter, tab, ctrl+c）
- scroll: 滚动页面（值: up/down/up_much/down_much）
- wait: 等待（值: 秒数）
- screenshot: 截屏确认当前状态（不需要target）

输出格式 (纯JSON):
{
  "steps": [
    {"step_id": 1, "description": "打开浏览器", "action_type": "press", "target": "win+r", "value": ""},
    {"step_id": 2, "description": "输入网址", "action_type": "type", "target": "地址栏", "value": "https://www.baidu.com"},
    {"step_id": 3, "description": "确认", "action_type": "press", "target": "enter", "value": ""}
  ]
}

注意:
1. 第一步建议先截屏确认当前桌面状态
2. 如果目标不明确，加一个 screenshot step 让AI观察
3. 每个步骤的 target 要具体（如"搜索输入框"而非"输入框"）

用户任务: {task}

请输出JSON:"""


def analyze_task(task: str, model_name: str = "doubao-seed-2-0-lite-260428") -> List[ActionStep]:
    """用 AI 将用户任务分解为操作步骤。"""
    from agent.llm_client import ChatDoubaoVL
    from langchain_core.messages import HumanMessage

    prompt = ANALYZER_PROMPT.format(task=task)

    try:
        llm = ChatDoubaoVL(model_name=model_name)
        response = llm.invoke([HumanMessage(content=prompt)])
        text = response.content if hasattr(response, 'content') else ""

        # 提取 JSON（支持 markdown code block）
        import re as _re
        # 先取 ```json ... ``` 代码块
        m = _re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
        if m:
            text = m.group(1)
        else:
            # 直接找第一个 { 到最后一个 }
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]

        data = json.loads(text)
        steps = []
        raw_steps = data.get("steps", data.get("actions", data.get("operations", [])))
        for i, s in enumerate(raw_steps, 1):
            steps.append(ActionStep(
                step_id=s.get("step_id", s.get("id", i)),
                description=s.get("description", s.get("desc", s.get("action", ""))),
                action_type=s.get("action_type", s.get("type", "click")),
                target=s.get("target", s.get("element", s.get("目标", ""))),
                value=s.get("value", s.get("text", s.get("content", ""))),
            ))
        return steps
    except Exception as e:
        print(f"[Analyzer] 解析失败: {e}\n原始输出: {text[:500]}")

    # 回退：整个任务作为单步
    return [ActionStep(
        step_id=1, description=task,
        action_type="screenshot", target="", value=""
    )]


# ============================================================
# 2. Executor — 逐步执行
# ============================================================

EXECUTOR_PROMPT = """你是一个精确的屏幕操作执行器。我会给你当前屏幕截图和一个操作指令。
你需要观察截图，找出目标元素的位置，返回精确坐标。

输出格式 (纯JSON):
{
  "found": true/false,
  "x": 500,
  "y": 300,
  "reason": "搜索框位于页面顶部居中位置，坐标约为500,300"
}

如果没有找到目标元素，found 设为 false，说明原因。

当前操作: {action_description}
操作类型: {action_type}
目标: {target}
输入值: {value}"""


def _screenshot_pil() -> Image.Image:
    """截取全屏，返回 PIL Image。"""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app:
        screen = app.primaryScreen()
        if screen:
            pixmap = screen.grabWindow(0)
            byte_arr = io.BytesIO()
            pixmap.save(byte_arr, "PNG")
            return Image.open(byte_arr)

    # 回退：pyautogui
    import pyautogui
    return pyautogui.screenshot()


def _pil_to_base64(img: Image.Image) -> str:
    """PIL Image → base64 JPEG。"""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _executor_locate(step: ActionStep, screenshot_b64: str,
                     model_name: str = "doubao-seed-2-0-mini-260428") -> Optional[tuple]:
    """AI 观察截图，定位操作目标。"""
    from agent.llm_client import ChatDoubaoVL
    from langchain_core.messages import HumanMessage

    content = [
        {"type": "text", "text": EXECUTOR_PROMPT.format(
            action_description=step.description,
            action_type=step.action_type,
            target=step.target,
            value=step.value,
        )},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
    ]

    try:
        llm = ChatDoubaoVL(model_name=model_name)
        response = llm.invoke([HumanMessage(content=content)])
        text = response.content if hasattr(response, 'content') else ""

        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(text[start:end])
            if data.get("found") and data.get("x") and data.get("y"):
                return (int(data["x"]), int(data["y"]))
    except Exception as e:
        print(f"[Executor] 定位失败: {e}")

    return None


def execute_step(step: ActionStep, model_name: str = "doubao-seed-2-0-mini-260428") -> bool:
    """执行单个操作步骤。返回是否成功。"""
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.3

    step.status = "running"

    action = step.action_type
    if action == "wait":
        try:
            time.sleep(float(step.value or 1))
        except Exception:
            time.sleep(1)
        step.status = "done"
        return True

    if action == "screenshot":
        step.status = "done"
        return True

    # 需要定位的动作
    if action in ("click", "type", "scroll"):
        # 截图 → AI 定位
        img = _screenshot_pil()
        b64 = _pil_to_base64(img)

        pos = _executor_locate(step, b64, model_name)
        if pos is None:
            step.status = "failed"
            print(f"[Executor] 找不到目标: {step.target}")
            return False
        step.position = pos

    try:
        if action == "click":
            x, y = step.position
            pyautogui.click(x, y)

        elif action == "type":
            if step.position:
                x, y = step.position
                pyautogui.click(x, y)
                time.sleep(0.2)
            pyautogui.write(step.value, interval=0.05)

        elif action == "press":
            key = step.target.lower()
            key_map = {
                "enter": "enter", "tab": "tab", "escape": "esc",
                "win+r": ("win", "r"), "ctrl+c": ("ctrl", "c"),
                "ctrl+v": ("ctrl", "v"), "ctrl+a": ("ctrl", "a"),
                "ctrl+f": ("ctrl", "f"), "alt+tab": ("alt", "tab"),
                "backspace": "backspace", "delete": "delete",
                "space": "space", "up": "up", "down": "down",
                "left": "left", "right": "right",
                "pageup": "pageup", "pagedown": "pagedown",
                "home": "home", "end": "end",
            }
            mapped = key_map.get(key, key)
            if isinstance(mapped, tuple):
                pyautogui.hotkey(*mapped)
            else:
                pyautogui.press(mapped)

        elif action == "scroll":
            amount = {"up": 3, "down": -3, "up_much": 10, "down_much": -10}
            clicks = amount.get(step.value, -3)
            if step.position:
                x, y = step.position
                pyautogui.moveTo(x, y)
            pyautogui.scroll(clicks)

        step.status = "done"
        return True

    except Exception as e:
        step.status = "failed"
        print(f"[Executor] 执行失败: {step.description} - {e}")
        return False


# ============================================================
# 3. Auditor — 结果验证
# ============================================================

AUDITOR_PROMPT = """你是一个结果审计员。我会给你当前屏幕截图和原始任务描述。
请判断任务是否已经成功完成。

输出格式 (纯JSON):
{
  "success": true/false,
  "confidence": 0.95,
  "reason": "页面显示了百度搜索结果，包含Python教程相关链接",
  "need_human": false
}

任务: {task}
请输出JSON:"""


def audit_result(task: str, screenshot_b64: str,
                 model_name: str = "doubao-seed-2-0-pro-260215") -> dict:
    """审计执行结果。"""
    from agent.llm_client import ChatDoubaoVL
    from langchain_core.messages import HumanMessage

    content = [
        {"type": "text", "text": AUDITOR_PROMPT.format(task=task)},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}},
    ]

    try:
        llm = ChatDoubaoVL(model_name=model_name)
        response = llm.invoke([HumanMessage(content=content)])
        text = response.content if hasattr(response, 'content') else ""

        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception as e:
        print(f"[Auditor] 审计失败: {e}")

    return {"success": False, "confidence": 0, "reason": str(e), "need_human": True}


# ============================================================
# 4. Orchestrator — 主控
# ============================================================

def run_gui_task(task: str,
                 analyzer_model: str = "doubao-seed-2-0-lite-260428",
                 executor_model: str = "doubao-seed-2-0-mini-260428",
                 auditor_model: str = "doubao-seed-2-0-pro-260215",
                 progress_callback: Optional[Callable] = None,
                 ) -> AgentResult:
    """
    执行 GUI 自动化任务。

    Args:
        task: 用户任务描述，如"打开百度搜索Python"
        analyzer_model: 分析模型
        executor_model: 执行模型
        auditor_model: 审计模型
        progress_callback: 进度回调 (step描述)

    Returns:
        AgentResult
    """
    result = AgentResult(success=False, message="")

    # Phase 1: Analyze
    if progress_callback:
        progress_callback("🔍 分析任务...")
    print(f"[GUI-Agent] 分析任务: {task}")
    steps = analyze_task(task, analyzer_model)

    if not steps:
        result.message = "任务分析失败，无法生成步骤"
        return result

    result.steps = steps
    print(f"[GUI-Agent] 分析完成: {len(steps)} 步")
    for s in steps:
        print(f"  {s.step_id}. [{s.action_type}] {s.description}")

    # Phase 2: Execute
    for i, step in enumerate(steps):
        if progress_callback:
            progress_callback(f"▶ 执行 ({i+1}/{len(steps)}): {step.description}")

        print(f"[GUI-Agent] 执行 {i+1}/{len(steps)}: {step.description}")
        success = execute_step(step, executor_model)

        if not success and step.action_type not in ("screenshot", "wait"):
            # 失败不中断，继续尝试后续步骤
            print(f"[GUI-Agent] 步骤 {step.step_id} 失败，继续...")

        time.sleep(0.5)

    # Phase 3: Audit
    if progress_callback:
        progress_callback("🔎 审计结果...")

    print("[GUI-Agent] 审计中...")
    final_img = _screenshot_pil()
    final_b64 = _pil_to_base64(final_img)
    audit = audit_result(task, final_b64, auditor_model)

    result.success = audit.get("success", False)
    result.message = audit.get("reason", "")
    if audit.get("need_human"):
        result.message += "\n⚠️ 建议人工审查"

    if progress_callback:
        if result.success:
            progress_callback(f"✅ 执行成功: {result.message}")
        else:
            progress_callback(f"❌ 执行失败: {result.message}")

    return result
