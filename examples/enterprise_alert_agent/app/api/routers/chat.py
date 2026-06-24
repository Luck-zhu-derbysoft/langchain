from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.application.services.chat_service import ChatService
from app.config.settings import settings
from app.config.tracing_config import get_langsmith_client,is_langsmith_enabled
from app.infrastructure.embedding.embedding_client import EmbeddingClient
from app.infrastructure.llm.model_client import (
    ModelAuthError,
    ModelClient,
    ModelRequestError,
)
from app.infrastructure.vectorstore.chroma_store import ChromaStore
from app.rag.retrieval.retriever import Retriever
from app.schemas.chat import ChatRequest, ChatResponse, ClearRequest
from app.observability.langsmith_tracer import LangSmithTracer
from app.infrastructure.memory.redis_postgres_conversation_memory import RedisPostgresConversationMemoryStore,MemoryScope
from app.main import limiter
router = APIRouter(prefix="/chat", tags=["chat"])

@lru_cache(maxsize=1)
def _build_chat_service() -> ChatService:
    # 初始化依赖链
    _trace = LangSmithTracer(
        client=get_langsmith_client(),
        _enabled=is_langsmith_enabled(),
        project_name=settings.langsmith_project,
        service_name="enterprise-alert-agent",
    )
    _embedding_client = EmbeddingClient(tracer=_trace)
    _chroma_store = ChromaStore(embedding_client=_embedding_client, tracer=_trace)
    _memory = RedisPostgresConversationMemoryStore()

    return ChatService(
        model_client=ModelClient(tracer=_trace),
        retriever=Retriever(chroma_store=_chroma_store, tracer=_trace),
        trace=_trace,
        memory=_memory,
    )

def _get_chat_service() -> ChatService:
    return _build_chat_service()

@router.post("", response_model=ChatResponse)
@limiter.limit("10/minute")
def chat(request: Request, req: ChatRequest, service: Annotated[ChatService, Depends(_get_chat_service)]) -> ChatResponse:
    # 新增 root run，追踪整个聊天请求的生命周期
    root_run = service.trace.start_root(
        name="api.chat",
        run_type="chain",
        inputs={"query": req.query,"business_context": req.business_context},
        tags=["chat", "request"],
    )


    try:
        resp = service.ask(req,parent_run=root_run)
        service.trace.end_run(
            root_run,
            outputs={
                "request_id": resp.request_id,
                "answer_preview": resp.answer[:1000] if resp.answer else "",
                "answer_length": len(resp.answer),
                "citations_count": len(resp.citations),
                },
        )
        return resp
    except ModelAuthError as exc:
            service.trace.end_run(
                root_run,
                error=LangSmithTracer.format_error(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="模型鉴权失败，请检查 DASHSCOPE_API_KEY 是否正确且可用。",
            ) from exc
    except ModelRequestError as exc:
        service.trace.end_run(
            root_run,
            error=LangSmithTracer.format_error(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="模型服务请求失败，请稍后重试。",
        ) from exc

    except Exception as exc:
        service.trace.end_run(
            root_run,
            error=LangSmithTracer.format_error(exc),
        )
        raise


@router.post("/memory/clear")
def clear_memory(req: ClearRequest, service: Annotated[ChatService, Depends(_get_chat_service)]) -> dict[str, str]:
    scope = MemoryScope(
        tenant_id=req.tenant_id,
        user_id=req.user_id,
        thread_id=req.thread_id,
    )
    service.memory.clear_memory(scope)
    return {"message": "Memory cleared for the specified thread."}
