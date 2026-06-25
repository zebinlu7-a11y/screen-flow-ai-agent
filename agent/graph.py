"""
LangGraph 核心状态机 + 流式接口。

图结构：trim_history → call_vlm → END

流式接口 stream_graph：
  - 保留最近 N 轮对话上下文
  - 直接调用 ChatDoubaoVL.stream() 获取逐 token 输出
"""
from typing import AsyncIterator, Optional, List

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

from agent.state import AgentState
from agent.llm_client import ChatDoubaoVL, build_text_message, build_multimodal_message
from config import RECENT_ROUNDS

# FAISS 向量存储（懒加载，避免启动时加载模型）
_vec_store = None


def _get_conv_store():
    global _vec_store
    if _vec_store is None:
        from utils.vector_store import get_conversation_vector_store
        _vec_store = get_conversation_vector_store()
    return _vec_store


def index_conversation_messages(messages):
    """将对话消息索引到 FAISS（调用方负责去重）。"""
    if not messages:
        return
    try:
        from langchain_core.messages import HumanMessage, AIMessage
        store = _get_conv_store()
        texts = []
        ids = []
        for i, m in enumerate(messages):
            role = "Q" if isinstance(m, HumanMessage) else "A"
            content = m.content
            if isinstance(content, list):
                content = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
            if isinstance(content, str) and len(content.strip()) > 5:
                texts.append(content[:500])
                ids.append(f"msg_{i}_{role}")
        if texts:
            store.add(texts, ids)
    except Exception as e:
        pass  # FAISS 失败不阻塞主流程


def search_conversation_history(query: str, top_k: int = 3) -> str:
    """从对话历史中语义搜索相关内容，返回格式化文本。"""
    try:
        store = _get_conv_store()
        results = store.search(query, top_k)
        if results:
            lines = ["\n## 历史相关对话（语义检索）"]
            for _, text, score in sorted(results, key=lambda x: x[1], reverse=True):
                lines.append(f"- {text[:200]}")
            return "\n".join(lines)
    except Exception:
        pass
    return ""
from utils.memory_store import build_memory_context, load_profile
from utils.user_manager import user_id_from_key, get_active_conversation_id, load_conversation

# 基础系统提示词
BASE_SYSTEM_PROMPT = (
    "你是一个实用的桌面 AI 助手，用户可能发送文字、截图或两者结合。"
    "请仔细分析所有输入内容，给出详细、完整、有深度的回答。"
    "如果是代码问题，请解释原理并给出代码示例；"
    "如果是图表或数据，请详细解读趋势和关键信息；"
    "如果是报错信息，请分析原因并提供具体解决步骤；"
    "如果用户只是提问，请充分展开回答，不要过于简短。"
    "回答风格：专业但不啰嗦，结构清晰，善用 Markdown 排版。"
)

# 记忆缓存（避免每轮读磁盘 + 调 API）
_mem_cache: dict = {"user_id": "", "last_check": 0, "context": ""}


_KW_STOP_WORDS = set("的 了 吗 呢 啊 这 那 一个 一些 什么 怎么 为什么 可以 不能 没有 不是 我 你 他 她 它 我们 你们 他们 以及 与 和 或 就 都 还 也 很 更 被 把 给 对 在 向 从 请 帮 一下".split())


def _extract_keywords(text: str) -> List[str]:
    """提取用于直返路由的关键词。"""
    if not text:
        return []

    keywords: List[str] = []
    seen = set()

    for token in re.findall(r"[A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", text):
        token = token.strip().lower()
        if not token or token in _KW_STOP_WORDS:
            continue
        if token not in seen:
            seen.add(token)
            keywords.append(token)

    return keywords


def _direct_keyword_reply(user_text: str, user_id: str) -> str:
    """关键词命中时直接返回，不调用大模型。"""
    profile = load_profile(user_id)
    facts = profile.get("facts", []) or []
    if not facts:
        return ""

    keywords = _extract_keywords(user_text)
    if not keywords:
        return ""

    matched = []
    for fact in facts:
        content = str(fact.get("content", ""))
        if not content:
            continue
        content_lower = content.lower()
        hit_count = sum(1 for kw in keywords if kw in content_lower or kw in content)
        if hit_count > 0:
            matched.append((hit_count, fact))

    if not matched:
        return ""

    matched.sort(key=lambda item: item[0], reverse=True)
    top_facts = [fact for _, fact in matched[:3]]
    lines = ["我直接根据关键词检索到了这些相关记忆："]
    for fact in top_facts:
        fact_type = fact.get("type", "fact")
        content = fact.get("content", "")
        lines.append(f"- [{fact_type}] {content}")
    return "\n".join(lines)

