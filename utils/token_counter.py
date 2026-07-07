# -*- coding: utf-8 -*-
"""
简易 Token 估算器。
针对中英文混合文本和多模态消息进行粗略的 token 计数。
"""
import re
from typing import List
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

from config import MAX_TOKEN_ESTIMATE


def estimate_text_tokens(text: str) -> int:
    """
    估算文本的 token 数量。
    中文字符 ≈ 1.5 token
    英文单词 ≈ 1.3 token
    标点符号 ≈ 1 token
    """
    if not text:
        return 0

    # 统计中文字符
    chinese_chars = len(re.findall(r'[一-鿿㐀-䶿]', text))
    # 统计英文单词
    english_words = len(re.findall(r'[a-zA-Z]+', text))
    # 其余字符（标点、数字等）
    other_chars = len(re.findall(r'[^一-鿿㐀-䶿a-zA-Z\s]', text))

    tokens = chinese_chars * 1.5 + english_words * 1.3 + other_chars * 1.0
    return int(tokens)


def estimate_image_tokens(base64_str: str) -> int:
    """
    根据 base64 编码长度粗略估算图片 token 数。
    豆包 VL 图片 token 计算通常是按图片被分割成的 patch 数量。
    粗略方法：base64 长度 / 4 ≈ 原始字节数，然后按 ~85 tokens per 512x512 tile 估算。
    这里使用简化版：每 1KB base64 ≈ 200 tokens（经验值）。
    """
    if not base64_str:
        return 0
    kb = len(base64_str) / 1024.0
    return int(kb * 200)


def estimate_message_tokens(message: BaseMessage) -> int:
    """
    估算单条消息的 token 数量。
    """
    tokens = 0
    content = message.content

    if isinstance(content, str):
        tokens += estimate_text_tokens(content)
    elif isinstance(content, list):
        # 多模态内容列表
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    tokens += estimate_text_tokens(block.get("text", ""))
                elif block.get("type") == "image_url":
                    image_url = block.get("image_url", {}).get("url", "")
                    if image_url.startswith("data:image/"):
                        # 提取 base64 部分
                        b64_part = image_url.split("base64,", 1)[-1]
                        tokens += estimate_image_tokens(b64_part)

    return tokens


def estimate_total_tokens(messages: List[BaseMessage]) -> int:
    """
    估算整个消息列表的总 token 数量。
    """
    total = 0
    for msg in messages:
        total += estimate_message_tokens(msg)
    return total


def is_over_token_limit(messages: List[BaseMessage], limit: int = MAX_TOKEN_ESTIMATE) -> bool:
    """
    判断是否超过 token 限制。
    """
    return estimate_total_tokens(messages) > limit


def strip_images_from_message(message: BaseMessage) -> BaseMessage:
    """
    将消息中的图片部分移除，只保留文本内容。
    用于历史消息的 Token 裁剪。
    """
    content = message.content
    if isinstance(content, str):
        return message

    if isinstance(content, list):
        text_parts = []
        has_text = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "").strip()
                if text:
                    text_parts.append(text)
                    has_text = True

        if has_text:
            new_text = " ".join(text_parts)
        else:
            new_text = "[已截屏，图片内容省略]"

        if isinstance(message, HumanMessage):
            return HumanMessage(content=new_text)
        else:
            return AIMessage(content=message.content if isinstance(message.content, str) else message.content)

    return message
