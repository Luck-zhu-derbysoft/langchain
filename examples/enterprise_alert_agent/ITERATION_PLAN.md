# 多任务并发执行系统 - 分步迭代方案

## 📊 迭代版本规划

```
V1 (第一个版本)  → 基础意图分类         ~200行代码
  ↓
V2 (第二个版本)  → 工具选择理由         +100行代码  
  ↓
V3 (第三个版本)  → 任务拆解和多任务     +300行代码
  ↓
V4 (第四个版本)  → 重试和降级机制       +400行代码
  ↓
V5 (完整版本)    → 人工接管和指标       +900行代码
```

---

## 🎯 版本功能对应表

| 版本 | 核心功能 | 新增文件 | 修改文件 | 代码量 | 实现时间 |
|-----|---------|---------|---------|-------|---------|
| **V1** | 意图分类可见 | 1个 | 2个 | ~200行 | 10min |
| V2 | 工具选择理由 | 1个 | 1个 | +100行 | 10min |
| V3 | 多任务并发 | 1个 | 1个 | +300行 | 20min |
| V4 | 重试降级 | 1个 | 1个 | +400行 | 25min |
| V5 | 完整功能 | 1个 | 1个 | +900行 | 30min |

---

## 📝 V1 版本详细说明

### V1 目标
- ✅ 显示用户查询的意图分类结果
- ✅ 返回意图类型和置信度
- ✅ 在 ChatResponse 中展示

### V1 涉及的文件

```
新增：
  ✨ app/infrastructure/agent/a2a_protocol.py    (50行)
  
修改：
  ✏️  app/schemas/chat.py                         (10行)
  ✏️  app/application/services/chat_service.py   (40行)
```

### V1 的响应示例

```json
{
  "response": "查询的是客户信息",
  "intent": "query_customer_info",           // ✅ 新增
  "intent_confidence": 0.92,                 // ✅ 新增
  "trace_id": "req_xxx"                      // ✅ 新增
}
```

### V1 实现流程

```python
# 用户发送查询
POST /chat
{
  "query": "查询客户李明的信息"
}

# 后端处理流程（V1）
1. 调用 LLM 分类意图
   ↓
2. 构建 IntentClassification 对象
   ↓
3. 返回给客户端
```

---

## 🚀 快速开始（仅需10分钟）

### 第一步：创建新文件 (3 分钟)
从 **V1_NEW_FILES.md** 复制代码到：
- `app/infrastructure/agent/a2a_protocol.py`

### 第二步：修改现有文件 (5 分钟)
从 **V1_MODIFICATIONS.md** 按修改点：
- 修改 `app/schemas/chat.py`
- 修改 `app/application/services/chat_service.py`

### 第三步：验证 (2 分钟)
```bash
# 启动应用
python -m uvicorn app.main:app --reload

# 发送测试请求
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "查询客户信息"}'

# 验证响应包含 intent 和 intent_confidence
```

---

## ✅ V1 完成标准

实现 V1 版本后，应该能够：
- ✅ 应用能正常启动
- ✅ 单个查询返回意图分类结果
- ✅ 响应包含 `intent` 和 `intent_confidence` 字段
- ✅ 日志显示意图分类过程

---

## 📄 相关文档

### V1 专用文档（即将生成）
- **V1_NEW_FILES.md** - 仅需创建的1个新文件完整代码
- **V1_MODIFICATIONS.md** - 仅需修改的2个文件具体改动点
- **V1_CHECKLIST.md** - V1 实现检查清单

### 进阶文档
- **ITERATION_PLAN.md** (本文件) - 完整迭代计划
- **V2_PLAN.md** - V2 版本计划（实现工具选择理由）
- **V3_PLAN.md** - V3 版本计划（多任务执行）
- 等等...

---

## 💡 设计哲学

每个版本都是**完整可工作的**，而不是功能碎片：

```
❌ 不推荐的分割方式
  V1: 仅创建文件但不实现
  V2: 仅修改响应但不显示
  
✅ 推荐的分割方式
  V1: 意图分类功能端到端可工作
  V2: 在 V1 基础上添加工具选择
  V3: 在 V2 基础上添加多任务
```

---

## 🎓 学习路径

**建议按以下顺序学习和实现：**

1. **V1** - 理解基本的 Intent Classification 流程
2. **V2** - 学习 Tool Selection 和决策理由
3. **V3** - 理解任务拆解和并行执行
4. **V4** - 学习错误处理和重试策略
5. **V5** - 集成完整的人工接管流程

---

## ⚡ 快速切换版本

如果你已经实现了 V1，想跳到 V3：

```bash
# V1 → V3 的增量修改
# 查看 V3_MODIFICATIONS.md 中标记为 V2 和 V3 的部分
# 逐一应用即可
```

---

## 📌 下一步

👉 **准备好实施 V1 了吗？**

查看以下文件：
1. **V1_NEW_FILES.md** - 新建文件代码
2. **V1_MODIFICATIONS.md** - 修改点说明
3. **V1_CHECKLIST.md** - 检查清单

祝你实现顺利！🚀
