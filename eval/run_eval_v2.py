#!/usr/bin/env python3
"""Evaluate prediction JSONL with the versioned V2 benchmark registry."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

try:
    from .evaluator_v2 import evaluate_record
except ImportError:
    from evaluator_v2 import evaluate_record

SPLIT_EXCLUDED_BENCHMARKS = {
    "id": {"MSMARCO-Rerank"},
    "ood": {"HELMET-Rerank"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--split",
        choices=("id", "ood"),
        default=None,
        help="Evaluation split. Excludes the split-inapplicable rerank task.",
    )
    parser.add_argument(
        "--exclude-benchmark",
        action="append",
        default=[],
        help="Additional benchmark to omit (can be repeated).",
    )
    parser.add_argument(
        "--protocol",
        choices=("native", "tagged"),
        default="native",
        help="native for Base/API responses; tagged for SFT/RL responses.",
    )
    parser.add_argument(
        "--prediction-field",
        default="prediction",
        help="Prediction field; falls back to answer/output/response.",
    )
    return parser.parse_args()


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_number}: invalid JSON: {exc}"
                ) from exc


def record_id(record: Mapping[str, Any]) -> str:
    value = record.get("id") or record.get("case_id")
    if value in (None, ""):
        raise ValueError("record is missing id/case_id")
    return str(value)


def prediction_value(record: Mapping[str, Any], preferred: str) -> Any:
    for key in (preferred, "answer", "output", "response", "text"):
        if key in record:
            return record[key]
    return ""


def index_jsonl(path: Path, kind: str) -> Dict[str, Dict[str, Any]]:
    """Load JSONL records without allowing order-dependent ID overwrites."""
    indexed: Dict[str, Dict[str, Any]] = {}
    duplicates = []
    for row in read_jsonl(path):
        case_id = record_id(row)
        if case_id in indexed:
            duplicates.append(case_id)
            continue
        indexed[case_id] = row
    if duplicates:
        duplicate_ids = ", ".join(sorted(set(duplicates))[:10])
        suffix = "..." if len(set(duplicates)) > 10 else ""
        raise ValueError(
            f"{path}: duplicate {kind} ID(s): {duplicate_ids}{suffix}"
        )
    return indexed


def main() -> None:
    args = parse_args()
    references = index_jsonl(args.data, "reference")
    predictions = index_jsonl(args.predictions, "prediction")
    unknown_prediction_ids = sorted(set(predictions) - set(references))
    if unknown_prediction_ids:
        preview = ", ".join(unknown_prediction_ids[:10])
        suffix = "..." if len(unknown_prediction_ids) > 10 else ""
        raise ValueError(
            f"{args.predictions}: prediction ID(s) absent from GT: "
            f"{preview}{suffix}"
        )
    # The rerank benchmark belongs to the other split in this dataset. Keep
    # this rule in the runner so a report cannot silently include a
    # non-applicable task just because the caller forgot a manual exclusion.
    split = args.split or (args.data.stem.lower() if args.data.stem.lower() in SPLIT_EXCLUDED_BENCHMARKS else None)
    excluded = set(args.exclude_benchmark)
    if split:
        excluded.update(SPLIT_EXCLUDED_BENCHMARKS[split])
    active_references = {
        case_id: record
        for case_id, record in references.items()
        if str(record.get("benchmark") or "") not in excluded
    }

    grouped = defaultdict(list)
    normalization_status = defaultdict(int)
    missing = []
    per_case = []
    for case_id, record in active_references.items():
        prediction_record = predictions.get(case_id)
        if prediction_record is None:
            missing.append(case_id)
            prediction = ""
        else:
            prediction = prediction_value(
                prediction_record,
                args.prediction_field,
            )
        result = evaluate_record(record, prediction, protocol=args.protocol)
        grouped[("benchmark", str(record.get("benchmark") or ""))].append(
            result.score
        )
        grouped[("ability", str(record.get("ability") or ""))].append(
            result.score
        )
        grouped[("metric", result.metric)].append(result.score)
        normalization_status[str(result.details.get("normalization_status") or "")] += 1
        per_case.append(
            {
                "id": case_id,
                "benchmark": record.get("benchmark"),
                "ability": record.get("ability"),
                "score": result.score,
                "metric": result.metric,
                "details": result.details,
            }
        )

    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    by_benchmark = {
        name: {"count": len(values), "score": mean(values)}
        for (group_type, name), values in sorted(grouped.items())
        if group_type == "benchmark"
    }
    longbench_scores = [
        value["score"]
        for name, value in by_benchmark.items()
        if name.startswith("LongBench-Pro-T")
    ]
    by_task = {
        name: value
        for name, value in by_benchmark.items()
        if not name.startswith("LongBench-Pro-T")
    }
    if longbench_scores:
        by_task["LongBench-Pro"] = {
            "count": sum(
                value["count"]
                for name, value in by_benchmark.items()
                if name.startswith("LongBench-Pro-T")
            ),
            "subtask_count": len(longbench_scores),
            "score": mean(longbench_scores),
        }

    report = {
        "evaluator_version": "v2",
        "data": str(args.data),
        "predictions": str(args.predictions),
        "protocol": args.protocol,
        "split": split,
        "case_count": len(active_references),
        "prediction_count": sum(
            case_id in predictions for case_id in active_references
        ),
        "excluded_case_count": len(references) - len(active_references),
        "excluded_benchmarks": sorted(excluded),
        "missing_prediction_count": len(missing),
        "missing_prediction_ids": missing,
        "case_macro_score": mean([item["score"] for item in per_case]),
        "benchmark_macro_score": mean([item["score"] for item in by_benchmark.values()]),
        "task_macro_score": mean([item["score"] for item in by_task.values()]),
        "by_benchmark": by_benchmark,
        "by_task": dict(sorted(by_task.items())),
        "by_ability": {
            name: {"count": len(values), "score": mean(values)}
            for (group_type, name), values in sorted(grouped.items())
            if group_type == "ability"
        },
        "by_metric": {
            name: {"count": len(values), "score": mean(values)}
            for (group_type, name), values in sorted(grouped.items())
            if group_type == "metric"
        },
        "normalization_status": dict(sorted(normalization_status.items())),
        "per_case": per_case,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(
        json.dumps(
            {
                "case_count": report["case_count"],
                "missing_prediction_count": report[
                    "missing_prediction_count"
                ],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
