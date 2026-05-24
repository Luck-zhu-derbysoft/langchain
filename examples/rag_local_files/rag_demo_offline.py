"""
RAG 本地文件搜索 —— 无需 API Key 的离线测试版本

本文件使用 FakeEmbeddings（随机向量）代替真实 Embedding API，
让你无需任何 API Key 即可跑通整个 RAG 流程。

适用场景：
- 理解 RAG 的代码结构和数据流
- 调试 DocumentLoader / TextSplitter 逻辑
- 单元测试

注意：因为使用假向量，搜索结果不会有语义相关性（结果随机）。
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.documents import Document
from langchain_core.embeddings import FakeEmbeddings
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter


def main() -> None:
    """离线测试版主流程，不需要任何 API Key。"""

    print("=" * 60)
    print("RAG 离线测试版（使用 FakeEmbeddings）")
    print("=" * 60)

    # --- 步骤 1: 准备文档 ---
    # 直接在代码中定义文档（跳过文件加载，简化演示）
    documents = [
        Document(
            page_content="Python 是一种高级编程语言，支持面向对象和函数式编程。",
            metadata={"source": "python.txt"},
        ),
        Document(
            page_content="LangChain 是构建 LLM 应用的框架，核心抽象是 Runnable。",
            metadata={"source": "langchain.txt"},
        ),
        Document(
            page_content="RAG 是检索增强生成技术，结合搜索和大模型生成回答。",
            metadata={"source": "rag.txt"},
        ),
        Document(
            page_content="向量数据库通过计算向量之间的余弦相似度来实现语义搜索。",
            metadata={"source": "vectordb.txt"},
        ),
        Document(
            page_content="Agent 是能自主决策的 AI 系统，通过工具调用完成复杂任务。",
            metadata={"source": "agent.txt"},
        ),
    ]

    print(f"\n📂 已加载 {len(documents)} 个文档")

    # --- 步骤 2: 分块 ---
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=100,
        chunk_overlap=20,
    )
    chunks = splitter.split_documents(documents)
    print(f"✂️  切分为 {len(chunks)} 个块")

    # --- 步骤 3: 构建向量数据库（使用假 Embedding）---
    # FakeEmbeddings 会生成随机向量，用于结构验证
    # size 参数指定向量维度（真实 OpenAI 模型通常是 1536 或 3072）
    fake_embeddings = FakeEmbeddings(size=128)

    vector_store = InMemoryVectorStore.from_documents(
        documents=chunks,
        embedding=fake_embeddings,
    )
    print(f"🧮 向量数据库构建完成")

    # --- 步骤 4: 搜索测试 ---
    print("\n" + "-" * 40)
    print("🔍 搜索测试（注意：FakeEmbeddings 结果为随机）")
    print("-" * 40)

    test_queries = ["什么是 RAG", "Python 编程", "Agent 工具"]

    for query in test_queries:
        results = vector_store.similarity_search(query, k=2)
        print(f"\n查询: '{query}'")
        for i, doc in enumerate(results, 1):
            print(f"  结果 {i}: [{doc.metadata['source']}] {doc.page_content[:50]}...")

    # --- 步骤 5: 演示 Retriever 接口 ---
    # Retriever 是 VectorStore 的"搜索视图"
    # 类比 Java: Repository 接口（只暴露查询方法）
    print("\n" + "-" * 40)
    print("🔗 使用 Retriever 接口（推荐方式）")
    print("-" * 40)

    # as_retriever() 将 VectorStore 转换为 Retriever
    # 类比 Java: SearchService searchService = vectorStore.asSearchService();
    retriever = vector_store.as_retriever(
        search_kwargs={"k": 2},  # 每次返回 top 2
    )

    # Retriever 实现了 Runnable 接口，可以用 invoke() 调用
    # 类比 Java: List<Document> results = searchService.search(query);
    results = retriever.invoke("向量搜索原理")
    print(f"\nRetriever 返回 {len(results)} 个结果:")
    for doc in results:
        print(f"  - [{doc.metadata['source']}] {doc.page_content[:50]}...")

    print("\n✅ 测试完成！")
    print("\n💡 提示: 将 FakeEmbeddings 替换为 OpenAIEmbeddings 即可获得真实语义搜索能力")


if __name__ == "__main__":
    main()
