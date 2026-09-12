from app.infrastructure.llm.context_budget import estimate_tokens, trim_text


def test_estimate_tokens_for_empty_ascii_and_chinese_text() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 2
    assert estimate_tokens("告警") == 1


def test_trim_text_returns_text_within_budget() -> None:
    text = "a" * 1000

    trimmed = trim_text(text, 100)

    assert estimate_tokens(trimmed) <= 100
    assert "上下文已裁剪" in trimmed


def test_trim_text_keeps_requested_side() -> None:
    text = "old-context\n" * 100 + "latest-context"

    head = trim_text(text, 20, keep="head")
    tail = trim_text(text, 20, keep="tail")

    assert "old-context" in head
    assert "latest-context" in tail


def test_trim_text_handles_budget_smaller_than_marker() -> None:
    text = "0123456789"

    trimmed = trim_text(text, 1)

    assert trimmed == "01"
    assert estimate_tokens(trimmed) <= 1
