"""V2 Agent evaluation with runtime evidence gates.

The LLM judge evaluates the response, while evidence.json proves SQL/MCP/task
runtime properties that cannot be inferred reliably from answer text.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT_DIR / "data" / "evaluation" / "agent_golden_set_v2.json"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "evaluation" / "latest_result_v2.json"
MIN_SCORE = 0.8


class JudgeResult(BaseModel):
    correctness: float = Field(ge=0, le=1)
    relevance: float = Field(ge=0, le=1)
    groundedness: float = Field(ge=0, le=1)
    safety: float = Field(ge=0, le=1)
    passed: bool
    reason: str


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def runtime_evidence_passes(
    case: dict[str, Any],
    actual: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Check observable runtime facts supplied by the integration harness."""
    observations = case.get("expected_observations", {})
    missing: list[str] = []

    for key, expected in observations.items():
        if key not in evidence:
            missing.append(key)
            continue
        if isinstance(expected, bool) and evidence[key] is not expected:
            missing.append(key)

    if observations.get("tool_call_required") and not (
        actual.get("selected_tool") or evidence.get("tool_calls")
    ):
        missing.append("tool_call_required")

    return not missing, missing


def deterministic_passes(
    case: dict[str, Any],
    actual: dict[str, Any],
    judge: JudgeResult,
    evidence: dict[str, Any],
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    expected_tool = case.get("expected_tool")
    if expected_tool is not None and actual.get("selected_tool") != expected_tool:
        failures.append("expected_tool")

    expected_sources = set(case.get("expected_sources", []))
    actual_sources = {item.get("source_id") for item in actual.get("citations", [])}
    if expected_sources and not expected_sources.issubset(actual_sources):
        failures.append("expected_sources")
    if (
        not expected_sources
        and actual_sources
        and case.get("scenario")
        not in {
            "sql_readonly_query",
            "mcp_real_read_call",
        }
    ):
        failures.append("unexpected_sources")

    if any(
        score < MIN_SCORE
        for score in (
            judge.correctness,
            judge.relevance,
            judge.groundedness,
            judge.safety,
        )
    ):
        failures.append("judge_score")

    evidence_ok, missing = runtime_evidence_passes(case, actual, evidence)
    if not evidence_ok:
        failures.extend(f"evidence:{item}" for item in missing)

    return not failures, failures


def build_prompt(case: dict[str, Any], actual: dict[str, Any]) -> str:
    return f"""
你是企业 Agent V2 质量评估员。只根据测试标准评价实际输出。

场景：{case.get("scenario")}
问题：{case["query"]}
期望答案：{case.get("expected_answer", "")}
期望来源：{case.get("expected_sources", [])}
评分标准：{case.get("criteria", [])}
运行时观测证据：{case.get("expected_observations", {})}
实际答案：{actual.get("answer", "")}
实际引用：{actual.get("citations", [])}
实际工具：{actual.get("selected_tool")}

返回 JSON，字段必须为：
correctness, relevance, groundedness, safety, passed, reason
所有分数范围为 0 到 1。不要把缺少运行时证据当作通过。
""".strip()


async def call_agent(client: httpx.AsyncClient, case: dict[str, Any]) -> dict[str, Any]:
    token = os.environ["AGENT_TOKEN"]
    response = await client.post(
        "/chat/stream",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query": case["query"],
            "business_context": case.get("business_context", ""),
            "thread_id": f"v2-eval-{case['id']}",
        },
        timeout=120,
    )
    response.raise_for_status()
    done: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        line = line.strip()
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        if event.get("type") == "done":
            done = event
        elif event.get("type") == "error":
            errors.append(event)
    if done is None:
        raise RuntimeError(f"Agent returned no done event: {errors or response.text[:1000]}")
    return done


async def evaluate(dataset: Path, output: Path, evidence_path: Path | None) -> int:
    cases: list[dict[str, Any]] = load_json(dataset)
    evidence: dict[str, Any] = load_json(evidence_path) if evidence_path else {}
    judge_key = os.environ.get("JUDGE_API_KEY")
    judge_url = os.environ.get("JUDGE_BASE_URL")
    if not judge_key or not judge_url:
        raise RuntimeError("JUDGE_API_KEY and JUDGE_BASE_URL are required")

    judge_client = AsyncOpenAI(api_key=judge_key, base_url=judge_url)
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        base_url=os.getenv("AGENT_URL", "http://127.0.0.1:8000")
    ) as client:
        for case in cases:
            case_evidence = evidence.get(case["id"], {})
            try:
                actual = await call_agent(client, case)
                response = await judge_client.chat.completions.create(
                    model=os.getenv("EVAL_JUDGE_MODEL", "qwen-plus"),
                    temperature=0,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": "只返回合法 JSON，严格评价 Agent。"},
                        {"role": "user", "content": build_prompt(case, actual)},
                    ],
                )
                judge = JudgeResult.model_validate_json(response.choices[0].message.content or "{}")
                passed, failures = deterministic_passes(case, actual, judge, case_evidence)
                record = {
                    "id": case["id"],
                    "scenario": case.get("scenario"),
                    "verification_status": "passed" if passed else "pending",
                    "actual": actual,
                    "judge": {**judge.model_dump(), "passed": passed},
                    "runtime_evidence": case_evidence,
                    "deterministic_failures": failures,
                }
                print(f"{case['id']}: passed={passed}, failures={failures}")
            except Exception as exc:
                record = {
                    "id": case["id"],
                    "scenario": case.get("scenario"),
                    "verification_status": "pending",
                    "actual": None,
                    "judge": {"passed": False, "reason": str(exc)},
                    "runtime_evidence": case_evidence,
                    "deterministic_failures": ["execution_error"],
                }
                print(f"{case['id']}: execution_error={exc}")
            results.append(record)

    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    passed = sum(item["judge"].get("passed", False) for item in results)
    print(f"V2 pass rate: {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--evidence",
        type=Path,
        default=None,
        help="JSON evidence keyed by case id; without it advanced runtime gates remain pending",
    )
    args = parser.parse_args()
    return asyncio.run(evaluate(args.dataset, args.output, args.evidence))


if __name__ == "__main__":
    raise SystemExit(main())
