# V1 版本 - 新增文件代码

## 📄 仅需创建 1 个新文件

### app/infrastructure/agent/a2a_protocol.py (50 行)

```python
"""A2A 协议 - V1 基础版本 (仅包含意图分类)"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any
from uuid import uuid4
from datetime import datetime


class MessageType(str, Enum):
    """消息类型"""
    INTENT_CLASSIFICATION = "intent_classification"
    TASK_REQUEST = "task_request"
    TASK_RESPONSE = "task_response"


class MessagePriority(str, Enum):
    """消息优先级"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class IntentClassification:
    """意图分类结果 - V1 核心数据结构"""
    
    intent: str
    """分类的意图类型，如 'query_customer_info'"""
    
    confidence: float
    """置信度，范围 0-1"""
    
    category: str = ""
    """意图分类，如 'retrieve', 'modify', 'create'"""
    
    entities: Optional[Dict[str, Any]] = None
    """抽取的实体，如 {'customer_name': 'Li Ming'}"""
    
    reasoning: str = ""
    """分类理由，供日志和调试使用"""
    
    def __post_init__(self):
        if not (0 <= self.confidence <= 1):
            raise ValueError(f"Confidence must be between 0 and 1, got {self.confidence}")


@dataclass
class A2AMessage:
    """A2A 基础消息结构 - V1 版本"""
    
    message_type: MessageType
    """消息类型"""
    
    payload: Dict[str, Any]
    """消息内容"""
    
    conversation_id: Optional[str] = None
    """对话ID，用于追踪"""
    
    trace_id: Optional[str] = None
    """追踪ID，默认生成 UUID"""
    
    priority: MessagePriority = MessagePriority.NORMAL
    """消息优先级"""
    
    timestamp: Optional[datetime] = None
    """时间戳，默认为当前时间"""
    
    def __post_init__(self):
        if not self.trace_id:
            self.trace_id = str(uuid4())
        if not self.timestamp:
            self.timestamp = datetime.now()
        if not self.conversation_id:
            self.conversation_id = str(uuid4())
```

---

## 📝 说明

### 这是什么？
- `IntentClassification`: 意图分类的数据结构
- `A2AMessage`: Agent-to-Agent 通信的基础消息格式
- `MessageType` 和 `MessagePriority`: 枚举定义

### 为什么这么简洁？
V1 只需要支持意图分类功能，其他类型在 V2、V3、V4 版本中逐步添加。

### 关键字段
- `IntentClassification.intent` - 意图类型
- `IntentClassification.confidence` - 置信度（0-1）
- `A2AMessage.trace_id` - 追踪 ID，用于日志关联

---

## ✅ 创建方式

1. 创建目录: `mkdir -p app/infrastructure/agent`
2. 创建文件: `app/infrastructure/agent/a2a_protocol.py`
3. 复制上面的代码
4. 保存文件

**就这么简单！** 这个文件只有 50 行代码，没有复杂逻辑。

---

## 🔗 后续关联

- V1 修改点见: **V1_MODIFICATIONS.md**
- V1 检查清单见: **V1_CHECKLIST.md**
