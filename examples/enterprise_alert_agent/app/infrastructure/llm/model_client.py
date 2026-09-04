import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from langsmith.run_trees import RunTree, logger
from openai import APIConnectionError, APIError, AsyncOpenAI, AuthenticationError

from app.config.settings import settings
from app.infrastructure.fault.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.observability.langsmith_tracer import LangSmithTracer


class ModelClient:
    def __init__(self, tracer: LangSmithTracer) -> None:
        self._async_client_map = {
            "dashscope": AsyncOpenAI(
                api_key=settings.dashscope_api_key,
                base_url=settings.dashscope_base_url,
                timeout=settings.request_timeout_seconds,
            ),
            "openai": AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                timeout=settings.request_timeout_seconds,
            ),
        }
        self._tracer = tracer
        self._circuit_breaker = CircuitBreaker(
            name="llm",
            failure_threshold=settings.circuit_breaker_failure_threshold,
            recovery_seconds=settings.circuit_breaker_recovery_seconds,
        )

    async def achat(
        self,
        user_query: str,
        system_prompt: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        return_message: bool = False,
        parent_run: RunTree | None = None,
        _token_counter: list[int] | None = None,
    ) -> Any:
        candidates = self._route_request()
        selected_model = candidates[0][1]
        """Asynchronously call the configured model with retry and fallback."""
        llm_run = self._tracer.start_child(
            parent_run=parent_run,
            name="llm.chat_completion.async",
            run_type="llm",
            inputs={
                "user_query": user_query,
                "system_prompt_length": len(system_prompt),
                "model": selected_model,
                "tools_count": len(tools) if tools else 0,
            },
            tags=["llm", selected_model, "async"],
        )
        try:
            completion = None
            for provider, model_name in candidates:
                _async_client = self._async_client_map.get(provider)
                if _async_client is None:
                    logger.warning("LLM client for provider '%s' not found, skipping.", provider)
                    continue

                payload: dict[str, Any] = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_query},
                    ],
                    "temperature": 0.1,
                }
                if tools:
                    payload["tools"] = tools

                for attempt in range(settings.model_max_retries + 1):
                    try:
                        self._circuit_breaker.before_call()
                        completion = await _async_client.chat.completions.create(**payload)
                        self._circuit_breaker.record_success()
                        break
                    except AuthenticationError:
                        raise
                    except Exception as exc:
                        self._circuit_breaker.record_failure()
                        if not self._classify_error(exc):
                            raise
                        if attempt < settings.model_max_retries:
                            await asyncio.sleep(
                                min(
                                    settings.model_retry_backoff_seconds * (2**attempt),
                                    8.0,
                                )
                            )
                if completion is not None:
                    break
            if completion is None:
                raise ModelRequestError("Model request failed after retries and fallback attempts.")
            message = completion.choices[0].message
            usage = getattr(completion, "usage", None)
            if usage is not None and _token_counter is not None:
                _token_counter[0] += (
                    usage.total_tokens if isinstance(usage.total_tokens, int) else 0
                )
                if _token_counter[0] >= settings.max_tokens_per_request:
                    raise BudgetExceededError("Model request exceeds the maximum token limit.")
            outputs = {
                "answer_length": len(message.content or ""),
                "model": completion.model or selected_model,
                "total_tokens": getattr(usage, "total_tokens", 0) if usage else 0,
            }
            self._tracer.end_run(llm_run, outputs=outputs)
            return message if return_message else message.content or ""
        except CircuitOpenError as exc:
            self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
            raise ModelRequestError("LLM circuit is open") from exc
        except AuthenticationError as exc:
            self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
            raise ModelAuthError("Model authentication failed") from exc
        except (APIConnectionError, APIError) as exc:
            self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
            raise ModelRequestError("Model request failed") from exc
        except BudgetExceededError as exc:
            self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
            raise

    async def aclose(self) -> None:
        """Close all asynchronous provider clients."""
        for async_client in self._async_client_map.values():
            try:
                await async_client.close()
            except (RuntimeError, AttributeError, TypeError):
                logger.exception("Failed to close async LLM client")

    async def astream_chat(
        self,
        user_query: str,
        system_prompt: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        parent_run: RunTree | None = None,
    ) -> AsyncGenerator[str, None]:
        """Asynchronously stream model output without blocking the event loop."""
        candidates = self._route_request()
        selected_model = candidates[0][1]
        llm_run = self._tracer.start_child(
            parent_run=parent_run,
            name="llm.chat_completion.astream",
            run_type="llm",
            inputs={"user_query": user_query, "stream": True, "model": selected_model},
            tags=["llm", selected_model, "async", "stream"],
        )

        try:
            for provider, model_name in candidates:
                _async_client = self._async_client_map.get(provider)
                if not _async_client:
                    logger.warning("No async client found for provider: %s", provider)
                    continue

                payload: dict[str, Any] = {
                    "model": model_name,
                    "stream": True,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_query},
                    ],
                    "temperature": 0.1,
                }
                if tools:
                    payload["tools"] = tools

                try:
                    self._circuit_breaker.before_call()
                    stream = await _async_client.chat.completions.create(**payload)
                    self._circuit_breaker.record_success()
                    answer_parts: list[str] = []
                    async for chunk in stream:
                        if not chunk.choices or not chunk.choices[0].delta:
                            continue
                        piece = getattr(chunk.choices[0].delta, "content", "")
                        if piece:
                            answer_parts.append(piece)
                            yield piece
                    self._tracer.end_run(
                        llm_run,
                        outputs={"answer_length": len("".join(answer_parts)), "stream": True},
                    )
                    return
                except CircuitOpenError as exc:
                    self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
                    raise ModelRequestError("LLM circuit is open") from exc
                except AuthenticationError as exc:
                    self._circuit_breaker.record_failure()
                    self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
                    raise ModelAuthError("Model authentication failed") from exc
                except (APIConnectionError, APIError) as exc:
                    self._circuit_breaker.record_failure()
                    logger.warning(
                        "Async stream failed for provider=%s model=%s, trying fallback: %s",
                        provider,
                        model_name,
                        exc,
                    )
                    continue
                except Exception as exc:
                    self._circuit_breaker.record_failure()
                    self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
                    raise

            raise ModelRequestError("Model request failed after retries and fallback attempts.")
        except CircuitOpenError as exc:
            self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
            raise ModelRequestError("LLM circuit is open") from exc

    def _classify_error(self, error: Exception) -> bool:
        """判断错误是否可重试"""
        transient_errors = (
            APIConnectionError,
            TimeoutError,
            ConnectionResetError,
        )
        non_retryable = (
            AuthenticationError,
            ValueError,
        )
        if isinstance(error, non_retryable):
            return False
        return isinstance(error, transient_errors)

    def _route_request(self) -> list[tuple[str, str]]:
        """Return ordered provider/model candidates for routing and fallback."""
        provider = settings.llm_provider.lower()

        if provider == "openai":
            return [("openai", settings.openai_model)]

        if provider == "dashscope":
            return [
                ("dashscope", settings.default_model),
                ("dashscope", settings.fallback_model),
            ]

        return [
            ("dashscope", settings.default_model),
            ("dashscope", settings.fallback_model),
        ]

    # def probe(self) -> None:
    #     """Run a lightweight provider check during service startup."""
    #     provider, model_name = self._route_request()
    #     client = self._client_map.get(provider)
    #     if not client:
    #         raise ModelRequestError(f"No client found for provider: {provider}")
    #     try:
    #         self._circuit_breaker.call(
    #             lambda: client.chat.completions.create(
    #                 model=model_name,
    #                 messages=[
    #                     {"role": "system", "content": "health-check"},
    #                     {"role": "user", "content": "ping"},
    #                 ],
    #                 temperature=0,
    #                 max_tokens=1,
    #             )
    #         )
    #     except CircuitOpenError as exc:
    #         raise ModelRequestError("LLM circuit is open") from exc
    #     except AuthenticationError as exc:
    #         raise ModelAuthError("Model authentication failed") from exc
    #     except (APIConnectionError, APIError) as exc:
    #         raise ModelRequestError("Model request failed") from exc


class ModelAuthError(Exception):
    """Raised when model provider authentication fails."""


class ModelRequestError(Exception):
    """Raised when model request fails for transient/provider reasons."""


class BudgetExceededError(Exception):
    """Raised when model request exceeds the maximum token limit."""
