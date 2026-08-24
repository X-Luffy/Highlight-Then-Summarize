#!/usr/bin/env python3
"""Regression checks for source question extraction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PIPELINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))

from pipeline_common import question_from_prompt  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--canonical-ood",
        type=Path,
        default=PIPELINE_DIR.parent.parent.parent.parent
        / "data/v3/processed/v3_benchmark_gate_20260812/canonical_splits/ood.jsonl",
    )
    return parser.parse_args()


def assert_clean(question: str) -> None:
    assert question.strip(), "question is empty"
    for marker in ("Ranking:", "[ID:", "Document:", "Query:"):
        assert marker.lower() not in question.lower(), (
            "question still contains {!r}: {!r}".format(marker, question[:300])
        )


def main() -> None:
    synthetic = (
        "[ID: 1] Document: first document\n"
        "Query: first query\n"
        "Ranking: 1\n\n"
        "[ID: 2] Document: second document\n"
        "Query: second query\n"
    )
    assert question_from_prompt("HELMET-Rerank", synthetic) == "second query"

    path = parse_args().canonical_ood
    checked = 0
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row.get("source_dataset") != "HELMET-Rerank":
                    continue
                question = question_from_prompt(
                    "HELMET-Rerank",
                    row.get("prompt"),
                    str(row.get("problem") or ""),
                )
                assert_clean(question)
                checked += 1
    print("pipeline_common question regression PASS; HELMET-Rerank cases checked={}".format(checked))


if __name__ == "__main__":
    main()
