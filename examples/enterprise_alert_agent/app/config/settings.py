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

    token_budget_daily: int = 200000
    vector_db_path: str = "./data/vectorstore"
    sqlite_path: str = "./data/app.db"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
