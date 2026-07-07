# -*- coding: utf-8 -*-
"""
LangGraph 状态定义
"""
from typing import Annotated, List
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """
    AI Agent 的全局状态。

    Attributes:
        messages: 多轮对话历史（包含多模态用户消息和 AI 回复）。
                  使用 add_messages reducer 自动拼接新消息。
        max_turns: 最大保留轮数（用于裁剪历史）。
    """
    messages: Annotated[List[BaseMessage], add_messages]
    max_turns: int
