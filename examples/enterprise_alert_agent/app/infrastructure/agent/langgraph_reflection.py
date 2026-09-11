from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)


@dataclass
class ReviewDecision:
    approved: bool
    score: float
    feedback: str = ""


class ReflectionState(TypedDict):
    query: str
    evidence: str
    draft: str
    approved: bool
    score: float
    feedback: str
    rounds: int
    trail: list[str]
    review_error: str


@dataclass
class ReflectionOutcome:
    answer: str
    approved: bool
    score: float
    rounds: int
    trail: list[str]
    review_error: str = ""


@dataclass
class ReflectionExecutor:
    review: Callable[[str, str, str], Awaitable[ReviewDecision]]
    revise: Callable[[str, str, str, str], Awaitable[str]]


class ReflectionWorkflow:
    def __init__(self, execute: ReflectionExecutor, *, max_rounds: int = 3) -> None:
        self.execute = execute
        self.max_rounds = max(0, max_rounds)
        self._compiled_graph: CompiledStateGraph | None = None

    async def _review(self, state: ReflectionState) -> dict[str, object]:
        trail = [*state.get("trail", [])]
        try:
            decision = await self.execute.review(state["query"], state["draft"], state["evidence"])
        except Exception as e:
            logger.error(f"Review failed: {e}")
            trail.append("review:error->fail_open")
            return {"approved": False, "score": 0.0, "feedback": "", "trail": trail}
        trail.append("review:approved" if decision.approved else "review:rejected")
        return {
            "approved": decision.approved,
            "score": decision.score,
            "feedback": decision.feedback,
            "trail": trail,
        }

    async def reviser(self, state: ReflectionState) -> dict[str, object]:
        trail = [*state.get("trail", [])]
        revised_draft = await self.execute.revise(
            state["query"],
            state["draft"],
            state["evidence"],
            state.get("feedback", ""),
        )
        rounds = state.get("rounds", 0) + 1
        return {
            "draft": revised_draft.strip() or state["draft"],
            "rounds": rounds,
            "trail": [*trail, f"revise:{rounds}"],
        }

    def _route(self, state: ReflectionState) -> str:
        if state.get("approved", False):
            return "end"
        if state.get("rounds", 0) >= self.max_rounds:
            return "end"
        return "reviser"

    def build(self) -> StateGraph:
        state = StateGraph(ReflectionState)
        state.add_node("review", self._review)
        state.add_node("reviser", self.reviser)
        state.add_edge(START, "review")
        state.add_conditional_edges("review", self._route, {"reviser": "reviser", "end": END})
        state.add_edge("reviser", "review")
        return state

    def compiled(self) -> CompiledStateGraph:
        if not self._compiled_graph:
            self._compiled_graph = self.build().compile()
        return self._compiled_graph

    async def run(
        self,
        *,
        query: str,
        draft: str,
        evidence: str,
    ) -> ReflectionOutcome:
        result = await self.compiled().ainvoke(
            {
                "query": query,
                "draft": draft,
                "evidence": evidence,
                "approved": False,
                "score": 0.0,
                "feedback": "",
                "rounds": 0,
                "trail": [],
                "review_error": "",
            }
        )
        return ReflectionOutcome(
            answer=str(result.get("draft", draft)),
            approved=bool(result.get("approved", False)),
            score=float(result.get("score", 0.0)),
            rounds=int(result.get("rounds", 0)),
            trail=list(result.get("trail", [])),
            review_error=str(result.get("review_error", "")),
        )
