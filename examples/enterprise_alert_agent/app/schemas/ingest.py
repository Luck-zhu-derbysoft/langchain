"""文档摄入相关的请求/响应模型。"""

from pydantic import BaseModel, Field


class IngestTextRequest(BaseModel):
    """纯文本摄入请求。"""

    content: str = Field(..., min_length=1, description="待摄入的文本内容")
    source_id: str = Field(..., min_length=1, description="文档来源标识，如文件名或编号")
    metadata: dict[str, str] = Field(default_factory=dict, description="附加元数据")


class IngestResponse(BaseModel):
    """摄入操作的返回结果。"""

    source_id: str = Field(..., description="文档来源标识")
    chunks_count: int = Field(..., description="切分后的块数")
    total_docs: int = Field(..., description="向量库中的文档总数")
    message: str = Field(default="摄入成功", description="操作结果描述")
