"""
多任务并发执行系统 - 完整代码清单
=====================================

本文件列出所有新增和修改的代码，可直接复制使用。
"""

# ============================================================================
# 新增文件 1: app/infrastructure/agent/__init__.py
# ============================================================================

"""Agent 基础设施模块 - A2A 协议与多任务支持"""

from app.infrastructure.agent.agent_signature import (
    AgentSignature,
    AgentCapability,
    AgentCredential,
    AgentPermission,
    PermissionScope,
    AgentRegistry,
)
from app.infrastructure.agent.a2a_protocol import (
    A2AMessage,
    MessageType,
    IntentClassification,
    ToolSelection,
    TaskDecomposition,
    TaskRequest,
    TaskResponse,
    ManualInterventionRequest,
    MetricsReport,
)
from app.infrastructure.agent.agent_runtime import (
    AgentRuntime,
    RetryPolicy,
    RetryStrategy,
    FallbackPolicy,
    FallbackStrategy,
)
from app.infrastructure.agent.agent_coordinator import (
    AgentCoordinator,
    MessageRouter,
    MultiAgentOrchestrator,
)
from app.infrastructure.agent.multi_task_executor import (
    MultiTaskExecutor,
    TaskExecutionContext,
    ParallelTaskResult,
)

__all__ = [
    "AgentSignature",
    "AgentCapability",
    "AgentCredential",
    "AgentPermission",
    "PermissionScope",
    "AgentRegistry",
    "A2AMessage",
    "MessageType",
    "IntentClassification",
    "ToolSelection",
    "TaskDecomposition",
    "TaskRequest",
    "TaskResponse",
    "ManualInterventionRequest",
    "MetricsReport",
    "AgentRuntime",
    "RetryPolicy",
    "RetryStrategy",
    "FallbackPolicy",
    "FallbackStrategy",
    "AgentCoordinator",
    "MessageRouter",
    "MultiAgentOrchestrator",
    "MultiTaskExecutor",
    "TaskExecutionContext",
    "ParallelTaskResult",
]


# ============================================================================
# 新增文件 2: app/infrastructure/agent/agent_signature.py
# ============================================================================

"""Agent 签名和能力声明系统 - A2A 协议基础"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional
import hashlib
from datetime import datetime
from pydantic import BaseModel, Field


class PermissionScope(str, Enum):
    """Agent 权限范围"""
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    ADMIN = "admin"


class AgentCapability(BaseModel):
    """Agent 能力声明"""
    name: str = Field(..., description="能力名称")
    description: str = Field(..., description="能力描述")
    input_schema: dict[str, Any] = Field(default_factory=dict, description="输入参数 schema")
    output_schema: dict[str, Any] = Field(default_factory=dict, description="输出参数 schema")
    version: str = Field(default="1.0.0", description="能力版本")
    tags: list[str] = Field(default_factory=list, description="能力标签")
    retry_policy: Optional[dict[str, Any]] = Field(default=None, description="重试策略")
    fallback_capability: Optional[str] = Field(default=None, description="降级能力")


class AgentCredential(BaseModel):
    """Agent 身份凭证"""
    agent_id: str = Field(..., description="Agent 唯一标识")
    agent_name: str = Field(..., description="Agent 名称")
    issued_at: datetime = Field(default_factory=datetime.utcnow, description="签发时间")
    expires_at: Optional[datetime] = Field(default=None, description="过期时间")
    issuer: str = Field(default="system", description="签发者")
    signature: str = Field(default="", description="凭证签名")


class AgentPermission(BaseModel):
    """Agent 权限配置"""
    agent_id: str = Field(..., description="Agent ID")
    scopes: list[PermissionScope] = Field(default_factory=list, description="权限范围")
    resource_tags: list[str] = Field(default_factory=list, description="可访问的资源标签")
    rate_limit: int = Field(default=100, description="每分钟请求限制")
    allowed_tools: list[str] = Field(default_factory=list, description="允许使用的工具")
    denied_tools: list[str] = Field(default_factory=list, description="禁止使用的工具")
    max_concurrent_tasks: int = Field(default=5, description="最大并发任务数")


class AgentSignature(BaseModel):
    """Agent 签名卡片 - 包含身份、能力、权限信息"""
    credential: AgentCredential = Field(..., description="身份凭证")
    capabilities: list[AgentCapability] = Field(default_factory=list, description="声明的能力")
    permissions: AgentPermission = Field(..., description="权限配置")
    endpoint: str = Field(..., description="Agent 服务端点")
    protocol_version: str = Field(default="1.0.0", description="A2A 协议版本")
    metadata: dict[str, Any] = Field(default_factory=dict, description="额外元数据")
    is_available: bool = Field(default=True, description="Agent 是否可用")
    last_heartbeat: datetime = Field(default_factory=datetime.utcnow, description="最后心跳时间")
    
    def get_capability(self, name: str) -> Optional[AgentCapability]:
        """获取指定能力"""
        for cap in self.capabilities:
            if cap.name == name:
                return cap
        return None
    
    def has_permission(self, scope: PermissionScope, tool_name: str = "") -> bool:
        """检查是否具有指定权限"""
        if scope not in self.permissions.scopes:
            return False
        if tool_name:
            if tool_name in self.permissions.denied_tools:
                return False
            if self.permissions.allowed_tools and tool_name not in self.permissions.allowed_tools:
                return False
        return True


class AgentRegistry:
    """Agent 签名注册和管理"""
    
    def __init__(self):
        self._agents: dict[str, AgentSignature] = {}
    
    def register(self, signature: AgentSignature) -> bool:
        """注册 Agent 签名"""
        self._agents[signature.credential.agent_id] = signature
        return True
    
    def get(self, agent_id: str) -> Optional[AgentSignature]:
        """获取 Agent 签名"""
        return self._agents.get(agent_id)
    
    def list_by_capability(self, capability_name: str) -> list[AgentSignature]:
        """列出具有指定能力的 Agent"""
        return [
            agent for agent in self._agents.values()
            if agent.get_capability(capability_name) is not None
        ]
    
    def list_available(self) -> list[AgentSignature]:
        """列出所有可用 Agent"""
        return [agent for agent in self._agents.values() if agent.is_available]
    
    def verify_permission(self, agent_id: str, scope: PermissionScope, tool_name: str = "") -> bool:
        """验证 Agent 权限"""
        agent = self.get(agent_id)
        if not agent:
            return False
        return agent.has_permission(scope, tool_name)


# ============================================================================
# 新增文件 3: app/infrastructure/agent/a2a_protocol.py
# ============================================================================

"""A2A Agent-to-Agent 协议 - 通信消息格式"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4
from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """A2A 消息类型"""
    INTENT_CLASSIFICATION = "intent.classification"
    TOOL_SELECTION = "tool.selection"
    DECOMPOSITION = "task.decomposition"
    TASK_REQUEST = "task.request"
    TASK_RESPONSE = "task.response"
    TASK_CANCEL = "task.cancel"
    MANUAL_INTERVENTION = "manual.intervention"
    INTERVENTION_RESPONSE = "manual.response"
    HEARTBEAT = "heartbeat"
    METRIC_REPORT = "metric.report"
    ERROR_REPORT = "error.report"


