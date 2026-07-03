"""
Lightweight BM25 text store for conversation and memory retrieval.

The public API intentionally stays compatible with the old VectorStore wrapper:
`add(texts, ids)`, `search(query, top_k)`, `clear()`, and `count`.
"""
import json
import math
import os
from collections import Counter
from typing import List, Optional, Tuple

from utils.retrieval_ranker import tokenize

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "airag_data")


class VectorStore:
    """
    BM25-backed local text store.

    The class name is kept to avoid touching callers. It no longer builds
    TF-IDF vectors or FAISS indexes; it stores text metadata and ranks with BM25.
    """

    def __init__(self, collection_name: str):
        self._name = collection_name
        self._dir = os.path.join(DATA_DIR, "vectors")
        os.makedirs(self._dir, exist_ok=True)
        self._meta_path = os.path.join(self._dir, f"{collection_name}.json")

        self._texts: List[str] = []
        self._ids: List[str] = []
        self._tokenized: List[List[str]] = []
        self._doc_freq: Counter = Counter()
        self._avg_doc_len = 0.0
        self._embedding_vectors: List[List[float]] = []
        self._embedding_ready = False
        self._embedding_failed = False

        self._load()
        self._rebuild_index()

    def add(self, texts: List[str], ids: Optional[List[str]] = None):
        if not texts:
            return
        if ids is None:
            ids = [str(len(self._ids) + i) for i in range(len(texts))]

        existing_ids = set(self._ids)
        deduped = [(text, id_) for text, id_ in zip(texts, ids) if id_ not in existing_ids]
        if not deduped:
            return

        self._texts.extend(text for text, _ in deduped)
        self._ids.extend(id_ for _, id_ in deduped)
        self._embedding_ready = False
        self._rebuild_index()
        self._save()

    def search(self, query: str, top_k: int = 5) -> List[Tuple[str, str, float]]:
        if not query or not self._texts:
            return []

        query_terms = tokenize(query)
        if not query_terms:
            return []

        scored = []
        for idx, doc_terms in enumerate(self._tokenized):
            score = self._bm25_score(query_terms, doc_terms)
            if score > 0:
                scored.append((self._ids[idx], self._texts[idx], score))

        scored.sort(key=lambda item: item[2], reverse=True)
        return scored[:top_k]

    def search_dense(self, query: str, top_k: int = 5) -> List[Tuple[str, str, float]]:
        """Optional dense embedding retrieval branch."""
        if not query or not self._texts:
            return []
        if not self._ensure_embeddings():
            return []

        query_vectors = _embed_texts([query])
        if not query_vectors:
            return []

        query_vector = _normalize(query_vectors[0])
        scored = []
        for idx, vector in enumerate(self._embedding_vectors):
            score = _dot(query_vector, vector)
            if score > 0:
                scored.append((self._ids[idx], self._texts[idx], score))

        scored.sort(key=lambda item: item[2], reverse=True)
        return scored[:top_k]

    def remove(self, id_to_remove: str):
        if id_to_remove not in self._ids:
            return
        idx = self._ids.index(id_to_remove)
        self._texts.pop(idx)
        self._ids.pop(idx)
        self._embedding_ready = False
        self._rebuild_index()
        self._save()

    def clear(self):
        self._texts = []
        self._ids = []
        self._embedding_vectors = []
        self._embedding_ready = False
        self._rebuild_index()
        self._save()

    @property
    def count(self) -> int:
        return len(self._texts)

    def _bm25_score(self, query_terms: List[str], doc_terms: List[str]) -> float:
        if not doc_terms:
            return 0.0

        k1 = 1.5
        b = 0.75
        doc_len = len(doc_terms)
        term_counts = Counter(doc_terms)
        total_docs = len(self._tokenized)
        score = 0.0

        for term in query_terms:
            tf = term_counts.get(term, 0)
            if tf <= 0:
                continue
            df = self._doc_freq.get(term, 0)
            idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
            denom = tf + k1 * (1 - b + b * doc_len / max(self._avg_doc_len, 1.0))
            score += idf * (tf * (k1 + 1)) / max(denom, 1e-9)

        return float(score)

    def _rebuild_index(self):
        self._tokenized = [tokenize(text) for text in self._texts]
        self._doc_freq = Counter()
        total_len = 0
        for terms in self._tokenized:
            total_len += len(terms)
            self._doc_freq.update(set(terms))
        self._avg_doc_len = total_len / len(self._tokenized) if self._tokenized else 0.0

    def _save(self):
        meta = {
            "backend": "bm25+dense_optional",
            "ids": self._ids,
            "texts": self._texts,
        }
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    def _load(self):
        if not os.path.exists(self._meta_path):
            return

        try:
            with open(self._meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            self._ids = meta.get("ids", [])
            self._texts = meta.get("texts", [])
            print(f"[VectorStore:{self._name}] loaded {len(self._texts)} BM25 docs")
        except Exception as e:
            print(f"[VectorStore:{self._name}] load failed: {e}")
            self._ids = []
            self._texts = []

    def _ensure_embeddings(self) -> bool:
        if self._embedding_ready:
            return True
        if self._embedding_failed or not self._texts:
            return False

        vectors = _embed_texts(self._texts)
        if not vectors or len(vectors) != len(self._texts):
            self._embedding_failed = True
            return False

        self._embedding_vectors = [_normalize(vector) for vector in vectors]
        self._embedding_ready = True
        return True


_memory_store: Optional[VectorStore] = None
_conversation_store: Optional[VectorStore] = None


def get_memory_vector_store() -> VectorStore:
    """Long-term memory BM25 store."""
    global _memory_store
    if _memory_store is None:
        _memory_store = VectorStore("memory_facts")
    return _memory_store


def get_conversation_vector_store() -> VectorStore:
    """Conversation history BM25 store."""
    global _conversation_store
    if _conversation_store is None:
        _conversation_store = VectorStore("conversations")
    return _conversation_store


def _embed_texts(texts: List[str]) -> List[List[float]]:
    try:
        from openai import OpenAI
        from config import ARK_API_KEY, ARK_BASE_URL, EMBEDDING_ENABLED, EMBEDDING_MODEL_NAME

        if not EMBEDDING_ENABLED or not EMBEDDING_MODEL_NAME or not ARK_API_KEY:
            return []

        client = OpenAI(base_url=ARK_BASE_URL, api_key=ARK_API_KEY)
        response = client.embeddings.create(model=EMBEDDING_MODEL_NAME, input=texts)
        return [item.embedding for item in response.data]
    except Exception as exc:
        print(f"[VectorStore] dense embedding unavailable: {exc}")
        return []


def _normalize(vector: List[float]) -> List[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm <= 0:
        return vector
    return [v / norm for v in vector]


def _dot(a: List[float], b: List[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b)))
