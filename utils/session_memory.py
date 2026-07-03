"""
Optional Redis-backed short-term memory for active conversations.

The existing in-process message list and JSON conversation files remain the
fallback. Redis is used only when the `redis` package and Redis server are
available.
"""
import json
from typing import List, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from config import (
    REDIS_KEEP_IMAGE_TURNS,
    REDIS_SHORT_TERM_ENABLED,
    REDIS_SHORT_TERM_MAX_MESSAGES,
    REDIS_SHORT_TERM_TTL_SECONDS,
    REDIS_URL,
)


_client = None
_client_checked = False


def _get_client():
    global _client, _client_checked
    if _client_checked:
        return _client

    _client_checked = True
    if not REDIS_SHORT_TERM_ENABLED:
        return None

    try:
        import redis

        client = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=0.2,
            socket_timeout=0.5,
        )
        client.ping()
        _client = client
    except Exception as exc:
        print(f"[SessionMemory] Redis unavailable, using local memory: {exc}")
        _client = None

    return _client


def _key(user_id: str, conversation_id: str) -> str:
    safe_user = user_id or "anon"
    safe_conv = conversation_id or "default"
    return f"airag:session:{safe_user}:{safe_conv}:messages"


def _serialize_message(message: BaseMessage, keep_image: bool) -> dict:
    role = "user" if isinstance(message, HumanMessage) else "assistant"
    content = getattr(message, "content", "")

    if not isinstance(content, list):
        return {"role": role, "content": content}

    blocks = []
    for block in content:
        if not isinstance(block, dict):
            continue

        block_copy = dict(block)
        if block_copy.get("type") == "image_url":
            image_url = dict(block_copy.get("image_url") or {})
            url = image_url.get("url", "")
            if not keep_image and url.startswith("data:image/"):
                image_url["url"] = "[IMAGE_BASE64_OMITTED]"
            block_copy["image_url"] = image_url
        blocks.append(block_copy)

    return {"role": role, "content": blocks}


def _deserialize_message(entry: dict) -> BaseMessage:
    role = entry.get("role", "user")
    content = entry.get("content", "")
    if role == "assistant":
        return AIMessage(content=content)
    return HumanMessage(content=content)


def redis_available() -> bool:
    return _get_client() is not None


def load_session_messages(user_id: str, conversation_id: str, limit: Optional[int] = None) -> List[BaseMessage]:
    client = _get_client()
    if client is None:
        return []

    max_items = limit or REDIS_SHORT_TERM_MAX_MESSAGES
    try:
        raw_items = client.lrange(_key(user_id, conversation_id), -max_items, -1)
    except Exception as exc:
        print(f"[SessionMemory] Redis read failed: {exc}")
        return []

    messages: List[BaseMessage] = []
    for raw in raw_items:
        try:
            messages.append(_deserialize_message(json.loads(raw)))
        except Exception:
            continue
    return messages


def sync_session_messages(user_id: str, conversation_id: str, messages: List[BaseMessage]) -> None:
    client = _get_client()
    if client is None:
        return

    keep_image_messages = max(0, REDIS_KEEP_IMAGE_TURNS * 2)
    recent_messages = list(messages[-REDIS_SHORT_TERM_MAX_MESSAGES:])
    serialized = []
    total = len(recent_messages)
    for idx, message in enumerate(recent_messages):
        keep_image = total - idx <= keep_image_messages
        serialized.append(json.dumps(_serialize_message(message, keep_image), ensure_ascii=False))

    key = _key(user_id, conversation_id)
    try:
        pipe = client.pipeline()
        pipe.delete(key)
        if serialized:
            pipe.rpush(key, *serialized)
            pipe.expire(key, REDIS_SHORT_TERM_TTL_SECONDS)
        pipe.execute()
    except Exception as exc:
        print(f"[SessionMemory] Redis sync failed: {exc}")


def clear_session_messages(user_id: str, conversation_id: str) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        client.delete(_key(user_id, conversation_id))
    except Exception as exc:
        print(f"[SessionMemory] Redis clear failed: {exc}")
