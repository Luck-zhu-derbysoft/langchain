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
from langsmith.run_trees import RunTree
from app.observability.langsmith_tracer import LangSmithTracer  # type: ignore[import-untyped]



class ChromaStore:
    """基于 ChromaDB 的持久化向量存储。"""

    def __init__(self, embedding_client: EmbeddingClient,tracer:LangSmithTracer) -> None:
        self._embedding_client = embedding_client
        self._tracer = tracer

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
        #新增parent_run 参数和追踪逻辑
    def add_documents(
        self,
        texts: list[str],
        metadatas: list[dict[str, Any]],
        ids: list[str] | None = None,
        *,
        parent_run: RunTree | None = None,
    ) -> list[str]:
        #创建 vectorstore.add_documents 子 run
        run = self._tracer.start_child(
            parent_run=parent_run,
            name="vectorstore.add_documents",
            run_type="tool",
            inputs={"texts_count": len(texts)},
            tags=["vectorstore", "chroma", "write"],
        )
        try:
            if not texts:
                self._tracer.end_run(run, outputs={"inserted": 0})
                return []

                # 生成 embedding 向量
            embeddings = self._embedding_client.embed_texts(texts,parent_run=run)

            if ids is None:
                ids = [str(uuid.uuid4()) for _ in texts]

            self._collection.add(
                ids=ids,
                embeddings=embeddings,  # type: ignore[arg-type]
                documents=texts,
                metadatas=metadatas,  # type: ignore[arg-type]
            )
            self._tracer.end_run(run, outputs={"inserted": len(ids)})
            return ids
        except Exception as e:
            # 新增：异常路径记录
            self._tracer.end_run(run, error=LangSmithTracer.format_error(e))
            raise

    def query(self,
              query_text: str,
              top_k: int = 3,
              *,
                parent_run: RunTree | None = None
              ) -> list[dict[str, str]]:
                #创建 vectorstore.query 子 run
                run = self._tracer.start_child(
                    parent_run=parent_run,
                    name="vectorstore.query",
                    run_type="retriever",
                    inputs={"query_text": query_text, "top_k": top_k},
                    tags=["vectorstore", "chroma", "query"],
                )
                try:
                    # 新增：调用 embedding 时透传 parent_run
                    query_embedding = self._embedding_client.embed_query(query_text, parent_run=run)
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
                    self._tracer.end_run(run, outputs={"hits": len(docs)})
                    return docs
                except Exception as e:
                    # 新增：异常路径记录
                    self._tracer.end_run(run, error=LangSmithTracer.format_error(e))
                    raise

    def count(self) -> int:
        """返回当前 collection 中的文档总数。"""
        return self._collection.count()
