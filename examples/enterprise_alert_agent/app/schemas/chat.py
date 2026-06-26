from typing import Optional

from pydantic import BaseModel, Field


class Citation(BaseModel):
	source_id: str = Field(..., description="知识来源标识")
	snippet: str = Field(..., description="引用片段")


class ChatRequest(BaseModel):
	query: str = Field(..., min_length=1, description="用户问题")
	business_context: str = Field(default="", description="业务上下文")
	thread_id: str = Field(default="default-thread", description="会话线程ID，用于记忆复用")
	tenant_id: str = Field(default="default-tenant", description="租户ID，用于多租户场景")
	user_id: str = Field(default="default-user", description="用户ID，用于个性化和权限控制")


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
