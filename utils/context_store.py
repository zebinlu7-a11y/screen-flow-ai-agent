# -*- coding: utf-8 -*-
"""
上下文持久化：将 LangChain 消息序列化为 JSON 并保存/加载。

JSON 格式：
{
  "version": 1,
  "messages": [
    {
      "role": "user",       // "user" | "assistant"
      "content": "text" | [{"type": "text", "text": "..."}, {"type": "image_url", ...}],
      "has_image": true/false,
      "image_base64": "..."  // 仅最近 N 轮保留，其余省略
    }
  ]
}
"""
import json
import os
from typing import List, Optional
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

from config import CONTEXT_FILE, MAX_IMAGE_BASE64_KEEP_TURNS


def _serialize_message(msg: BaseMessage, keep_image: bool = False) -> dict:
    """将单条 LangChain 消息序列化为字典。"""
    entry: dict = {}

    if isinstance(msg, HumanMessage):
        entry["role"] = "user"
    elif isinstance(msg, AIMessage):
        entry["role"] = "assistant"
    else:
        entry["role"] = "unknown"

    content = msg.content
    has_image = False
    image_base64 = None

    # 检查是否为多模态内容
    if isinstance(content, list):
        serialized_content = []
        for block in content:
            if isinstance(block, dict):
                block_copy = dict(block)
                if block.get("type") == "image_url" and keep_image:
                    url = block.get("image_url", {}).get("url", "")
                    if url.startswith("data:image/"):
                        has_image = True
                        b64_part = url.split("base64,", 1)[-1]
                        image_base64 = b64_part
                        # 保存时 base64 太长，可选择截断存储或保留
                        block_copy["image_url"]["url"] = "[IMAGE_BASE64_STORED]"
                serialized_content.append(block_copy)
        entry["content"] = serialized_content
    else:
        entry["content"] = content

    entry["has_image"] = has_image
    entry["image_base64"] = image_base64 if keep_image else None

    return entry


def _deserialize_message(entry: dict) -> BaseMessage:
    """将 JSON 字典反序列化为 LangChain 消息对象。"""
    role = entry.get("role", "user")
    content = entry.get("content", "")
    has_image = entry.get("has_image", False)
    image_base64 = entry.get("image_base64")

    # 如果保存时保留了 base64 且在内容中标记了占位符，则还原
    if has_image and image_base64 and isinstance(content, list):
        restored_content = []
        for block in content:
            if isinstance(block, dict):
                block_copy = dict(block)
                url = block.get("image_url", {}).get("url", "")
                if url == "[IMAGE_BASE64_STORED]":
                    block_copy["image_url"]["url"] = f"data:image/jpeg;base64,{image_base64}"
                restored_content.append(block_copy)
        content = restored_content

    if role == "user":
        return HumanMessage(content=content)
    elif role == "assistant":
        return AIMessage(content=content)
    else:
        return HumanMessage(content=content)


def save_context(messages: List[BaseMessage], filepath: str = CONTEXT_FILE) -> None:
    """
    将消息列表保存到 JSON 文件。
    只保留最近 MAX_IMAGE_BASE64_KEEP_TURNS 轮消息中的图片 base64 数据。
    """
    num_messages = len(messages)
    serialized = []

    for i, msg in enumerate(messages):
        # 从后往前数，最近的 N 条消息保留图片
        distance_from_end = num_messages - i
        keep_image = distance_from_end <= MAX_IMAGE_BASE64_KEEP_TURNS * 2
        serialized.append(_serialize_message(msg, keep_image=keep_image))

    data = {
        "version": 1,
        "messages": serialized,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_context(filepath: str = CONTEXT_FILE) -> List[BaseMessage]:
    """
    从 JSON 文件加载并反序列化为 LangChain 消息列表。
    如果文件不存在则返回空列表。
    """
    if not os.path.exists(filepath):
        return []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return []

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        messages = []
        for entry in data.get("messages", []):
            messages.append(_deserialize_message(entry))
        return messages
    except (json.JSONDecodeError, IOError, KeyError):
        return []


def clear_context(filepath: str = CONTEXT_FILE) -> None:
    """清空上下文文件。"""
    if os.path.exists(filepath):
        os.remove(filepath)
