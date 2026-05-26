from typing import Any

from fastapi import APIRouter, Request

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
