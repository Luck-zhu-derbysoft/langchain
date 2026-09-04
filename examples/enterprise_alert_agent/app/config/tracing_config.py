import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Protocol, cast

from dotenv import load_dotenv
from langsmith import Client

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

_langsmith_client: Client | None = None
_langsmith_enabled: bool = False

class _SettingsLike(Protocol):
    langsmith_tracing: str
    langsmith_api_key: str
    langsmith_project: str


settings = cast(
    _SettingsLike,
    importlib.import_module("app.config.settings").settings,
)


def _resolve_setting(name: str) -> str:
    env_value = os.getenv(name)
    if env_value is not None:
        return env_value

    return str(getattr(settings, name.lower(), ""))


def configure_langsmith() -> None:
    """加载配置并初始化 LangSmith Client（从注解处理模式改为显式 Client）。"""
    global _langsmith_client, _langsmith_enabled

    load_dotenv(dotenv_path=BASE_DIR / ".env")
    # 环境变量优先，其次 settings
    tracing = os.getenv("langsmith_tracing", str(settings.langsmith_tracing)).strip().lower()
    api_key = os.getenv("langsmith_api_key", settings.langsmith_api_key).strip()
    project = os.getenv("LANGSMITH_PROJECT", settings.langsmith_project).strip()

    os.environ["LANGSMITH_TRACING"] = tracing
    os.environ["LANGSMITH_API_KEY"] = api_key
    os.environ["LANGSMITH_PROJECT"] = project

    # 判断是否启用追踪
    _langsmith_enabled = tracing in ("1", "true", "yes")

    if _langsmith_enabled and api_key:
        _langsmith_client = Client(api_key=api_key)
        logger.info("✅ LangSmith client initialized")
    elif _langsmith_enabled:
        logger.warning("⚠️ LangSmith tracing enabled but API key is missing. Tracing will not work.")
    else:
        logger.info("ℹ️ LangSmith tracing is disabled.")


def get_langsmith_client() -> Client | None:
    """获取全局 LangSmith 客户端。"""
    return _langsmith_client

def is_langsmith_enabled() -> bool:
    """检查 LangSmith 追踪是否启用。"""
    return _langsmith_enabled is True