class MessagePriority(str, Enum):
    """消息优先级"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class IntentClassification(BaseModel):
    """意图分类结果"""
    intent: str = Field(..., description="分类的意图")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度")
    category: str = Field(default="", description="意图类别")
    entities: dict[str, Any] = Field(default_factory=dict, description="提取的实体")
    reasoning: str = Field(default="", description="分类推理过程")


class ToolSelection(BaseModel):
    """工具选择决策"""
    tool_name: str = Field(..., description="选择的工具名称")
    agent_id: str = Field(..., description="执行工具的 Agent ID")
    confidence: float = Field(..., ge=0.0, le=1.0, description="选择置信度")
    fallback_tools: list[str] = Field(default_factory=list, description="备选工具")
    reasoning: str = Field(default="", description="选择理由")
    estimated_cost: Optional[float] = Field(default=None, description="估计成本/代价")


class SubTask(BaseModel):
    """子任务定义"""
    task_id: str = Field(default_factory=lambda: str(uuid4()), description="子任务ID")
    name: str = Field(..., description="子任务名称")
    description: str = Field(default="", description="子任务描述")
    task_type: str = Field(default="", description="任务类型")
    required_tool: Optional[str] = Field(default=None, description="需要的工具")
    parameters: dict[str, Any] = Field(default_factory=dict, description="任务参数")
    priority: int = Field(default=0, description="优先级，0=正常，值越大越优先")


class TaskDecomposition(BaseModel):
    """任务拆解结果"""
    main_task: str = Field(..., description="主任务")
    subtasks: list[SubTask] = Field(default_factory=list, description="子任务列表")
    dependencies: dict[str, list[str]] = Field(default_factory=dict, description="任务依赖关系")
    execution_order: list[str] = Field(default_factory=list, description="执行顺序")
    parallel_groups: list[list[str]] = Field(default_factory=list, description="可并行执行的任务组")
    estimated_duration: Optional[float] = Field(default=None, description="估计耗时（秒）")


class A2AMessage(BaseModel):
    """A2A 协议消息"""
    message_id: str = Field(default_factory=lambda: str(uuid4()), description="消息唯一ID")
    message_type: MessageType = Field(..., description="消息类型")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="消息时间戳")
    priority: MessagePriority = Field(default=MessagePriority.NORMAL, description="消息优先级")
    from_agent: str = Field(..., description="来源 Agent ID")
    to_agent: Optional[str] = Field(default=None, description="目标 Agent ID（可选）")
    conversation_id: str = Field(default="", description="会话 ID，用于追踪")
    parent_message_id: Optional[str] = Field(default=None, description="父消息 ID")
    payload: dict[str, Any] = Field(default_factory=dict, description="消息负载")
    metadata: dict[str, Any] = Field(default_factory=dict, description="元数据")
    signature: str = Field(default="", description="消息签名")
    trace_id: str = Field(default="", description="链路追踪 ID")


class TaskRequest(BaseModel):
    """Agent 之间的任务请求"""
    task_id: str = Field(default_factory=lambda: str(uuid4()), description="任务ID")
    task_name: str = Field(..., description="任务名称")
    agent_id: str = Field(..., description="执行 Agent ID")
    parameters: dict[str, Any] = Field(default_factory=dict, description="任务参数")
    timeout: int = Field(default=30, description="任务超时（秒）")
    retry_policy: Optional[dict[str, Any]] = Field(default=None, description="重试策略")
    priority: MessagePriority = Field(default=MessagePriority.NORMAL, description="任务优先级")
    conversation_id: str = Field(default="", description="会话ID")
    parent_task_id: Optional[str] = Field(default=None, description="父任务ID")


class TaskResponse(BaseModel):
    """Agent 任务执行结果"""
    task_id: str = Field(..., description="任务ID")
    success: bool = Field(..., description="是否成功")
    result: Optional[dict[str, Any]] = Field(default=None, description="执行结果")
    error: Optional[str] = Field(default=None, description="错误信息")
    execution_time: float = Field(default=0.0, description="执行时间（秒）")
    retried: bool = Field(default=False, description="是否重试过")
    fallback_used: Optional[str] = Field(default=None, description="使用的降级方案")
    trace_id: str = Field(default="", description="链路追踪ID")


class ManualInterventionRequest(BaseModel):
    """人工接管请求"""
    intervention_id: str = Field(default_factory=lambda: str(uuid4()), description="接管ID")
    trigger_agent: str = Field(..., description="触发接管的 Agent ID")
    reason: str = Field(..., description="接管原因")
    context: dict[str, Any] = Field(default_factory=dict, description="上下文信息")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="时间戳")
    suggested_action: Optional[str] = Field(default=None, description="建议的人工操作")


class MetricsReport(BaseModel):
    """Agent 指标上报"""
    agent_id: str = Field(..., description="Agent ID")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="上报时间")
    intent_accuracy: Optional[float] = Field(default=None, description="意图识别准确率")
    intent_attempts: int = Field(default=0, description="意图识别尝试次数")
    tool_call_count: int = Field(default=0, description="工具调用总数")
    tool_success_rate: float = Field(default=0.0, description="工具成功率")
    tool_error_count: int = Field(default=0, description="工具错误次数")
    manual_intervention_count: int = Field(default=0, description="人工接管次数")
    auto_recovery_count: int = Field(default=0, description="自动恢复次数")
    avg_response_time: float = Field(default=0.0, description="平均响应时间（秒）")
    max_response_time: float = Field(default=0.0, description="最大响应时间（秒）")
    metadata: dict[str, Any] = Field(default_factory=dict, description="额外指标")


# ============================================================================
# 新增文件 4: app/infrastructure/agent/agent_runtime.py
# ============================================================================

"""Agent 运行时 - 包含重试/降级、人工接管、指标上报"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Optional, Dict, List
import random

