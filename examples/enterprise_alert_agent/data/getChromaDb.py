"""查看本地 ChromaDB 存储内容的调试脚本。

用途：
1. 打印集合中的文档总数
2. 打印每条文档的 ID、正文、元数据
3. 打印每条向量的前 5 个值和维度
"""

from pathlib import Path
from typing import Any, cast

import chromadb

# 固定为“脚本所在目录/data/chroma_db”，避免因工作目录不同导致找错库。
db_path = Path(__file__).resolve().parent / "chroma_db"
client = chromadb.PersistentClient(path=str(db_path))

collection_name = "alert_knowledge"

# 先列出当前 DB 中所有 collection，便于诊断名称是否匹配。
existing_collections = [item.name for item in client.list_collections()]
print(f"Chroma 路径: {db_path}")
print(f"已有 collections: {existing_collections}")

if collection_name not in existing_collections:
    raise SystemExit(
        f"Collection [{collection_name}] 不存在。"
        "请先调用 /ingest/text 导入文档，或确认 collection 名称配置一致。"
    )

collection = client.get_collection(name=collection_name)


# 1) 查看文档总数
print(f"文档总数: {collection.count()}")


# # 2) 查看所有文档内容和元数据
# # 注意：Chroma 的返回类型里 documents/metadatas 可能是 None，
# # 这里用 `or []` 做兜底，避免类型检查和运行时报错。
results = collection.get(include=["documents", "metadatas"])
ids = cast(list[str], results.get("ids") or [])
documents = cast(list[str], results.get("documents") or [])
metadatas = cast(list[dict[str, Any]], results.get("metadatas") or [])

print("\n=== 文档与元数据 ===")
for doc_id, doc, metadata in zip(ids, documents, metadatas):
    print(f"ID: {doc_id}\n内容: {doc}\n元数据: {metadata}\n---")


# # 3) 查看 embedding 向量
# # embeddings 返回类型可能是 list 或 ndarray，也可能是 None。
if not ids:
    print("\n没有文档，无法查看向量。")
    exit()
else:
    embedding_results = collection.get(ids=ids, include=["embeddings"])
    embedding_ids = cast(list[str], embedding_results.get("ids") or [])
    embeddings_raw = embedding_results.get("embeddings")
    embeddings: list[Any] = list(embeddings_raw) if embeddings_raw is not None else []


print("\n=== 向量信息 ===")
for doc_id, embedding in zip(embedding_ids, embeddings):
    print(f"ID: {doc_id}\nEmbedding向量: {embedding[:5]}...（共{len(embedding)}维）\n---")

# 按照id删除数据
# collection.delete(ids=["c23228cf-9c1b-4648-8b1e-d171862af651"])
# print(f"已删除 1 条数据。当前文档总数: {collection.count()}")

# # 按照 where 条件删除数据（示例：删除 source_id 为 "alert-rule-001" 的文档）
# collection.delete(where={"source_id": "0527001"})
# print("deleted by where(source_id)")

# 删除 collection 中的所有数据（慎用！）
# all_data = collection.get(include=[])
# all_ids = all_data.get("ids") or []
# if all_ids:
#     collection.delete(ids=all_ids)

# print("collection cleared, remaining:", collection.count())

# # 4) 直接删除整个 collection（最彻底，结构也删掉）

# client = chromadb.PersistentClient(path="./data/chroma_db")
# client.delete_collection(name="alert_knowledge")

# print("collection deleted")
