"""Persistent operation memory for GUI/ReAct automation."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import List

from utils.user_manager import _ensure_dir, get_user_dir


def _safe_name(value: str) -> str:
    value = value or "default"
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)[:120]


def _path(user_id: str, memory_key: str) -> str:
    base = os.path.join(get_user_dir(user_id or "anon"), "gui_operations")
    _ensure_dir(base)
    return os.path.join(base, f"{_safe_name(memory_key)}.json")


def load_operation_memory(user_id: str, memory_key: str) -> dict:
    empty = {
        "user_id": user_id,
        "memory_key": memory_key,
        "summary": "",
        "recent_steps": [],
        "last_result": {},
        "updated": "",
    }
    path = _path(user_id, memory_key)
    if not os.path.exists(path):
        return empty
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("last_result", {})
        data.setdefault("recent_steps", [])
        return data
    except Exception:
        return empty


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
        lines.append("## operation history summary")
        lines.append(memory["summary"])

    last_result = memory.get("last_result") or {}
    if last_result:
        lines.append("## last result")
        lines.append(f"- success: {last_result.get('success', False)}")
        if last_result.get("transfer_state"):
            lines.append(f"- transfer_state: {last_result.get('transfer_state')}")

    recent_steps = memory.get("recent_steps") or []
    if recent_steps:
        lines.append("## recent operations")
        for step in recent_steps[-8:]:
            lines.append(f"- {step}")

    if query.strip():
        lines.append("## current query")
        lines.append(query.strip())

    return "\n".join(lines).strip()


def summarize_operation(task: str, history: List[str], audit: dict, previous_summary: str = "") -> str:
    recent = "\n".join(history[-12:])
    status = "success" if audit.get("success") else "incomplete"
    reason = (audit.get("reason") or "")[:300]
    transfer_state = audit.get("transfer_state", "")
    transfer_line = f"transfer_state: {transfer_state}\n" if transfer_state else ""
    return (
        f"previous task: {task}\n"
        f"result: {status}. {reason}\n"
        f"{transfer_line}"
        f"existing flow: {previous_summary[:500]}\n"
        f"recent steps:\n{recent[:1200]}"
    )[:1800]


def update_operation_memory(user_id: str, memory_key: str, task: str, history: List[str], audit: dict) -> dict:
    memory = load_operation_memory(user_id, memory_key)
    summary = summarize_operation(task, history, audit, memory.get("summary", ""))
    recent_steps = list(memory.get("recent_steps") or [])
    recent_steps.extend(history[-10:])
    memory["summary"] = summary
    memory["recent_steps"] = recent_steps[-30:]
    memory["last_result"] = {
        "success": bool(audit.get("success")),
        "reason": audit.get("reason", ""),
        "transfer_state": audit.get("transfer_state", ""),
    }
    save_operation_memory(user_id, memory_key, memory)

    try:
        from utils.vector_store import get_conversation_vector_store

        store = get_conversation_vector_store()
        doc_id = f"guiop:{_safe_name(memory_key)}:{int(time.time() * 1000)}"
        store.add([summary], [doc_id])
    except Exception as exc:
        print(f"[GuiOpMemory] vector sync skipped: {exc}")
    return memory
