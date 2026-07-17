"""故障诊断类型定义"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

class FaultType(str, Enum):
    """故障类型"""
    TOOL_NOT_FOUND = "tool_not_found"           # 工具不存在
    TOOL_EXECUTION_ERROR = "tool_execution_error"  # 工具执行失败
    TOOL_TIMEOUT = "tool_timeout"               # 工具超时
    AGENT_CIRCUIT_OPEN = "agent_circuit_open"   # 智能体熔断打开
    AUTH_ERROR = "auth_error"                   # 认证错误
    NETWORK_ERROR = "network_error"             # 网络错误
    RATE_LIMIT = "rate_limit"                   # 速率限制
    DEPENDENCY_FAILED = "dependency_failed"     # 依赖任务失败
    TIMEOUT = "timeout"                         # 通用超时
    UNKNOWN = "unknown"                         # 未知错误
class FaultSeverity(str, Enum):
    """故障严重性"""
    LOW = "low"                 # 低 - 可自动恢复
    MEDIUM = "medium"           # 中 - 需要备选方案
    HIGH = "high"               # 高 - 需要用户确认
    CRITICAL = "critical"       # 严重 - 需要立即告警
@dataclass
class FaultDiagnosis:
    """故障诊断结果"""
    fault_id: str                           # 故障诊断ID
    fault_type: FaultType                   # 故障类型
    severity: FaultSeverity                 # 严重程度
    root_cause: str                         # 根因分析 (300字以内)
    affected_tasks: list[str]               # 受影响的任务ID列表
    recovery_suggestions: list[str]         # 恢复建议列表
    retry_feasible: bool                    # 是否可以重试
    estimated_recovery_time: float  # 预计恢复时间
    retry_recommendation: str = ""          # 重试建议 ("immediate", "wait_30s", "skip")
    timestamp: datetime = field(default_factory=datetime.utcnow)
    context: dict = field(default_factory=dict)  # 诊断上下文

@dataclass
class RecoveryStep:
    """恢复步骤"""
    step_id: str                    # 步骤ID
    description: str                # 描述
    action_type: str                # "retry", "skip", "fallback", "manual"
    params: dict = field(default_factory=dict)  # 参数    # 后续步骤
@dataclass
class FaultContext:
    """故障诊断的上下文信息"""
    request_id: str                 # 请求ID
    task_id: str                    # 任务ID
    agent_id: str                   # 智能体ID
    tool_name: str                  # 工具名称
    error_message: str              # 错误消息
    error_type: str                 # 错误类型
    retry_count: int = 0            # 重试次数
    elapsed_time_ms: float = 0.0    # 已耗时
