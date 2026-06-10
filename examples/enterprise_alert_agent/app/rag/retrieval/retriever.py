"""检索器 - 从向量库中检索相关文档。

升级后使用 ChromaDB 做真实的语义相似度检索，
如果向量库为空则回退到内存假数据（兼容开发阶段）。
"""

import math
import re
from typing import Any, Counter

from langsmith import RunTree
from app.observability.langsmith_tracer import LangSmithTracer
from app.infrastructure.vectorstore.chroma_store import ChromaStore
from app.config.settings import settings



class Retriever:
    """混合检索器，支持向量检索和基于规则的回退机制。"""

    def __init__(self, chroma_store: ChromaStore, tracer: LangSmithTracer) -> None:
        self._store = chroma_store
        self._tracer = tracer # 新增：保存 tracer 实例
        # 回退用的内存假数据（向量库为空时使用）

    def retrieve(self, query: str, top_k: int | None = None,*,
                 history_text: str = "",
                 where:dict[str, Any] | None = None,
                 parent_run: RunTree | None = None) -> list[dict[str, str]]:
        # 创建 retriever 层的子 run
        run = self._tracer.start_child(
            parent_run=parent_run,
            name="rag.retrieve",
            run_type="retriever",
            inputs={"query": query, "top_k": top_k},
            tags=["rag","retriever"],
        )
        try:
            if self._store.count() == 0:
                self._tracer.end_run(run, outputs={"hits": 0, "mode": "fallback"})
                return []
            # 查询重写（Query Rewriting）
            rewritten_query = self._rewrite_query(query, history_text)
            candidate_k = max(settings.retrieval_candidate_k, (top_k or settings.retrieval_final_k))
            candidates = self._store.query(
                              rewritten_query,
                              top_k=candidate_k,
                              where=where,
                              parent_run=run)  # 传递 parent_run 以便在向量库查询中记录路径
            if not candidates:
                self._tracer.end_run(run, outputs={"hits": 0, "mode": "empty"})
                return []

            # RAG 混合检索（Hybrid Search + 重排）
            reranked = self._hybird_rerank(rewritten_query, candidates)
            filtered = [doc for doc in reranked if doc["score"] >= settings.retrieval_min_score]
            final_k = top_k or settings.retrieval_final_k
            # 如果过滤后没有了，就用重排结果reranked的 top_k
            final_docs = filtered[:final_k] if filtered else reranked[:final_k]

            self._tracer.end_run(run, outputs={
                    "hits": len(final_docs),
                    "candidate_k": candidate_k,
                    "final_k": final_k,
                    "filtered_count": len(filtered),
                     })
            return final_docs
        except Exception as e:
            # 异常路径也要记录
            self._tracer.end_run(run, outputs={"error": str(e)}, error=LangSmithTracer.format_error(e))
            raise

    def _rewrite_query(self, query: str, history_text: str) -> str:
        if not settings.retrieval_query_rewrite or not history_text.strip():
            return query
        short_history = self._tail_turns(history_text, settings.retrieval_max_history_turns)
        return f"{query}\n\n最近上下文:\n{short_history}"

    #RAG 混合检索（Hybrid Search + 重排）
    def _hybird_rerank(self, query: str, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        query_token = self._tokenize(query)
        # # 稠密分占比
        alpha = float(settings.retrieval_hybrid_alpha)
        recoreds: list[dict[str, Any]] = []
        for doc in docs:
            dense = float(doc.get("score", 0.0))  # 确保有 score 字段
            lexical= self._lexical_score(query_token, self._tokenize(str(doc.get("content", ""))))
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
            turns.append(("User: " + p) if idx > 0 else (p if p.startswith("User: ") else "User: " + p))
        return "\n".join(turns[-max_turns:])

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return [t for t in re.split(r"\W+", text.lower()) if t]

    @staticmethod
    def _lexical_score(query_token:list[str], doc_token:list[str]) -> float:
        if not query_token or not doc_token:
            return 0.0
        q_count = Counter(query_token)
        d_count = Counter(doc_token)
        overlap = sum(min(q_count[t], d_count[t]) for t in q_count)
        norm = math.sqrt(sum(c**2 for c in q_count.values())) * math.sqrt(sum(c**2 for c in d_count.values()))
        return float(overlap / norm) if norm > 0 else 0.0
