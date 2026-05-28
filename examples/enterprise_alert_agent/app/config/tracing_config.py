import os
import sys
from pathlib import Path
from typing import Protocol, cast
import importlib
from agents import TracingProcessor, set_trace_processors
from dotenv import load_dotenv
from langsmith.integrations.openai_agents_sdk import OpenAIAgentsTracingProcessor

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


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
    load_dotenv(dotenv_path=BASE_DIR / ".env")

    os.environ["LANGSMITH_TRACING"] = _resolve_setting("LANGSMITH_TRACING")
    os.environ["LANGSMITH_API_KEY"] = _resolve_setting("LANGSMITH_API_KEY")
    os.environ["LANGSMITH_PROJECT"] = _resolve_setting("LANGSMITH_PROJECT")

    # # 可选：自定义 trace 的基础名称（会作为所有 trace 的前缀）
    # os.environ["LANGSMITH_BASE_RUN_NAME"] = "Agent workflow"

    langsmith_key = os.getenv("LANGSMITH_API_KEY")
    if langsmith_key:
        set_trace_processors(cast(list[TracingProcessor], [OpenAIAgentsTracingProcessor()]))
    print("✅ LangSmith tracing configured for the entire project")
