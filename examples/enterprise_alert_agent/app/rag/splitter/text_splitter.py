"""文本切块工具。

将长文档按字符数切分为小块，保留重叠部分确保语义连续性。
轻量实现，适合中文场景，无外部依赖。
"""

from langsmith.run_trees import RunTree

from app.config.settings import settings
from app.observability.langsmith_tracer import LangSmithTracer


class TextSplitter:
    """按字符数切块，支持重叠。"""

    def __init__(
        self,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        tracer: LangSmithTracer | None = None,
    ) -> None:
        self._chunk_size = chunk_size or settings.chunk_size
        self._chunk_overlap = chunk_overlap or settings.chunk_overlap
        self._tracer = tracer

    def split(self, text: str, *, parent_run: RunTree | None = None) -> list[str]:
        """将文本切分为多个块。

        逻辑：先按段落分割 → 合并到不超过 chunk_size → 超长段落强制切分。
        """
        split_run = None
        if self._tracer is not None:
            split_run = self._tracer.start_child(
                parent_run=parent_run,
                name="rag.text_split",
                run_type="tool",
                inputs={"input_length": len(text)},
                tags=["rag", "splitter"],
            )

        try:
            if not text.strip():
                if self._tracer is not None:
                    self._tracer.end_run(split_run, outputs={"chunks": 0})
                return []

            # 按双换行分段
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
            # 如果无段落分隔则按单换行
            if len(paragraphs) == 1:
                paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

            chunks: list[str] = []
            current_chunk = ""

            for para in paragraphs:
                # 超长段落强制切分
                if len(para) > self._chunk_size:
                    if current_chunk:
                        chunks.append(current_chunk)
                        current_chunk = ""
                    chunks.extend(self._force_split(para))
                    continue

                candidate = (current_chunk + "\n" + para) if current_chunk else para

                if len(candidate) <= self._chunk_size:
                    current_chunk = candidate
                else:
                    chunks.append(current_chunk)
                    current_chunk = para

            if current_chunk:
                chunks.append(current_chunk)

            # 添加重叠
            if self._chunk_overlap > 0 and len(chunks) > 1:
                chunks = self._add_overlap(chunks)

            if self._tracer is not None:
                self._tracer.end_run(
                    split_run,
                    outputs={
                        "chunks": len(chunks),
                        "chunk_size": self._chunk_size,
                        "chunk_overlap": self._chunk_overlap,
                    },
                )
            return chunks
        except Exception as exc:
            if self._tracer is not None:
                self._tracer.end_run(split_run, error=LangSmithTracer.format_error(exc))
            raise

    def _force_split(self, text: str) -> list[str]:
        """强制按字符数切分超长文本。"""
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + self._chunk_size
            chunks.append(text[start:end])
            start = end - self._chunk_overlap
        return chunks

    def _add_overlap(self, chunks: list[str]) -> list[str]:
        """为相邻块添加重叠内容。"""
        result = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-self._chunk_overlap :]
            result.append(prev_tail + "\n" + chunks[i])
        return result
