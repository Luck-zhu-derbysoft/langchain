from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.config.settings import settings

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health(request: Request) -> dict[str, Any]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
        "model_ready": bool(getattr(request.app.state, "model_ready", False)),
        "model_check_message": getattr(
            request.app.state,
            "model_check_message",
            "not checked",
        ),
    }


@router.get("/live")
def liveness() -> dict[str, str]:
    return {"status": "alive", "app": settings.app_name}


@router.get("/ready")
def readiness(request: Request) -> dict[str, Any]:
    checks = {
        "model": bool(getattr(request.app.state, "model_ready", False)),
        "mcp": bool(getattr(request.app.state, "mcp_ready", True)),
    }
    if not all(checks.values()):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "not_ready",
                "checks": checks,
                "model_message": getattr(
                    request.app.state,
                    "model_check_message",
                    "not checked",
                ),
            },
        )
    return {"status": "ready", "checks": checks}
