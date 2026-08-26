import time
from collections.abc import Callable
from enum import Enum
from threading import Lock
from typing import TypeVar

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a dependency circuit is open."""


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at = 0.0
        self._half_open_call = False
        self._lock = Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._refresh_state()
            return self._state

    def _refresh_state(self) -> None:
        if (
            self._state == CircuitState.OPEN
            and time.monotonic() - self._opened_at >= self.recovery_seconds
        ):
            self._state = CircuitState.HALF_OPEN
            self._half_open_call = False

    def before_call(self) -> None:
        with self._lock:
            self._refresh_state()
            if self._state == CircuitState.OPEN:
                raise CircuitOpenError(f"Circuit is open: {self.name}")
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_call:
                    raise CircuitOpenError(f"Circuit is testing recovery: {self.name}")
                self._half_open_call = True

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            self._half_open_call = False

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._half_open_call = False
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

    def call(self, operation: Callable[[], T]) -> T:
        self.before_call()
        try:
            result = operation()
        except Exception:
            self.record_failure()
            raise
        else:
            self.record_success()
            return result
