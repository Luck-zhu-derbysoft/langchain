import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.evaluate_agent import (
    JudgeResult,
    build_judge_prompt,
    is_case_passed,
    load_case,
)

GOLDEN_SET = Path(__file__).parents[2] / "data" / "evaluation" / "agent_golden_set.json"


def passing_result() -> JudgeResult:
    return JudgeResult(
        correctness=0.9,
        relevance=0.9,
        groundedness=0.9,
        safety=0.9,
        passed=True,
        reason="pass",
    )


def test_golden_set_has_required_fields() -> None:
    cases = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))

    assert cases
    for case in cases:
        assert case["id"]
        assert case["query"]
        assert case["criteria"]
        assert "expected_tool" in case


def test_load_case_reads_golden_set() -> None:
    assert len(load_case()) == 2


def test_judge_result_rejects_out_of_range_score() -> None:
    with pytest.raises(ValidationError):
        JudgeResult(
            correctness=1.1,
            relevance=1,
            groundedness=1,
            safety=1,
            passed=True,
            reason="invalid",
        )


def test_is_case_passed_requires_scores_and_expected_tool() -> None:
    case = {"expected_tool": "rag"}
    actual = {"selected_tool": "rag"}

    assert is_case_passed(case, actual, passing_result()) is True
    assert is_case_passed(case, {"selected_tool": "time"}, passing_result()) is False

    low_score = passing_result().model_copy(update={"correctness": 0.79})
    assert is_case_passed(case, actual, low_score) is False


def test_judge_prompt_contains_expected_tool_and_actual_answer() -> None:
    prompt = build_judge_prompt(
        {
            "query": "连续失败几次需要升级？",
            "expected_answer": "连续 3 次失败后升级",
            "expected_tool": "rag",
            "criteria": ["必须包含 3 次"],
        },
        {
            "answer": "连续 3 次失败后升级到二线",
            "citations": [],
            "selected_tool": "rag",
        },
    )

    assert "期望工具" in prompt
    assert "rag" in prompt
    assert "连续 3 次失败后升级到二线" in prompt
