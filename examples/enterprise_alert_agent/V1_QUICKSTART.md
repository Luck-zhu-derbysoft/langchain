# 🚀 V1 版本快速入门（10 分钟）

## 📌 概览

**V1 目标**: 在 chat 接口响应中显示**用户意图分类结果**

**代码量**: ~200 行（非常小）
**实现时间**: 10-15 分钟
**难度**: ⭐ 简单

---

## 📦 包含内容

| 类型 | 项目 | 代码量 |
|-----|------|-------|
| 新增文件 | 1 个 (a2a_protocol.py) | 50 行 |
| 修改文件 | 2 个 (chat.py, chat_service.py) | 55 行 |
| **总计** | | **~105 行** |

---

## ⚡ 3 步实现

### 第 1 步：创建新文件 (3 分钟)

📁 创建路径: `app/infrastructure/agent/`

```bash
mkdir -p app/infrastructure/agent
```

📄 创建文件: `app/infrastructure/agent/a2a_protocol.py`

📋 从 **V1_NEW_FILES.md** 复制代码到此文件
- 总共 50 行代码
- 包含 3 个类/枚举

---

### 第 2 步：修改 schemas/chat.py (2 分钟)

打开: `app/schemas/chat.py`

**找到 ChatResponse 类，添加 3 个字段：**

```python
class ChatResponse(BaseModel):
    response: str
    # ... 其他现有字段 ...
    
    # ✨ V1 新增
    intent: Optional[str] = None
    intent_confidence: Optional[float] = None
    trace_id: Optional[str] = None
```

就这样！3 行代码。

---

### 第 3 步：修改 chat_service.py (5 分钟)

打开: `app/application/services/chat_service.py`

#### 3.1 添加导入（顶部）

```python
from typing import Optional, Dict, Any
import json
import logging
from uuid import uuid4
from app.infrastructure.agent.a2a_protocol import IntentClassification

logger = logging.getLogger(__name__)
```

#### 3.2 修改 ask() 方法

在 `ask()` 方法开始处添加：

```python
async def ask(self, req: ChatRequest, parent_run=None):
    # 第 1 行：生成追踪 ID
    trace_id = str(uuid4())
    
    # 第 2-10 行：调用意图分类
    intent_result = await self._classify_intent(req.query)
    intent_classification = IntentClassification(
        intent=intent_result.get("intent", "unknown"),
        confidence=intent_result.get("confidence", 0.0),
        category=intent_result.get("category", ""),
    )
    
    # ... 你现有的逻辑 ...
    
    # 修改返回语句：
    return ChatResponse(
        response=final_response,
        intent=intent_classification.intent,           # ✨ 新增
        intent_confidence=intent_classification.confidence,  # ✨ 新增
        trace_id=trace_id,                               # ✨ 新增
    )
```

#### 3.3 添加新方法

在 `ChatService` 类中添加这个新方法：

```python
async def _classify_intent(self, query: str) -> Dict[str, Any]:
    """分类用户意图"""
    try:
        prompt = f"""分析用户查询的意图，返回 JSON:
        
查询: {query}

返回 JSON (仅返回 JSON，无其他内容):
{{
    "intent": "意图类型",
    "confidence": 0.8,
    "category": "retrieve|create|update|delete",
    "entities": {{}},
    "reasoning": "理由"
}}"""
        
        response = await self.model_client.agenerate(
            input=prompt,
            tags=["intent_classification"]
        )
        result = json.loads(response.generations[0].text)
        return result
    except Exception as e:
        logger.error(f"Intent classification failed: {e}")
        return {
            "intent": "unknown",
            "confidence": 0.0,
            "category": "unknown",
            "entities": {},
            "reasoning": f"Error: {str(e)}",
        }
```

**完成！** 这个方法就 30 行代码。

---

## ✅ 验证

### 启动应用

```bash
cd examples/enterprise_alert_agent
python -m uvicorn app.main:app --reload
```

**看到这样的输出表示成功：**
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete
```

### 测试请求

在另一个终端执行：

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "查询客户李明的订单", "business_context": "CRM"}'
```

### 期望响应

```json
{
  "response": "客户李明的订单详情...",
  "intent": "query_customer_orders",
  "intent_confidence": 0.87,
  "trace_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**关键看这 3 个新字段：**
- ✅ `intent` - 不是 "unknown"
- ✅ `intent_confidence` - 0-1 之间
- ✅ `trace_id` - UUID 格式

---

## 📋 快速检查清单

完成后检查：

- [ ] 创建了 `app/infrastructure/agent/a2a_protocol.py`
- [ ] 文件包含 `IntentClassification` 和 `A2AMessage` 类
- [ ] 修改了 `app/schemas/chat.py` 中的 `ChatResponse`
- [ ] 修改了 `app/application/services/chat_service.py`
- [ ] 添加了 `_classify_intent()` 方法
- [ ] 应用能启动
- [ ] 请求返回 200 OK
- [ ] 响应包含 `intent` 和 `intent_confidence`
- [ ] 日志显示意图分类过程

---

## 🎯 完成标志

当你看到这样的响应时，V1 完成了：

```json
{
  "response": "...",
  "intent": "query_customer_info",
  "intent_confidence": 0.92,
  "trace_id": "xxx-xxx-xxx"
}
```

🎉 **恭喜！** V1 版本实现完成！

---

## 📚 详细文档

需要更多细节？查看：
- **V1_NEW_FILES.md** - 新文件完整代码
- **V1_MODIFICATIONS.md** - 修改点详细说明
- **V1_CHECKLIST.md** - 完整检查清单

---

## 🚀 下一步

V1 完成后，下一步可以是：

### V2 版本 (添加工具选择理由)
- 显示系统选择了哪个工具
- 显示选择理由
- 显示备选工具

### V3 版本 (多任务并发)
- 复杂查询自动拆解为多个子任务
- 显示任务拆解结果
- 并发执行多个子任务

### V4 版本 (重试和降级)
- 自动重试失败的任务
- 执行降级策略
- 人工接管流程

---

## 💡 Tips

1. **LLM 调用慢？** 
   - 这是正常的，意图分类需要调用 LLM
   - 可以考虑使用缓存优化后续版本

2. **Intent 总是 "unknown"？**
   - 检查 LLM 返回的 JSON 格式
   - 在 `_classify_intent()` 中添加 print 调试
   - 尝试更清晰的提示词

3. **想跳过 V2/V3 直接做 V4？**
   - 完全可以！每个版本是独立的
   - 只需按需要选择和组合

---

**准备好了？立即开始 V1 版本实现！** 🚀

需要帮助？查看完整文档或检查清单。
