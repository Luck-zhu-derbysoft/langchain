"""文档摄入服务 - 编排 切块 → 向量化 → 存储 的完整流程。"""

import logging

from langsmith.run_trees import RunTree

from app.infrastructure.vectorstore.chroma_store import ChromaStore
from app.observability.langsmith_tracer import LangSmithTracer
from app.rag.splitter.text_splitter import TextSplitter
from app.schemas.ingest import IngestResponse, IngestTextRequest

logger = logging.getLogger(__name__)


class IngestService:
    """将原始文档切块后写入向量库。"""

    def __init__(self, chroma_store: ChromaStore, trace: LangSmithTracer) -> None:
        self._store = chroma_store
        self._trace = trace
        self._splitter = TextSplitter(tracer=trace)

    def ingest_text(
        self, req: IngestTextRequest, *, parent_run: RunTree | None = None
    ) -> IngestResponse:
        """处理纯文本摄入请求。

        流程：
        1. 文本切块
        2. 为每个块附加元数据
        3. 写入 ChromaDB
        """
        ingest_run = self._trace.start_child(
            parent_run=parent_run,
            name="service.ingest_text",
            run_type="chain",
            inputs={"source_id": req.source_id, "content_length": len(req.content)},
            tags=["service", "ingest"],
        )
        try:
            logger.info(
                "Ingest start: source_id=%s content_length=%d",
                req.source_id,
                len(req.content),
            )
            # 切块
            chunks = self._splitter.split(req.content, parent_run=ingest_run)
            logger.info("Ingest split done: source_id=%s chunks=%d", req.source_id, len(chunks))

            if not chunks:
                logger.warning("Ingest aborted: empty content for source_id=%s", req.source_id)
                resp = IngestResponse(
                    source_id=req.source_id,
                    chunks_count=0,
                    total_docs=self._store.count(),
                    message="文本为空，未写入任何内容",
                )
                self._trace.end_run(ingest_run, outputs=resp.model_dump())
                return resp

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
            self._store.add_documents(texts=chunks, metadatas=metadatas, parent_run=ingest_run)

            resp = IngestResponse(
                source_id=req.source_id,
                chunks_count=len(chunks),
                total_docs=self._store.count(),
                message=f"成功摄入 {len(chunks)} 个文本块",
            )
            logger.info(
                "Ingest done: source_id=%s chunks=%d total_docs=%d",
                req.source_id,
                resp.chunks_count,
                resp.total_docs,
            )
            self._trace.end_run(ingest_run, outputs=resp.model_dump())
            return resp
        except Exception as exc:
            logger.exception("Ingest failed: source_id=%s err=%s", req.source_id, exc)
            self._trace.end_run(ingest_run, error=LangSmithTracer.format_error(exc))
            raise
