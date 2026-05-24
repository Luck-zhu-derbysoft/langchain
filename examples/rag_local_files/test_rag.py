"""
RAG 单元测试 —— 演示如何编写测试

对照 Java 的 JUnit 测试理解：
- pytest 函数 = @Test 方法
- fixture = @BeforeEach / @Bean 注入
- assert = assertEquals / assertTrue
- 无需 class 包装（Python 中函数即可作为测试）

运行方式:
    cd examples/rag_local_files
    pytest test_rag.py -v
"""

from __future__ import annotations

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import FakeEmbeddings
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter


# ============================================================
# Fixture（类似 Java 的 @BeforeEach 或 Spring @Bean）
# pytest 会自动注入这些 fixture 到测试函数的参数中
# ============================================================


@pytest.fixture
def sample_documents() -> list[Document]:
    """提供测试用的示例文档。

    类比 Java:
        @BeforeEach
        void setUp() {
            this.documents = List.of(new Document("content1"), ...);
        }
    """
    return [
        Document(page_content="Python 是一种编程语言。" * 5, metadata={"source": "a.txt"}),
        Document(page_content="LangChain 框架介绍。" * 5, metadata={"source": "b.txt"}),
        Document(page_content="RAG 检索增强生成。" * 5, metadata={"source": "c.txt"}),
    ]


@pytest.fixture
def fake_vector_store(sample_documents: list[Document]) -> InMemoryVectorStore:
    """构建测试用的向量数据库。

    类比 Java:
        @Bean
        VectorStore testVectorStore() {
            return new InMemoryVectorStore(new FakeEmbeddings());
        }
    """
    embeddings = FakeEmbeddings(size=64)
    return InMemoryVectorStore.from_documents(
        documents=sample_documents,
        embedding=embeddings,
    )


# ============================================================
# 测试用例
# ============================================================


class TestTextSplitter:
    """文本分割器测试。类比 Java: class TextSplitterTest"""

    def test_split_produces_chunks(self, sample_documents: list[Document]) -> None:
        """验证分割后块数 >= 原始文档数。"""
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=50,
            chunk_overlap=10,
        )
        chunks = splitter.split_documents(sample_documents)

        # 分块后数量应该 >= 原始文档数
        assert len(chunks) >= len(sample_documents)

    def test_chunk_size_respected(self) -> None:
        """验证每个块不超过 chunk_size。"""
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=100,
            chunk_overlap=0,
        )
        doc = Document(page_content="a " * 200)  # 400 字符的文档
        chunks = splitter.split_documents([doc])

        for chunk in chunks:
            # 每个块不应超过 chunk_size（允许小幅溢出因为不会在单词中间切）
            assert len(chunk.page_content) <= 110  # 留 10% 余量

    def test_metadata_preserved(self, sample_documents: list[Document]) -> None:
        """验证分块后 metadata 被保留。"""
        splitter = RecursiveCharacterTextSplitter(chunk_size=50, chunk_overlap=0)
        chunks = splitter.split_documents(sample_documents)

        # 每个 chunk 都应该保留原始 metadata
        for chunk in chunks:
            assert "source" in chunk.metadata


class TestVectorStore:
    """向量数据库测试。"""

    def test_similarity_search_returns_results(
        self, fake_vector_store: InMemoryVectorStore
    ) -> None:
        """验证搜索能返回结果。"""
        results = fake_vector_store.similarity_search("测试查询", k=2)

        assert len(results) == 2
        assert all(isinstance(r, Document) for r in results)

    def test_similarity_search_k_parameter(
        self, fake_vector_store: InMemoryVectorStore
    ) -> None:
        """验证 k 参数控制返回数量。"""
        results_1 = fake_vector_store.similarity_search("查询", k=1)
        results_3 = fake_vector_store.similarity_search("查询", k=3)

        assert len(results_1) == 1
        assert len(results_3) == 3

    def test_retriever_interface(
        self, fake_vector_store: InMemoryVectorStore
    ) -> None:
        """验证 Retriever 接口可用。"""
        retriever = fake_vector_store.as_retriever(search_kwargs={"k": 1})

        # Retriever 使用 invoke() 方法（Runnable 接口）
        results = retriever.invoke("查询")

        assert len(results) == 1
        assert isinstance(results[0], Document)


class TestDocumentModel:
    """Document 模型测试。"""

    def test_document_creation(self) -> None:
        """验证 Document 对象创建。"""
        doc = Document(
            page_content="测试内容",
            metadata={"key": "value"},
        )

        assert doc.page_content == "测试内容"
        assert doc.metadata["key"] == "value"

    def test_document_with_id(self) -> None:
        """验证带 ID 的 Document。"""
        doc = Document(
            id="doc-001",
            page_content="内容",
            metadata={},
        )

        assert doc.id == "doc-001"