from app.infrastructure.agent.a2a_protocol import (
    TaskRequest,
    TaskResponse,
    ManualInterventionRequest,
    MetricsReport,
)


logger = logging.getLogger(__name__)


class RetryStrategy(str, Enum):
    """重试策略"""
    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    FIXED = "fixed"
    NO_RETRY = "no_retry"


class FallbackStrategy(str, Enum):
    """降级策略"""
    USE_CACHE = "use_cache"
    USE_DEFAULT = "use_default"
    USE_ALTERNATIVE_TOOL = "use_alt"
    MANUAL_ESCALATION = "escalate"


@dataclass
class RetryPolicy:
    """重试策略配置"""
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    max_retries: int = 3
    initial_delay: float = 1.0
    max_delay: float = 30.0
    backoff_factor: float = 2.0
    jitter: bool = True
    
    def calculate_delay(self, attempt: int) -> float:
        """计算重试延迟"""
        if self.strategy == RetryStrategy.EXPONENTIAL:
            delay = self.initial_delay * (self.backoff_factor ** attempt)
        elif self.strategy == RetryStrategy.LINEAR:
            delay = self.initial_delay * (attempt + 1)
        elif self.strategy == RetryStrategy.FIXED:
            delay = self.initial_delay
        else:
            return 0.0
        
        delay = min(delay, self.max_delay)
        
        if self.jitter:
            delay *= (0.5 + random.random())
        
        return delay


@dataclass
class FallbackPolicy:
    """降级策略配置"""
    strategy: FallbackStrategy = FallbackStrategy.USE_DEFAULT
    alternative_tool: Optional[str] = None
    default_value: Optional[Any] = None
    cache_key: Optional[str] = None
    enable_manual_escalation: bool = True


