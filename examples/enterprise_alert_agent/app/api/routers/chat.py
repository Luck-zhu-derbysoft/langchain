from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
import uuid

from app.application.services.chat_service import ChatService
from app.infrastructure.agent.a2a_protocol import ManualInterventionRequest
from app.infrastructure.agent.intervention_handler import InterventionHandler
from app.infrastructure.llm.model_client import (
    ModelAuthError,
    ModelRequestError,
)
from app.schemas.chat import ChatRequest, ChatResponse, ClearRequest
from app.observability.langsmith_tracer import LangSmithTracer
from app.infrastructure.memory.redis_postgres_conversation_memory import MemoryScope
from app.main import limiter
router = APIRouter(prefix="/chat", tags=["chat"])
# 在模块级别创建干预处理器实例
_intervention_handler = InterventionHandler()

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

@router.get("/{request_id}/pending-intervention")
async def get_pending_intervention(request_id: str):
    """
    获取正在等待用户干预的请求

    Response:
    {
        "task_id": "task_1",
        "reason": "工具执行失败，已重试 2 次",
        "suggestions": ["重试", "跳过", "修改参数"],
        "can_modify_params": true
    }
    """
    # 这需要与 chat_service 集成，存储待处理请求
    pass
@router.post("/{request_id}/intervention")
async def submit_intervention(request_id: str, intervention:ManualInterventionRequest):
    """
    提交人工干预请求

    Body:
    {
        "task_id": "task_1",
        "intervention_type": "retry" | "skip" | "modify_params" | "abort",
        "retry_params": {"query": "modified query"},  // 仅 modify_params 需要
        "skip_reason": "用户决定跳过"  // 仅 skip 需要
    }

    Response:
    {
        "intervention_id": "intervention_task_1_abc123",
        "success": true,
        "output": "任务执行结果",
        "elapsed_time_ms": 1234
    }
    """
    try:
        result = _intervention_handler.submit_intervention(
            task_id=request_id,
            intervention=intervention,
            execute_callback=lambda iv: f"Result from task {intervention.task_id}",
            timeout_seconds=300.0
        )
        return {
            "intervention_id": result.intervention_id,
            "success": result.success,
            "output": result.output,
            "error_message": result.error_message,
            "elapsed_time_ms": result.elapsed_time_ms
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"处理干预请求时出错: {str(e)}"
        )
@router.get("/{request_id}/intervention-history")
async def get_intervention_history(request_id: str):
    """获取该请求的所有干预历史"""
    # 返回所有对该请求进行过的干预记录
    pass

