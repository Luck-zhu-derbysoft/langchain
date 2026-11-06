# tests/unit/test_retry_policy.py
import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from app.infrastructure.fault import retry_policy
from app.infrastructure.fault.retry_policy import (
    DeadLine,
    backoff_delay,
    is_retryable,
    retry_after_seconds,
)

_REQUEST = httpx.Request("POST", "https://llm.example.com/v1/chat")


def _http_status_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    response = httpx.Response(status, headers=headers, request=_REQUEST)
    return httpx.HTTPStatusError("err", request=_REQUEST, response=response)


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_http_status_is_retryable(status: int) -> None:
    assert is_retryable(_http_status_error(status))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_http_status_is_not_retryable(status: int) -> None:
    assert not is_retryable(_http_status_error(status))


def test_network_errors_are_retryable() -> None:
    assert is_retryable(TimeoutError())
    assert is_retryable(ConnectionError())
    assert is_retryable(httpx.ConnectError("down", request=_REQUEST))
    assert is_retryable(APIConnectionError(request=_REQUEST))


def test_openai_status_error_follows_status_table() -> None:
    retryable = APIStatusError("x", response=httpx.Response(503, request=_REQUEST), body=None)
    permanent = APIStatusError("x", response=httpx.Response(401, request=_REQUEST), body=None)

    assert is_retryable(retryable)
    assert not is_retryable(permanent)


def test_business_error_is_not_retryable() -> None:
    assert not is_retryable(ValueError("bad input"))


def test_retry_after_header_parsing() -> None:
    assert retry_after_seconds(_http_status_error(429, {"retry-after": "3"})) == 3
    assert retry_after_seconds(_http_status_error(429, {"retry-after": "abc"})) is None
    assert retry_after_seconds(_http_status_error(429, {"retry-after": "-5"})) == 0
    assert retry_after_seconds(_http_status_error(429)) is None
    assert retry_after_seconds(ValueError("no response")) is None


def test_backoff_delay_prefers_server_hint_capped() -> None:
    exc = _http_status_error(429, {"retry-after": "30"})

    assert backoff_delay(0, base=1, cap=10, exc=exc) == 10
    assert backoff_delay(0, base=1, cap=60, exc=exc) == 30


def test_backoff_delay_exponential_upper_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retry_policy.random, "uniform", lambda _low, high: high)

    assert backoff_delay(0, base=0.5, cap=100) == 0.5
    assert backoff_delay(3, base=0.5, cap=100) == 4.0
    assert backoff_delay(10, base=0.5, cap=5) == 5


def test_deadline_budget() -> None:
    deadline = DeadLine.after(10)

    assert deadline.can_afford(1)
    assert not deadline.can_afford(20)
    assert DeadLine(expires_at=0.0).remaining() == 0
