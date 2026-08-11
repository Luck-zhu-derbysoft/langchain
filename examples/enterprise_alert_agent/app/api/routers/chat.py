import json
from collections.abc import Generator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.application.services.chat_service import ChatService
from app.infrastructure.agent.a2a_protocol import ManualInterventionRequest
from app.infrastructure.memory.redis_postgres_conversation_memory import MemoryScope
from app.infrastructure.security.auth import TokenPayload, require_auth
from app.main import limiter
from app.observability.alert_types import AlertSeverity, AlertTypes
from app.schemas.chat import ChatRequest, ClearRequest

router = APIRouter(prefix="/chat", tags=["chat"])
# 在模块级别创建干预处理器实例


def _get_chat_service(request: Request) -> ChatService:
    deps = request.app.state.shared_dependencies
    return ChatService(
        model_client=deps["model_client"],
        retriever=deps["retriever"],
        trace=deps["trace"],
        memory=deps["memory"],
        _intervention_handler=deps["intervention_handler"],
        _alert_manager=deps["alert_manager"],
        _metrics_collector=deps["metrics_collector"],
        agent_registry=deps["agent_registry"],
        orchestrator=deps["orchestrator"],
    )


@router.post("/stream")
@limiter.limit("10/minute")
def chat_stream(
    request: Request,
    req: ChatRequest,
    service: Annotated[ChatService, Depends(_get_chat_service)],
    _auth: Annotated[TokenPayload, Depends(require_auth)],
) -> StreamingResponse:
    root_run = service.trace.start_root(
        name="api.chat.stream",
        run_type="chain",
        inputs={"query": req.query, "business_context": req.business_context},
        tags=["chat", "request", "stream"],
    )

    def iter_sse() -> Generator[str, None, None]:
        stream_request_id: str | None = None
        try:
            for chunk in service.ask_stream(req, parent_run=root_run):
                stream_request_id = str(chunk.get("request_id") or "")
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        finally:
            service.trace.end_run(
                root_run,
                outputs={
                    "request_id": stream_request_id,
                    "stream": True,
                },
            )

    return StreamingResponse(
        iter_sse(),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.post("/memory/clear")
def clear_memory(
    request: Request,
    req: ClearRequest,
    service: Annotated[ChatService, Depends(_get_chat_service)],
    _auth: Annotated[TokenPayload, Depends(require_auth)],
) -> dict[str, str]:
    scope = MemoryScope(
        tenant_id=req.tenant_id,
        user_id=req.user_id,
        thread_id=req.thread_id,
    )
    service.memory.clear_memory(scope)
    return {"message": "Memory cleared for the specified thread."}


@router.get("/{request_id}/pending-intervention")
@limiter.limit("5/minute")
async def get_pending_intervention(
    request_id: str,
    service: Annotated[ChatService, Depends(_get_chat_service)],
    _auth: Annotated[TokenPayload, Depends(require_auth)],
):
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
    pending_list = service._intervention_handler.get_pending_intervention(request_id)
    return {
        "request_id": request_id,
        "pending_interventions": [p.dict() if hasattr(p, "dict") else p for p in pending_list],
        "count": len(pending_list),
    }


@router.post("/{request_id}/intervention")
@limiter.limit("10/minute")
async def submit_intervention(
    request_id: str,
    intervention: ManualInterventionRequest,
    service: Annotated[ChatService, Depends(_get_chat_service)],
    _auth: Annotated[TokenPayload, Depends(require_auth)],
):
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
    result = service._intervention_handler.submit_intervention(
        task_id=request_id,
        request=intervention,
    )
    return {
        "intervention_id": result.intervention_id,
        "success": result.success,
        "output": result.output,
        "error_message": result.error_message,
        "elapsed_time_ms": result.elapsed_time_ms,
    }


@router.get("/{request_id}/intervention-history")
@limiter.limit("10/minute")
async def get_intervention_history(
    request_id: str,
    service: Annotated[ChatService, Depends(_get_chat_service)],
    _auth: Annotated[TokenPayload, Depends(require_auth)],
):
    """获取该请求的所有干预历史"""
    history = service._intervention_handler.get_intervention_history(request_id)
    return {
        "request_id": request_id,
        "interventions": [h.dict() if hasattr(h, "dict") else h for h in history],
        "total": len(history),
    }


@router.get("/alerts")
@limiter.limit("20/minute")
async def get_alerts(
    service: Annotated[ChatService, Depends(_get_chat_service)],
    _auth: Annotated[TokenPayload, Depends(require_auth)],
    alert_type: str | None = None,
    severity: str | None = None,
    limit: int = 100,
):
    # 调用 alert_manager 获取告警
    alert_type_enum = AlertTypes(alert_type) if alert_type else None
    severity_enum = AlertSeverity(severity) if severity else None
    alerts = service.alert_manager.get_alerts(
        alert_type=alert_type_enum, severity=severity_enum, limit=limit
    )
    return {
        "total": len(alerts),
        "alerts": [
            {
                "alert_id": alert.alert_id,
                "type": alert.alert_type.value,
                "severity": alert.severity.value,
                "title": alert.title,
                "message": alert.message,
                "resource": alert.affected_resource,
                "acknowledged": alert.acknowledged,
                "timestamp": alert.timestamp.isoformat(),
            }
            for alert in alerts
        ],
    }


@router.post("/alerts/{alert_id}/acknowledge")
@limiter.limit("30/minute")
async def acknowledge_alert(
    alert_id: str,
    acknowledged_by: str,
    service: Annotated[ChatService, Depends(_get_chat_service)],
    _auth: Annotated[TokenPayload, Depends(require_auth)],
):
    """确认告警"""
    success = service.alert_manager.acknowledge_alert(alert_id, acknowledged_by)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"告警 {alert_id} 未找到")
    return {"success": True}


@router.get("/metrics/{request_id}")
async def get_metrics(
    request_id: str,
    service: Annotated[ChatService, Depends(_get_chat_service)],
    _auth: Annotated[TokenPayload, Depends(require_auth)],
):
    """获取该请求的性能指标"""
    metrics = service.metrics_collector.get_metrics(request_id)
    if not metrics:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"请求 {request_id} 的性能指标未找到"
        )
    return {
        "request_id": metrics.request_id,
        "total_time_ms": metrics.total_time_ms,
        "p50_latency_ms": metrics.get_p50_latency(),
        "p95_latency_ms": metrics.get_p95_latency(),
        "p99_latency_ms": metrics.get_p99_latency(),
        "token_usage": metrics.token_usage,
        "estimated_cost_usd": round(metrics.estimated_cost_usd, 4),
        "cache_hit_rate": round(metrics.get_cache_hit_rate(), 3),
        "success_rate": round(metrics.get_success_rate(), 3),
        "error_count": metrics.error_count,
        "retry_count": metrics.retry_count,
    }
