"""Embedding 客户端 - 封装 DashScope text-embedding API。

通过 OpenAI 兼容接口调用 DashScope 的 text-embedding-v3 模型，
将文本转换为向量表示，供向量检索使用。
"""

from openai import OpenAI

from app.config.settings import settings


class EmbeddingClient:
    """调用 DashScope embedding 接口，返回文本的向量表示。"""

    def __init__(self) -> None:
        self._client = OpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.base_url,
            timeout=settings.request_timeout_seconds,
        )
        self._model = settings.embedding_model
        self._dimensions = settings.embedding_dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量获取文本的 embedding 向量。

        Args:
            texts: 需要向量化的文本列表。

        Returns:
            与输入等长的向量列表，每个向量维度为 embedding_dimensions。
        """
        if not texts:
            return []

        response = self._client.embeddings.create(
            model=self._model,
            input=texts,
            dimensions=self._dimensions,
        )
        # 按 index 排序确保顺序与输入一致
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]

    def embed_query(self, text: str) -> list[float]:
        """获取单条查询文本的 embedding 向量。"""
        return self.embed_texts([text])[0]
