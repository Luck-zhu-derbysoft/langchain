"""检索器 - 从向量库中检索相关文档。

升级后使用 ChromaDB 做真实的语义相似度检索，
如果向量库为空则回退到内存假数据（兼容开发阶段）。
"""

from langsmith import RunTree
from app.observability.langsmith_tracer import LangSmithTracer
from app.infrastructure.vectorstore.chroma_store import ChromaStore


class Retriever:
    """基于向量相似度的文档检索器。"""

    def __init__(self, chroma_store: ChromaStore, tracer: LangSmithTracer) -> None:
        self._store = chroma_store
        self._tracer = tracer # 新增：保存 tracer 实例
        # 回退用的内存假数据（向量库为空时使用）
        self._fallback_docs = [
            {
                "source_id": "kb-001",
                "content": "高优先级告警需要在15分钟内确认并进入人工复核流程。",
            },
            {
                "source_id": "kb-002",
                "content": "连续三次阈值超限可触发自动工单创建。",
            },
            {
                "source_id": "kb-003",
                "content": "夜间窗口期告警可先降噪聚合再通知值班人员。",
            },
        ]

    def retrieve(self, query: str, top_k: int = 3,*, parent_run: RunTree | None = None) -> list[dict[str, str]]:
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
                docs = self._fallback_docs[:top_k]
                self._tracer.end_run(run, outputs={"hits": len(docs), "mode": "fallback"})
                return docs

            # 调用下游时透传 parent_run
            docs = self._store.query(query, top_k=top_k, parent_run=run)
            # vectorstore 路径记录
            self._tracer.end_run(run, outputs={"hits": len(docs), "mode": "vectorstore"})
            return docs
        except Exception as e:
            # 异常路径也要记录
            self._tracer.end_run(run, outputs={"error": str(e)}, error=LangSmithTracer.format_error(e))
            raise

