from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class AlertTypes(str, Enum):
    """告警类型"""

    FAULT_ALERT = "fault_alert"  # 故障告警
    PERFORMANCE_ALERT = "performance_alert"  # 性能告警
    QUOTA_ALERT = "quota_alert"  # 配额告警
    CIRCUIT_BREAKER = "circuit_breaker"  # 熔断告警
    TASK_FAILURE = "task_failure"  # 任务失败告警


class AlertSeverity(str, Enum):
    """告警级别"""

    CRITICAL = "critical"  # 严重
    WARNING = "warning"  # 警告
    INFO = "info"  # 信息
    HIGH = "high"  # 高


@dataclass
class Alert:
    """告警事件"""

    alert_id: str  # 告警ID
    alert_type: AlertTypes  # 告警类型
    severity: AlertSeverity  # 严重程度
    title: str  # 告警标题
    message: str  # 告警消息
    affected_resource: str  # 受影响资源（如 task_id, agent_id）
    acknowledged_at: datetime | None = None
    context: dict = field(default_factory=dict)  # 上下文信息
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    acknowledged: bool = False  # 是否已确认
    acknowledged_by: str = ""  # 确认者
