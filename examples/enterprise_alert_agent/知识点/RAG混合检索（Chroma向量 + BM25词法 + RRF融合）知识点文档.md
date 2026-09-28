# RAG混合检索（Chroma向量 \+ BM25词法 \+ RRF融合）知识点文档

# RAG 混合检索（Chroma 向量 \+ BM25 词法 \+ RRF 融合）知识点文档


混合检索核心思路：**稠密向量检索（语义）\+ BM25 词法检索（关键词），使用 RRF 倒数排名融合合并两路结果**

## 目录

1. 整体检索链路

2. 模块 1：Chroma 稠密向量检索（代码 \+ 注释 \+ 示例）

3. 模块 2：BM25 词法检索 `_lexical_search`（代码 \+ 注释 \+ 示例）

4. 模块 3：RRF 倒数排名融合 `_rrf_merge`（代码 \+ 注释 \+ 示例）

5. 核心调参说明

6. 工程风险清单

7. 完整调用伪代码

---

## 1\. 整体检索链路

```Plain Text
用户Query
├─ 稠密检索：Chroma → dense_docs 【语义相似，理解句子含义】
└─ 词法检索：BM25 _lexical_search → lexical_docs【关键词命中，专有名词、编号】
        ↓
RRF融合 _rrf_merge(dense_docs, lexical_docs)
        ↓
重排后的最终文档列表，送入LLM做回答生成
```

> 为什么不直接相加分数？
> Chroma 向量距离分数、BM25 词法分数值域完全不一样，单位含义不同，无法直接加权求和。RRF**只使用排名，不使用原始分数**，完美解决这个问题。
> 
> 

---

## 2\. 模块 1：Chroma 稠密向量检索

### 代码（带注释）

```python
results = self._collection.query(
    query_embeddings=[query_embedding], # 传入向量化后的query，Chroma接收数组
    n_results=top_k,                    # 召回top_k条相似文档
    where=where,                        # metadata元数据过滤条件
    include=["documents", "metadatas", "distances"], # 指定要返回的附属字段
)

docs: list[dict[str, Any]] = []
# 校验返回结果是否存在
if results["documents"] and results["metadatas"] and results["distances"]:
    # Chroma返回是二维数组，[0]取第一个query对应的结果
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
            "score": float(1 - float(distance)), # L2距离转相似度：距离越小越相似
        })
```

### 数据示例

Query：`LangGraph HITL`
向量库返回：

|id|content|distance|score\(1\-distance\)|rank|
|---|---|---|---|---|
|d3|MCP 协议用于 Agent 工具调用通信|0\.10|0\.90|1|
|d2|LangGraph 支持 HITL 人工介入中断恢复|0\.15|0\.85|2|
|d1|LangGraph 可以构建 Agent 工作流|0\.30|0\.70|3|

```python
dense_docs = [
    {"id": "d3", "content": "MCP协议用于Agent工具调用通信", "score":0.90},
    {"id": "d2", "content": "LangGraph支持HITL人工介入中断恢复", "score":0.85},
    {"id": "d1", "content": "LangGraph可以构建Agent工作流", "score":0.70},
]
```

### 关键点

- Chroma 默认使用 L2 欧氏距离；`distance`越小代表向量越相似

- 返回结果是二维列表 `results["ids"][0]`，多 query 才会有多组结果

- `where`仅能过滤 metadata 字段，**不能对文档正文 content 过滤**

---

## 3\. 模块 2：BM25 词法检索 \_lexical\_search

### 代码（带注释）

```python
def _lexical_search(
    self, query: str, top_k: int, *, where: dict[str, Any] | None
) -> list[dict[str, Any]]:
    # 1. 根据where条件过滤，取出符合条件的全部文档
    documents = list(self._store.iter_documents(where=where))
    # 2. 对查询语句分词
    query_tokens = self._tokenize(query)
    # 边界判断：无文档 / query分词为空，直接返回空列表
    if not documents or not query_tokens:
        return []

    # 3. 对候选文档批量分词，构建语料库corpus
    corpus = [self._tokenize(doc["content"]) for doc in documents]
    # 边界：全部文档分词结果为空，返回空
    if not any(corpus):
        return []
    # 4. BM25Okapi计算每篇文档和query的词法相关性分数
    scores = BM25Okapi(corpus).get_scores(query_tokens)
    # 5. 根据分数降序排序，得到文档下标顺序
    ranked = sorted(range(len(documents)), key=lambda idx: scores[idx], reverse=True)
    # 6. 过滤分数>0（至少命中一个关键词），截取top_k条返回
    return [documents[idx] for idx in ranked if scores[idx] > 0][:top_k]
```

### 数据示例

where 过滤后拿到 3 条文档：

```python
documents = [
    {"id": "d1", "content": "LangGraph 可以构建 Agent 工作流"},
    {"id": "d2", "content": "LangGraph 支持 HITL 人工介入中断恢复"},
    {"id": "d3", "content": "MCP 协议用于Agent工具调用通信"}
]
query = "LangGraph HITL"
query_tokens = ["LangGraph", "HITL"]
corpus = [
    ["LangGraph", "可以", "构建", "Agent", "工作流"],
    ["LangGraph", "支持", "HITL", "人工", "介入", "中断恢复"],
    ["MCP", "协议", "用于", "Agent", "工具调用", "通信"]
]
scores = [0.62, 1.45, 0.0]
```

