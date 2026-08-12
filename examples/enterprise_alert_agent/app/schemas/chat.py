from typing import Optional

from pydantic import BaseModel, Field


class Citation(BaseModel):
	source_id: str = Field(..., description="知识来源标识")
	snippet: str = Field(..., description="引用片段")


class ChatRequest(BaseModel):
	query: str = Field(..., min_length=1, description="用户问题")
	business_context: str = Field(default="", description="业务上下文")
	thread_id: str = Field(default="default-thread", description="会话线程ID，用于记忆复用")
	tenant_id: str = Field(default="", description="租户ID，由服务端从 JWT 强制注入")
	user_id: str = Field(default="", description="用户ID，由服务端从 JWT 强制注入")


class ClearRequest(BaseModel):
	thread_id: str = Field(..., description="会话线程ID，用于记忆复用")
	tenant_id: str = Field(..., description="租户ID，用于多租户场景")
	user_id: str = Field(..., description="用户ID，用于个性化和权限控制")


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    model: str
    request_id: str
    intent: Optional[str] = Field(default=None, description="意图识别结果")
    intent_confidence: Optional[float] = Field(default=None, description="意图识别置信度")
    trace_id: Optional[str] = Field(default=None, description="追踪ID，用于链路追踪和调试")
    selected_tool: Optional[str] = Field(default=None, description="选择的工具名称")
    tool_confidence: Optional[float] = Field(default=None, description="选择工具的置信度")
    fallback_tool:list[str] = Field(default=[], description="备选工具列表")
    tool_selection_reason: Optional[str] = Field(default=None, description="选择工具的原因")
    task_decomposed: bool = Field(default=False, description="是否进行了任务拆分")
    is_multi_task: bool = Field(default=False, description="是否进入多任务执行模式")
    multi_task_results: Optional[dict] = Field(default=None, description="多任务执行统计结果")
    failed_tasks: list[str] = Field(default_factory=list, description="失败的子任务ID列表")
    manual_intervention_required: bool = Field(default=False, description="是否需要人工介入")
    retry_count: int = Field(default=0, description="重试次数")
    fallback_used: bool = Field(default=False, description="是否使用了备用策略")
    fallback_strategy: str = Field(default="", description="备用策略名称")
    active_agent_id: Optional[str] = Field(default=None, description="当前处理请求的主Agent")
    assigned_agent_ids: list[str] = Field(default_factory=list, description="分配的Agent ID列表")
    performance_metrics: Optional[dict] = Field(default=None, description="性能指标")
    # 示例结构:
    # {
    #   "total_time_ms": 1234,
    #   "p50_latency_ms": 100,
    #   "p95_latency_ms": 250,
    #   "p99_latency_ms": 500,
    #   "token_usage": 500,
    #   "estimated_cost_usd": 0.001,
    #   "cache_hit_rate": 0.45,
    #   "success_rate": 0.98,
    # }



