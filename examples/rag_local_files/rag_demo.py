"""
RAG 本地文件搜索 —— 完整示例

=== 整体架构（对照 Java 思维理解）===

Java 中的典型搜索系统:
    Controller → Service → Repository(ES/Solr) → 返回结果

LangChain RAG 系统:
    DocumentLoader → TextSplitter → Embeddings → VectorStore → Retriever → LLM

=== 核心概念映射 ===

| LangChain 概念      | Java 类比                          | 作用                    |
|---------------------|------------------------------------|-----------------------|
| Document            | Entity / DTO                       | 一段文本 + 元数据         |
| DocumentLoader      | FileReader / InputStream           | 从文件读取内容            |
| TextSplitter        | StringUtils.split (但更智能)        | 把长文档切成小块           |
| Embeddings          | Word2Vec / TF-IDF (但用神经网络)    | 文本 → 向量(数字数组)      |
| VectorStore         | Elasticsearch (但用向量相似度搜索)   | 存储向量并支持相似度查询    |
| Retriever           | Repository.findByXxx()             | 根据查询检索相关文档       |
| ChatModel           | 外部 API 客户端                     | 调用 LLM 生成回答         |

=== 运行前提 ===
1. 设置环境变量 OPENAI_API_KEY
2. 安装依赖: pip install langchain-core langchain-openai langchain-text-splitters
"""

from __future__ import annotations

import os
from pathlib import Path

# ============================================================
# 第一步：导入所需模块
# ============================================================

# Document: 文档对象，类似 Java 中的 Entity
# 包含 page_content(文本内容) 和 metadata(元数据字典)
from langchain_core.documents import Document

# InMemoryVectorStore: 内存向量数据库
# 类比 Java 中用 HashMap 实现的简单缓存，但支持向量相似度搜索
from langchain_core.vectorstores import InMemoryVectorStore

# RecursiveCharacterTextSplitter: 递归字符分割器
# 智能地按段落 → 句子 → 字符的顺序递归切分文本
from langchain_text_splitters import RecursiveCharacterTextSplitter

# OpenAIEmbeddings: 调用 OpenAI API 将文本转换为向量
# 类似 Java 中调用外部 REST API 的 HTTP Client
from langchain_openai import OpenAIEmbeddings


# ============================================================
# 第二步：定义文件加载器（类比 Java 的 FileReader）
# ============================================================

def load_local_files(directory: str) -> list[Document]:
    """从本地目录加载所有 .txt 和 .md 文件。

    类比 Java:
        public List<Document> loadFiles(String directory) {
            return Files.walk(Paths.get(directory))
                .filter(p -> p.toString().endsWith(".txt"))
                .map(p -> new Document(Files.readString(p), Map.of("source", p)))
                .collect(Collectors.toList());
        }

    Args:
        directory: 本地文件目录路径。

    Returns:
        Document 对象列表，每个文件对应一个 Document。
    """
    documents: list[Document] = []
    dir_path = Path(directory)

    # 支持的文件扩展名
    supported_extensions = {".txt", ".md"}

    for file_path in dir_path.rglob("*"):
        if file_path.suffix.lower() in supported_extensions and file_path.is_file():
            # 读取文件内容
            content = file_path.read_text(encoding="utf-8")

            # 创建 Document 对象（类似 Java 的 new Entity(...)）
            # metadata 相当于 Java Entity 中的额外字段
            doc = Document(
                page_content=content,
                metadata={
                    "source": str(file_path),          # 文件来源路径
                    "filename": file_path.name,         # 文件名
                    "file_type": file_path.suffix,      # 文件类型
                },
            )
            documents.append(doc)
            print(f"  已加载: {file_path.name} ({len(content)} 字符)")

    return documents


# ============================================================
# 第三步：文本分块（类比"把一本书拆成章节段落"）
# ============================================================

