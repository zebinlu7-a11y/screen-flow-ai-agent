"""
FAISS 向量存储 — 语义检索对话历史和用户记忆。

混合本地 + 云端 embedding：
  优先用火山引擎 embedding API（快、无需本地模型）
  失败时回退到本地 sentence-transformers 模型
"""
import os
import json
import numpy as np
from typing import List, Optional, Tuple

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "airag_data")


class VectorStore:
    """
    FAISS 向量存储封装。

    用法:
        store = VectorStore("memories")
        store.add(texts=["用户喜欢Python", "正在开发截图工具"], ids=["m1","m2"])
        results = store.search("Python开发", top_k=3)
    """

    def __init__(self, collection_name: str):
        self._name = collection_name
        self._dir = os.path.join(DATA_DIR, "vectors")
        os.makedirs(self._dir, exist_ok=True)
        self._index_path = os.path.join(self._dir, f"{collection_name}.index")
        self._meta_path = os.path.join(self._dir, f"{collection_name}.json")

        self._index = None           # faiss.IndexFlatIP
        self._embedder = None        # SentenceTransformer
        self._texts: List[str] = []  # id → text 映射
        self._ids: List[str] = []
        self._dim = 512              # 默认维度

        self._load()

    # ============================================================
    # Embedding
    # ============================================================

    def _get_embedder(self):
        """懒加载 TF-IDF 向量化器（轻量，无 DLL 依赖）。"""
        if self._embedder is not None:
            return self._embedder
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            import jieba

            def _tokenizer(text):
                return jieba.lcut(text)

            self._embedder = TfidfVectorizer(
                tokenizer=_tokenizer,
                max_features=512,
                norm='l2',
            )
            print(f"[VectorStore] TF-IDF 向量化器就绪 (max_features=512)")
        except Exception as e:
            print(f"[VectorStore] TF-IDF 加载失败: {e}")
            raise
        return self._embedder

    def _embed(self, texts: List[str]) -> np.ndarray:
        """将文本转为 TF-IDF 向量。"""
        if not texts:
            return np.array([]).reshape(0, 0)

        vec = self._get_embedder()
        try:
            # fit_transform on first call, transform on subsequent
            if not hasattr(vec, 'vocabulary_') or len(vec.vocabulary_) == 0:
                vectors = vec.fit_transform(texts)
            else:
                vectors = vec.transform(texts)
        except Exception:
            # Re-fit if transform fails (vocabulary mismatch)
            vectors = vec.fit_transform(texts)

        # Convert sparse to dense
        dense = vectors.toarray().astype("float32")

        # Normalize for cosine similarity
        norms = np.linalg.norm(dense, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        dense = dense / norms

        return dense

    # ============================================================
    # FAISS CRUD
    # ============================================================

    def add(self, texts: List[str], ids: Optional[List[str]] = None):
        """添加文本到向量库。"""
        if not texts:
            return
        if ids is None:
            ids = [str(len(self._ids) + i) for i in range(len(texts))]

        vectors = self._embed(texts)

        import faiss
        if self._index is None:
            self._dim = vectors.shape[1]
            self._index = faiss.IndexFlatIP(self._dim)  # 内积相似度

        self._index.add(vectors)
        self._texts.extend(texts)
        self._ids.extend(ids)
        self._save()

    def search(self, query: str, top_k: int = 5) -> List[Tuple[str, str, float]]:
        """
        搜索最相似的文本。
        返回: [(id, text, score), ...] 按相似度降序
        """
        if self._index is None or self._index.ntotal == 0:
            return []

        vec = self._embed([query])
        if vec.shape[0] == 0:
            return []

        scores, indices = self._index.search(vec, min(top_k, self._index.ntotal))

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx >= 0 and idx < len(self._texts) and score > 0.3:  # 最低相似度阈值
                results.append((self._ids[idx], self._texts[idx], float(score)))
        return results

    def remove(self, id_to_remove: str):
        """删除指定 ID（重建索引，FAISS 不支持直接删）。"""
        if id_to_remove not in self._ids:
            return
        idx = self._ids.index(id_to_remove)
        self._texts.pop(idx)
        self._ids.pop(idx)
        self._rebuild_index()

    def clear(self):
        """清空所有数据。"""
        self._texts = []
        self._ids = []
        self._index = None
        self._save()

    def _rebuild_index(self):
        """重建 FAISS 索引。"""
        import faiss
        if not self._texts:
            self._index = None
            self._save()
            return
        vectors = self._embed(self._texts)
        self._index = faiss.IndexFlatIP(self._dim)
        self._index.add(vectors)
        self._save()

    @property
    def count(self) -> int:
        return len(self._texts)

    # ============================================================
    # 持久化
    # ============================================================

    def _save(self):
        """保存索引和元数据到磁盘。"""
        import faiss
        # 保存 FAISS 索引
        if self._index is not None and self._index.ntotal > 0:
            faiss.write_index(self._index, self._index_path)
        elif os.path.exists(self._index_path):
            os.remove(self._index_path)

        # 保存元数据
        meta = {"ids": self._ids, "texts": self._texts, "dim": self._dim}
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    def _load(self):
        """从磁盘加载。"""
        import faiss
        if os.path.exists(self._index_path) and os.path.exists(self._meta_path):
            try:
                self._index = faiss.read_index(self._index_path)
                with open(self._meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                self._ids = meta.get("ids", [])
                self._texts = meta.get("texts", [])
                self._dim = meta.get("dim", 512)
                print(f"[VectorStore:{self._name}] 已加载 {len(self._texts)} 条向量")
            except Exception as e:
                print(f"[VectorStore:{self._name}] 加载失败: {e}")
                self._index = None
                self._texts = []
                self._ids = []


# ============================================================
# 全局实例
# ============================================================

_memory_store: Optional[VectorStore] = None
_conversation_store: Optional[VectorStore] = None


def get_memory_vector_store() -> VectorStore:
    """用户长期记忆向量库。"""
    global _memory_store
    if _memory_store is None:
        _memory_store = VectorStore("memory_facts")
    return _memory_store


def get_conversation_vector_store() -> VectorStore:
    """对话历史向量库。"""
    global _conversation_store
    if _conversation_store is None:
        _conversation_store = VectorStore("conversations")
    return _conversation_store
