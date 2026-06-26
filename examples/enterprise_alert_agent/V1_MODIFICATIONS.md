

# V1 版本 - 现有文件修改

## 📝 需要修改 2 个现有文件

---

## ✏️ 修改点 1: app/schemas/chat.py

### 步骤
1. 打开文件: `app/schemas/chat.py`
2. 找到 `ChatResponse` 类
3. 添加以下 3 个字段

### 原始代码
```python
class ChatResponse(BaseModel):
    response: str
    context: Optional[Dict[str, Any]] = None
    # ... 其他现有字段
```

### 修改后代码
```python
from typing import Optional, Dict, Any

class ChatResponse(BaseModel):
    response: str
    context: Optional[Dict[str, Any]] = None
    # ... 其他现有字段
    
    # ===== V1 新增字段 =====
    intent: Optional[str] = None
    """用户意图分类，如 'query_customer_info'"""
    
    intent_confidence: Optional[float] = None
    """意图分类的置信度，0-1 之间"""
    
    trace_id: Optional[str] = None
    """追踪 ID，用于日志关联"""
    # ===== V1 新增字段结束 =====
```

### 修改说明
- `intent`: 分类的意图类型（字符串）
- `intent_confidence`: 置信度（0.0-1.0 的浮点数）
- `trace_id`: 用于追踪请求的 UUID 字符串

---

## ✏️ 修改点 2: app/application/services/chat_service.py

### 2.1 添加导入 (在文件顶部)

```python
# 在现有导入后添加：
from typing import Optional, Dict, Any
import json
import logging
from uuid import uuid4
from app.infrastructure.agent.a2a_protocol import (
    IntentClassification,
    MessageType,
)

logger = logging.getLogger(__name__)
```

### 2.2 修改 ask() 方法

#### 原始代码结构
```python
class ChatService:
    async def ask(self, req: ChatRequest, parent_run=None):
        # 现有逻辑...
        
        result = {"response": final_response}
        return result
```

#### 修改后代码 (在 ask() 方法中)

```python
class ChatService:
    async def ask(self, req: ChatRequest, parent_run=None):
        # 生成追踪 ID
        trace_id = str(uuid4())
        logger.info(f"[{trace_id}] Processing query: {req.query}")
        
        # ===== V1 步骤 1: 意图分类 =====
        intent_result = await self._classify_intent(req.query)
        intent_classification = IntentClassification(
            intent=intent_result.get("intent", "unknown"),
            confidence=intent_result.get("confidence", 0.0),
            category=intent_result.get("category", ""),
            entities=intent_result.get("entities", {}),
            reasoning=intent_result.get("reasoning", ""),
        )
        logger.info(
            f"[{trace_id}] Intent classification: {intent_classification.intent} "
            f"(confidence: {intent_classification.confidence})"
        )
        # ===== V1 步骤 1 结束 =====
        
        # 现有逻辑...
        final_response = "..."
        
        # ===== V1 步骤 2: 构建响应 =====
        result = ChatResponse(
            response=final_response,
            context={},  # 或现有值
            intent=intent_classification.intent,                    # ✅ 新增
            intent_confidence=intent_classification.confidence,      # ✅ 新增
            trace_id=trace_id,                                       # ✅ 新增
        )
        logger.info(f"[{trace_id}] Response sent successfully")
        return result
        # ===== V1 步骤 2 结束 =====
```

### 2.3 添加新方法 _classify_intent() (在 ChatService 类中)

```python
class ChatService:
    # ... 其他方法 ...
    
    async def _classify_intent(self, query: str) -> Dict[str, Any]:
        """
        分类用户意图
        
        Args:
            query: 用户输入的查询
            
        Returns:
            包含意图分类结果的字典
        """
        try:
            # 调用 LLM 进行意图分类
            prompt = f"""请分析以下用户查询的意图，返回 JSON 格式结果。
            
查询: {query}

请返回以下 JSON 格式的结果（不要返回其他内容）:
{{
    "intent": "意图类型，如 query_customer_info, create_order, etc",
    "confidence": 0.8,
    "category": "retrieve|create|update|delete",
    "entities": {{"key": "value"}},
    "reasoning": "为什么这样分类"
}}"""
            
            # 调用你的 LLM (假设通过 self.llm 或 self.model_client)
            response = await self.model_client.agenerate(
                input=prompt,
                tags=["intent_classification"]
            )
            
            # 提取 JSON 结果
            response_text = response.generations[0].text
            result = json.loads(response_text)
            
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

---

## 📊 修改汇总

| 文件 | 修改点 | 代码行数 |
|-----|--------|---------|
| `app/schemas/chat.py` | 添加 3 个字段到 ChatResponse | +5行 |
| `app/application/services/chat_service.py` | 导入 (2行) + ask() 修改 (15行) + 新方法 (30行) | +50行 |
| **总计** | 2 个文件，2 个修改点 | **~55行** |

---

## ⚙️ 实现细节

### 关键点 1: 追踪 ID
```python
trace_id = str(uuid4())  # 每个请求都生成唯一 ID
# 用于：
# - 日志关联
# - 错误追踪
# - 用户查询
```

### 关键点 2: 意图分类
```python
intent_classification = IntentClassification(
    intent="query_customer_info",
    confidence=0.92,
    # ... 其他字段
)
# 返回给前端显示
```

### 关键点 3: 错误处理
```python
try:
    # 调用 LLM
except Exception as e:
    # 返回默认值而不是崩溃
    return {"intent": "unknown", "confidence": 0.0, ...}
```

---

## ✅ 修改检查清单

修改完成后，检查以下项目：

- [ ] `app/schemas/chat.py` 中 ChatResponse 包含 3 个新字段
- [ ] `app/application/services/chat_service.py` 中有新的导入
- [ ] `ask()` 方法调用 `_classify_intent()`
- [ ] `_classify_intent()` 方法已实现
- [ ] 返回的 ChatResponse 包含 intent、intent_confidence、trace_id
- [ ] 代码有异常处理
- [ ] 日志记录意图分类结果

---

## 🧪 测试方式

修改完成后测试：

```bash
# 1. 启动应用
cd examples/enterprise_alert_agent
python -m uvicorn app.main:app --reload

# 2. 发送测试请求
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "查询客户李明的信息", "business_context": "CRM"}'

# 3. 验证响应 (应该包含以下字段)
{
  "response": "...",
  "intent": "query_customer_info",
  "intent_confidence": 0.92,
  "trace_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

## 🔗 下一步

- 检查清单：**V1_CHECKLIST.md**
- 下一版本（V2）计划：**V2_MODIFICATIONS.md**
