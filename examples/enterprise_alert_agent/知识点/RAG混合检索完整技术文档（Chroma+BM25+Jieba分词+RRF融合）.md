# RAG混合检索完整技术文档（Chroma\+BM25\+Jieba分词\+RRF融合）

**可直接下载保存 · 含完整代码、逐行注释、数据样例、调参、风险点**

混合检索核心思路：**稠密向量检索（语义理解）\+ BM25词法检索（关键词精准匹配）\+ Jieba中文分词 \+ RRF倒数排名融合**

## 目录

1. 整体检索链路与核心原理

2. 模块1：Chroma 稠密向量检索（代码\+注释\+数据示例）

3. 模块2：Jieba 中文分词（BM25依赖核心）

4. 模块3：BM25词法检索 \_lexical\_search（代码\+注释\+数据示例）

5. 模块4：RRF倒数排名融合 \_rrf\_merge（代码\+注释\+计算示例）

6. 核心参数调参手册

7. 工程风险与优化方案

8. 完整调用伪代码

---

## 1\. 整体检索链路

```Plain Text
用户Query
├─ 稠密检索：Chroma向量 → dense_docs（语义相似、模糊匹配）
└─ 词法检索：Jieba分词 + BM25 → lexical_docs（关键词、专有名词精准命中）
        ↓
RRF倒数排名融合（合并两路结果、去重、重排）
        ↓
最终有序文档列表 → 送入LLM生成答案
```

### 为什么需要混合检索？

- **纯向量检索**：擅长语义理解，但容易丢失专有名词、编号、精准关键词

- **纯BM25检索**：擅长字面匹配，无法理解语义、同义替换

- **RRF融合优势**：不依赖两路完全不同的分数值域，**只靠排名融合**，结果更稳、泛化性更强

---

## 2\. 模块1：Chroma 稠密向量检索

### 核心代码（带完整注释）

```python
results = self._collection.query(
    query_embeddings=[query_embedding], # Chroma要求向量外层包数组，支持批量query
    n_results=top_k,                    # 单次召回文档数量
    where=where,                        # metadata元数据过滤（仅过滤元数据，不过滤正文）
    include=["documents", "metadatas", "distances"], # 主动返回相似度距离、文档、元数据
)

docs: list[dict[str, Any]] = []
# 非空校验，防止索引越界
if results["documents"] and results["metadatas"] and results["distances"]:
    # Chroma结果为二维数组，[0]取当前唯一query的结果集
    for doc_id, doc, meta, distance in zip(
        results["ids"][0],
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        docs.append({
            "id": doc_id,
            "source_id": str(meta.get("source_id", "unknown")) if meta else "unknown",
            "content": doc or "",
            "metadata": meta or {},
            "score": float(1 - float(distance)), # L2距离转相似度分数：距离越小越相似
        })
```

### 数据示例

Query：`LangGraph HITL`

|id|content|distance|score\(1\-distance\)|rank|
|---|---|---|---|---|
|d3|MCP 协议用于 Agent 工具调用通信|0\.10|0\.90|1|
|d2|LangGraph 支持 HITL 人工介入中断恢复|0\.15|0\.85|2|
|d1|LangGraph 可以构建 Agent 工作流|0\.30|0\.70|3|

### 关键特性

- Chroma 默认使用 **L2欧氏距离**

- 距离越小，向量相似度越高

- `where` 仅过滤 metadata，无法筛选正文内容

---

## 3\. 模块2：Jieba 中文分词（BM25底层依赖）

BM25 无法直接处理字符串，必须依赖分词后的 token 列表，项目统一使用 **jieba 精确模式**

### 3\.1 三种分词模式（RAG仅用精确模式）

1. **精确模式（默认）**：精准切分、无冗余、检索专用

2. **全模式**：穷举所有词语、重叠冗余、检索不用

3. **搜索引擎模式**：细粒度拆分，适合短文本搜索

### 3\.2 标准分词代码（项目 \_tokenize 实现规范）

```python
import jieba

# 停用词集合（过滤无意义虚词）
STOP_WORDS = {"的", "是", "可以", "用于", "和", "了", "在"}

def _tokenize(text: str) -> list[str]:
    # 1. jieba精确分词
    tokens = jieba.lcut(text, cut_all=False)
    # 2. 过滤停用词、空字符、空格
    tokens = [w for w in tokens if w not in STOP_WORDS and w.strip()]
    return tokens
```

### 3\.3 分词示例

输入：`LangGraph 支持 HITL 人工介入中断恢复`

分词结果：`["LangGraph", "支持", "HITL", "人工", "介入", "中断", "恢复"]`

### 3\.4 关键工程配置（解决专业名词拆分错误）

jieba 默认词典不认识 AI 专业术语，需手动加载自定义词典

自定义词典 `custom_dict.txt`

```Plain Text
LangGraph 100
MCP 100
HITL 100
Agent 100
```

代码加载：

```Plain Text
jieba.load_userdict("custom_dict.txt")
```

### 3\.5 jieba 核心坑点

- 区分大小写：`Langgraph` / `LangGraph` 视为不同词，需统一标准化

- 自带标点符号，需额外过滤

- 实时大文档分词 CPU 开销极高，建议预缓存分词结果

---

## 4\. 模块3：BM25词法检索 \_lexical\_search

### 完整带注释代码

