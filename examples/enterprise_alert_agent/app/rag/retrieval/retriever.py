"""检索器 - 从向量库中检索相关文档。

升级后使用 ChromaDB 做真实的语义相似度检索，
如果向量库为空则回退到内存假数据（兼容开发阶段）。
"""

import json
import logging
import math
import re
from collections import Counter
from copy import deepcopy
from threading import RLock
from typing import Any

from langsmith import RunTree

from app.config.settings import settings
from app.infrastructure.vectorstore.chroma_store import ChromaStore
from app.observability.langsmith_tracer import LangSmithTracer

logger = logging.getLogger(__name__)


class Retriever:
    """混合检索器，支持向量检索和基于规则的回退机制。"""

    def __init__(self, chroma_store: ChromaStore, tracer: LangSmithTracer) -> None:
        self._store = chroma_store
        self._tracer = tracer  # 新增：保存 tracer 实例
        self._query_cache: dict[str, list[dict]] = {}  # 简单的查询缓存，避免重复查询同一问题
        self._cache_max_size = 100
        self._cache_lock = RLock()

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        *,
        history_text: str = "",
        where: dict[str, Any] | None = None,
        parent_run: RunTree | None = None,
    ) -> list[dict[str, str]]:
        # 创建 retriever 层的子 run
        run = self._tracer.start_child(
            parent_run=parent_run,
            name="rag.retrieve",
            run_type="retriever",
            inputs={"query": query, "top_k": top_k},
            tags=["rag", "retriever"],
        )
        where_key = json.dumps(where, sort_keys=True, ensure_ascii=False, default=str)
        cache_key = f"{query}|{history_text}|{top_k}|{where_key}"
        with self._cache_lock:
            cached_docs = self._query_cache.get(cache_key)
            if cached_docs is not None:
                cached_docs = deepcopy(cached_docs)
        if cached_docs is not None:
            self._tracer.end_run(run, outputs={"hits": len(cached_docs), "mode": "cache"})
            logger.debug("Cache hit for query: %s", query[:50])
            return cached_docs
        try:
            if self._store.count() == 0:
                self._tracer.end_run(run, outputs={"hits": 0, "mode": "fallback"})
                return []
            # 查询重写（Query Rewriting）
            rewritten_query = self._rewrite_query(query, history_text)
            candidate_k = max(settings.retrieval_candidate_k, (top_k or settings.retrieval_final_k))
            candidates = self._store.query(
                rewritten_query, top_k=candidate_k, where=where, parent_run=run
            )  # 传递 parent_run 以便在向量库查询中记录路径
            if not candidates:
                self._tracer.end_run(run, outputs={"hits": 0, "mode": "empty"})
                return []

            # RAG 混合检索（Hybrid Search + 重排）
            reranked = self._hybird_rerank(rewritten_query, candidates)
            filtered = [doc for doc in reranked if doc["score"] >= settings.retrieval_min_score]
            final_k = top_k or settings.retrieval_final_k
            # 如果过滤后没有了，就用重排结果reranked的 top_k
            final_docs = filtered[:final_k] if filtered else reranked[:final_k]

            self._tracer.end_run(
                run,
                outputs={
                    "hits": len(final_docs),
                    "candidate_k": candidate_k,
                    "final_k": final_k,
                    "filtered_count": len(filtered),
                },
            )
            with self._cache_lock:
                if len(self._query_cache) >= self._cache_max_size:
                    oldest_cache_key = next(iter(self._query_cache))
                    self._query_cache.pop(oldest_cache_key)
                self._query_cache[cache_key] = deepcopy(final_docs)
            logger.debug("Cache updated for query: %s", query[:50])
            return deepcopy(final_docs)
        except Exception as e:
            # 异常路径也要记录
            self._tracer.end_run(
                run, outputs={"error": str(e)}, error=LangSmithTracer.format_error(e)
            )
            raise

    def _rewrite_query(self, query: str, history_text: str) -> str:
        if not settings.retrieval_query_rewrite or not history_text.strip():
            return query
        short_history = self._tail_turns(history_text, settings.retrieval_max_history_turns)
        return f"{query}\n\n最近上下文:\n{short_history}"

    # RAG 混合检索（Hybrid Search + 重排）
    def _hybird_rerank(self, query: str, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        query_token = self._tokenize(query)
        # # 稠密分占比
        alpha = float(settings.retrieval_hybrid_alpha)
        recoreds: list[dict[str, Any]] = []
        for doc in docs:
            dense = float(doc.get("score", 0.0))  # 确保有 score 字段
            lexical = self._lexical_score(query_token, self._tokenize(str(doc.get("content", ""))))
            hybrid = alpha * dense + (1 - alpha) * lexical
            item = dict(doc)
            item["dense_score"] = dense
            item["lexical_score"] = lexical
            item["score"] = hybrid
            recoreds.append(item)
        return sorted(recoreds, key=lambda d: d["score"], reverse=True)

    @staticmethod
    def _tail_turns(history_text: str, max_turns: int) -> str:
        parts = history_text.split("\nUser: ")
        turns = []
        for idx, p in enumerate(parts):
            p = p.strip()
            if not p:
                continue
            turns.append(
                ("User: " + p) if idx > 0 else (p if p.startswith("User: ") else "User: " + p)
            )
        return "\n".join(turns[-max_turns:])

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return [t for t in re.split(r"\W+", text.lower()) if t]

    @staticmethod
    def _lexical_score(query_token: list[str], doc_token: list[str]) -> float:
        if not query_token or not doc_token:
            return 0.0
        q_count = Counter(query_token)
        d_count = Counter(doc_token)
        overlap = sum(min(q_count[t], d_count[t]) for t in q_count)
        norm = math.sqrt(sum(c**2 for c in q_count.values())) * math.sqrt(
            sum(c**2 for c in d_count.values())
        )
        return float(overlap / norm) if norm > 0 else 0.0
