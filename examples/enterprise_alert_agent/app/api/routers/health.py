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
    import psycopg
    import redis as redis_lib

    checks: dict[str, Any] = {}

    # Redis 实际连通性
    try:
        r = redis_lib.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password,
            socket_connect_timeout=2,
        )
        checks["redis"] = bool(r.ping())
    except Exception:
        checks["redis"] = False

    # PostgreSQL 实际连通性
    try:
        with psycopg.connect(
            f"postgresql://{settings.pg_user}:{settings.pg_password}@"
            f"{settings.pg_host}:{settings.pg_port}/{settings.pg_db}",
            connect_timeout=2,
        ) as conn:
            conn.execute("SELECT 1")
        checks["postgres"] = True
    except Exception:
        checks["postgres"] = False

    # Chroma 实际连通性（复用已实例化的 store，不新建连接）
    chroma = request.app.state.shared_dependencies.get("chroma_store")
    try:
        checks["chroma"] = getattr(chroma, "count", lambda: None)() is not None
    except Exception:
        checks["chroma"] = False

    # MCP 连通性
    from app.infrastructure.mcp.mcp_client import _mcp_client

    checks["mcp"] = _mcp_client is not None and _mcp_client._initialized

    # 模型（启动探活结果）
    checks["model"] = bool(getattr(request.app.state, "model_ready", False))
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
