import sys

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "enterprise-alert-agent"
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    dashscope_api_key: str = ""
    model_name: str = "qwen-plus"
    fallback_model_name: str = "qwen-turbo"  # 主模型失败后的降级模型
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    request_timeout_seconds: int = 30
    model_startup_probe_enabled: bool = True
    model_max_retries: int = 2
    agent_tool_failure_threshold: int = 2  # 工具连续失败超过此数降级为纯 RAG
    # ========================================================
    # 🚀 新增：Agentic & Function Calling 配置（提升含金量核心）
    # ========================================================
    agent_max_iterations: int = (
        3  # 允许 Agent 思考与调用工具的最大轮数            # LLM 失败重试次数
    )
    task_max_retries: int = 2
    task_initial_backoff_seconds: float = 0.5
    task_max_workers: int = 4
    task_timeout_seconds: int = 20
    enable_fallback_chain: bool = True
    # --- Embedding 配置 ---
    embedding_model: str = "text-embedding-v3"
    embedding_dimensions: int = 1024

    # --- ChromaDB 向量库配置 ---
    chroma_collection_name: str = "alert_knowledge"
    chroma_persist_directory: str = "./data/chroma_db"

    # --- 文档切块配置 ---
    chunk_size: int = 500
    chunk_overlap: int = 50

    # ============== 新增：LangSmith 配置（3行） ==============
    # --- LangSmith 配置 ---
    langsmith_tracing: bool = True
    langsmith_api_key: str = ""
    langsmith_project: str = "enterprise-alert-agent"
    # ================================================

    # --- 检索与重排配置 ---
    retrieval_top_k: int = 8
    context_top_k: int = 3
    rerank_enabled: bool = True
    rerank_model: str = "ms-marco-MiniLM-L-12-v2"

    max_tokens_per_request: int = 200000
    vector_db_path: str = "./data/vectorstore"
    sqlite_path: str = "./data/app.db"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    # mysql数据库配置
    mysql_host: str = ""
    mysql_port: int = 3306
    mysql_db: str = "agent"
    mysql_user: str = "root"
    mysql_password: str = ""
    sql_max_rows: int = 200
    sql_query_timeout_seconds: int = 5
    # 混合检索配置
    retrieval_candidate_k: int = 12  # 初始召回
    retrieval_final_k: int = 4  # 最终给上下文
    retrieval_min_score: float = 0.45  # 过滤阈值
    retrieval_hybrid_alpha: float = 0.7  # 稠密分占比
    retrieval_query_rewrite: bool = True
    retrieval_max_history_turns: int = 2
    # --- Time / Clock Tool 配置 ---
    app_timezone: str = "Asia/Shanghai"
    time_tool_enabled: bool = True
    time_query_skip_retrieval: bool = True
    time_query_skip_memory_write: bool = True
    # redis配置
    redis_host: str = ""
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    memory_redact_pii: bool = True
    memory_ttl_days: int = 7
    redis_cache_ttl_seconds: int = 3600
    memory_summary_update_turn_threshold: int = 5
    cache_recent_turns_limit: int = 10
    # PostgreSQL 配置（替代 MySQL）
    pg_host: str = ""
    pg_port: int = 5432
    pg_db: str = "postgres"
    pg_user: str = "postgres"
    pg_password: str = ""
    pg_ssl_mode: str = "prefer"
    # --- MCP 配置 ---
    mcp_service_url: str = ""
    mcp_enabled: bool = True
    mcp_api_key: str = ""
    mcp_cookie: str = ""
    # ========== 故障诊断配置==========
    fault_diagnosis_enabled: bool = True
    fault_alert_threshold: str = "high"  # "low", "medium", "high", "critical"
    fault_recovery_auto_retry: bool = True
    fault_recovery_wait_strategies: dict = {
        "immediate": 0.1,
        "wait_10s": 10.0,
        "wait_30s": 30.0,
    }
    # ========== 安全配置==========
    admin_jwt_secret: str = ""
    admin_jwt_algorithm: str = "HS256"
    admin_jwt_exp_minutes: int = 120
    admin_api_key: str = ""


def _validate_secrets(s: "Settings") -> None:
    # fail fast if any required secret is missing
    required = {
        "DASHSCOPE_API_KEY": s.dashscope_api_key,
        "ADMIN_JWT_SECRET": s.admin_jwt_secret,
        "MYSQL_PASSWORD": s.mysql_password,
        "REDIS_PASSWORD": s.redis_password,
        "PG_PASSWORD": s.pg_password,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        print(
            f"[FATAL] Missing required secrets: {missing}. "
            "Set them via environment variables or .env file.",
            file=sys.stderr,
        )
        sys.exit(1)


settings = Settings()
_validate_secrets(settings)
_validate_secrets(settings)
