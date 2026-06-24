# 1. 先导入并执行 LangSmith 配置（必须在所有业务代码之前！）
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config.settings import settings
from app.config.tracing_config import configure_langsmith,get_langsmith_client, is_langsmith_enabled
from app.infrastructure.llm.model_client import ModelAuthError, ModelClient, ModelRequestError
from app.observability.langsmith_tracer import LangSmithTracer
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    configure_langsmith()
    from app.api.routers.chat import router as chat_router
    from app.api.routers.health import router as health_router
    from app.api.routers.ingest import router as ingest_router

    app = FastAPI(
        title=settings.app_name,
        version="0.2.0",
    )
    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(ingest_router)
    app.state.limiter = limiter

    # 静态文件和首页
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(static_dir / "index.html"))

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request, exc):
        return JSONResponse(
            status_code=429,
            content={"detail": "请求过于频繁，请稍后再试"}
        )

    @app.on_event("startup")
    async def startup_checks() -> None:
        from app.infrastructure.mcp.mcp_tool import async_init_mcp
        app.state.model_ready = False
        app.state.model_check_message = "not checked"
        app.state.mcp_ready = False

        if not settings.dashscope_api_key.strip():
            msg = "DASHSCOPE_API_KEY is empty. /chat requests will fail with 401."
            app.state.model_check_message = msg
            logger.warning(msg)
            return

        if not settings.model_startup_probe_enabled:
            app.state.model_ready = True
            app.state.model_check_message = "startup probe disabled"
            return
        if settings.mcp_enabled:
            app.state.mcp_ready = await async_init_mcp()
            if not app.state.mcp_ready:
                logger.warning("MCP initialization failed during startup")

        try:
            startup_tracer = LangSmithTracer(
                client=get_langsmith_client(),
                _enabled=is_langsmith_enabled(),
                project_name=settings.langsmith_project,
                service_name="enterprise-alert-agent",
            )

            ModelClient(tracer=startup_tracer).probe()
            app.state.model_ready = True
            app.state.model_check_message = "ok"
        except ModelAuthError:
            msg = "model auth failed during startup probe"
            app.state.model_check_message = msg
            logger.warning(msg)
        except ModelRequestError:
            msg = "model request failed during startup probe"
            app.state.model_check_message = msg
            logger.warning(msg)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
