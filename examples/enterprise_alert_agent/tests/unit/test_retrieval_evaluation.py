import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.evaluate_retrieval import calculate_metrics

GOLDEN_SET = Path(__file__).parents[2] / "data/evaluation/retrieval_golden_set.json"


def test_calculate_metrics_for_second_rank_hit() -> None:
    metrics = calculate_metrics(["irrelevant", "relevant"], {"relevant"}, top_k=2)

    assert metrics == {
        "hit_rate": 1.0,
        "recall": 1.0,
        "mrr": 0.5,
        "ndcg": pytest.approx(1 / math.log2(3)),
    }


def test_calculate_metrics_for_multiple_relevant_documents() -> None:
    metrics = calculate_metrics(
        ["relevant-a", "irrelevant", "relevant-b"],
        {"relevant-a", "relevant-b"},
        top_k=2,
    )

    assert metrics["hit_rate"] == 1.0
    assert metrics["recall"] == 0.5
    assert metrics["mrr"] == 1.0
    assert metrics["ndcg"] == pytest.approx(1 / (1 + 1 / math.log2(3)))


def test_calculate_metrics_rejects_empty_relevance_set() -> None:
    with pytest.raises(ValueError, match="relevant_ids must not be empty"):
        calculate_metrics(["doc"], set(), top_k=1)


def test_retrieval_golden_set_references_existing_documents() -> None:
    dataset = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
    corpus_ids = {document["id"] for document in dataset["corpus"]}

    assert dataset["cases"]
    assert len({case["id"] for case in dataset["cases"]}) == len(dataset["cases"])
    for case in dataset["cases"]:
        assert case["query"]
        assert case["top_k"] > 0
        assert set(case["relevant_doc_ids"]) <= corpus_ids


def test_flashrank_rerank_uses_text_passages(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.rag.retrieval import retriever as retriever_module

    class FakeRanker:
        def rerank(self, request: SimpleNamespace) -> list[dict[str, Any]]:
            assert request.passages == [
                {"id": "0", "text": "first"},
                {"id": "1", "text": "second"},
            ]
            return [{"id": "1", "score": 0.9}, {"id": "0", "score": 0.2}]

    monkeypatch.setattr(retriever_module, "_get_reranker", lambda _model: FakeRanker())
    monkeypatch.setattr(
        retriever_module,
        "RerankRequest",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    documents = [
        {"id": "first", "content": "first", "score": 0.1},
        {"id": "second", "content": "second", "score": 0.1},
    ]

    result = retriever_module.Retriever._flashrank_rerank("query", documents)

    assert [document["id"] for document in result] == ["second", "first"]
