from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "enterprise-alert-agent"
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"


    dashscope_api_key: str = "DASHSCOPE_API_KEY"
    model_name: str = "qwen-plus"
    fallback_model_name: str = "qwen-turbo"     # 主模型失败后的降级模型
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    request_timeout_seconds: int = 30
    model_startup_probe_enabled: bool = True
    model_max_retries: int = 2
    agent_tool_failure_threshold: int = 2      # 工具连续失败超过此数降级为纯 RAG
    # ========================================================
    # 🚀 新增：Agentic & Function Calling 配置（提升含金量核心）
    # ========================================================
    agent_max_iterations: int = 5  # 允许 Agent 思考与调用工具的最大轮数            # LLM 失败重试次数

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
    langsmith_api_key: str = "LANGSMITH_API_KEY"
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
    #mysql数据库配置
    mysql_host: str = "mysql_host"
    mysql_port: int = 3306
    mysql_db: str = "mysql_db"
    mysql_user: str = "mysql_user"
    mysql_password: str = "mysql_password"
    sql_max_rows: int = 200
    sql_query_timeout_seconds: int = 5
    # 混合检索配置
    retrieval_candidate_k: int = 12      # 初始召回
    retrieval_final_k: int = 4           # 最终给上下文
    retrieval_min_score: float = 0.45    # 过滤阈值
    retrieval_hybrid_alpha: float = 0.7  # 稠密分占比
    retrieval_query_rewrite: bool = True
    retrieval_max_history_turns: int = 2
    # --- Time / Clock Tool 配置 ---
    app_timezone: str = "Asia/Shanghai"
    time_tool_enabled: bool = True
    time_query_skip_retrieval: bool = True
    time_query_skip_memory_write: bool = True
    # redis配置
    redis_host: str = "10.200.0.241"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = "derbysoft"
    memory_redact_pii: bool = True
    memory_ttl_days: int = 7
    redis_cache_ttl_seconds: int = 3600
    memory_summary_update_turn_threshold: int = 5
    cache_recent_turns_limit: int = 10
    # PostgreSQL 配置（替代 MySQL）
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_db: str = "rag"
    pg_user: str = "postgres"
    pg_password: str = "admin"
    pg_ssl_mode: str = "prefer"
    # --- MCP 配置 ---
    mcp_service_url: str = "http://127.0.0.1:3000/mcp"
    mcp_enabled: bool = True
    mcp_timeout: int = 30
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 3000
    mcp_path: str = "/mcp"
    mcp_api_key: str = ""



settings = Settings()
