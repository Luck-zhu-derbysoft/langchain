"""A2A 协议 - V1 基础版本 (仅包含意图分类)"""


from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
from sqlalchemy import Enum
import uuid

class MessageType(str,Enum):
    """消息类型"""

    INTENT_CLASSIFICATION = "intent_classification"
    # 其他消息类型可以在此处添加
    TASK_REQUEST = "task_request"
    TASK_RESPONSE = "task_response"

class MessagePriority(str,Enum):
    """消息优先级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    NORMAL = "normal"

@dataclass
class IntentClassification:
    """分类的意图类型，如 'query_customer_info'"""
    intent: str
    """置信度，范围 0-1"""
    confidence: float
    """意图分类，如 'retrieve', 'modify', 'create'"""
    category: str = ""
    """抽取的实体，如 {'customer_name': 'Li Ming'}"""
    entities: Optional[Dict[str, Any]] = None
    reasoning: str = ""
    """分类理由，供日志和调试使用"""
    def __post_init__(self):
        if not (0 <= self.confidence <= 1):
            raise ValueError(f"Confidence must be between 0 and 1, got {self.confidence}")

@dataclass
class A2AMessage:
    message_type: MessageType
    """消息类型"""

    payload: Dict[str, Any]
    """消息内容"""

    conversation_id: Optional[str] = None
    """对话ID，用于追踪"""

    trace_id: Optional[str] = None
    """追踪ID，默认生成 UUID"""

    priority: MessagePriority = MessagePriority.NORMAL # type: ignore
    """消息优先级"""

    timestamp: Optional[datetime] = None
    """时间戳，默认为当前时间"""
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()
        if self.trace_id is None:
            self.trace_id = str(uuid.uuid4())
        if not self.conversation_id:
            self.conversation_id = str(uuid.uuid4())





class A2AProtocol:
    """A2A 协议 - V1 基础版本 (仅包含意图分类)"""

    def __init__(self):
        self.version = "1.0"
        self.intent_classification = None

    def set_intent_classification(self, intent):
        """设置意图分类"""
        self.intent_classification = intent

    def get_intent_classification(self):
        """获取意图分类"""
        return self.intent_classification
@dataclass
class ToolSelection:
    """工具选择"""
    tool_name: str
    """工具名称"""
    confidence: float
    """置信度，范围 0-1"""
    fallback_tools: list[str] = field(default_factory=list)
    """备用工具"""
    reasoning: str = ""
    """选择理由"""
    agent_id: str = ""
    """智能体 ID"""
    def __post_init__(self):
        if not (0 <= self.confidence <= 1):
            raise ValueError(f"Confidence must be between 0 and 1, got {self.confidence}")


