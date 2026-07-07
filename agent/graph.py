# -*- coding: utf-8 -*-
import hashlib
import time
from dataclasses import dataclass
from typing import AsyncIterator, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agent.llm_client import ChatDoubaoVL, build_multimodal_message, build_text_message
from agent.state import AgentState
from config import RECENT_ROUNDS
from utils.memory_store import build_memory_context
from utils.retrieval_ranker import lexical_score, rerank, rrf_fuse


BASE_SYSTEM_PROMPT = (
    "你是一名专业编程老师。"
    "请根据用户提供的截图内容或问题，优先给出可直接使用的回答。"
    "如果是编程题，请按 LeetCode 风格组织答案：先给思路，再给代码，再给复杂度。"
    "代码必须添加清晰中文注释，解释关键步骤和变量含义，便于学习理解。"
    "如果截图里有题目、报错、界面状态或代码片段，请结合截图逐项分析，不要只做泛泛总结。"
    "如果信息不足，请明确说明缺少哪些关键信息，并给出下一步要怎么补充。"
    "回答风格：专业、简洁、结构清楚，优先 Markdown。"
)

MID_TERM_TOP_K = 4
MAX_SNIPPET_CHARS = 240
MEMORY_CACHE_SECONDS = 30

_conv_store = None
_memory_cache: dict = {
    "user_id": "",
    "query_hash": "",
    "checked_at": 0.0,
    "context": "",
}


@dataclass
class RetrievedMemory:
    short_term_messages: List[BaseMessage]
    mid_term_context: str
    long_term_context: str

    def as_prompt_context(self) -> str:
        parts = [self.long_term_context, self.mid_term_context]
        return "".join(part for part in parts if part)


def _get_conv_store():
    global _conv_store
    if _conv_store is None:
        from utils.vector_store import get_conversation_vector_store

        _conv_store = get_conversation_vector_store()
    return _conv_store


def _message_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, list):
        return " ".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
    return str(content).strip()


def _message_role(message: BaseMessage) -> str:
    if isinstance(message, HumanMessage):
        return "鐢ㄦ埛"
    if isinstance(message, AIMessage):
        return "AI"
    return "绯荤粺"


def index_conversation_messages(messages: List[BaseMessage], conversation_id: str = "default") -> None:
    """Index older conversation snippets for mid-term semantic recall."""
    if not messages:
        return

    try:
        store = _get_conv_store()
        texts: List[str] = []
        ids: List[str] = []

        for idx, message in enumerate(messages):
            text = _message_text(message)
            if len(text) < 8:
                continue
            role = "user" if isinstance(message, HumanMessage) else "assistant"
            texts.append(f"{_message_role(message)}: {text[:500]}")
            ids.append(f"{conversation_id}:{idx}:{role}")

        if texts:
            store.add(texts, ids)
    except Exception as exc:
        print(f"[Memory] mid-term index failed: {exc}")


def _keyword_search_messages(query: str, messages: List[BaseMessage], top_k: int) -> List[tuple]:
    scored = []
    for idx, message in enumerate(messages):
        text = _message_text(message)
        if len(text) < 8:
            continue
        score = lexical_score(query, text)
        if score > 0:
            role = "user" if isinstance(message, HumanMessage) else "assistant"
            scored.append((f"keyword:{idx}:{role}", f"{_message_role(message)}: {text[:500]}", score))

    scored.sort(key=lambda item: item[2], reverse=True)
    return scored[:top_k]


def search_conversation_history(
    query: str,
    older_messages: Optional[List[BaseMessage]] = None,
    top_k: int = MID_TERM_TOP_K,
) -> str:
    """Return formatted mid-term context from older conversation history."""
    if not query or not query.strip():
        return ""

    try:
        store = _get_conv_store()
        bm25_results = store.search(query, max(top_k * 3, 10))
        dense_results = store.search_dense(query, max(top_k * 3, 10))
        keyword_results = _keyword_search_messages(query, older_messages or [], max(top_k * 3, 10))
        fused = rrf_fuse([bm25_results, dense_results, keyword_results])
        results = rerank(query, fused, top_k=top_k)
    except Exception as exc:
        print(f"[Memory] mid-term search failed: {exc}")
        return ""

    if not results:
        return ""

    lines = ["\n\n## 中期记忆：历史相关对话"]
    seen = set()
    for _, text, score in results:
        snippet = text.strip()
        if not snippet or snippet in seen:
            continue
        seen.add(snippet)
        lines.append(f"- ({score:.2f}) {snippet[:MAX_SNIPPET_CHARS]}")

    return "\n".join(lines) if len(lines) > 1 else ""


