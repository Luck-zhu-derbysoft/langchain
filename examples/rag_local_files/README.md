# 本地文件 RAG 搜索示例

## 功能
- 读取本地 `.txt` / `.md` 文件
- 对文档进行分块（chunking）
- 使用 Embedding 模型生成向量
- 存入内存向量数据库
- 支持自然语言搜索，返回相关文档片段
- 可选：接入 LLM 生成回答（完整 RAG 链路）

## 运行方式

```bash
cd examples/rag_local_files

# 安装依赖（从项目根目录）
uv pip install langchain-core langchain-openai langchain-text-splitters

# 设置 API Key（用于 Embedding 和 LLM）
set OPENAI_API_KEY=sk-xxx

# 运行
python rag_demo.py
```

## 架构流程图

```
本地文件 → DocumentLoader → TextSplitter → Embeddings → VectorStore → Retriever → LLM → 回答
```
