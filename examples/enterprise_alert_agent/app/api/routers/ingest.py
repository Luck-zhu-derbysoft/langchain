"""文档摄入 API 端点。

提供 /ingest/text 接口用于接收文本并存入向量库。
"""

from fastapi import APIRouter

from app.application.services.ingest_service import IngestService
from app.infrastructure.embedding.embedding_client import EmbeddingClient
from app.infrastructure.vectorstore.chroma_store import ChromaStore
from app.schemas.ingest import IngestResponse, IngestTextRequest

router = APIRouter(prefix="/ingest", tags=["ingest"])

# 初始化依赖链：EmbeddingClient → ChromaStore → IngestService
_embedding_client = EmbeddingClient()
_chroma_store = ChromaStore(embedding_client=_embedding_client)
_ingest_service = IngestService(chroma_store=_chroma_store)


@router.post("/text", response_model=IngestResponse)
def ingest_text(req: IngestTextRequest) -> IngestResponse:
    """接收纯文本并摄入向量库。

    请求示例:
    {
        "content": "高优先级告警需要在15分钟内确认...",
        "source_id": "alert-rule-001",
        "metadata": {"category": "告警规则"}
    }
    """
    return _ingest_service.ingest_text(req)


@router.get("/stats")
def ingest_stats() -> dict[str, int]:
    """查看当前向量库中的文档总数。"""
    return {"total_documents": _chroma_store.count()}
