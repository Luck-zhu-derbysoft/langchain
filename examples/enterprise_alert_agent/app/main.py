# 1. 先导入并执行 LangSmith 配置（必须在所有业务代码之前！）
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config.settings import settings
from app.config.tracing_config import (
    configure_langsmith,
    get_langsmith_client,
    is_langsmith_enabled,
)
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
from app.observability import alert_manager
from app.observability.langsmith_tracer import LangSmithTracer
from app.observability.metrics import MetricsCollector
from app.rag.retrieval.retriever import Retriever

limiter = Limiter(key_func=get_remote_address)

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    configure_langsmith()
    from app.api.routers.admin import router as admin_router
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

    orchestrator = MultiAgentOrchestrator(agent_registry)
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