@dataclass
class ToolExecutionContext:
    """工具执行上下文"""
    tool_name: str
    agent_id: str
    task_id: str
    conversation_id: str
    parameters: Dict[str, Any]
    
    attempt: int = 0
    start_time: datetime = field(default_factory=datetime.utcnow)
    last_error: Optional[Exception] = None
    
    trace_id: str = ""
    parent_trace_id: Optional[str] = None


class AgentRuntime:
    """Agent 运行时 - 管理任务执行、重试、降级、人工接管"""
    
    def __init__(
        self,
        agent_id: str,
        default_retry_policy: Optional[RetryPolicy] = None,
        default_fallback_policy: Optional[FallbackPolicy] = None,
    ):
        self.agent_id = agent_id
        self.default_retry_policy = default_retry_policy or RetryPolicy()
        self.default_fallback_policy = default_fallback_policy or FallbackPolicy()
        
        self._running_tasks: Dict[str, Dict[str, Any]] = {}
        self._metrics = MetricsReport(agent_id=agent_id)
        self._manual_interventions: Dict[str, ManualInterventionRequest] = {}
        self._execution_cache: Dict[str, Any] = {}
    
    async def execute_task(
        self,
        task: TaskRequest,
        tool_executor: Callable,
        *,
        retry_policy: Optional[RetryPolicy] = None,
        fallback_policy: Optional[FallbackPolicy] = None,
        timeout: Optional[float] = None,
    ) -> TaskResponse:
        """执行任务 - 支持重试和降级"""
        
        retry_policy = retry_policy or self.default_retry_policy
        fallback_policy = fallback_policy or self.default_fallback_policy
        timeout = timeout or task.timeout
        
        context = ToolExecutionContext(
            tool_name=task.task_name,
            agent_id=self.agent_id,
            task_id=task.task_id,
            conversation_id=task.conversation_id,
            parameters=task.parameters,
            trace_id="",
        )
        
        self._running_tasks[task.task_id] = {
            "status": "running",
            "start_time": datetime.utcnow(),
            "context": context,
        }
        
        logger.info(
            "Task execution start: task_id=%s tool=%s agent=%s",
            task.task_id, task.task_name, self.agent_id
        )
        
        try:
            result = await self._execute_with_retry(
                context=context,
                tool_executor=tool_executor,
                retry_policy=retry_policy,
                fallback_policy=fallback_policy,
                timeout=timeout,
            )
            
            return TaskResponse(
                task_id=task.task_id,
                success=True,
                result=result,
                execution_time=self._get_execution_time(context),
                trace_id=context.trace_id,
            )
        
        except Exception as exc:
            logger.exception("Task execution failed: task_id=%s", task.task_id)
            
            fallback_result = await self._try_fallback(
                context=context,
                error=exc,
                fallback_policy=fallback_policy,
            )
            
            if fallback_result is not None:
                return TaskResponse(
                    task_id=task.task_id,
                    success=True,
                    result=fallback_result,
                    fallback_used=fallback_policy.strategy.value,
                    execution_time=self._get_execution_time(context),
                    trace_id=context.trace_id,
                )
            
            if fallback_policy.enable_manual_escalation:
                intervention = await self._request_manual_intervention(
                    agent_id=self.agent_id,
                    task_id=task.task_id,
                    error=exc,
                    context_info=context.__dict__,
                )
                logger.warning(
                    "Manual intervention requested: intervention_id=%s task_id=%s",
                    intervention.intervention_id, task.task_id
                )
            
            return TaskResponse(
                task_id=task.task_id,
                success=False,
                error=str(exc),
                execution_time=self._get_execution_time(context),
                trace_id=context.trace_id,
            )
        
        finally:
            if task.task_id in self._running_tasks:
                del self._running_tasks[task.task_id]
    
    async def _execute_with_retry(
        self,
        context: ToolExecutionContext,
        tool_executor: Callable,
        retry_policy: RetryPolicy,
        fallback_policy: FallbackPolicy,
        timeout: float,
    ) -> Any:
        """执行工具 - 包含重试逻辑"""
        
        last_error = None
        
        for attempt in range(retry_policy.max_retries + 1):
            context.attempt = attempt
            
            try:
                result = await asyncio.wait_for(
                    self._run_tool(context, tool_executor),
                    timeout=timeout
                )
                
                if attempt > 0:
                    self._metrics.auto_recovery_count += 1
                
                logger.info(
                    "Tool execution success: tool=%s attempt=%d",
                    context.tool_name, attempt
                )
                
                return result
            
            except asyncio.TimeoutError as e:
                last_error = e
                logger.warning(
                    "Tool execution timeout: tool=%s attempt=%d timeout=%s",
                    context.tool_name, attempt, timeout
                )
            
            except Exception as e:
                last_error = e
                context.last_error = e
                logger.warning(
                    "Tool execution error: tool=%s attempt=%d error=%s",
                    context.tool_name, attempt, str(e)
                )
            
            if attempt < retry_policy.max_retries:
                delay = retry_policy.calculate_delay(attempt)
                logger.info("Retrying after %.2f seconds...", delay)
                await asyncio.sleep(delay)
            
            self._metrics.tool_call_count += 1
            self._metrics.tool_error_count += 1
        
        if last_error:
            raise last_error
        
        raise RuntimeError("Tool execution failed with unknown error")
    
    async def _run_tool(
        self,
        context: ToolExecutionContext,
        tool_executor: Callable,
    ) -> Any:
        """执行工具"""
        
        if asyncio.iscoroutinefunction(tool_executor):
            result = await tool_executor(context.parameters)
        else:
            result = tool_executor(context.parameters)
        
        return result
    
    async def _try_fallback(
        self,
        context: ToolExecutionContext,
        error: Exception,
        fallback_policy: FallbackPolicy,
    ) -> Optional[Any]:
        """尝试降级"""
        
        logger.info(
            "Attempting fallback: tool=%s strategy=%s",
            context.tool_name, fallback_policy.strategy.value
        )
        
        if fallback_policy.strategy == FallbackStrategy.USE_DEFAULT:
            return fallback_policy.default_value
        
        elif fallback_policy.strategy == FallbackStrategy.USE_CACHE:
            if fallback_policy.cache_key:
                return self._execution_cache.get(fallback_policy.cache_key)
        
        return None
    
    async def _request_manual_intervention(
        self,
        agent_id: str,
        task_id: str,
        error: Exception,
        context_info: Dict[str, Any],
    ) -> ManualInterventionRequest:
        """请求人工接管"""
        
        request = ManualInterventionRequest(
            trigger_agent=agent_id,
            reason=f"Tool execution failed: {str(error)}",
            context={
                "task_id": task_id,
                "error_type": type(error).__name__,
                "error_message": str(error),
                **context_info,
            },
        )
        
        self._manual_interventions[request.intervention_id] = request
        self._metrics.manual_intervention_count += 1
        
        return request
    
    def _get_execution_time(self, context: ToolExecutionContext) -> float:
        """获取执行时间"""
        return (datetime.utcnow() - context.start_time).total_seconds()
    
    def get_metrics(self) -> MetricsReport:
        """获取当前指标"""
        return self._metrics
    
    def cache_execution_result(self, key: str, result: Any, ttl: int = 3600):
        """缓存执行结果"""
        self._execution_cache[key] = {
            "result": result,
            "timestamp": datetime.utcnow(),
            "ttl": ttl,
        }
    
    def get_running_tasks(self) -> List[Dict[str, Any]]:
        """获取运行中的任务"""
        return list(self._running_tasks.values())


