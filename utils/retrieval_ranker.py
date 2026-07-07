# -*- coding: utf-8 -*-
"""Lightweight retrieval fusion and reranking helpers."""
import math
import re
from typing import Iterable, List, Sequence, Tuple

SearchResult = Tuple[str, str, float]

_STOP_WORDS = {
    "的", "了", "呢", "吗", "啊", "是", "我", "你", "他", "她", "它", "我们", "你们",
    "这个", "那个", "什么", "怎么", "为什么", "可以", "不能", "有没有", "一下",
}


def tokenize(text: str) -> List[str]:
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


def lexical_score(query: str, text: str) -> float:
    query_terms = tokenize(query)
    if not query_terms or not text:
        return 0.0

    text_lower = text.lower()
    hits = sum(1 for term in query_terms if term in text_lower)
    coverage = hits / max(len(query_terms), 1)
    density = hits / max(math.log(len(text_lower) + 10), 1.0)
    return coverage + density


def rrf_fuse(rankings: Sequence[Sequence[SearchResult]], k: int = 60) -> List[SearchResult]:
    """
    Reciprocal Rank Fusion over multiple retrieval result lists.

    Each input list should already be ranked from best to worst. Returned score
    is the fused RRF score, not the original retriever score.
    """
    fused = {}
    for results in rankings:
        for rank, (item_id, text, _score) in enumerate(results, start=1):
            key = item_id or text
            if key not in fused:
                fused[key] = {"id": item_id, "text": text, "score": 0.0}
            fused[key]["score"] += 1.0 / (k + rank)

    ordered = sorted(fused.values(), key=lambda item: item["score"], reverse=True)
    return [(item["id"], item["text"], float(item["score"])) for item in ordered]


def rerank(query: str, candidates: Iterable[SearchResult], top_k: int = 5) -> List[SearchResult]:
    """
    Lightweight final reranker.

    It combines the fused retrieval score with lexical coverage. This is much
    cheaper than a cross-encoder and works offline for desktop use.
    """
    ranked = []
    seen = set()
    for item_id, text, retrieval_score in candidates:
        key = item_id or text
        if key in seen:
            continue
        seen.add(key)
        final_score = float(retrieval_score) + 0.35 * lexical_score(query, text)
        ranked.append((item_id, text, final_score))

    ranked.sort(key=lambda item: item[2], reverse=True)
    return ranked[:top_k]
