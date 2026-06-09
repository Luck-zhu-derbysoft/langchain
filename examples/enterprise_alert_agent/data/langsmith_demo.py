import asyncio
import importlib
import os
import sys
from pathlib import Path
from typing import Protocol, cast

from agents import Agent, Runner, TracingProcessor, set_default_openai_client, set_trace_processors
from dotenv import load_dotenv
from openai import AsyncOpenAI

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_ENV_FILE = PROJECT_ROOT / ".env"


class _SettingsLike(Protocol):
    dashscope_api_key: str
    model_name: str
    base_url: str


settings = cast(
    _SettingsLike,
    importlib.import_module("app.config.settings").settings,
)


def _configure_client() -> None:
    """Configure the global OpenAI-compatible client.

    Loads .env and resolves model settings from `app.config.settings`.
    """
    load_dotenv(_ENV_FILE)
    client = AsyncOpenAI(api_key=settings.dashscope_api_key, base_url=settings.base_url)
    set_default_openai_client(client, use_for_tracing=False)


async def main() -> None:
    load_dotenv(_ENV_FILE)
    model = settings.model_name

    agent = Agent(
        name="小助手",
        instructions="You are 小助手, 使用中文回复问题",
        model=model,
    )

    question = "如何学习langchain框架？"
    result = await Runner.run(agent, question)
    print(result.final_output)


if __name__ == "__main__":
    _configure_client()

    langsmith_key = os.getenv("LANGSMITH_API_KEY")
    if langsmith_key:
        from langsmith.integrations.openai_agents_sdk import OpenAIAgentsTracingProcessor

        set_trace_processors(cast(list[TracingProcessor], [OpenAIAgentsTracingProcessor()]))

    asyncio.run(main())
