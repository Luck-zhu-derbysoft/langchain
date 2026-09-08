import asyncio

from app.infrastructure.agent.langgraph_fault_recovery import (
    HUMAN_RETRY,
    HUMAN_SKIP,
    FaultExecutor,
    FaultRecoveryWorkflow,
    RecoveryAttempt,
)
from app.infrastructure.fault.fault_types import FaultContext


def _ctx(task_id: str = "task-1", message: str = "network error") -> FaultContext:
    return FaultContext(
        request_id=f"request-{task_id}",
        task_id=task_id,
        agent_id="test-agent",
        tool_name="test-tool",
        error_message=message,
        error_type="RuntimeError",
    )


def _executor(
    retry_results: tuple[bool, ...] = (False, False, False),
    *,
    fallback_success: bool = False,
    rag_success: bool = False,
) -> FaultExecutor:
    async def retry(ctx: FaultContext, attempt_number: int) -> RecoveryAttempt:
        success = retry_results[min(attempt_number, len(retry_results) - 1)]
        return RecoveryAttempt(success, f"retry-{attempt_number}")

    async def fallback(ctx: FaultContext, diagnosis: object) -> RecoveryAttempt:
        return RecoveryAttempt(fallback_success, "fallback-result")

    async def rag_only(ctx: FaultContext, diagnosis: object) -> RecoveryAttempt:
        return RecoveryAttempt(rag_success, "rag-result")

    return FaultExecutor(retry=retry, fallback=fallback, rag_only=rag_only)


def test_l1_loop_then_success() -> None:
    workflow = FaultRecoveryWorkflow(executor=_executor((False, True)))

    outcome = asyncio.run(workflow.run(_ctx("loop")))

    assert outcome.resolved
    assert outcome.attempts == 1
    assert outcome.trail[-2:] == ["l1_retry:fail->retry", "l1_retry:ok"]


def test_l1_escalates_to_l2() -> None:
    workflow = FaultRecoveryWorkflow(
        executor=_executor((False, False, False), fallback_success=True)
    )

    outcome = asyncio.run(workflow.run(_ctx("fallback")))

    assert outcome.resolved
    assert outcome.level == 2
    assert outcome.output == "fallback-result"
    assert outcome.trail[-1] == "l2_adjust:ok"


def test_l2_escalates_to_l3_rag() -> None:
    workflow = FaultRecoveryWorkflow(executor=_executor((False, False, False), rag_success=True))

    outcome = asyncio.run(workflow.run(_ctx("rag")))

    assert outcome.resolved
    assert outcome.level == 3
    assert outcome.output == "rag-result"
    assert outcome.trail[-1] == "l3_rag_only:ok"


def test_hitl_skip_resumes_and_completes() -> None:
    workflow = FaultRecoveryWorkflow(executor=_executor())

    waiting = asyncio.run(workflow.run(_ctx("skip"), thread_id="skip"))
    completed = asyncio.run(workflow.run(_ctx("skip"), thread_id="skip", resume=HUMAN_SKIP))

    assert waiting.status == "awaiting_human"
    assert waiting.human_question is not None
    assert completed.resolved
    assert completed.trail[-1] == "l4_human_intervention:skip"


def test_hitl_retry_returns_to_l1() -> None:
    retry_calls = 0

    async def retry(ctx: FaultContext, attempt_number: int) -> RecoveryAttempt:
        nonlocal retry_calls
        retry_calls += 1
        return RecoveryAttempt(retry_calls >= 4, f"retry-{retry_calls}")

    workflow = FaultRecoveryWorkflow(
        executor=FaultExecutor(retry=retry, fallback=None, rag_only=None)
    )

    waiting = asyncio.run(workflow.run(_ctx("retry"), thread_id="retry"))
    completed = asyncio.run(workflow.run(_ctx("retry"), thread_id="retry", resume=HUMAN_RETRY))

    assert waiting.status == "awaiting_human"
    assert completed.resolved
    assert "l4_human_intervention:retry" in completed.trail
    assert completed.trail[-1] == "l1_retry:ok"
