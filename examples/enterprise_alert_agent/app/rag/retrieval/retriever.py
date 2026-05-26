"""检索器 - 从向量库中检索相关文档。

升级后使用 ChromaDB 做真实的语义相似度检索，
如果向量库为空则回退到内存假数据（兼容开发阶段）。
"""

from app.infrastructure.vectorstore.chroma_store import ChromaStore


class Retriever:
    """基于向量相似度的文档检索器。"""

    def __init__(self, chroma_store: ChromaStore) -> None:
        self._store = chroma_store

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

    def retrieve(self, query: str, top_k: int = 3) -> list[dict[str, str]]:
        """检索与查询最相关的文档。

        如果向量库有数据则走向量检索，否则回退到假数据。
        """
        if self._store.count() == 0:
            # 向量库为空，回退假数据
            return self._fallback_docs[:top_k]

        return self._store.query(query_text=query, top_k=top_k)
