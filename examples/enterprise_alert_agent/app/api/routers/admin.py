from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.config.dynamic_settings import ALLOWED_CONFIG_KEYS, _dynamic_settings
from app.infrastructure.audit.audit_logger import AuditAction, AuditResult, audit_logger
from app.infrastructure.queue.dlq_handler import dead_letter_queue
from app.infrastructure.security.auth import (
    Role,
    TokenPayload,
    create_access_token,
    require_roles,
    verify_api_key,
)
from app.main import limiter

router = APIRouter(prefix="/admin", tags=["admin"])


class TokenRequest(BaseModel):
    user_id: str
    role: Role = Role.OPERATOR
    api_key: str
    tenant_id: str = Field(..., min_length=1, description="租户 ID，写入 JWT")


@router.post("/token")
@limiter.limit("5/minute")  # 限制每分钟最多请求次数
async def create_token(request: Request, token_request: TokenRequest):
    if not verify_api_key(token_request.user_id, token_request.api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    token = create_access_token(
        token_request.user_id,
        token_request.role,
        token_request.tenant_id,
    )
    return {"token": token}


@router.post("/config/{key}")
@limiter.limit("5/minute")  # 限制每分钟最多请求次数
async def update_config(
    request: Request,
    key: str,
    value: Any,
    user_id: str = "system",
    current_user: TokenPayload = Depends(require_roles(Role.ADMIN, Role.OPERATOR, Role.AUDITOR)),
):
    if key not in ALLOWED_CONFIG_KEYS:
        raise HTTPException(status_code=404, detail=f"Config key not found: {key}")
    try:
        success = _dynamic_settings.set(key, value, user_id=user_id)

        if not success:
            raise HTTPException(status_code=400, detail=f"Failed to update config for key: {key}")
        audit_logger.log(
            action=AuditAction.CONFIG_CHANGE,
            user_id=current_user.sub,
            result=AuditResult.SUCCESS,
            tenant_id=current_user.tenant_id,
            resource=f"/admin/config/{key}",
            detail={"new_value": value, "operator_user_id": user_id},
        )

        return {
            "success": True,
            "key": key,
            "value": value,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
    except Exception as exc:
        audit_logger.log(
            action=AuditAction.CONFIG_CHANGE,
            user_id=current_user.sub,
            result=AuditResult.FAILURE,
            tenant_id=current_user.tenant_id,
            resource=f"/admin/config/{key}",
            detail={"error": str(exc), "operator_user_id": user_id},
        )
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/config")
@limiter.limit("5/minute")  # 限制每分钟最多请求次数
async def get_all_configs(
    request: Request,
    current_user: TokenPayload = Depends(require_roles(Role.ADMIN)),
):
    return _dynamic_settings.get_all_overrides()


@router.get("/config/{key}")
@limiter.limit("30/minute")  # 限制每分钟最多请求次数
async def get_config(
    request: Request,
    key: str,
    current_user: TokenPayload = Depends(require_roles(Role.ADMIN, Role.OPERATOR, Role.AUDITOR)),
):
    try:
        value = _dynamic_settings.get(key)
        return {"key": key, "value": value}
    except Exception:
        raise HTTPException(status_code=404, detail=f"Config key not found: {key}")


@router.post("/config/{key}/reset")
@limiter.limit("5/minute")  # 限制每分钟最多请求次数
async def reset_config(
    request: Request,
    key: str,
    user_id: str = "system",
    current_user: TokenPayload = Depends(require_roles(Role.ADMIN, Role.OPERATOR, Role.AUDITOR)),
):
    try:
        success = _dynamic_settings.reset(key, user_id=user_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Failed to reset config for key: {key}")
        audit_logger.log(
            action=AuditAction.CONFIG_CHANGE,
            user_id=current_user.sub,
            result=AuditResult.SUCCESS,
            tenant_id=current_user.tenant_id,
            resource=f"/admin/config/{key}/reset",
            detail={"operator_user_id": user_id},
        )
        return {
            "success": True,
            "key": key,
            "message": f"Config {key} reset to default",
        }
    except Exception as exc:
        audit_logger.log(
            action=AuditAction.CONFIG_CHANGE,
            user_id=current_user.sub,
            result=AuditResult.FAILURE,
            tenant_id=current_user.tenant_id,
            resource=f"/admin/config/{key}/reset",
            detail={"error": str(exc), "operator_user_id": user_id},
        )
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/config/history")
@limiter.limit("5/minute")  # 限制每分钟最多请求次数
async def get_config_change_history(
    request: Request,
    limit: int = 50,
    current_user: TokenPayload = Depends(require_roles(Role.ADMIN, Role.OPERATOR, Role.AUDITOR)),
):
    history = _dynamic_settings.get_change_history(limit=limit)
    return {"history": history}


@router.get("/dlq/stats")
@limiter.limit("30/minute")
async def get_dlq_stats(
    request: Request,
    current_user: TokenPayload = Depends(require_roles(Role.ADMIN, Role.AUDITOR)),
) -> dict[str, Any]:
    """查看 DLQ 各状态计数。"""
    return await dead_letter_queue.stats()


@router.get("/dlq/pending")
@limiter.limit("30/minute")
async def get_dlq_pending(
    request: Request,
    current_user: TokenPayload = Depends(require_roles(Role.ADMIN, Role.AUDITOR)),
) -> dict[str, Any]:
    """列出所有待处理/重试中的 DLQ 条目。"""
    entries = await dead_letter_queue.list_pending()
    return {"count": len(entries), "items": [e.to_dict() for e in entries]}


@router.post("/dlq/{dlq_id}/retry")
@limiter.limit("10/minute")
async def retry_dlq_entry(
    request: Request,
    dlq_id: str,
    current_user: TokenPayload = Depends(require_roles(Role.ADMIN)),
) -> dict[str, Any]:
    """手动触发单条 DLQ 条目重试。"""
    result = await dead_letter_queue.retry(dlq_id, lambda payload: payload)
    return {"success": result, "dlq_id": dlq_id}
