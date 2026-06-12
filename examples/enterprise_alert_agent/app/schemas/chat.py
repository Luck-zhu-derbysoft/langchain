from pydantic import BaseModel, Field


class Citation(BaseModel):
	source_id: str = Field(..., description="知识来源标识")
	snippet: str = Field(..., description="引用片段")


class ChatRequest(BaseModel):
	query: str = Field(..., min_length=1, description="用户问题")
	business_context: str = Field(..., description="业务上下文")
	thread_id: str = Field(..., description="会话线程ID，用于记忆复用")
	tenant_id: str =Field(..., description="租户ID，用于多租户场景")
	user_id: str = Field(..., description="用户ID，用于个性化和权限控制")


class ClearRequest(BaseModel):
	thread_id: str = Field(..., description="会话线程ID，用于记忆复用")
	tenant_id: str =Field(..., description="租户ID，用于多租户场景")
	user_id: str = Field(..., description="用户ID，用于个性化和权限控制")


class ChatResponse(BaseModel):
	answer: str
	citations: list[Citation]
	model: str
	request_id: str