def _get_dynamic_system_prompt(api_key: str = "", query: str = "") -> str:
    """构建动态 System Prompt：基础提示 + 长期记忆（缓存版本）。"""
    import time, hashlib
    uid = hashlib.sha256((api_key or "anon").encode()).hexdigest()[:16]

    # 缓存：同一用户60秒内复用
    now = time.time()
    if _mem_cache["user_id"] == uid and (now - _mem_cache["last_check"]) < 60:
        return BASE_SYSTEM_PROMPT + _mem_cache["context"]

    _mem_cache["user_id"] = uid
    _mem_cache["last_check"] = now

    try:
        mem = build_memory_context(uid, query)
        _mem_cache["context"] = mem
        return BASE_SYSTEM_PROMPT + mem
    except Exception:
        return BASE_SYSTEM_PROMPT


# ============================================================
# Node Functions
# ============================================================

def trim_history_node(state: AgentState) -> dict:
    """
    历史裁剪节点：只保留最近 N 轮（条数限制，不做 Token 检查）。
    Token 层面的控制由 stream_graph 中的智能检索负责。
    """
    messages = list(state["messages"])
    max_turns = state.get("max_turns", 10)
    max_messages = max_turns * 2

    if len(messages) > max_messages:
        messages = messages[-max_messages:]

    return {"messages": messages}


def call_vlm_node(state: AgentState) -> dict:
    """非流式调用 VLM（graph.invoke 备用）。"""
    messages = state["messages"]
    if not messages:
        return {"messages": [AIMessage(content="没有收到任何输入。")]}

    llm = ChatDoubaoVL()
    all_messages = [SystemMessage(content=_get_dynamic_system_prompt())] + list(messages)
    response = llm.invoke(all_messages)
    return {"messages": [response]}


# ============================================================
# Graph Construction
# ============================================================

def build_graph() -> StateGraph:
    """编译 LangGraph，使用 MemorySaver 按 thread_id 持久化状态。"""
    workflow = StateGraph(AgentState)
    workflow.add_node("trim_history", trim_history_node)
    workflow.add_node("call_vlm", call_vlm_node)
    workflow.set_entry_point("trim_history")
    workflow.add_edge("trim_history", "call_vlm")
    workflow.add_edge("call_vlm", END)

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)


# ============================================================
# Streaming Interface — 智能检索 + 流式输出
# ============================================================

async def stream_graph(
    graph: StateGraph,
    messages: List[BaseMessage],
    user_text: str,
    image_base64_list: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    max_turns: int = 10,
) -> AsyncIterator[str]:
    """
    流式执行：智能检索相关历史 → 拼装上下文 → 逐 token 输出。

    混合策略：
    - 最近 RECENT_ROUNDS 轮完整保留
    - 从更早历史中检索最多 MAX_RETRIEVED_MESSAGES 条相关消息
    - 移除 Token 硬上限，靠检索条数控制上下文大小

    Args:
        graph: 编译好的 LangGraph
        messages: 全部对话历史（可能很长）
        user_text: 本轮用户文本
        image_base64_list: 本轮截图的 base64 列表
        max_turns: 最大保留轮数（备份）

    Yields:
        逐 token 文本片段。
    """
    # 1. 构建本轮输入消息
    if image_base64_list:
        input_msg = build_multimodal_message(user_text, image_base64_list=image_base64_list)
    elif user_text and user_text.strip():
        input_msg = build_text_message(user_text)
    else:
        input_msg = HumanMessage(content="请描述当前看到的内容。")

    # 1.5 关键词直返：命中长期记忆时直接回复，不走大模型
    if user_text and user_text.strip() and user_id:
        direct_reply = _direct_keyword_reply(user_text, user_id)
        if direct_reply:
            yield direct_reply
            return

    # 2. 上下文：最近 N 轮 + FAISS 语义召回早期相关消息
    if messages:
        recent_count = RECENT_ROUNDS * 2
        recent = list(messages[-recent_count:]) if len(messages) > recent_count else list(messages)

        # FAISS 语义搜索早期对话
        faiss_context = ""
        if user_text and len(messages) > recent_count:
            older = list(messages[:-recent_count])
            index_conversation_messages(older)
            faiss_context = search_conversation_history(user_text, top_k=3)

        # 将 FAISS 结果注入最近的系统消息中
        if faiss_context:
            relevant = list(recent)
            # 把 FAISS 上下文附到 system prompt（在步骤3拼接时生效）
        else:
            relevant = recent
    else:
        relevant = []
        faiss_context = ""

    # 3. 拼装：动态系统提示（含长期记忆+FAISS语义历史） + 近期历史 + 本轮新消息
    system_prompt = _get_dynamic_system_prompt(query=user_text or "")
    if faiss_context:
        system_prompt += faiss_context
    all_messages = [SystemMessage(content=system_prompt)] + relevant + [input_msg]

    # 4. 直接流式调用 ChatDoubaoVL
    llm = ChatDoubaoVL()
    for chunk in llm.stream(all_messages):
        if chunk.content:
            content = chunk.content
            if isinstance(content, str):
                yield content
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and content:
                        block_type = block.get("type", "")
                        if "text" in block_type:
                            yield block.get("text", "")
