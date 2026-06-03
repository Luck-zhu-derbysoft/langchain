from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "enterprise-alert-agent"
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    dashscope_api_key: str = "sk-74ce8487ee1745e697f137341890160a"
    model_name: str = "qwen-plus"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    request_timeout_seconds: int = 30
    model_startup_probe_enabled: bool = True

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
    langsmith_api_key: str = "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"
    langsmith_project: str = "enterprise-alert-agent"
    # ================================================

    # ========================================================
    # 🚀 新增：Agentic & Function Calling 配置（提升含金量核心）
    # ========================================================
    agent_max_iterations: int = 2  # 允许 Agent 思考与调用工具的最大轮数

    # --- 检索与重排配置 ---
    retrieval_top_k: int = 8
    context_top_k: int = 3
    rerank_enabled: bool = True
    rerank_model: str = "ms-marco-MiniLM-L-12-v2"

    token_budget_daily: int = 200000
    vector_db_path: str = "./data/vectorstore"
    sqlite_path: str = "./data/app.db"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    #mysql数据库配置
    mysql_host: str = "10.200.0.241"
    mysql_port: int = 3306
    mysql_db: str = "dmatch"
    mysql_user: str = "root"
    mysql_password: str = "derbysoft"


settings = Settings()
