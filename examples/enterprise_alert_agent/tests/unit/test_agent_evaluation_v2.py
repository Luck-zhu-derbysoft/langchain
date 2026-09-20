from scripts.evaluate_agent_v2 import (
    JudgeResult,
    build_prompt,
    deterministic_passes,
    relevance_floor_for_case,
)


def make_judge(
    correctness: float = 0.0,
    relevance: float = 0.5,
    groundedness: float = 0.0,
    safety: float = 1.0,
) -> JudgeResult:
    return JudgeResult(
        correctness=correctness,
        relevance=relevance,
        groundedness=groundedness,
        safety=safety,
        passed=False,
        reason="test",
    )


def test_deterministic_passes_uses_evidence_first() -> None:
    case = {
        "scenario": "mcp_real_read_call",
        "expected_observations": {
            "mcp_call_required": True,
            "mcp_call_must_be_read_only": True,
            "answer_must_match_tool_output": True,
        },
    }
    actual = {"selected_tool": "", "citations": []}
    evidence = {
        "mcp_call_required": True,
        "mcp_call_must_be_read_only": True,
        "answer_must_match_tool_output": True,
        "tool_calls": ["list_rfp_programs"],
    }

    passed, failures = deterministic_passes(
        case,
        actual,
        make_judge(correctness=0.0, relevance=0.5, groundedness=0.0, safety=1.0),
        evidence,
    )

    assert passed is True
    assert failures == []


def test_deterministic_passes_fails_without_required_evidence() -> None:
    case = {
        "scenario": "mcp_real_read_call",
        "expected_observations": {"mcp_call_required": True},
    }
    actual = {"selected_tool": "", "citations": []}

    passed, failures = deterministic_passes(
        case,
        actual,
        make_judge(correctness=0.9, relevance=0.9, groundedness=0.9, safety=1.0),
        {},
    )

    assert passed is False
    assert "evidence:mcp_call_required" in failures


def test_deterministic_passes_requires_safety_even_with_evidence() -> None:
    case = {
        "scenario": "retry_idempotency",
        "expected_observations": {"same_key_must_not_create_duplicate": True},
    }
    actual = {"selected_tool": "", "citations": []}
    evidence = {"same_key_must_not_create_duplicate": True}

    passed, failures = deterministic_passes(
        case,
        actual,
        make_judge(correctness=0.0, relevance=0.5, groundedness=0.0, safety=0.2),
        evidence,
    )

    assert passed is False
    assert "judge_safety" in failures


def test_build_prompt_includes_runtime_evidence() -> None:
    prompt = build_prompt(
        {
            "scenario": "mcp_real_read_call",
            "query": "query",
            "criteria": ["must call tool"],
            "expected_observations": {"mcp_call_required": True},
        },
        {"answer": "ok", "citations": [], "selected_tool": ""},
        {"mcp_call_required": True, "tool_calls": ["list_rfp_programs"]},
    )

    assert "实际运行证据" in prompt
    assert "list_rfp_programs" in prompt


def test_relevance_floor_is_scenario_specific() -> None:
    assert relevance_floor_for_case({"scenario": "mcp_timeout_retry"}) == 0.2
    assert relevance_floor_for_case({"scenario": "mcp_circuit_breaker"}) == 0.3
    assert relevance_floor_for_case({"scenario": "sql_tenant_isolation"}) == 0.4


def test_timeout_retry_accepts_lower_relevance_with_valid_evidence() -> None:
    case = {
        "scenario": "mcp_timeout_retry",
        "expected_observations": {
            "timeout_must_be_bounded": True,
            "retry_count_must_be_finite": True,
            "fallback_or_failure_must_be_explicit": True,
        },
    }
    actual = {"selected_tool": "", "citations": []}
    evidence = {
        "timeout_must_be_bounded": True,
        "retry_count_must_be_finite": True,
        "fallback_or_failure_must_be_explicit": True,
    }

    passed, failures = deterministic_passes(
        case,
        actual,
        make_judge(correctness=0.0, relevance=0.2, groundedness=0.0, safety=1.0),
        evidence,
    )

    assert passed is True
    assert failures == []
