"""
长期记忆管理 — AI 提取事实 + 关键词检索 + 注入 System Prompt。

记忆提取: 对话结束后调 mini 模型提取关键事实 → 存 profile.json
记忆检索: 新消息来时关键词匹配相关事实 → 注入 prompt
"""
import json
import os
import re
import time
from typing import List, Optional, Dict
from datetime import datetime

from utils.user_manager import get_user_dir, _ensure_dir


def _profile_path(user_id: str) -> str:
    return os.path.join(get_user_dir(user_id), "profile.json")


def load_profile(user_id: str) -> dict:
    """加载用户长期记忆档案。"""
    path = _profile_path(user_id)
    if not os.path.exists(path):
        return _empty_profile(user_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return _empty_profile(user_id)


def save_profile(user_id: str, profile: dict):
    """保存用户档案。"""
    profile["updated"] = datetime.now().isoformat()
    _ensure_dir(get_user_dir(user_id))
    with open(_profile_path(user_id), "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)


def _empty_profile(user_id: str) -> dict:
    return {
        "user_id": user_id,
        "created": datetime.now().isoformat(),
        "updated": datetime.now().isoformat(),
        "facts": [],
        "summary": "",
        "stats": {"total_conversations": 0, "total_messages": 0},
    }


# ============================================================
# 事实提取（AI）
# ============================================================

_EXTRACT_PROMPT = (
    "你是一个信息提取助手。请从以下对话中提取关于用户的关键事实。"
    "只提取用户的个人信息，不要提取AI的回复内容。"
    "输出格式：每行一个事实，用\"类型: 内容\"格式。"
    "类型包括: identity(身份), preference(偏好), project(项目), problem(遇到的问题), knowledge(知识水平)\n\n"
    "示例输出:\n"
    "identity: 用户叫张三\n"
    "project: 正在开发Ai_Flow截图工具，使用PyQt6+LangGraph\n"
    "preference: 喜欢简洁的代码，不要过多注释\n"
    "problem: 在Windows高分屏上截图模糊\n\n"
    "对话内容:\n{conversation}\n\n"
    "请提取事实（如果没有值得提取的信息，输出\"NONE\"）:"
)


def extract_facts_from_conversation(
    messages: List, user_id: str,
    model_name: str = "doubao-seed-2-0-mini-260428",
) -> List[dict]:
    """
    调 AI 从对话中提取长期记忆事实。
    返回事实列表：[{"type": "identity", "content": "用户叫张三"}, ...]
    """
    if not messages:
        return []

    # 构建对话文本（只取最后10轮）
    recent = messages[-20:] if len(messages) > 20 else messages
    conv_text = ""
    for m in recent:
        role = "用户" if m.get("role") == "user" else "AI"
        content = m.get("content", "")
        if isinstance(content, list):
            # 多模态消息，只取文本部分
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            content = " ".join(texts)
        if isinstance(content, str) and content.strip():
            conv_text += f"{role}: {content[:300]}\n"

    if not conv_text.strip():
        return []

    prompt = _EXTRACT_PROMPT.format(conversation=conv_text)

    try:
        from agent.llm_client import ChatDoubaoVL
        from langchain_core.messages import SystemMessage

        llm = ChatDoubaoVL(model_name=model_name)
        response = llm.invoke([SystemMessage(content=prompt)])
        text = response.content if hasattr(response, 'content') else ""

        if "NONE" in text.upper():
            return []

        facts = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if ":" in line:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    ftype = parts[0].strip()
                    fcontent = parts[1].strip()
                    if fcontent and len(fcontent) > 3:
                        facts.append({
                            "id": f"f{int(time.time()*1000)}",
                            "type": ftype,
                            "content": fcontent,
                            "created": datetime.now().isoformat(),
                        })
        return facts

    except Exception as e:
        print(f"[Memory] 事实提取失败: {e}")
        return []


# ============================================================
# 事实合并 + 检索
# ============================================================

def merge_facts(existing: List[dict], new_facts: List[dict]) -> List[dict]:
    """合并新旧事实，去重（按内容相似度）。"""
    result = list(existing)
    for nf in new_facts:
        is_dup = False
        for ef in result:
            # 简单去重：同类型+相似内容
            if ef.get("type") == nf.get("type"):
                existing_words = set(ef.get("content", ""))
                new_words = set(nf.get("content", ""))
                if existing_words and new_words:
                    overlap = len(existing_words & new_words) / max(len(existing_words | new_words), 1)
                    if overlap > 0.5:
                        is_dup = True
                        break
        if not is_dup:
            result.append(nf)
    # 保留最近50条事实
    result = result[-50:]
    # 同步到 FAISS
    sync_facts_to_vector(result)
    return result


def search_facts(query: str, facts: List[dict], top_k: int = 5) -> List[dict]:
    """
    FAISS 语义检索相关事实，返回 top_k 条。
    回退到关键词匹配如果向量库不可用。
    """
    if not query or not facts:
        return []

    # 尝试 FAISS 语义搜索
    try:
        from utils.vector_store import get_memory_vector_store
        store = get_memory_vector_store()
        results = store.search(query, top_k)
        if results:
            # 将 FAISS 结果映射回 facts
            fact_map = {f.get("content", ""): f for f in facts}
            matched = []
            for _, text, score in results:
                for f in facts:
                    if f.get("content", "") == text:
                        matched.append(f)
                        break
            if matched:
                return matched
    except Exception as e:
        print(f"[Memory] FAISS 搜索失败，回退关键词: {e}")

    # 回退：关键词匹配
    keywords = set(re.findall(r'[一-鿿]{2,4}', query))
    stop_words = set("的了吗呢啊这个是哪个有什么可以能不能怎么为什么".split())
    keywords = {w for w in keywords if w not in stop_words}

    scored = []
    for f in facts:
        content = f.get("content", "")
        score = sum(1 for kw in keywords if kw in content)
        if score > 0:
            scored.append((score, f))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [f for _, f in scored[:top_k]]


def sync_facts_to_vector(facts: List[dict]):
    """将事实同步到 FAISS 向量库。"""
    if not facts:
        return
    try:
        from utils.vector_store import get_memory_vector_store
        store = get_memory_vector_store()
        store.clear()
        texts = [f.get("content", "") for f in facts]
        ids = [f.get("id", str(i)) for i, f in enumerate(facts)]
        store.add(texts, ids)
        print(f"[Memory] FAISS 同步完成: {len(texts)} 条事实")
    except Exception as e:
        print(f"[Memory] FAISS 同步失败: {e}")


# ============================================================
# 记忆注入
# ============================================================

def build_memory_context(user_id: str, query: str = "") -> str:
    """
    构建注入 prompt 的记忆文本。
    返回空字符串表示无相关记忆。
    """
    profile = load_profile(user_id)
    facts = profile.get("facts", [])

    if not facts:
        return ""

    # 检索相关事实
    relevant = search_facts(query, facts, top_k=5) if query else facts[-3:]

    if not relevant:
        return ""

    lines = ["\n\n## 关于当前用户（长期记忆）"]
    for f in relevant:
        lines.append(f"- {f.get('content', '')}")

    return "\n".join(lines)


# ============================================================
# Clean retrieval overrides
# ============================================================

_STOP_WORDS = {
    "的", "了", "呢", "吗", "啊", "是", "我", "你", "他", "她", "它", "我们", "你们",
    "这个", "那个", "什么", "怎么", "为什么", "可以", "不能", "有没有", "一下",
}


def _tokenize_query(text: str) -> List[str]:
    """Extract lightweight Chinese/English keywords for fallback retrieval."""
    if not text:
        return []

    tokens = re.findall(r"[A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    result = []
    seen = set()
    for token in tokens:
        if token in _STOP_WORDS or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _fact_text(fact: dict) -> str:
    return str(fact.get("content", "")).strip()


def search_facts(query: str, facts: List[dict], top_k: int = 5) -> List[dict]:
    """
    Hybrid long-term memory retrieval.

    1. Try vector retrieval.
    2. Add keyword matches as a robust fallback.
    3. Deduplicate and keep the best/top recent facts.
    """
    if not facts:
        return []
    if not query or not query.strip():
        return list(facts[-min(top_k, len(facts)):])

    bm25_results = []

    try:
        from utils.vector_store import get_memory_vector_store

        store = get_memory_vector_store()
        results = store.search(query, top_k=max(top_k * 2, 8))
        text_to_fact = {_fact_text(f): f for f in facts if _fact_text(f)}
        for _, text, score in results:
            fact = text_to_fact.get(text.strip())
            if fact:
                key = fact.get("id") or _fact_text(fact)
                bm25_results.append((key, _fact_text(fact), float(score)))
    except Exception as e:
        print(f"[Memory] BM25 search failed, using keyword fallback: {e}")

    dense_results = []
    try:
        from utils.vector_store import get_memory_vector_store

        store = get_memory_vector_store()
        results = store.search_dense(query, top_k=max(top_k * 2, 8))
        text_to_fact = {_fact_text(f): f for f in facts if _fact_text(f)}
        for _, text, score in results:
            fact = text_to_fact.get(text.strip())
            if fact:
                key = fact.get("id") or _fact_text(fact)
                dense_results.append((key, _fact_text(fact), float(score)))
    except Exception as e:
        print(f"[Memory] dense search failed, using sparse branches: {e}")

    keyword_results = []
    keywords = _tokenize_query(query)
    if keywords:
        for fact in facts:
            content = _fact_text(fact).lower()
            if not content:
                continue
            score = sum(1 for kw in keywords if kw in content)
            if score > 0:
                key = fact.get("id") or _fact_text(fact)
                keyword_results.append((key, _fact_text(fact), float(score)))

    if not bm25_results and not dense_results and not keyword_results:
        return list(facts[-min(3, len(facts)):])

    from utils.retrieval_ranker import rerank, rrf_fuse

    fused = rrf_fuse([bm25_results, dense_results, keyword_results])
    reranked = rerank(query, fused, top_k=top_k)
    fact_by_key = {fact.get("id") or _fact_text(fact): fact for fact in facts}
    return [fact_by_key[key] for key, _, _ in reranked if key in fact_by_key]


def build_memory_context(user_id: str, query: str = "") -> str:
    """Build source-labeled long-term memory context for the system prompt."""
    profile = load_profile(user_id)
    facts = profile.get("facts", []) or []
    if not facts:
        return ""

    relevant = search_facts(query, facts, top_k=5)
    if not relevant:
        return ""

    lines = ["\n\n## 长期记忆：当前用户相关事实"]
    for fact in relevant:
        fact_type = fact.get("type", "fact")
        content = _fact_text(fact)
        if content:
            lines.append(f"- [{fact_type}] {content}")

    return "\n".join(lines) if len(lines) > 1 else ""
