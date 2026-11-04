# tests/unit/test_circuit_breaker.py
import pytest

from app.infrastructure.fault.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)


def _fail() -> None:
    raise ValueError("boom")


def test_opens_after_failure_threshold() -> None:
    breaker = CircuitBreaker("llm", failure_threshold=3, recovery_seconds=60)

    for _ in range(3):
        breaker.record_failure()

    assert breaker.state == CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_success_resets_failure_count() -> None:
    breaker = CircuitBreaker("llm", failure_threshold=3, recovery_seconds=60)

    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()

    assert breaker.state == CircuitState.CLOSED


def test_call_propagates_error_and_short_circuits_when_open() -> None:
    breaker = CircuitBreaker("llm", failure_threshold=1, recovery_seconds=60)
    calls: list[int] = []

    with pytest.raises(ValueError):
        breaker.call(_fail)
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: calls.append(1))

    assert calls == []


def test_half_open_allows_single_probe_then_closes_on_success() -> None:
    breaker = CircuitBreaker("llm", failure_threshold=1, recovery_seconds=0)
    breaker.record_failure()

    assert breaker.state == CircuitState.HALF_OPEN
    breaker.before_call()
    with pytest.raises(CircuitOpenError):
        breaker.before_call()

    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.call(lambda: "ok") == "ok"
