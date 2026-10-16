import random
import time

import httpx
from openai import APIConnectionError, APIStatusError
from pydantic.dataclasses import dataclass

_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


def is_retryable(exc: BaseException) -> bool:

    if isinstance(exc, (APIConnectionError, TimeoutError, ConnectionError, httpx.TransportError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in _RETRYABLE_STATUS
    return False


def retry_after_seconds(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    value = response.headers.get("retry-after")

    try:
        return max(0, float(value)) if value is not None else None
    except ValueError:
        return None


def backoff_delay(attempt: int, base: float, cap: float, exc: BaseException | None = None) -> float:
    hinted = retry_after_seconds(exc)  # type: ignore
    if hinted is not None:
        return min(hinted, cap)
    return random.uniform(0, min(cap, base * (2 ** (attempt))))


@dataclass(slots=True)
class DeadLine:
    expires_at: float

    @classmethod
    def after(cls, seconds: float) -> DeadLine:  # noqa: F821
        return cls(time.monotonic() + seconds)

    def remaining(self) -> float:
        return max(0, self.expires_at - time.monotonic())

    def can_afford(self, delay: float, min_attempt_seconds: float = 1.0) -> bool:
        return self.remaining() > delay + min_attempt_seconds