def _get_cached_long_term_context(user_id: str, query: str) -> str:
    query_hash = hashlib.sha256((query or "").encode("utf-8")).hexdigest()[:16]
    now = time.time()

    if (
        _memory_cache["user_id"] == user_id
        and _memory_cache["query_hash"] == query_hash
        and now - _memory_cache["checked_at"] < MEMORY_CACHE_SECONDS
    ):
        return _memory_cache["context"]

    context = build_memory_context(user_id, query)
    _memory_cache.update(
        {
            "user_id": user_id,
            "query_hash": query_hash,
            "checked_at": now,
            "context": context,
        }
    )
    return context


def retrieve_memory_context(
    messages: List[BaseMessage],
    query: str,
    user_id: Optional[str],
    conversation_id: str = "default",
) -> RetrievedMemory:
    """
    Retrieve short, mid, and long-term memory for the current user query.

    Short-term memory is passed as real chat messages. Mid/long-term memory is
    injected into the system prompt as compact, source-labeled context.
    """
    recent_count = max(1, RECENT_ROUNDS * 2)
    short_term = list(messages[-recent_count:]) if messages else []
    older = list(messages[:-recent_count]) if len(messages) > recent_count else []

    mid_term_context = ""
    if query and older:
        index_conversation_messages(older, conversation_id=conversation_id)
        mid_term_context = search_conversation_history(query, older_messages=older, top_k=MID_TERM_TOP_K)

    long_term_context = ""
    if user_id:
        try:
            long_term_context = _get_cached_long_term_context(user_id, query or "")
        except Exception as exc:
            print(f"[Memory] long-term retrieval failed: {exc}")

    return RetrievedMemory(
        short_term_messages=short_term,
        mid_term_context=mid_term_context,
        long_term_context=long_term_context,
    )


def _build_system_prompt(memory: RetrievedMemory) -> str:
    context = memory.as_prompt_context()
    if not context:
        return BASE_SYSTEM_PROMPT

    return (
        BASE_SYSTEM_PROMPT
        + "\n\n请把下面的记忆当作辅助上下文使用；如果和用户本轮输入冲突，以本轮输入为准。"
        + context
    )


def trim_history_node(state: AgentState) -> dict:
    """Keep only the latest turns for the non-streaming LangGraph path."""
    messages = list(state["messages"])
    max_turns = state.get("max_turns", 10)
    max_messages = max_turns * 2
    return {"messages": messages[-max_messages:]} if len(messages) > max_messages else {"messages": messages}


def call_vlm_node(state: AgentState) -> dict:
    """Fallback non-streaming node used by graph.invoke."""
    messages = state["messages"]
    if not messages:
        return {"messages": [AIMessage(content="没有收到任何输入。")]}

    memory = RetrievedMemory(short_term_messages=list(messages), mid_term_context="", long_term_context="")
    llm = ChatDoubaoVL()
    response = llm.invoke([SystemMessage(content=_build_system_prompt(memory))] + list(messages))
    return {"messages": [response]}


def build_graph() -> StateGraph:
    """Compile the small LangGraph state machine with an in-memory checkpointer."""
    workflow = StateGraph(AgentState)
    workflow.add_node("trim_history", trim_history_node)
    workflow.add_node("call_vlm", call_vlm_node)
    workflow.set_entry_point("trim_history")
    workflow.add_edge("trim_history", "call_vlm")
    workflow.add_edge("call_vlm", END)
    return workflow.compile(checkpointer=MemorySaver())


async def stream_graph(
    graph: StateGraph,
    messages: List[BaseMessage],
    user_text: str,
    image_base64_list: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    max_turns: int = 10,
) -> AsyncIterator[str]:
    """
    Stream a response using explicit three-layer memory retrieval.

    The `graph` and `max_turns` arguments are kept for compatibility with the
    existing GUI worker. The streaming path calls the model directly.
    """
    del graph, max_turns

    if image_base64_list:
        input_msg = build_multimodal_message(user_text, image_base64_list=image_base64_list)
    elif user_text and user_text.strip():
        input_msg = build_text_message(user_text)
    else:
        input_msg = HumanMessage(content="请描述当前看到的内容。")

    memory = retrieve_memory_context(
        messages=list(messages or []),
        query=user_text or "",
        user_id=user_id,
        conversation_id=user_id or "default",
    )

    all_messages = [
        SystemMessage(content=_build_system_prompt(memory)),
        *memory.short_term_messages,
        input_msg,
    ]

    llm = ChatDoubaoVL()
    for chunk in llm.stream(all_messages):
        content = getattr(chunk, "content", "")
        if isinstance(content, str) and content:
            yield content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "text" in block.get("type", ""):
                    yield block.get("text", "")