def split_documents(documents: list[Document]) -> list[Document]:
    """将长文档切分成小块。

    为什么要分块？
    1. LLM 有上下文长度限制（类比 Java 中 HTTP 请求体大小限制）
    2. 小块文本的向量表示更精确（搜索更准确）
    3. 类比：把一整本书塞进搜索引擎 vs 按段落索引

    Args:
        documents: 原始文档列表。

    Returns:
        切分后的文档块列表（数量 >= 原始文档数）。
    """
    # 创建分割器
    # chunk_size: 每块最大字符数（类比 Java 中 substring 的长度）
    # chunk_overlap: 块之间的重叠字符数（保证上下文连贯性）
    # separators: 优先按这些分隔符切分（段落 > 换行 > 句号 > 空格）
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,          # 每块最多 500 个字符
        chunk_overlap=100,       # 相邻块重叠 100 个字符（防止信息丢失）
        separators=[
            "\n\n",  # 首先按双换行（段落）切分
            "\n",    # 然后按单换行切分
            "。",    # 中文句号
            ".",     # 英文句号
            " ",     # 空格
            "",      # 最后逐字符切分（兜底）
        ],
    )

    # split_documents 会保留原始 metadata 并添加分块信息
    chunks = text_splitter.split_documents(documents)
    print(f"  原始文档: {len(documents)} 个 → 切分后: {len(chunks)} 个块")
    return chunks


# ============================================================
# 第四步：构建向量数据库（类比 "建立搜索索引"）
# ============================================================

def build_vector_store(chunks: list[Document]) -> InMemoryVectorStore:
    """将文档块转换为向量并存入向量数据库。

    流程:
    1. 每个文本块 → 调用 OpenAI Embedding API → 得到一个浮点数数组（向量）
    2. 向量 + 原始文本 + metadata → 存入 VectorStore

    类比 Java:
        // 类似 Elasticsearch 建索引
        for (Document doc : chunks) {
            float[] vector = embeddingClient.embed(doc.getContent());
            elasticsearchClient.index(doc.getId(), vector, doc);
        }

    Args:
        chunks: 切分后的文档块。

    Returns:
        构建好的向量数据库实例。
    """
    # 初始化 Embedding 模型
    # 类比 Java: EmbeddingClient client = new OpenAIEmbeddingClient(apiKey);
    embeddings = OpenAIEmbeddings(
        model="text-embedding-3-small",  # 使用小模型，速度快且便宜
    )

    # 从文档列表创建向量数据库
    # 内部会自动：遍历每个 chunk → 调用 embedding API → 存储向量
    # 类比 Java: VectorStore store = VectorStore.fromDocuments(chunks, client);
    vector_store = InMemoryVectorStore.from_documents(
        documents=chunks,
        embedding=embeddings,
    )

    print(f"  向量数据库构建完成，共 {len(chunks)} 个向量")
    return vector_store


# ============================================================
# 第五步：搜索功能（类比 "根据关键词搜索"）
# ============================================================

def search(vector_store: InMemoryVectorStore, query: str, top_k: int = 3) -> list[Document]:
    """在向量数据库中进行相似度搜索。

    原理:
    1. 将用户查询文本 → 转换为向量
    2. 在数据库中找到与查询向量最相似的 top_k 个文档向量
    3. 返回对应的文档

    类比 Java:
        float[] queryVector = embeddingClient.embed(query);
        List<Document> results = vectorStore.similaritySearch(queryVector, topK);

    与传统关键词搜索的区别:
    - 关键词搜索: "苹果手机" 只能匹配包含"苹果"或"手机"的文档
    - 向量搜索:   "苹果手机" 还能匹配 "iPhone"、"iOS设备" 等语义相近的文档

    Args:
        vector_store: 向量数据库实例。
        query: 用户的搜索查询文本。
        top_k: 返回最相关的前 k 个结果。

    Returns:
        最相关的文档列表。
    """
    results = vector_store.similarity_search(query, k=top_k)
    return results


