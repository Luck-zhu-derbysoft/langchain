"""文档摄入 API 端点。

支持：
1. /ingest/text 纯文本摄入
2. /ingest/file 文件上传摄入
3. /ingest/stats 查看向量库统计
"""

from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, UploadFile
from pypdf import PdfReader

from app.application.services.ingest_service import IngestService
from app.infrastructure.embedding.embedding_client import EmbeddingClient
from app.infrastructure.vectorstore.chroma_store import ChromaStore
from app.schemas.ingest import IngestResponse, IngestTextRequest

router = APIRouter(prefix="/ingest", tags=["ingest"])

# 初始化依赖链：EmbeddingClient → ChromaStore → IngestService
_embedding_client = EmbeddingClient()
_chroma_store = ChromaStore(embedding_client=_embedding_client)
_ingest_service = IngestService(chroma_store=_chroma_store)


_ALLOWED_TEXT_SUFFIXES = {".txt", ".md", ".json", ".csv", ".log"}


def _parse_upload_to_text(file_name: str, content_bytes: bytes) -> str:
    """根据上传文件的类型解析成文本字符串。目前仅支持纯文本文件。"""
    suffix = Path(file_name).suffix.lower()
    # PDF、Word、Excel 等复杂格式的文件解析需要引入额外库，且可能存在安全风险，这里先限制为纯文本。
    if suffix == ".pdf":
        reader = PdfReader(BytesIO(content_bytes))  # noqa: F821
        pages = [(page.extract_text() or "") for page in reader.pages]
        text = "\n".join(pages).strip()
        if not text:
            raise HTTPException(status_code=400, detail="PDF 文件解析失败，未提取到文本内容。")
        return text
    # 其他纯文本格式直接解码
    elif suffix in _ALLOWED_TEXT_SUFFIXES:
        try:
            return content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return content_bytes.decode("gbk")

    raise HTTPException(status_code=400, detail="不支持的文件类型，仅支持 txt/md/json/csv/log/pdf")


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


@router.post("/file")
async def ingest_file(
    file: UploadFile,
    source_id: str = Form(...),
    category: str = Form("文件导入"),
) -> IngestResponse:
    """接收上传文件并摄入向量库。"""
    file_name = file.filename or "uploaded_file"
    content_bytes = await file.read()
    if not content_bytes:
        raise HTTPException(status_code=400, detail="上传文件内容为空")
    content_str = _parse_upload_to_text(file_name, content_bytes)
    resolved_source_id = source_id.strip() or Path(file_name).stem
    ingest_req = IngestTextRequest(
        content=content_str,
        source_id=resolved_source_id,
        metadata={
            "category": category,
            "filename": file_name,
            "suffix": Path(file_name).suffix,
        },
    )
    return _ingest_service.ingest_text(ingest_req)