# ============================================================================
# 新增文件 5: app/infrastructure/agent/agent_coordinator.py
# ============================================================================

"""多Agent 协调系统 - 管理 Agent 之间的通信和任务编排"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from app.infrastructure.agent.a2a_protocol import (
    A2AMessage,
    MessageType,
    IntentClassification,
    ToolSelection,
    TaskDecomposition,
    TaskRequest,
    TaskResponse,
    MetricsReport,
)
from app.infrastructure.agent.agent_signature import AgentRegistry, AgentSignature, PermissionScope
from app.infrastructure.agent.agent_runtime import AgentRuntime


logger = logging.getLogger(__name__)


class MessageRouter:
    """A2A 消息路由器 - 负责消息的发送和接收"""
    
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self._message_handlers: Dict[MessageType, List[Callable]] = {}
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._message_history: List[A2AMessage] = []
        self._max_history = 1000
    
    def register_handler(
        self,
        message_type: MessageType,
        handler: Callable,
    ):
        """注册消息处理器"""
        if message_type not in self._message_handlers:
            self._message_handlers[message_type] = []
        self._message_handlers[message_type].append(handler)
    
    async def send_message(self, message: A2AMessage) -> bool:
        """发送消息"""
        message.from_agent = self.agent_id
        message.timestamp = datetime.utcnow()
        self._add_to_history(message)
        logger.info(
            "Sending message: type=%s to_agent=%s conversation=%s",
            message.message_type.value, message.to_agent, message.conversation_id
        )
        return True
    
    async def receive_message(self, message: A2AMessage):
        """接收消息"""
        self._add_to_history(message)
        await self._message_queue.put(message)
        logger.info(
            "Received message: type=%s from_agent=%s",
            message.message_type.value, message.from_agent
        )
        await self._dispatch_message(message)
    
    async def _dispatch_message(self, message: A2AMessage):
        """分发消息到相应的处理器"""
        handlers = self._message_handlers.get(message.message_type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(message)
                else:
                    handler(message)
            except Exception as e:
                logger.exception("Message handler error: %s", e)
    
    def _add_to_history(self, message: A2AMessage):
        """添加到消息历史"""
        self._message_history.append(message)
        if len(self._message_history) > self._max_history:
            self._message_history = self._message_history[-self._max_history:]
    
    def get_conversation_history(self, conversation_id: str) -> List[A2AMessage]:
        """获取会话历史"""
        return [
            msg for msg in self._message_history
            if msg.conversation_id == conversation_id
        ]


class AgentCoordinator:
    """多Agent 协调器 - 管理任务分解、意图分类、工具选择"""
    
    def __init__(
        self,
        primary_agent_id: str,
        agent_registry: AgentRegistry,
    ):
        self.primary_agent_id = primary_agent_id
        self.agent_registry = agent_registry
        self._router = MessageRouter(primary_agent_id)
        self._agent_runtimes: Dict[str, AgentRuntime] = {}
    
    def register_agent_runtime(self, agent_id: str, runtime: AgentRuntime):
        """注册 Agent 运行时"""
        self._agent_runtimes[agent_id] = runtime
    
    async def classify_intent(
        self,
        query: str,
        intent_classifier: Callable,
        conversation_id: str = "",
    ) -> IntentClassification:
        """分类意图"""
        logger.info("Classifying intent: query=%s", query[:100])
        try:
            result = await self._call_with_async_support(intent_classifier, query)
            if isinstance(result, IntentClassification):
                classification = result
            else:
                classification = IntentClassification(
                    intent=result.get("intent", "unknown"),
                    confidence=result.get("confidence", 0.0),
                    category=result.get("category", ""),
                    entities=result.get("entities", {}),
                    reasoning=result.get("reasoning", ""),
                )
            logger.info(
                "Intent classified: intent=%s confidence=%.2f",
                classification.intent, classification.confidence
            )
            return classification
        except Exception as e:
            logger.exception("Intent classification failed")
            raise
    
    async def select_tools(
        self,
        intent: str,
        available_tools: List[Dict[str, Any]],
        tool_selector: Callable,
        conversation_id: str = "",
    ) -> ToolSelection:
        """选择合适的工具"""
        logger.info("Selecting tools: intent=%s tools_count=%d", intent, len(available_tools))
        try:
            result = await self._call_with_async_support(tool_selector, intent, available_tools)
            if isinstance(result, ToolSelection):
                selection = result
            else:
                selection = ToolSelection(
                    tool_name=result.get("tool_name", ""),
                    agent_id=result.get("agent_id", self.primary_agent_id),
                    confidence=result.get("confidence", 0.0),
                    fallback_tools=result.get("fallback_tools", []),
                    reasoning=result.get("reasoning", ""),
                )
            
            agent_signature = self.agent_registry.get(selection.agent_id)
            if agent_signature:
                has_permission = agent_signature.has_permission(PermissionScope.EXECUTE, selection.tool_name)
                if not has_permission and selection.fallback_tools:
                    selection.tool_name = selection.fallback_tools[0]
            
            logger.info(
                "Tool selected: tool=%s reasoning=%s",
                selection.tool_name, selection.reasoning[:100] if selection.reasoning else ""
            )
            return selection
        except Exception as e:
            logger.exception("Tool selection failed")
            raise
    
    async def decompose_task(
        self,
        query: str,
        intent: str,
        task_decomposer: Callable,
        conversation_id: str = "",
    ) -> TaskDecomposition:
        """拆解任务"""
        logger.info("Decomposing task: intent=%s", intent)
        try:
            result = await self._call_with_async_support(task_decomposer, query, intent)
            if isinstance(result, TaskDecomposition):
                decomposition = result
            else:
                decomposition = TaskDecomposition(
                    main_task=query,
                    subtasks=result.get("subtasks", []),
                    dependencies=result.get("dependencies", {}),
                    execution_order=result.get("execution_order", []),
                    parallel_groups=result.get("parallel_groups", []),
                )
            logger.info(
                "Task decomposed: subtasks=%d parallel_groups=%d",
                len(decomposition.subtasks), len(decomposition.parallel_groups)
            )
            return decomposition
        except Exception as e:
            logger.exception("Task decomposition failed")
            raise
    
    async def _call_with_async_support(
        self,
        func: Callable,
        *args,
        **kwargs,
    ) -> Any:
        """调用函数 - 支持同步和异步"""
        if asyncio.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        else:
            return func(*args, **kwargs)
    
    def get_router(self) -> MessageRouter:
        """获取消息路由器"""
        return self._router


class MultiAgentOrchestrator:
    """多Agent 编排引擎 - 协调整个 Agent 生态"""
    
    def __init__(self):
        self.agent_registry = AgentRegistry()
        self.coordinators: Dict[str, AgentCoordinator] = {}
        self._metrics_history: List[MetricsReport] = []
    
    def register_agent(self, signature: AgentSignature) -> bool:
        """注册 Agent"""
        self.agent_registry.register(signature)
        coordinator = AgentCoordinator(
            primary_agent_id=signature.credential.agent_id,
            agent_registry=self.agent_registry,
        )
        self.coordinators[signature.credential.agent_id] = coordinator
        logger.info(
            "Agent registered: id=%s name=%s capabilities=%d",
            signature.credential.agent_id,
            signature.credential.agent_name,
            len(signature.capabilities),
        )
        return True
    
    def get_coordinator(self, agent_id: str) -> Optional[AgentCoordinator]:
        """获取 Agent 协调器"""
        return self.coordinators.get(agent_id)
    
    async def report_metrics(self, metrics: MetricsReport):
        """上报指标"""
        self._metrics_history.append(metrics)
        logger.info(
            "Metrics reported: agent=%s tool_success=%.2f",
            metrics.agent_id, metrics.tool_success_rate
        )
    
    def get_metrics_summary(self, agent_id: Optional[str] = None) -> Dict[str, Any]:
        """获取指标汇总"""
        if agent_id:
            agent_metrics = [m for m in self._metrics_history if m.agent_id == agent_id]
        else:
            agent_metrics = self._metrics_history
        
        if not agent_metrics:
            return {}
        
        avg_tool_success = sum(
            m.tool_success_rate for m in agent_metrics
        ) / len(agent_metrics) if agent_metrics else 0.0
        
        return {
            "avg_tool_success_rate": avg_tool_success,
            "reports_count": len(agent_metrics),
        }


# ============================================================================
# 新增文件 6: app/infrastructure/agent/multi_task_executor.py
# ============================================================================

"""多任务执行器 - 支持并发执行多个任务"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from app.infrastructure.agent.a2a_protocol import (
    TaskDecomposition,
    SubTask,
    TaskRequest,
    TaskResponse,
)
from app.infrastructure.agent.agent_runtime import AgentRuntime, RetryPolicy, FallbackPolicy


