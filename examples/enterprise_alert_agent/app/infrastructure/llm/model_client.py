import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from langsmith.run_trees import RunTree, logger
from openai import APIConnectionError, APIError, AsyncOpenAI, AuthenticationError

from app.config.settings import settings
from app.infrastructure.fault.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.infrastructure.fault.retry_policy import DeadLine, backoff_delay, is_retryable
from app.infrastructure.llm.context_budget import estimate_tokens
from app.observability.langsmith_tracer import LangSmithTracer


class ModelClient:
    def __init__(self, tracer: LangSmithTracer) -> None:
        self._async_client_map = {}
        _timeout = httpx.Timeout(
            settings.request_timeout_seconds, connect=settings.connect_timeout_seconds
        )
        self._async_client_map["dashscope"] = AsyncOpenAI(
            api_key=settings.dashscope_api_key,
            base_url=settings.dashscope_base_url,
            timeout=_timeout,
            max_retries=0,
        )
        self._async_client_map["openai"] = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=_timeout,
            max_retries=0,
        )
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
        self._validate_input_budget(user_query, system_prompt, tools)
        try:
            completion = None
            last_error: Exception | None = None
            deadline = DeadLine.after(settings.model_total_deadline_seconds)
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
                    "max_tokens": settings.llm_output_token_budget,
                }
                if tools:
                    payload["tools"] = tools

                for attempt in range(settings.model_max_retries + 1):
                    remaining = deadline.remaining()
                    if remaining <= 0:
                        break
                    try:
                        self._circuit_breaker.before_call()
                        completion = await _async_client.with_options(
                            timeout=httpx.Timeout(
                                min(remaining, settings.request_timeout_seconds),
                                connect=min(remaining, settings.connect_timeout_seconds),
                            )
                        ).chat.completions.create(**payload)
                        self._circuit_breaker.record_success()
                        break
                    except AuthenticationError:
                        raise
                    except Exception as exc:
                        last_error = exc
                        if not is_retryable(exc):
                            raise
                        self._circuit_breaker.record_failure()
                        delay = backoff_delay(
                            attempt,
                            base=settings.model_retry_backoff_seconds,
                            cap=settings.model_retry_backoff_cap_seconds,
                            exc=exc,
                        )
                        if attempt >= settings.model_max_retries or not deadline.can_afford(delay):
                            break  # 当前模型不再重试，换下一个候选模型
                        logger.warning(
                            "LLM transient error, retrying: provider=%s attempt=%d delay=%.2fs err=%s",
                            provider,
                            attempt + 1,
                            delay,
                            exc,
                        )
                        await asyncio.sleep(delay)
                if completion is not None:
                    break
            if completion is None:
                message = "Model request failed after retries and fallback attempts."
                if last_error is not None:
                    message = f"{message} Last error: {type(last_error).__name__}: {last_error}"
                raise ModelRequestError(message) from last_error
            message = completion.choices[0].message
            message_content = getattr(message, "content", None) or ""
            usage = getattr(completion, "usage", None)
            if usage is not None and _token_counter is not None:
                _token_counter[0] += (
                    usage.total_tokens if isinstance(usage.total_tokens, int) else 0
                )
                if _token_counter[0] >= settings.max_tokens_per_request:
                    raise BudgetExceededError("Model request exceeds the maximum token limit.")
            outputs = {
                "answer_length": len(message_content),
                "model": completion.model or selected_model,
                "total_tokens": getattr(usage, "total_tokens", 0) if usage else 0,
            }
            self._tracer.end_run(llm_run, outputs=outputs)
            return message if return_message else message_content
        except CircuitOpenError as exc:
            self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
            raise ModelRequestError("LLM circuit is open") from exc
        except AuthenticationError as exc:
            self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
            raise ModelAuthError("Model authentication failed") from exc
        except (APIConnectionError, APIError) as exc:
            self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
            raise ModelRequestError(f"Model request failed: {type(exc).__name__}: {exc}") from exc
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

    async def probe(self) -> None:
        """Verify that the configured model accepts a minimal completion request."""
        await self.achat(
            user_query="Respond with pong.",
            system_prompt="You are a service health probe. Reply concisely.",
        )

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
        self._validate_input_budget(user_query, system_prompt, tools)

        try:
            deadline = DeadLine.after(settings.model_total_deadline_seconds)
            emitted = False
            last_error: Exception | None = None
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
                    "max_tokens": settings.llm_output_token_budget,
                }
                if tools:
                    payload["tools"] = tools
                for attempt in range(settings.model_max_retries + 1):
                    remaining = deadline.remaining()
                    if remaining <= 0:
                        break
                    stream = None
                    try:
                        self._circuit_breaker.before_call()
                        stream = await _async_client.with_options(
                            timeout=httpx.Timeout(
                                min(remaining, settings.request_timeout_seconds),
                                connect=min(remaining, settings.connect_timeout_seconds),
                            )
                        ).chat.completions.create(**payload)
                        answer_parts: list[str] = []
                        iterator = stream.__aiter__()
                        while True:
                            try:
                                chunk = await asyncio.wait_for(
                                    anext(iterator), timeout=settings.stream_idle_timeout_seconds
                                )
                            except StopAsyncIteration:
                                break
                            if not chunk.choices or not chunk.choices[0].delta:
                                continue
                            piece = getattr(chunk.choices[0].delta, "content", "")
                            if piece:
                                emitted = True
                                answer_parts.append(piece)
                                yield piece
                        self._circuit_breaker.record_success()
                        self._tracer.end_run(
                            llm_run,
                            outputs={"answer_length": len("".join(answer_parts)), "stream": True},
                        )
                        return
                    except CircuitOpenError as exc:
                        self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
                        raise ModelRequestError("LLM circuit is open") from exc
                    except AuthenticationError as exc:
                        self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
                        raise ModelAuthError("Model authentication failed") from exc
                    except Exception as exc:
                        last_error = exc
                        if emitted:
                            # 已经输出过部分内容，重试或切模型会把内容再发一遍，只能直接失败
                            self._circuit_breaker.record_failure()
                            self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
                            raise ModelRequestError(
                                "Stream interrupted after partial output"
                            ) from exc
                        if not is_retryable(last_error):
                            self._tracer.end_run(
                                llm_run, error=LangSmithTracer.format_error(last_error)
                            )
                            raise
                        self._circuit_breaker.record_failure()
                        delay = backoff_delay(
                            attempt,
                            base=settings.model_retry_backoff_seconds,
                            cap=settings.model_retry_backoff_cap_seconds,
                            exc=exc,
                        )
                        if attempt >= settings.model_max_retries or not deadline.can_afford(delay):
                            break
                        await asyncio.sleep(delay)
                    finally:
                        if stream is not None:
                            await stream.close()

            final_error = ModelRequestError(
                "Model request failed after retries and fallback attempts."
            )
            self._tracer.end_run(
                llm_run, error=LangSmithTracer.format_error(last_error or final_error)
            )
            raise final_error from last_error
        except CircuitOpenError as exc:
            self._tracer.end_run(llm_run, error=LangSmithTracer.format_error(exc))
            raise ModelRequestError("LLM circuit is open") from exc

    # def _classify_error(self, error: Exception) -> bool:
    #     """判断错误是否可重试"""
    #     transient_errors = (
    #         APIConnectionError,
    #         TimeoutError,
    #         ConnectionResetError,
    #     )
    #     non_retryable = (
    #         AuthenticationError,
    #         ValueError,
    #     )
    #     if isinstance(error, non_retryable):
    #         return False
    #     return isinstance(error, transient_errors)

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

    @staticmethod
    def _validate_input_budget(
        user_query: str,
        system_prompt: str,
        tools: list[dict[str, Any]] | None,
    ) -> None:
        tools_text = json.dumps(tools or [], ensure_ascii=False, separators=(",", ":"))
        estimated_tokens = (
            estimate_tokens(user_query)
            + estimate_tokens(system_prompt)
            + estimate_tokens(tools_text)
        )
        if estimated_tokens > settings.llm_input_token_budget:
            raise BudgetExceededError(
                f"Estimated token usage {estimated_tokens} exceeds the maximum limit of {settings.llm_input_token_budget}."
            )


class ModelAuthError(Exception):
    """Raised when model provider authentication fails."""


class ModelRequestError(Exception):
    """Raised when model request fails for transient/provider reasons."""


class BudgetExceededError(Exception):
    """Raised when model request exceeds the maximum token limit."""
