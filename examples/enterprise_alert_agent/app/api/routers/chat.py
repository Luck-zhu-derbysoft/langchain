from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.application.services.chat_service import ChatService
from app.infrastructure.llm.model_client import (
    ModelAuthError,
    ModelRequestError,
)
from app.schemas.chat import ChatRequest, ChatResponse, ClearRequest
from app.observability.langsmith_tracer import LangSmithTracer
from app.infrastructure.memory.redis_postgres_conversation_memory import MemoryScope
from app.main import limiter
router = APIRouter(prefix="/chat", tags=["chat"])


def _get_chat_service(request: Request) -> ChatService:
   deps = request.app.state.shared_dependencies
   return ChatService(
        model_client=deps["model_client"],
        retriever=deps["retriever"],
        trace=deps["trace"],
        memory=deps["memory"],
        agent_registry=deps["agent_registry"],
        orchestrator=deps["orchestrator"]
    )
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
def clear_memory(request: Request, req: ClearRequest, service: Annotated[ChatService, Depends(_get_chat_service)]) -> dict[str, str]:
    scope = MemoryScope(
        tenant_id=req.tenant_id,
        user_id=req.user_id,
        thread_id=req.thread_id,
    )
    service.memory.clear_memory(scope)
    return {"message": "Memory cleared for the specified thread."}
