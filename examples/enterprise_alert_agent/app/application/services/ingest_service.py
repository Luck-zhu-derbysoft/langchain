"""文档摄入服务 - 编排 切块 → 向量化 → 存储 的完整流程。"""

from app.infrastructure.vectorstore.chroma_store import ChromaStore
from app.rag.splitter.text_splitter import TextSplitter
from app.schemas.ingest import IngestResponse, IngestTextRequest


class IngestService:
    """将原始文档切块后写入向量库。"""

    def __init__(self, chroma_store: ChromaStore) -> None:
        self._store = chroma_store
        self._splitter = TextSplitter()

    def ingest_text(self, req: IngestTextRequest) -> IngestResponse:
        """处理纯文本摄入请求。

        流程：
        1. 文本切块
        2. 为每个块附加元数据
        3. 写入 ChromaDB
        """
        # 切块
        chunks = self._splitter.split(req.content)

        if not chunks:
            return IngestResponse(
                source_id=req.source_id,
                chunks_count=0,
                total_docs=self._store.count(),
                message="文本为空，未写入任何内容",
            )

        # 构建每个块的元数据
        metadatas = [
            {
                "source_id": req.source_id,
                "chunk_index": str(i),
                **req.metadata,
            }
            for i in range(len(chunks))
        ]

        # 写入向量库
        self._store.add_documents(texts=chunks, metadatas=metadatas)

        return IngestResponse(
            source_id=req.source_id,
            chunks_count=len(chunks),
            total_docs=self._store.count(),
            message=f"成功摄入 {len(chunks)} 个文本块",
        )
