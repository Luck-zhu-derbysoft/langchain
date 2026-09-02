# 1. 先导入并执行 LangSmith 配置（必须在所有业务代码之前！）
import asyncio
import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config.settings import settings
from app.config.tracing_config import (
    configure_langsmith,
    get_langsmith_client,
    is_langsmith_enabled,
)
from app.infrastructure.agent.a2a_protocol import A2AProtocol
from app.infrastructure.agent.agent_coordinator import MultiAgentOrchestrator
from app.infrastructure.agent.agent_registry import AgentDescriptor, AgentRegistry
from app.infrastructure.agent.intervention_handler import InterventionHandler
from app.infrastructure.audit.audit_logger import audit_logger
from app.infrastructure.embedding.embedding_client import EmbeddingClient
from app.infrastructure.fault.fault_analyzer import FaultAnalyzer
from app.infrastructure.llm.model_client import ModelAuthError, ModelClient, ModelRequestError
from app.infrastructure.memory.redis_postgres_conversation_memory import (
    RedisPostgresConversationMemoryStore,
)
from app.infrastructure.vectorstore.chroma_store import ChromaStore
from app.observability.alert_manager import alert_manager
from app.observability.langsmith_tracer import LangSmithTracer
from app.observability.logging_config import configure_logging, request_id_context
from app.observability.metrics import MetricsCollector
from app.observability.prometheus_metrics import (
    ACTIVE_REQUESTS,
    REQUEST_COUNT,
    REQUEST_LATENCY,
)
from app.rag.retrieval.retriever import Retriever


def get_user_rate_limit_key(request) -> str:
    """按用户/租户维度限流，降级回 IP。"""
    try:
        # 尝试从 Authorization token 中提取用户标识
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            # 简化：用 token 前 20 字符作为用户 key
            return f"user:{token[:20]}"
    except Exception:
        logger.exception("Failed to extract user key")
    # 降级为 IP 限流
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=get_user_rate_limit_key)
request_semaphore = asyncio.Semaphore(settings.max_concurrent_requests)

logger = logging.getLogger(__name__)


limiter = Limiter(key_func=get_user_rate_limit_key)


