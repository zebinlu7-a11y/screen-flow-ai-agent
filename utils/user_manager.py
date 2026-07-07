# -*- coding: utf-8 -*-
"""
用户与对话管理器。
每个 API Key 对应一个用户，每个用户可创建多个对话。
对话保存为独立 JSON 文件，用户档案保存在 profile.json。
"""
import os
import json
import hashlib
import time
from typing import List, Optional, Dict
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "airag_data")


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def user_id_from_key(api_key: str) -> str:
    """根据 API Key 生成唯一用户 ID（SHA256 前16位）。"""
    if not api_key:
        return "anonymous"
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def get_user_dir(user_id: str) -> str:
    return os.path.join(DATA_DIR, "users", user_id)


def get_conv_dir(user_id: str) -> str:
    d = os.path.join(get_user_dir(user_id), "conversations")
    _ensure_dir(d)
    return d


# ============================================================
# 对话管理
# ============================================================

def new_conversation(user_id: str, title: str = "") -> dict:
    """创建新对话，返回对话 dict。"""
    conv_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + hashlib.md5(str(time.time()).encode()).hexdigest()[:6]
    conv = {
        "id": conv_id,
        "created": datetime.now().isoformat(),
        "updated": datetime.now().isoformat(),
        "title": title or "新对话",
        "model": "",
        "messages": [],
    }
    save_conversation(user_id, conv)
    return conv


def save_conversation(user_id: str, conv: dict):
    """保存对话到 JSON 文件。"""
    conv["updated"] = datetime.now().isoformat()
    # 自动生成标题（取第一条用户消息前30字）
    if conv.get("title") in ("新对话", "") and conv.get("messages"):
        for m in conv["messages"]:
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                conv["title"] = m["content"][:30]
                break

    conv_dir = get_conv_dir(user_id)
    filepath = os.path.join(conv_dir, f"{conv['id']}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(conv, f, ensure_ascii=False, indent=2)


def load_conversation(user_id: str, conv_id: str) -> Optional[dict]:
    """加载指定对话。"""
    filepath = os.path.join(get_conv_dir(user_id), f"{conv_id}.json")
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def delete_conversation(user_id: str, conv_id: str):
    """删除对话文件。"""
    filepath = os.path.join(get_conv_dir(user_id), f"{conv_id}.json")
    if os.path.exists(filepath):
        os.remove(filepath)


def list_conversations(user_id: str) -> List[dict]:
    """列出用户所有对话（按时间倒序），返回摘要列表。"""
    conv_dir = get_conv_dir(user_id)
    if not os.path.exists(conv_dir):
        return []

    convs = []
    for fname in os.listdir(conv_dir):
        if fname.endswith(".json"):
            try:
                with open(os.path.join(conv_dir, fname), "r", encoding="utf-8") as f:
                    d = json.load(f)
                convs.append({
                    "id": d.get("id", fname[:-5]),
                    "created": d.get("created", ""),
                    "updated": d.get("updated", ""),
                    "title": d.get("title", "未命名对话"),
                    "msg_count": len(d.get("messages", [])),
                })
            except Exception:
                pass

    convs.sort(key=lambda x: x.get("updated", ""), reverse=True)
    return convs


def get_last_conversation(user_id: str) -> Optional[dict]:
    """获取最近一次对话。"""
    convs = list_conversations(user_id)
    if convs:
        return load_conversation(user_id, convs[0]["id"])
    return None


# ============================================================
# 活跃对话追踪
# ============================================================

_active_conv_path = os.path.join(DATA_DIR, "active_conversation.json")


def get_active_conversation_id() -> Optional[str]:
    """获取当前活跃对话 ID。"""
    if not os.path.exists(_active_conv_path):
        return None
    try:
        with open(_active_conv_path, "r") as f:
            return json.load(f).get("conv_id")
    except Exception:
        return None


def set_active_conversation_id(user_id: str, conv_id: str):
    """设置当前活跃对话。"""
    _ensure_dir(DATA_DIR)
    with open(_active_conv_path, "w") as f:
        json.dump({"user_id": user_id, "conv_id": conv_id}, f)