```python
def _lexical_search(
    self, query: str, top_k: int, *, where: dict[str, Any] | None
) -> list[dict[str, Any]]:
    # 1. 根据metadata条件过滤，拿到候选文档集
    documents = list(self._store.iter_documents(where=where))
    # 2. jieba分词处理查询语句
    query_tokens = self._tokenize(query)
    # 边界：无文档/无有效分词，直接返回空
    if not documents or not query_tokens:
        return []

    # 3. 对所有候选文档批量分词，构建BM25语料库
    corpus = [self._tokenize(doc["content"]) for doc in documents]
    if not any(corpus):
        return []

    # 4. BM25算法计算每篇文档相关性分数
    scores = BM25Okapi(corpus).get_scores(query_tokens)
    # 5. 按分数降序排序，获取文档下标顺序
    ranked = sorted(range(len(documents)), key=lambda idx: scores[idx], reverse=True)
    # 6. 过滤零命中文档，截取top_k结果
    return [documents[idx] for idx in ranked if scores[idx] > 0][:top_k]
```

### 数据执行示例

候选文档：

```Plain Text
documents = [
    {"id": "d1", "content": "LangGraph 可以构建 Agent 工作流"},
    {"id": "d2", "content": "LangGraph 支持 HITL 人工介入中断恢复"},
    {"id": "d3", "content": "MCP 协议用于Agent工具调用通信"}
]
query = "LangGraph HITL"
```

分词后分数：`scores = [0.62, 1.45, 0.0]`

排序过滤最终结果：

```Plain Text
lexical_docs = [
    {"id": "d2", "content": "LangGraph 支持 HITL 人工介入中断恢复"},
    {"id": "d1", "content": "LangGraph 可以构建 Agent 工作流"},
]
```

### 核心原理

- 分数越高：关键词命中越多、稀有词匹配越多

- score=0：无任何关键词命中，直接过滤

- 适合：专有名词、接口名、编号、精准问题

---

## 5\. 模块4：RRF倒数排名融合 \_rrf\_merge

### 核心公式

$RRF_{score} = \sum \frac{weight}{60+rank}$

**只看排名、不看原始分数**，解决向量/BM25分数值域不统一的问题

### 完整带注释代码

```python
@staticmethod
def _rrf_merge(
    dense_docs: list[dict[str, Any]],
    lexical_docs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # 用于文档去重、分数累加：key=唯一文档id
    merged: dict[str, dict[str, Any]] = {}

    # 权重配置：关闭BM25则纯向量检索
    dense_weight = (
        settings.retrieval_hybrid_alpha if settings.retrieval_use_bm25 else 1.0
    )

    # 遍历两路检索结果，分别加权计分
    for docs, weight in (
        (dense_docs, dense_weight),
        (lexical_docs, 1.0 - dense_weight),
    ):
        # rank从1开始（RRF标准定义）
        for rank, doc in enumerate(docs, start=1):
            # 存在则复用，不存在则初始化score=0
            item = merged.setdefault(doc["id"], {**doc, "score": 0.0})
            # 累加RRF分数
            item["score"] += weight / (60 + rank)

    # 总分降序返回最终排序结果
    return sorted(merged.values(), key=lambda doc: doc["score"], reverse=True)
```

### 完整计算示例

配置：`alpha=0.7`、向量权重0\.7、词法权重0\.3

输入两路结果：

- dense rank：d3\(1\)、d2\(2\)、d1\(3\)

- lexical rank：d2\(1\)、d1\(2\)

分数计算：

- d3：`0.7/61 ≈ 0.01148`

- d2：`0.7/62 + 0.3/61 ≈ 0.01621`

- d1：`0.7/63 + 0.3/62 ≈ 0.01595`

最终排序：**d2 \> d1 \> d3**

✅ 优势：两路表现均衡的文档自动置顶，检索更精准

---

## 6\. 核心调参手册

|参数|作用|推荐值|
|---|---|---|
|retrieval\_hybrid\_alpha|向量检索权重|语义问答0\.7\~0\.9；文档关键词检索0\.4\~0\.6|
|RRF k=60|压制后排文档权重|长列表固定60，短列表可改为10/20|
|top\_k|单路召回数量|10\~20|

---

## 7\. 工程风险与优化方案

### 7\.1 Chroma 风险

- 分数计算强依赖L2距离，切换余弦距离需改公式

- where无法过滤正文，只能过滤元数据

### 7\.2 BM25\+Jieba 风险

- 实时全量文档分词，大数量CPU开销极高

- 专业名词需自定义词典，否则分词错乱

- 无停用词过滤会严重干扰分数

### 7\.3 RRF 风险

- 完全依赖列表排序，顺序错误直接导致结果错误

- 必须保证文档id唯一，否则去重失效

---

## 8\. 完整调用伪代码

```python
# 1. 入参定义
query = "LangGraph HITL人工介入流程"
top_k = 15
where_filter = {"source_type": "agent_docs"}

# 2. 两路并行检索
dense_docs = self._dense_search(query, top_k, where=where_filter)
lexical_docs = self._lexical_search(query, top_k, where=where_filter)

# 3. RRF融合重排
final_docs = self._rrf_merge(dense_docs, lexical_docs)

# 4. 送入LLM上下文生成答案
```

---

## 9\. 文档总结

本项目混合检索采用 **向量语义 \+ jieba分词BM25关键词 \+ RRF排名融合** 工业级方案：

- 向量检索兜底语义理解

- Jieba分词 \+ BM25 兜底精准关键词命中

- RRF 解决分数不统一问题，融合效果优于加权求和

> （注：部分内容由豆包工作 AI 生成）