# ============================================================
# 第六步（可选）：接入 LLM 生成回答（完整 RAG 链路）
# ============================================================

def rag_answer(vector_store: InMemoryVectorStore, question: str) -> str:
    """完整的 RAG 问答流程：检索 + 生成。

    流程:
    1. 根据问题搜索相关文档（Retrieval）
    2. 将问题 + 相关文档拼接成 Prompt
    3. 调用 LLM 生成回答（Generation）

    类比 Java:
        List<Document> context = vectorStore.search(question);
        String prompt = buildPrompt(question, context);
        String answer = llmClient.chat(prompt);

    Args:
        vector_store: 向量数据库实例。
        question: 用户提出的问题。

    Returns:
        LLM 生成的回答文本。
    """
    from langchain_openai import ChatOpenAI

    # 第一步：检索相关文档
    relevant_docs = search(vector_store, question, top_k=3)

    # 第二步：构建 Prompt（将检索到的文档作为上下文）
    context = "\n\n---\n\n".join(
        f"[来源: {doc.metadata.get('filename', '未知')}]\n{doc.page_content}"
        for doc in relevant_docs
    )

    prompt = f"""请根据以下参考资料回答用户的问题。如果参考资料中没有相关信息，请说明无法回答。

## 参考资料
{context}

## 用户问题
{question}

## 回答"""

    # 第三步：调用 LLM 生成回答
    # 类比 Java: ChatClient client = new OpenAIChatClient("gpt-4o-mini");
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    # invoke() 是 LangChain 的统一调用方法（类比 Java 中的 call() / apply()）
    response = llm.invoke(prompt)

    # response.content 是 LLM 返回的文本
    return response.content


# ============================================================
# 主函数：串联整个流程
# ============================================================

def main() -> None:
    """RAG 演示主流程。

    整个流程类比 Java Spring Boot 中的:
        @PostConstruct
        public void init() {
            List<Document> docs = fileService.loadAll();
            List<Document> chunks = splitterService.split(docs);
            vectorStore.indexAll(chunks);
        }

        @GetMapping("/search")
        public List<Document> search(@RequestParam String q) {
            return vectorStore.search(q);
        }
    """
    # --- 配置 ---
    # 示例文档目录（相对于此脚本的位置）
    docs_dir = Path(__file__).parent / "sample_docs"

    # 检查目录是否存在
    if not docs_dir.exists():
        print(f"示例文档目录不存在: {docs_dir}")
        print("正在创建示例文档...")
        docs_dir.mkdir(parents=True, exist_ok=True)
        _create_sample_docs(docs_dir)

    # --- 流程开始 ---
    print("\n" + "=" * 60)
    print("🔄 RAG 本地文件搜索系统")
    print("=" * 60)

    # 步骤 1: 加载文件
    print("\n📂 [1/4] 加载本地文件...")
    documents = load_local_files(str(docs_dir))
    if not documents:
        print("未找到任何文档，退出。")
        return

    # 步骤 2: 分块
    print("\n✂️  [2/4] 文本分块...")
    chunks = split_documents(documents)

    # 步骤 3: 构建向量数据库
    print("\n🧮 [3/4] 构建向量索引...")
    vector_store = build_vector_store(chunks)

    # 步骤 4: 交互式搜索
    print("\n✅ [4/4] 系统就绪！")
    print("=" * 60)

    # 交互循环（类比 Java 的 Scanner 读取用户输入）
    while True:
        print("\n输入搜索内容（输入 'q' 退出，输入 'rag:' 前缀使用 LLM 回答）:")
        query = input("> ").strip()

        if query.lower() == "q":
            print("再见！")
            break

        if not query:
            continue

        # 判断是否使用 RAG 完整链路
        if query.startswith("rag:"):
            question = query[4:].strip()
            print(f"\n🤖 正在生成回答...")
            answer = rag_answer(vector_store, question)
            print(f"\n📝 回答:\n{answer}")
        else:
            # 纯搜索模式
            results = search(vector_store, query)
            print(f"\n🔍 找到 {len(results)} 个相关结果:\n")
            for i, doc in enumerate(results, 1):
                source = doc.metadata.get("filename", "未知")
                # 截取前 200 字符预览
                preview = doc.page_content[:200].replace("\n", " ")
                print(f"  [{i}] 来源: {source}")
                print(f"      内容: {preview}...")
                print()