logger = logging.getLogger(__name__)


@dataclass
class TaskExecutionContext:
    """任务执行上下文"""
    task_id: str
    name: str
    task_type: str
    parameters: Dict[str, Any]
    required_tool: Optional[str] = None
    priority: int = 0
    
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: Optional[datetime] = None
    status: str = "pending"
    result: Optional[Any] = None
    error: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)


@dataclass
class ParallelTaskResult:
    """并行任务执行结果"""
    conversation_id: str
    main_task_id: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    task_results: Dict[str, TaskResponse]
    
    total_execution_time: float = 0.0
    parallel_efficiency: float = 0.0
    intent: Optional[str] = None
    intent_confidence: Optional[float] = None
    tools_used: List[str] = field(default_factory=list)
    trace_id: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)


class MultiTaskExecutor:
    """多任务执行器 - 支持并发和顺序执行"""
    
    def __init__(self, agent_runtime: AgentRuntime):
        self.agent_runtime = agent_runtime
        self._task_graphs: Dict[str, Dict[str, TaskExecutionContext]] = {}
        self._execution_results: Dict[str, ParallelTaskResult] = {}
    
    async def execute_decomposed_tasks(
        self,
        decomposition: TaskDecomposition,
        conversation_id: str,
        tool_executors: Dict[str, Callable],
        intent: Optional[str] = None,
        intent_confidence: Optional[float] = None,
    ) -> ParallelTaskResult:
        """执行拆解后的任务 - 支持并发执行"""
        
        main_task_id = str(uuid4())
        logger.info(
            "[%s] Starting multi-task execution: subtasks=%d parallel_groups=%d",
            conversation_id, len(decomposition.subtasks), len(decomposition.parallel_groups)
        )
        
        task_contexts: Dict[str, TaskExecutionContext] = {}
        for subtask in decomposition.subtasks:
            ctx = TaskExecutionContext(
                task_id=subtask.task_id,
                name=subtask.name,
                task_type=subtask.task_type,
                parameters=subtask.parameters,
                required_tool=subtask.required_tool,
                priority=subtask.priority,
                dependencies=decomposition.dependencies.get(subtask.task_id, []),
            )
            task_contexts[subtask.task_id] = ctx
        
        self._task_graphs[conversation_id] = task_contexts
        
        task_results: Dict[str, TaskResponse] = {}
        tools_used: List[str] = []
        start_time = datetime.utcnow()
        
        try:
            if decomposition.parallel_groups:
                task_results = await self._execute_with_parallel_groups(
                    decomposition.parallel_groups,
                    task_contexts,
                    tool_executors,
                    conversation_id,
                )
            else:
                task_results = await self._execute_with_dependencies(
                    task_contexts,
                    tool_executors,
                    conversation_id,
                )
            
            for ctx in task_contexts.values():
                if ctx.required_tool:
                    tools_used.append(ctx.required_tool)
        
        except Exception as e:
            logger.exception("[%s] Multi-task execution failed", conversation_id)
        
        finally:
            end_time = datetime.utcnow()
            total_time = (end_time - start_time).total_seconds()
        
        completed = sum(1 for r in task_results.values() if r.success)
        failed = len(task_results) - completed
        
        result = ParallelTaskResult(
            conversation_id=conversation_id,
            main_task_id=main_task_id,
            total_tasks=len(task_contexts),
            completed_tasks=completed,
            failed_tasks=failed,
            task_results=task_results,
            total_execution_time=total_time,
            intent=intent,
            intent_confidence=intent_confidence,
            tools_used=list(set(tools_used)),
            trace_id=conversation_id,
        )
        
        self._execution_results[conversation_id] = result
        
        logger.info(
            "[%s] Multi-task completed: completed=%d failed=%d time=%.2fs",
            conversation_id, completed, failed, total_time
        )
        
        return result
    
    async def _execute_with_parallel_groups(
        self,
        parallel_groups: List[List[str]],
        task_contexts: Dict[str, TaskExecutionContext],
        tool_executors: Dict[str, Callable],
        conversation_id: str,
    ) -> Dict[str, TaskResponse]:
        """使用并行组执行任务"""
        
        all_results: Dict[str, TaskResponse] = {}
        
        for group_idx, group in enumerate(parallel_groups):
            logger.info(
                "[%s] Executing parallel group %d: tasks=%d",
                conversation_id, group_idx, len(group)
            )
            
            tasks = []
            for task_id in group:
                if task_id not in task_contexts:
                    continue
                
                ctx = task_contexts[task_id]
                
                if not self._check_dependencies_completed(ctx, all_results):
                    logger.warning("[%s] Task %s dependencies not completed", conversation_id, task_id)
                    continue
                
                tool_executor = tool_executors.get(ctx.required_tool)
                if not tool_executor:
                    logger.error("[%s] No executor for tool: %s", conversation_id, ctx.required_tool)
                    continue
                
                task_request = TaskRequest(
                    task_id=task_id,
                    task_name=ctx.name,
                    agent_id=self.agent_runtime.agent_id,
                    parameters=ctx.parameters,
                    conversation_id=conversation_id,
                )
                
                ctx.status = "running"
                
                task = asyncio.create_task(
                    self.agent_runtime.execute_task(
                        task=task_request,
                        tool_executor=tool_executor,
                        retry_policy=RetryPolicy(max_retries=2),
                        fallback_policy=FallbackPolicy(),
                    )
                )
                tasks.append((task_id, task))
            
            if tasks:
                results = await asyncio.gather(
                    *[task for _, task in tasks],
                    return_exceptions=True,
                )
                
                for (task_id, _), result in zip(tasks, results):
                    if isinstance(result, Exception):
                        all_results[task_id] = TaskResponse(
                            task_id=task_id,
                            success=False,
                            error=str(result),
                            execution_time=0.0,
                        )
                        task_contexts[task_id].status = "failed"
                        task_contexts[task_id].error = str(result)
                    else:
                        all_results[task_id] = result
                        task_contexts[task_id].status = "completed" if result.success else "failed"
                        task_contexts[task_id].result = result.result
                        if result.error:
                            task_contexts[task_id].error = result.error
        
        return all_results
    
    async def _execute_with_dependencies(
        self,
        task_contexts: Dict[str, TaskExecutionContext],
        tool_executors: Dict[str, Callable],
        conversation_id: str,
    ) -> Dict[str, TaskResponse]:
        """按照依赖关系执行任务"""
        
        all_results: Dict[str, TaskResponse] = {}
        
        sorted_contexts = sorted(
            task_contexts.items(),
            key=lambda x: (-x[1].priority, x[0])
        )
        
        for task_id, ctx in sorted_contexts:
            if not self._check_dependencies_completed(ctx, all_results):
                logger.warning("[%s] Skipping task %s - dependencies not met", conversation_id, task_id)
                continue
            
            tool_executor = tool_executors.get(ctx.required_tool)
            if not tool_executor:
                logger.error("[%s] No executor for tool: %s", conversation_id, ctx.required_tool)
                continue
            
            task_request = TaskRequest(
                task_id=task_id,
                task_name=ctx.name,
                agent_id=self.agent_runtime.agent_id,
                parameters=ctx.parameters,
                conversation_id=conversation_id,
            )
            
            ctx.status = "running"
            
            response = await self.agent_runtime.execute_task(
                task=task_request,
                tool_executor=tool_executor,
                retry_policy=RetryPolicy(max_retries=2),
                fallback_policy=FallbackPolicy(),
            )
            
            all_results[task_id] = response
            ctx.status = "completed" if response.success else "failed"
            ctx.result = response.result
            if response.error:
                ctx.error = response.error
        
        return all_results
    
    def _check_dependencies_completed(
        self,
        ctx: TaskExecutionContext,
        results: Dict[str, TaskResponse],
    ) -> bool:
        """检查任务的所有依赖是否完成"""
        for dep_id in ctx.dependencies:
            if dep_id not in results:
                return False
            if not results[dep_id].success:
                return False
        return True
    
    def get_task_graph(self, conversation_id: str) -> Optional[Dict[str, TaskExecutionContext]]:
        """获取任务图"""
        return self._task_graphs.get(conversation_id)
    
    def get_execution_result(self, conversation_id: str) -> Optional[ParallelTaskResult]:
        """获取执行结果"""
        return self._execution_results.get(conversation_id)
    
    def get_task_status(self, conversation_id: str, task_id: str) -> Optional[str]:
        """获取单个任务状态"""
        graph = self._task_graphs.get(conversation_id)
        if graph and task_id in graph:
            return graph[task_id].status
        return None
