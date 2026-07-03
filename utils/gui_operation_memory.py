"""
Persistent operation memory for GUI/ReAct automation.

It stores a compact per-window workflow summary so follow-up remote commands can
continue from the current desktop/application state.
"""
import json
import os
import time
from datetime import datetime
from typing import List

from utils.user_manager import get_user_dir, _ensure_dir


def _safe_name(value: str) -> str:
    value = value or "default"
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)[:120]


def _path(user_id: str, memory_key: str) -> str:
    base = os.path.join(get_user_dir(user_id or "anon"), "gui_operations")
    _ensure_dir(base)
    return os.path.join(base, f"{_safe_name(memory_key)}.json")


def load_operation_memory(user_id: str, memory_key: str) -> dict:
    path = _path(user_id, memory_key)
    if not os.path.exists(path):
        return {
            "user_id": user_id,
            "memory_key": memory_key,
            "summary": "",
            "recent_steps": [],
            "updated": "",
        }
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "user_id": user_id,
            "memory_key": memory_key,
            "summary": "",
            "recent_steps": [],
            "updated": "",
        }


def save_operation_memory(user_id: str, memory_key: str, memory: dict) -> None:
    memory["user_id"] = user_id
    memory["memory_key"] = memory_key
    memory["updated"] = datetime.now().isoformat()
    with open(_path(user_id, memory_key), "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)


def build_operation_context(user_id: str, memory_key: str, query: str = "") -> str:
    memory = load_operation_memory(user_id, memory_key)
    lines = []
    if memory.get("summary"):
        lines.append("## 当前操作窗口历史流程")
        lines.append(memory["summary"])
    recent_steps = memory.get("recent_steps") or []
    if recent_steps:
        lines.append("最近操作:")
        for step in recent_steps[-8:]:
            lines.append(f"- {step}")

    try:
        from utils.vector_store import get_conversation_vector_store

        store = get_conversation_vector_store()
        sparse = store.search(query or memory_key, top_k=3)
        dense = store.search_dense(query or memory_key, top_k=3)
        seen = set()
        retrieved = []
        for doc_id, text, _ in sparse + dense:
            if doc_id in seen or not doc_id.startswith(f"guiop:{_safe_name(memory_key)}:"):
                continue
            seen.add(doc_id)
            retrieved.append(text)
        if retrieved:
            lines.append("相关历史检索:")
            for text in retrieved[:3]:
                lines.append(f"- {text[:300]}")
    except Exception as exc:
        print(f"[GuiOpMemory] retrieval skipped: {exc}")

    return "\n".join(lines).strip()


def summarize_operation(task: str, history: List[str], audit: dict, previous_summary: str = "") -> str:
    recent = "\n".join(history[-12:])
    status = "成功" if audit.get("success") else "未完成"
    reason = audit.get("reason", "")
    fallback = (
        f"上一轮任务: {task}\n"
        f"结果: {status}。{reason[:300]}\n"
        f"已有流程: {previous_summary[:500]}\n"
        f"最近步骤:\n{recent[:1200]}"
    )
    try:
        from agent.llm_client import ChatDoubaoVL
        from langchain_core.messages import HumanMessage

        prompt = f"""请把桌面自动化操作历史压缩成一段简短流程记忆，用于下一轮继续操作。
要求:
1. 只保留当前打开了什么窗口/文件/网页、做到哪一步、下一步应注意什么。
2. 不要超过 180 字。
3. 如果失败，说明失败点和已尝试方法。

已有流程:
{previous_summary}

本轮任务: {task}
审计结果: {status}。{reason}
最近步骤:
{recent}

输出流程记忆:"""
        llm = ChatDoubaoVL(model_name="doubao-seed-2-0-mini-260428")
        response = llm.invoke([HumanMessage(content=prompt)])
        text = response.content if hasattr(response, "content") else ""
        text = str(text).strip()
        if text:
            return text[:500]
    except Exception as exc:
        print(f"[GuiOpMemory] LLM summary skipped: {exc}")
    return fallback[-1800:]


def update_operation_memory(user_id: str, memory_key: str, task: str, history: List[str], audit: dict) -> dict:
    memory = load_operation_memory(user_id, memory_key)
    summary = summarize_operation(task, history, audit, memory.get("summary", ""))
    recent_steps = list(memory.get("recent_steps") or [])
    recent_steps.extend(history[-10:])
    memory["summary"] = summary
    memory["recent_steps"] = recent_steps[-30:]
    save_operation_memory(user_id, memory_key, memory)

    try:
        from utils.vector_store import get_conversation_vector_store

        store = get_conversation_vector_store()
        doc_id = f"guiop:{_safe_name(memory_key)}:{int(time.time() * 1000)}"
        store.add([summary], [doc_id])
    except Exception as exc:
        print(f"[GuiOpMemory] vector sync skipped: {exc}")
    return memory