# ============================================================
# 辅助函数：创建示例文档
# ============================================================

def _create_sample_docs(docs_dir: Path) -> None:
    """创建用于演示的示例文档。"""

    # 示例文档 1: Python 介绍
    (docs_dir / "python_intro.txt").write_text(
        """Python 是一种高级编程语言，由 Guido van Rossum 于 1991 年发布。
Python 的设计哲学强调代码的可读性和简洁性。

Python 支持多种编程范式，包括面向对象、命令式、函数式和过程式编程。
Python 拥有丰富的标准库和第三方库生态系统，广泛应用于 Web 开发、
数据科学、人工智能、自动化运维等领域。

Python 的主要特点：
- 语法简洁优雅
- 动态类型系统
- 自动内存管理（垃圾回收）
- 丰富的标准库
- 强大的社区支持
""",
        encoding="utf-8",
    )

    # 示例文档 2: LangChain 介绍
    (docs_dir / "langchain_intro.md").write_text(
        """# LangChain 简介

LangChain 是一个用于构建大语言模型（LLM）应用的开源框架。
它提供了模块化的组件和工具，帮助开发者快速搭建 AI 应用。

## 核心概念

### Runnable（可运行单元）
Runnable 是 LangChain 的基础抽象，类似 Java 中的 Function 接口。
每个 Runnable 都有 invoke()、stream()、batch() 三个核心方法。

### Chain（链）
Chain 是多个 Runnable 按顺序组合的管道。
类似 Java Stream 的 pipeline: stream().map().filter().collect()

### Agent（智能体）
Agent 是能够自主决策的 AI 系统。它会：
1. 分析用户问题
2. 选择合适的工具（Tool）
3. 执行工具获取结果
4. 根据结果决定下一步动作
5. 循环直到得出最终答案

### Tool（工具）
Tool 是 Agent 可以调用的函数，类似 Java 中用 @Bean 注册的 Service。
""",
        encoding="utf-8",
    )

    # 示例文档 3: RAG 概念
    (docs_dir / "rag_concept.txt").write_text(
        """RAG（Retrieval-Augmented Generation，检索增强生成）是一种结合
信息检索和文本生成的 AI 技术。

RAG 的工作流程：
1. 索引阶段：将文档切分成块，转换为向量，存入向量数据库
2. 检索阶段：根据用户问题，从向量数据库中检索相关文档
3. 生成阶段：将检索到的文档作为上下文，让 LLM 生成回答

RAG 相比纯 LLM 的优势：
- 知识可更新：不需要重新训练模型
- 可追溯来源：回答可以标注信息来源
- 减少幻觉：基于真实文档生成回答
- 领域适配：可以针对特定领域的文档库

RAG 的典型应用场景：
- 企业知识库问答
- 技术文档搜索
- 客服智能助手
- 法律/医疗文献检索
""",
        encoding="utf-8",
    )

    print(f"  已创建 3 个示例文档在: {docs_dir}")


# ============================================================
# 入口点
# ============================================================

if __name__ == "__main__":
    # 检查 API Key（类比 Java 中 @Value("${openai.api-key}") 注入配置）
    if not os.environ.get("OPENAI_API_KEY"):
        print("⚠️  请设置环境变量 OPENAI_API_KEY")
        print("   Windows: set OPENAI_API_KEY=sk-xxx")
        print("   Linux:   export OPENAI_API_KEY=sk-xxx")
        print("\n如果只想看代码结构，可以将 OpenAIEmbeddings 替换为 FakeEmbeddings 进行测试")
    else:
        main()
