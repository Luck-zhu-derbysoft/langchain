from fastapi import APIRouter, HTTPException, status

from app.application.services.chat_service import ChatService
from app.infrastructure.embedding.embedding_client import EmbeddingClient
from app.infrastructure.llm.model_client import (
    ModelAuthError,
    ModelClient,
    ModelRequestError,
)
from app.infrastructure.vectorstore.chroma_store import ChromaStore
from app.rag.retrieval.retriever import Retriever
from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])

# 初始化依赖链
_embedding_client = EmbeddingClient()
_chroma_store = ChromaStore(embedding_client=_embedding_client)

_chat_service = ChatService(
    model_client=ModelClient(),
    retriever=Retriever(chroma_store=_chroma_store),
)


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    try:
        return _chat_service.ask(req)
    except ModelAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="模型鉴权失败，请检查 DASHSCOPE_API_KEY 是否正确且可用。",
        ) from exc
    except ModelRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="模型服务请求失败，请稍后重试。",
        ) from exc
