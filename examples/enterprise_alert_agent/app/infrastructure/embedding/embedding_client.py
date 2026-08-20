"""Embedding 客户端 - 封装 DashScope text-embedding API。

通过 OpenAI 兼容接口调用 DashScope 的 text-embedding-v3 模型，
将文本转换为向量表示，供向量检索使用。
"""

from langsmith.run_trees import RunTree
from openai import OpenAI

from app.config.settings import settings
from app.observability.langsmith_tracer import LangSmithTracer


class EmbeddingClient:
    """调用 DashScope embedding 接口，返回文本的向量表示。"""

    def __init__(self, tracer: LangSmithTracer) -> None:
        self._client = OpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.base_url,
            timeout=settings.request_timeout_seconds,
        )
        self._tracer = tracer
        self._model = settings.embedding_model
        self._dimensions = settings.embedding_dimensions

    def embed_texts(self, texts: list[str], parent_run: RunTree | None = None) -> list[list[float]]:
        """批量获取文本的 embedding 向量。

        Args:
            texts: 需要向量化的文本列表。

        Returns:
            与输入等长的向量列表，每个向量维度为 embedding_dimensions。
        """
        run = self._tracer.start_child(
            parent_run=parent_run,
            name="embedding.embed_texts",
            run_type="tool",
            inputs={"texts_count": len(texts), "model": self._model},
            tags=["embedding", self._model],
        )
        try:
            if not texts:
                self._tracer.end_run(run, outputs={"vectors_count": 0})
                return []

            response = self._client.embeddings.create(
                model=self._model,
                input=texts,
                dimensions=self._dimensions,
            )
            # 按 index 排序确保顺序与输入一致
            sorted_data = sorted(response.data, key=lambda x: x.index)
            vectors = [item.embedding for item in sorted_data]
            # 新增：成功结束，记录输出统计
            self._tracer.end_run(
                run,
                outputs={"vectors_count": len(vectors), "dimensions": self._dimensions},
            )

            return vectors
        except Exception as e:
            # 记录异常信息
            self._tracer.end_run(run, error=LangSmithTracer.format_error(e))
            raise

    def embed_query(self, text: str, *, parent_run: RunTree | None = None) -> list[float]:
        """获取单条查询文本的 embedding 向量。"""
        return self.embed_texts([text], parent_run=parent_run)[0]
