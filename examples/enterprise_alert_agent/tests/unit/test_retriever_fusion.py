import pytest

from app.config.settings import settings
from app.rag.retrieval.retriever import Retriever


@pytest.fixture(autouse=True)
def _hybrid_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "retrieval_use_bm25", True)
    monkeypatch.setattr(settings, "retrieval_hybrid_alpha", 0.7)


def _ids(docs: list[dict]) -> list[str]:
    return [doc["id"] for doc in docs]


def test_rrf_returns_empty_when_both_lists_empty() -> None:
    assert Retriever._rrf_merge([], []) == []


def test_rrf_boosts_documents_found_by_both_retrievers() -> None:
    dense = [{"id": "a"}, {"id": "b"}]
    lexical = [{"id": "b"}, {"id": "c"}]

    merged = Retriever._rrf_merge(dense, lexical)

    assert _ids(merged) == ["b", "a", "c"]
    assert merged[0]["score"] == pytest.approx(0.7 / 62 + 0.3 / 61)


def test_rrf_without_bm25_uses_dense_ranking_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "retrieval_use_bm25", False)

    merged = Retriever._rrf_merge([{"id": "a"}, {"id": "b"}], [{"id": "z"}])

    assert _ids(merged) == ["a", "b", "z"]
    assert merged[-1]["score"] == 0  # 词法权重为 0，仅拉低排序位置


def test_rrf_does_not_mutate_input_documents() -> None:
    dense = [{"id": "a", "content": "x"}]

    Retriever._rrf_merge(dense, [])

    assert "score" not in dense[0]


def test_tail_turns_keeps_latest_turns() -> None:
    history = "User: q1\nAssistant: a1\nUser: q2\nAssistant: a2\nUser: q3"

    tail = Retriever._tail_turns(history, 2)

    assert "q3" in tail
    assert "q1" not in tail
