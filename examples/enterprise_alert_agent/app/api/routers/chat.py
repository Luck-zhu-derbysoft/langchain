from fastapi import APIRouter, HTTPException, status

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
from app.schemas.chat import ChatRequest, ChatResponse
from app.observability.langsmith_tracer import LangSmithTracer
from langgraph.checkpoint.memory import InMemorySaver

router = APIRouter(prefix="/chat", tags=["chat"])

_trace = LangSmithTracer(
    client=get_langsmith_client(),
    _enabled=is_langsmith_enabled(),
    project_name=settings.langsmith_project,
    service_name="enterprise-alert-agent",
    )

# 初始化依赖链
_embedding_client = EmbeddingClient(tracer=_trace)
_chroma_store = ChromaStore(embedding_client=_embedding_client,tracer=_trace)
_memory = InMemorySaver()

_chat_service = ChatService(
    model_client=ModelClient(tracer=_trace),
    retriever=Retriever(chroma_store=_chroma_store,tracer=_trace),
    trace=_trace,
    memory=_memory,
)


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    #新增root run，追踪整个聊天请求的生命周期
    root_run = _trace.start_root(
        name="api.chat",
        run_type="chain",
        inputs={"query": req.query,"business_context": req.business_context},
        tags=["chat", "request"],
    )


    try:
        resp = _chat_service.ask(req,parent_run=root_run)
        _trace.end_run(
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
            _trace.end_run(
                root_run,
                error=LangSmithTracer.format_error(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="模型鉴权失败，请检查 DASHSCOPE_API_KEY 是否正确且可用。",
            ) from exc
    except ModelRequestError as exc:
        _trace.end_run(
            root_run,
            error=LangSmithTracer.format_error(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="模型服务请求失败，请稍后重试。",
        ) from exc

    except Exception as exc:
        _trace.end_run(
            root_run,
            error=LangSmithTracer.format_error(exc),
        )
        raise
