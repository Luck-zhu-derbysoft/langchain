from typing import Any

from langsmith.run_trees import RunTree, logger

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
              parent_run: RunTree | None = None,
              _token_counter: list[int] | None = None) -> Any:
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
            completion = None
            models = [settings.model_name, settings.fallback_model_name]
            for index, model_candidate in enumerate(models):
                payload["model"] = model_candidate

                for attempt in range(settings.model_max_retries + 1):
                    try:
                        completion = self._client.chat.completions.create(**payload)
                        break  # 成功则跳出重试循环
                    except AuthenticationError:
                        raise  # 鉴权错误不重试，直接向上抛
                    except (APIConnectionError, APIError) as exc:
                        if attempt < settings.model_max_retries:
                            logger.warning(f"LLM 请求失败，正在重试... (尝试 {attempt + 1}/{settings.model_max_retries}) 错误: {exc}")
                        else:
                            logger.error(f"LLM 请求失败，已达到最大重试次数。错误: {exc}   尝试下一个模型...")

                if completion is not None:
                    break  # 当前模型重试后仍失败，尝试下一个模型候选
            if completion is None:
                raise ModelRequestError("Model request failed after retries and fallback attempts.")

            message = completion.choices[0].message
            content = message.content or ""

            outputs: dict[str, Any] = {
                "answer_length": len(content),
                "model": payload["model"],
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
            if usage is not None and _token_counter is not None:
                _token_counter[0] += usage.total_tokens if isinstance(usage.total_tokens, int) else 0
                if _token_counter[0] <= settings.max_tokens_per_request:
                    outputs["total_tokens"] = usage.total_tokens
                else:
                    self._tracer.end_run(llm_run, outputs=outputs)
                    raise BudgetExceededError("Model request exceeds the maximum token limit.")

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

class BudgetExceededError(Exception):
    """Raised when model request exceeds the maximum token limit."""