排序后过滤掉 score=0 的 d3，取 top\_k=2：

```python
lexical_docs = [
    {"id": "d2", "content": "LangGraph 支持 HITL 人工介入中断恢复"}, # rank1 score1.45
    {"id": "d1", "content": "LangGraph 可以构建 Agent 工作流"},         # rank2 score0.62
]
```

### 关键点

- BM25：基于词频、逆文档频率做关键词匹配；适合匹配专有名词、编号

- 当前实现：内存实时计算 BM25；**文档量大时性能差，生产建议使用 ES 内置 BM25**

- 分数 = 0：文档没有命中 query 任何关键词，直接过滤

---

## 4\. 模块 3：RRF 倒数排名融合 \_rrf\_merge

> RRF 公式：$RRF_{score}=\sum \frac{weight}{k+rank}$，代码固定 k=60；rank 从 1 开始
> 
> 

### 代码（带注释）

```python
@staticmethod
def _rrf_merge(
    dense_docs: list[dict[str, Any]],
    lexical_docs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # 字典用于文档去重：key=文档id，value=文档+累计融合score
    merged: dict[str, dict[str, Any]] = {}
    # 读取配置：开启BM25则使用alpha；关闭BM25，稠密权重强制=1.0
    dense_weight = (
        settings.retrieval_hybrid_alpha if settings.retrieval_use_bm25 else 1.0
    )
    # 两路检索结果循环：稠密向量结果、词法BM25结果
    for docs, weight in (
        (dense_docs, dense_weight),
        (lexical_docs, 1.0 - dense_weight),
    ):
        # rank从1开始（RRF标准：第一名rank=1）
        for rank, doc in enumerate(docs, start=1):
            # 文档不存在则新建，score初始化为0；存在直接取出
            item = merged.setdefault(doc["id"], {**doc, "score": 0.0})
            # RRF核心公式累加分数
            item["score"] += weight / (60 + rank)
    # 按融合后的总分降序排序，返回结果
    return sorted(merged.values(), key=lambda doc: doc["score"], reverse=True)
```

### 数据示例

配置：`retrieval_use_bm25=True`，`retrieval_hybrid_alpha=0.7`

- dense\_weight = 0\.7

- lexical\_weight = 1 \- 0\.7 = 0\.3
输入两路结果：

```python
dense_docs = [{"id":"d3"}, {"id":"d2"}, {"id":"d1"}]
lexical_docs = [{"id":"d2"}, {"id":"d1"}]
```

逐项计算：

1. d3：仅稠密 rank1 → `0.7/(60+1) ≈0.01148`

2. d2：稠密 rank2 \+ BM25 rank1 → `0.7/62 + 0.3/61 ≈0.01129+0.00492 =0.01621`

3. d1：稠密 rank3 \+ BM25 rank2 → `0.7/63 +0.3/62≈0.01111+0.00484=0.01595`

融合后排序：`d2(0.01621) > d1(0.01595) > d3(0.01148)`

> ✅ 效果：d3 向量检索第一名，但没有关键词命中；d2 两路检索都靠前，融合后排到第一名。
> 
> 

### 边界案例

`retrieval_use_bm25=False`：lexical 权重 = 0，BM25 检索结果不贡献分数，等价纯向量检索。

---

## 5\. 核心调参说明

|参数|作用|推荐值|
|---|---|---|
|retrieval\_hybrid\_alpha|稠密向量检索权重|语义问答：0\.7\\0\.9；文档关键词重要：0\.4\\0\.6|
|RRF k=60|抑制排名靠后的文档得分|短召回列表 top\_k\<10：k=10/20；长列表：k=60|
|top\_k|每一路检索召回文档数量|一般 10\~20|

---

## 6\. 工程风险清单

### Chroma 稠密检索

1. `score =1-distance` 强依赖 L2 距离；切换余弦距离公式要修改

2. zip 拼接结果时，如果 ids/metadatas 长度不一致会静默丢数据

3. where 过滤只能作用在 metadata，不能过滤正文

### BM25 词法检索

1. 当前是内存实时 BM25，文档量大时 CPU 开销高，不适合海量文档

2. 每次调用新建 BM25Okapi 对象，无缓存

### RRF 融合

1. RRF**只依赖列表顺序**，列表必须是相关性降序，顺序错结果直接错误

2. 文档必须具备唯一`id`字段，否则去重逻辑失效

3. k=60 硬编码，不方便动态调参

---

## 7\. 完整调用伪代码

```python
# 1. 入参
query = "LangGraph HITL"
top_k = 3
where_filter = {"source": "agent_doc"}

# 2. 稠密向量检索
dense_docs = self._dense_search(query, top_k, where=where_filter)
# 3. BM25词法检索
lexical_docs = self._lexical_search(query, top_k, where=where_filter)
# 4. RRF融合重排
final_docs = self._rrf_merge(dense_docs, lexical_docs)
# 5. 送入LLM生成回答
```



> （注：部分内容由豆包工作 AI 生成）
