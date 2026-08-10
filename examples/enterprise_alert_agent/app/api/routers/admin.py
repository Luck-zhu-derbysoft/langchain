import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config.dynamic_settings import _dynamic_settings
from app.infrastructure.audit.audit_logger import AuditAction, AuditResult, audit_logger
from app.infrastructure.security.auth import (
    Role,
    TokenPayload,
    create_access_token,
    require_roles,
    verify_api_key,
)

router = APIRouter(prefix="/admin", tags=["admin"])


class TokenRequest(BaseModel):
    user_id: str
    role: Role = Role.OPERATOR
    api_key: str


@router.post("/token")
async def create_token(request: TokenRequest):
    if not verify_api_key(request.user_id, request.api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    token = create_access_token(request.user_id, request.role)
    return {"token": token}


@router.post("/config/{key}")
async def update_config(
    key: str,
    value: Any,
    user_id: str = "system",
    current_user: TokenPayload = Depends(require_roles(Role.ADMIN, Role.OPERATOR, Role.AUDITOR)),
):
    try:
        success = _dynamic_settings.set(key, value, user_id=user_id)

        if not success:
            raise HTTPException(status_code=400, detail=f"Failed to update config for key: {key}")
        audit_logger.log(
            action=AuditAction.CONFIG_CHANGE,
            user_id=current_user.sub,
            result=AuditResult.SUCCESS,
            tenant_id="default",
            resource=f"/admin/config/{key}",
            detail={"new_value": value, "operator_user_id": user_id},
        )

        return {
            "success": True,
            "key": key,
            "value": value,
            "timestamp": datetime.datetime.now().isoformat(),
        }
    except Exception as exc:
        audit_logger.log(
            action=AuditAction.CONFIG_CHANGE,
            user_id=current_user.sub,
            result=AuditResult.FAILURE,
            tenant_id="default",
            resource=f"/admin/config/{key}",
            detail={"error": str(exc), "operator_user_id": user_id},
        )
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/config")
async def get_all_configs(current_user: TokenPayload = Depends(require_roles(Role.ADMIN))):
    return _dynamic_settings.get_all_overrides()


@router.get("/config/{key}")
async def get_config(
    key: str,
    current_user: TokenPayload = Depends(require_roles(Role.ADMIN, Role.OPERATOR, Role.AUDITOR)),
):
    value = _dynamic_settings.get(key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"Config for key '{key}' not found")
    return {"key": key, "value": value}


@router.post("/config/{key}/reset")
async def reset_config(
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
            tenant_id="default",
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
            tenant_id="default",
            resource=f"/admin/config/{key}/reset",
            detail={"error": str(exc), "operator_user_id": user_id},
        )
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/config/history")
async def get_config_change_history(
    limit: int = 50,
    current_user: TokenPayload = Depends(require_roles(Role.ADMIN, Role.OPERATOR, Role.AUDITOR)),
):
    history = _dynamic_settings.get_change_history(limit=limit)
    return {"history": history}
