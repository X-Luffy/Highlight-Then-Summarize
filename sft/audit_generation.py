#!/usr/bin/env python3
"""Audit post-SFT generation structure and repetition."""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


TAG_NAMES = ("evidence", "summary", "answer")
TAG_RE = {
    name: re.compile(fr"<{name}>(.*?)</{name}>", re.IGNORECASE | re.DOTALL)
    for name in TAG_NAMES
}
OPEN_RE = {
    name: re.compile(fr"<{name}>", re.IGNORECASE) for name in TAG_NAMES
}
CLOSE_RE = {
    name: re.compile(fr"</{name}>", re.IGNORECASE) for name in TAG_NAMES
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def normalized_lines(text: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if len(re.sub(r"\s+", " ", line).strip()) >= 40
    ]


def row_metrics(prediction: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "chars": len(prediction),
        "starts_evidence": bool(re.match(r"\s*<evidence>", prediction, re.I)),
        "has_answer": bool(OPEN_RE["answer"].search(prediction)),
        "answer_closed": bool(CLOSE_RE["answer"].search(prediction)),
        "has_all_open_tags": all(OPEN_RE[name].search(prediction) for name in TAG_NAMES),
        "has_all_close_tags": all(
            CLOSE_RE[name].search(prediction) for name in TAG_NAMES
        ),
        "duplicate_long_line": False,
        "duplicate_line_count": 0,
        "post_answer_chars": 0,
    }
    line_counts = Counter(normalized_lines(prediction))
    duplicate_counts = [count - 1 for count in line_counts.values() if count > 1]
    metrics["duplicate_line_count"] = sum(duplicate_counts)
    metrics["duplicate_long_line"] = bool(duplicate_counts)

    answer_end = re.search(r"</answer>", prediction, re.IGNORECASE)
    if answer_end:
        metrics["post_answer_chars"] = len(prediction[answer_end.end() :].strip())

    for name in TAG_NAMES:
        match = TAG_RE[name].search(prediction)
        metrics[f"{name}_chars"] = len(match.group(1)) if match else 0
    return metrics


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"cases": 0}
    keys = (
        "starts_evidence",
        "has_answer",
        "answer_closed",
        "has_all_open_tags",
        "has_all_close_tags",
        "duplicate_long_line",
    )
    result: dict[str, Any] = {"cases": len(rows)}
    for key in keys:
        result[key] = sum(bool(row[key]) for row in rows)
    for key in ("chars", "evidence_chars", "summary_chars", "answer_chars"):
        values = sorted(int(row[key]) for row in rows)
        result[f"{key}_mean"] = round(statistics.mean(values), 2)
        result[f"{key}_p50"] = values[len(values) // 2]
        result[f"{key}_p90"] = values[min(len(values) - 1, int(len(values) * 0.9))]
        result[f"{key}_max"] = values[-1]
    return result


def main() -> None:
    args = parse_args()
    data_by_id = {str(row["id"]): row for row in read_jsonl(args.data)}
    predictions = read_jsonl(args.predictions)
    overall: list[dict[str, Any]] = []
    by_benchmark: dict[str, list[dict[str, Any]]] = defaultdict(list)
    examples: dict[str, list[str]] = defaultdict(list)

    for prediction_row in predictions:
        case_id = str(prediction_row.get("id") or "")
        prediction = str(prediction_row.get("prediction") or "")
        metrics = row_metrics(prediction)
        metrics["id"] = case_id
        benchmark = str(data_by_id.get(case_id, {}).get("benchmark") or "UNKNOWN")
        overall.append(metrics)
        by_benchmark[benchmark].append(metrics)
        for issue in (
            "has_answer",
            "answer_closed",
            "duplicate_long_line",
        ):
            if not metrics[issue] and len(examples[f"not_{issue}"]) < 10:
                examples[f"not_{issue}"].append(case_id)
        if metrics["duplicate_long_line"] and len(examples["duplicate"]) < 10:
            examples["duplicate"].append(case_id)

    report = {
        "predictions": str(args.predictions),
        "data": str(args.data),
        "overall": summarize(overall),
        "by_benchmark": {
            benchmark: summarize(rows)
            for benchmark, rows in sorted(by_benchmark.items())
        },
        "examples": dict(examples),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["overall"], ensure_ascii=False))


if __name__ == "__main__":
    main()
