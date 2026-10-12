import argparse
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from statistics import mean
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT_DIR / "data/evaluation/retrieval_golden_set.json"
DEFAULT_OUTPUT = ROOT_DIR / "data/evaluation/latest_retrieval_result.json"


def calculate_metrics(
    retrieved_ids: list[str],
    relevant_ids: set[str],
    top_k: int,
) -> dict[str, float]:
    if not relevant_ids:
        raise ValueError("relevant_ids must not be empty")

    ranked_ids = retrieved_ids[:top_k]
    relevant_ranks = [
        rank for rank, doc_id in enumerate(ranked_ids, start=1) if doc_id in relevant_ids
    ]
    hit_rate = float(bool(relevant_ranks))
    recall = len(set(ranked_ids) & relevant_ids) / len(relevant_ids)
    reciprocal_rank = 1 / relevant_ranks[0] if relevant_ranks else 0.0
    dcg = sum(1 / math.log2(rank + 1) for rank in relevant_ranks)
    ideal_hits = min(len(relevant_ids), top_k)
    idcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    ndcg = dcg / idcg if idcg else 0.0
    return {
        "hit_rate": hit_rate,
        "recall": recall,
        "mrr": reciprocal_rank,
        "ndcg": ndcg,
    }


def evaluate(args: argparse.Namespace) -> int:
    dataset: dict[str, Any] = json.loads(args.dataset.read_text(encoding="utf-8"))
    corpus = dataset.get("corpus", [])
    cases = dataset.get("cases", [])
    if not corpus or not cases:
        raise ValueError("dataset must contain non-empty corpus and cases")

    with tempfile.TemporaryDirectory(
        prefix="rag-evaluation-", ignore_cleanup_errors=True
    ) as temp_dir:
        # 必须在导入 app 模块前设置，确保不污染业务 Chroma collection。
        os.environ["CHROMA_PERSIST_DIRECTORY"] = temp_dir
        os.environ["CHROMA_COLLECTION_NAME"] = "retrieval_evaluation"
        os.environ["LANGSMITH_TRACING"] = "false"
        sys.path.insert(0, str(ROOT_DIR))
        from app.infrastructure.embedding.embedding_client import EmbeddingClient
        from app.infrastructure.vectorstore.chroma_store import ChromaStore
        from app.observability.langsmith_tracer import LangSmithTracer
        from app.rag.retrieval.retriever import Retriever

        trace = LangSmithTracer(
            client=None,
            _enabled=False,
            project_name="retrieval-evaluation",
        )
        store = ChromaStore(EmbeddingClient(trace), trace)
        retriever = Retriever(store, trace)
        store.add_documents(
            texts=[doc["content"] for doc in corpus],
            metadatas=[doc["metadata"] for doc in corpus],
            ids=[doc["id"] for doc in corpus],
        )
        case_results: list[dict[str, Any]] = []
        for case in cases:
            top_k = int(case.get("top_k", 3))
            documents = retriever.retrieve(case["query"], top_k=top_k)
            retrieved_ids = [doc["id"] for doc in documents]
            metrics = calculate_metrics(retrieved_ids, set(case["relevant_doc_ids"]), top_k)
            case_results.append(
                {
                    "id": case["id"],
                    "retrieved_ids": retrieved_ids,
                    "metrics": metrics,
                }
            )
    aggregate = {
        metric: mean(result["metrics"][metric] for result in case_results)
        for metric in ("hit_rate", "recall", "mrr", "ndcg")
    }
    thresholds = {
        "hit_rate": args.min_hit_rate,
        "recall": args.min_recall,
        "mrr": args.min_mrr,
        "ndcg": args.min_ndcg,
    }
    failures = [metric for metric, threshold in thresholds.items() if aggregate[metric] < threshold]
    args.output.write_text(
        json.dumps(
            {
                "metrics": aggregate,
                "thresholds": thresholds,
                "failures": failures,
                "cases": case_results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(aggregate, indent=2))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--min-hit-rate", type=float, default=0.8)
    parser.add_argument("--min-recall", type=float, default=0.8)
    parser.add_argument("--min-mrr", type=float, default=0.75)
    parser.add_argument("--min-ndcg", type=float, default=0.75)
    args = parser.parse_args()
    return evaluate(args)


if __name__ == "__main__":
    raise SystemExit(main())
