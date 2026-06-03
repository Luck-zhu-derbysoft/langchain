from typing import Any

from langsmith.run_trees import RunTree

from openai import APIConnectionError, APIError, AuthenticationError, OpenAI

from app.config.settings import settings
from app.observability.langsmith_tracer import LangSmithTracer


class ModelClient:
    def __init__(self, tracer: LangSmithTracer) -> None:
        self._client = OpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.base_url,
            timeout=settings.request_timeout_seconds,
        )
        self._tracer = tracer

    def chat(self, user_query: str,
              system_prompt: str,*,
              tools: list[dict[str, Any]] | None = None,
               return_message: bool = False,
              parent_run: RunTree | None = None) -> Any:
        llm_run = self._tracer.start_child(
            parent_run=parent_run,
            name="llm.chat_completion",
            run_type="llm",
            inputs={
                "user_query": user_query,
                "system_prompt_length": len(system_prompt),
                "model": settings.model_name,
                "tools_count": len(tools) if tools else 0,
                },
            tags=["llm", settings.model_name],
        )
        try:
            payload: dict[str,Any]  = {
                "model": settings.model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_query},
                ],
                "temperature": 0.1,
            }
            if tools:
                payload["tools"] = tools
            completion = self._client.chat.completions.create(
                **payload
            )
            message = completion.choices[0].message
            content = message.content or ""

            outputs: dict[str, Any] = {
                "answer_length": len(content),
                "model": settings.model_name,
                "answer_preview": content[:1000],
            }
            if getattr(message, "tool_calls", None):
                outputs["tool_calls"] = [
                    {
                        "id": tc.id,
                        "name": tc.function.name if tc.function else "",
                        "arguments": tc.function.arguments if tc.function else "",
                    }
                    for tc in message.tool_calls
                ]

            usage = getattr(completion, "usage", None)
            if usage is not None and hasattr(usage, "model_dump"):
                outputs["usage"] = usage.model_dump()
            self._tracer.end_run(llm_run, outputs=outputs)
            if return_message:
                return message
            return content or ""

        except AuthenticationError as exc:
            self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
            raise ModelAuthError("Model authentication failed") from exc
        except (APIConnectionError, APIError) as exc:
            self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
            raise ModelRequestError("Model request failed") from exc


    def probe(self) -> None:
        """Run a lightweight provider check during service startup."""
        try:
            self._client.chat.completions.create(
                model=settings.model_name,
                messages=[
                    {"role": "system", "content": "health-check"},
                    {"role": "user", "content": "ping"},
                ],
                temperature=0,
                max_tokens=1,
            )
        except AuthenticationError as exc:
            raise ModelAuthError("Model authentication failed") from exc
        except (APIConnectionError, APIError) as exc:
            raise ModelRequestError("Model request failed") from exc


class ModelAuthError(Exception):
    """Raised when model provider authentication fails."""


class ModelRequestError(Exception):
    """Raised when model request fails for transient/provider reasons."""
