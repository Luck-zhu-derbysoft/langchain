import asyncio

from app.infrastructure.agent.langgraph_reflection import (
    ReflectionExecutor,
    ReflectionWorkflow,
    ReviewDecision,
)


def test_approved_answer_skips_revision() -> None:
    revise_calls = 0

    async def review(query: str, draft: str, evidence: str) -> ReviewDecision:
        return ReviewDecision(approved=True, score=0.95)

    async def revise(
        query: str,
        draft: str,
        evidence: str,
        feedback: str,
    ) -> str:
        nonlocal revise_calls
        revise_calls += 1
        return "unexpected"

    workflow = ReflectionWorkflow(
        ReflectionExecutor(review=review, revise=revise),
        max_rounds=2,
    )
    outcome = asyncio.run(workflow.run(query="question", draft="draft", evidence="evidence"))

    assert outcome.approved
    assert outcome.answer == "draft"
    assert outcome.rounds == 0
    assert revise_calls == 0


def test_rejected_answer_is_revised_and_reviewed_again() -> None:
    review_calls = 0

    async def review(query: str, draft: str, evidence: str) -> ReviewDecision:
        nonlocal review_calls
        review_calls += 1
        return ReviewDecision(
            approved=review_calls >= 2,
            score=0.9 if review_calls >= 2 else 0.4,
            feedback="add risk statement",
        )

    async def revise(
        query: str,
        draft: str,
        evidence: str,
        feedback: str,
    ) -> str:
        return f"{draft}\nrisk: data may be incomplete"

    workflow = ReflectionWorkflow(
        ReflectionExecutor(review=review, revise=revise),
        max_rounds=2,
    )
    outcome = asyncio.run(workflow.run(query="question", draft="draft", evidence="evidence"))

    assert outcome.approved
    assert outcome.rounds == 1
    assert "risk" in outcome.answer
    assert outcome.trail == [
        "review:rejected",
        "revise:1",
        "review:approved",
    ]


def test_reviewer_failure_fails_open_without_revision() -> None:
    revise_calls = 0

    async def review(query: str, draft: str, evidence: str) -> ReviewDecision:
        raise RuntimeError("reviewer unavailable")

    async def revise(
        query: str,
        draft: str,
        evidence: str,
        feedback: str,
    ) -> str:
        nonlocal revise_calls
        revise_calls += 1
        return draft

    workflow = ReflectionWorkflow(
        ReflectionExecutor(review=review, revise=revise),
        max_rounds=2,
    )
    outcome = asyncio.run(workflow.run(query="question", draft="draft", evidence="evidence"))

    assert outcome.approved
    assert outcome.answer == "draft"
    assert outcome.rounds == 0
    assert outcome.review_error == "reviewer unavailable"
    assert revise_calls == 0


def test_reflection_stops_at_max_rounds() -> None:
    async def review(query: str, draft: str, evidence: str) -> ReviewDecision:
        return ReviewDecision(approved=False, score=0.2, feedback="still incomplete")

    async def revise(
        query: str,
        draft: str,
        evidence: str,
        feedback: str,
    ) -> str:
        return f"{draft}-revised"

    workflow = ReflectionWorkflow(
        ReflectionExecutor(review=review, revise=revise),
        max_rounds=2,
    )
    outcome = asyncio.run(workflow.run(query="question", draft="draft", evidence="evidence"))

    assert not outcome.approved
    assert outcome.rounds == 2
    assert outcome.answer == "draft-revised-revised"
