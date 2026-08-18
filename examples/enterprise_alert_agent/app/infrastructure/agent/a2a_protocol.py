"""A2A 协议 - V1 基础版本 (仅包含意图分类)"""

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Enum


class MessageType(str, Enum):
    """消息类型"""

    INTENT_CLASSIFICATION = "intent_classification"
    # 其他消息类型可以在此处添加
    TASK_REQUEST = "task_request"
    TASK_RESPONSE = "task_response"
    TASK_DECOMPOSITION = "task_decomposition"
    MULTI_TASK_EXECUTION = "multi_task_execution"


class MessagePriority(str, Enum):
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
    entities: dict[str, Any] | None = None
    reasoning: str = ""
    """分类理由，供日志和调试使用"""

    def __post_init__(self):
        if not (0 <= self.confidence <= 1):
            raise ValueError(f"Confidence must be between 0 and 1, got {self.confidence}")


@dataclass
class A2AMessage:
    message_type: MessageType
    """消息类型"""

    payload: dict[str, Any]
    """消息内容"""

    conversation_id: str | None = None
    """对话ID，用于追踪"""

    trace_id: str | None = None
    """追踪ID，默认生成 UUID"""

    priority: MessagePriority = MessagePriority.NORMAL  # type: ignore
    """消息优先级"""

    timestamp: datetime | None = None
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


@dataclass
class SubTask:
    """子任务"""

    task_id: str
    """子任务 ID"""
    description: str
    """子任务描述"""
    preferred_tool: str = ""
    depends_on: list[str] = field(default_factory=list)
    """子任务优先级，数值越大优先级越高"""
    priority: int = 0
    """分配的智能体 ID"""
    assigned_agent_id: str = ""


@dataclass
class TaskDecomposition:
    """任务分解"""

    subtasks: list[SubTask] = field(default_factory=list)
    """子任务列表"""
    parallel_groups: list[list[str]] = field(default_factory=list)
    """并行组"""
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    """依赖关系"""
    strategy: str = "parallel_first"


@dataclass
class FaultDiagnosisInfo:
    """故障诊断信息（在响应中返回）"""

    fault_id: str  # 故障ID
    fault_type: str  # 故障类型
    severity: str  # 严重程度
    root_cause: str  # 根因分析
    recovery_suggestions: list[str]  # 恢复建议
    retry_feasible: bool  # 是否可重试


@dataclass
class ParallelTaskResult:
    completed_tasks: int
    """已完成的任务数"""
    failed_tasks: int
    """失败的任务数"""
    total_tasks: int
    """总任务数"""
    total_time: float
    """总耗时"""
    tools_used: list[str] = field(default_factory=list)
    """使用的工具列表"""
    task_outputs: dict[str, str] = field(default_factory=dict)
    """任务输出"""
    failed_task_ids: list[str] = field(default_factory=list)
    """失败的任务 ID 列表"""
    task_agent_mapping: dict[str, str] = field(default_factory=dict)
    """任务与智能体的映射"""
    task_status_mapping: dict[str, str] = field(default_factory=dict)
    """任务状态映射"""
    execute_batches: list[list[str]] = field(default_factory=list)
    """执行批次"""
    skipped_task_ids: list[str] = field(default_factory=list)
    """被跳过的任务 ID 列表"""
    fault_diagnostics: dict[str, FaultDiagnosisInfo] = field(default_factory=dict)
    """故障诊断信息映射 (task_id -> diagnosis)"""


@dataclass
class AgentTaskExecutionRequest:
    task_id: str
    query: str
    agent_id: str
    preferred_tool: str = ""
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentTaskExecutionResult:
    task_id: str
    agent_id: str
    success: bool
    output: str
    error_type: str = ""
    latency_ms: float = 0.0


@dataclass
class AgentHealthState:
    agent_id: str
    consecutive_failures: int = 0
    is_open: bool = False  # True = 熔断打开，拒绝调用
    opened_at: float = 0.0  # 熔断打开时间戳


@dataclass
class CircuitBreakerEvent:
    agent_id: str
    event: str  # "opened" | "recovered" | "fallback_switched"
    fallback_agent_id: str = ""
    timestamp: float = field(default_factory=lambda: time.perf_counter())


@dataclass
class ManualInterventionRequest:
    """人工干预请求"""

    task_id: str  # 目标任务ID
    intervention_type: str  # "retry", "skip", "modify_params", "abort"
    retry_params: dict = field(default_factory=dict)  # 用于 modify_params
    skip_reason: str = ""  # 跳过理由
    user_id: str = ""  # 执行干预的用户ID
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ManualInterventionResult:
    """人工干预结果"""

    intervention_id: str
    task_id: str
    success: bool
    output: str = ""  # 如果重试成功，返回结果
    error_message: str = ""  # 如果失败，返回错误
    elapsed_time_ms: float = 0.0
