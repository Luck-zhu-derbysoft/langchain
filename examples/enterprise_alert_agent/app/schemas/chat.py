from pydantic import BaseModel, Field


class Citation(BaseModel):
	source_id: str = Field(..., description="知识来源标识")
	snippet: str = Field(..., description="引用片段")


class ChatRequest(BaseModel):
	query: str = Field(..., min_length=1, description="用户问题")
	business_context: str | None = Field(default=None, description="业务上下文")
	thread_id: str | None = Field(default=None, description="会话线程ID，用于记忆复用")


class ChatResponse(BaseModel):
	answer: str
	citations: list[Citation]
	model: str
	request_id: str
