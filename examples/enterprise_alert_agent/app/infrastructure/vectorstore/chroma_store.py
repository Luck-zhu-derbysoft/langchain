"""ChromaDB 向量存储封装。

提供文档写入和相似度检索两个核心能力：
- add_documents: 将文本块及其元数据写入向量库
- query: 根据查询向量返回最相似的文档
"""

import uuid
from typing import Any

import chromadb  # type: ignore[import-untyped]
from chromadb.config import Settings as ChromaSettings  # type: ignore[import-untyped]

from app.config.settings import settings
from app.infrastructure.embedding.embedding_client import EmbeddingClient


class ChromaStore:
    """基于 ChromaDB 的持久化向量存储。"""

    def __init__(self, embedding_client: EmbeddingClient) -> None:
        self._embedding_client = embedding_client

        # 初始化 ChromaDB 持久化客户端
        self._chroma_client = chromadb.PersistentClient(
            path=settings.chroma_persist_directory,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        # 获取或创建 collection（不使用内置 embedding function，由我们自己管理向量）
        self._collection = self._chroma_client.get_or_create_collection(
            name=settings.chroma_collection_name,
            metadata={"hnsw:space": "cosine"},  # 使用余弦相似度
        )

    def add_documents(
        self,
        texts: list[str],
        metadatas: list[dict[str, Any]],
        ids: list[str] | None = None,
    ) -> list[str]:
        """将文本块写入向量库。

        Args:
            texts: 文本内容列表。
            metadatas: 每条文本对应的元数据（如 source_id, filename）。
            ids: 可选文档 ID，不传则自动生成 UUID。

        Returns:
            写入的文档 ID 列表。
        """
        if not texts:
            return []

        # 生成 embedding 向量
        embeddings = self._embedding_client.embed_texts(texts)

        # 自动生成 ID
        if ids is None:
            ids = [str(uuid.uuid4()) for _ in texts]

        # 写入 ChromaDB
        self._collection.add(
            ids=ids,
            embeddings=embeddings,  # type: ignore[arg-type]
            documents=texts,
            metadatas=metadatas,  # type: ignore[arg-type]
        )
        return ids

    def query(self, query_text: str, top_k: int = 3) -> list[dict[str, str]]:
        """根据查询文本检索最相似的文档。

        Args:
            query_text: 用户的查询文本。
            top_k: 返回的最大文档数。

        Returns:
            包含 source_id, content, score 的字典列表，按相似度降序。
        """
        query_embedding = self._embedding_client.embed_query(query_text)

        results = self._collection.query(
            query_embeddings=[query_embedding],  # type: ignore[arg-type]
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        docs: list[dict[str, str]] = []
        if results["documents"] and results["metadatas"] and results["distances"]:
            for doc, meta, distance in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                source_id = str(meta.get("source_id", "unknown")) if meta else "unknown"
                docs.append({  # type: ignore[dict-item]
                    "source_id": source_id,
                    "content": doc or "",
                    "score": f"{1 - distance:.4f}",
                })
        return docs

    def count(self) -> int:
        """返回当前 collection 中的文档总数。"""
        return self._collection.count()