def create_app() -> FastAPI:
    configure_logging(settings.log_level)
    configure_langsmith()
    from app.api.routers.admin import router as admin_router
    from app.api.routers.chat import router as chat_router
    from app.api.routers.health import router as health_router
    from app.api.routers.ingest import router as ingest_router

    app = FastAPI(
        title=settings.app_name,
        version="0.2.0",
    )

    @app.middleware("http")
    async def concurrency_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not request.url.path.startswith(("/chat", "/ingest", "/admin")):
            return await call_next(request)
        try:
            await asyncio.wait_for(request_semaphore.acquire(), timeout=1.0)
        except TimeoutError:
            return JSONResponse(status_code=429, content={"detail": "服务当前繁忙，请稍后重试"})
        try:
            return await call_next(request)
        finally:
            request_semaphore.release()

    @app.middleware("http")
    async def observability_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        request_token = request_id_context.set(request_id)
        started_at = time.perf_counter()
        ACTIVE_REQUESTS.inc()
        response: Response | None = None
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            elapsed_seconds = time.perf_counter() - started_at
            REQUEST_COUNT.labels(
                method=request.method,
                path=request.url.path,
                status=str(status_code),
            ).inc()
            REQUEST_LATENCY.labels(
                method=request.method,
                path=request.url.path,
            ).observe(elapsed_seconds)
            ACTIVE_REQUESTS.dec()
            if response is not None:
                response.headers["X-Request-ID"] = request_id
            request_id_context.reset(request_token)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(ingest_router)
    app.include_router(admin_router)
    app.state.limiter = limiter

    trace = LangSmithTracer(
        client=get_langsmith_client(),
        _enabled=is_langsmith_enabled(),
        project_name=settings.langsmith_project,
        service_name="enterprise-alert-agent",
    )
    embedding_client = EmbeddingClient(tracer=trace)
    chroma_store = ChromaStore(embedding_client=embedding_client, tracer=trace)
    memory = RedisPostgresConversationMemoryStore()
    retriever = Retriever(chroma_store=chroma_store, tracer=trace)
    model_client = ModelClient(tracer=trace)
    fault_analyzer = FaultAnalyzer()
    intervention_handler = InterventionHandler()
    metrics_collector = MetricsCollector()
    agent_registry = AgentRegistry()
    agent_registry.register_agent(
        AgentDescriptor(
            agent_id="router_agent",
            display_name="Router Agent",
            capabilities=["intent_routing", "tool_selection"],
            supported_tools=[],
            priority=100,
        )
    )
    agent_registry.register_agent(
        AgentDescriptor(
            agent_id="rag_agent",
            display_name="RAG Agent",
            capabilities=["knowledge_retrieval", "summary_generation"],
            supported_tools=[],
            priority=80,
        )
    )
    agent_registry.register_agent(
        AgentDescriptor(
            agent_id="sql_agent",
            display_name="SQL Agent",
            capabilities=["database_query"],
            supported_tools=[],
            priority=90,
        )
    )
    agent_registry.register_agent(
        AgentDescriptor(
            agent_id="time_agent",
            display_name="Time Agent",
            capabilities=["time_query"],
            supported_tools=["get_current_datetime"],
            priority=95,
        )
    )

    a2a_protocol = A2AProtocol()
    orchestrator = MultiAgentOrchestrator(agent_registry, a2a_protocol)
    app.state.shared_dependencies = {
        "embedding_client": embedding_client,
        "chroma_store": chroma_store,
        "memory": memory,
        "retriever": retriever,
        "model_client": model_client,
        "trace": trace,
        "agent_registry": agent_registry,
        "orchestrator": orchestrator,
        "fault_analyzer": fault_analyzer,
        "intervention_handler": intervention_handler,
        "alert_manager": alert_manager,
        "metrics_collector": metrics_collector,
        "audit_logger": audit_logger,
    }

    # 静态文件和首页
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(static_dir / "index.html"))

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request, exc):
        return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})

    @app.on_event("startup")
    async def startup_checks() -> None:
        from app.infrastructure.mcp.mcp_client import async_init_mcp
        from app.infrastructure.queue.dlq_handler import dead_letter_queue

        await memory.awarmup()  # 异步预热 Redis/PostgreSQL 连接池
        await dead_letter_queue.startup()  # 异步初始化 DLQ，从 Redis 回捞历史数据
        app.state.model_ready = False
        app.state.model_check_message = "not checked"
        app.state.mcp_ready = not settings.mcp_enabled
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

    @app.on_event("shutdown")
    async def shutdown_resources() -> None:
        logger.info("Application shutdown started")
        # 1) 排空在途请求：等全局闸门完全空闲（或超时），期间停止接收新请求
        if not await _drain_inflight(request_semaphore, settings.graceful_shutdown_timeout_seconds):
            logger.warning("Not all in-flight requests completed before shutdown timeout")
        # 2) 关闭各类资源
        audit_logger.flush_to_file()  # 确保审计日志落盘

        dependencies = app.state.shared_dependencies
        try:
            from app.infrastructure.queue.dlq_handler import dead_letter_queue

            await asyncio.wait_for(
                dead_letter_queue.close(),
                timeout=settings.graceful_shutdown_timeout_seconds,
            )
        except TimeoutError:
            logger.error("DLQ worker did not stop within shutdown timeout")
        alert_manager_instance = dependencies.get("alert_manager")
        close_alert_manager = getattr(alert_manager_instance, "close", None)
        if close_alert_manager is not None:
            close_alert_manager(timeout=settings.graceful_shutdown_timeout_seconds)
        memory_store = dependencies.get("memory")
        if memory_store is not None:
            close_memory = getattr(memory_store, "aclose", None)
            if close_memory is not None:
                await close_memory()
            else:
                memory_store.close()
        model_client = dependencies.get("model_client")
        close_model = getattr(model_client, "aclose", None)
        if close_model is not None:
            await close_model()
        from app.infrastructure.mcp.mcp_client import async_close_mcp

        await async_close_mcp()
        await app.state.stream_worker.close()
        logger.info("Application shutdown completed")

    return app


async def _drain_inflight(sem: asyncio.Semaphore, timeout: float) -> bool:
    """排空在途请求：直到能连续拿到 max_concurrent_requests 个许可（= 无在途请求）。"""
    deadline = time.monotonic() + timeout
    acquired = 0
    while time.monotonic() < deadline:
        if sem.acquire_nowait():  # type: ignore # 公开 API，返回 bool，不抛异常
            acquired += 1
            if acquired >= settings.max_concurrent_requests:
                for _ in range(acquired):
                    sem.release()
                return True
        else:
            acquired = 0  # 还有请求占用许可，未排空，清零重来
        await asyncio.sleep(0.05)
    for _ in range(acquired):
        sem.release()
    return False


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
