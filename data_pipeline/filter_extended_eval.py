#!/usr/bin/env python3
"""Remove SFT/RL overlaps from an already-built extended evaluation pool."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

import build_extended_eval as builder


MIN_CASES = 100
MAX_CASES = 500
ALLOWED_SHORTFALL_PREFIXES = ("LongBench-Pro-",)
BLOCK_OPEN_RE = re.compile(r"\[BLOCK_ID:\s*[A-Za-z0-9_-]+\]")
BLOCK_CLOSE_RE = re.compile(
    r"\[/(?:BLOCK_ID|Block[ _]?ID)(?:\s*:\s*[A-Za-z0-9_-]+)?\]",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=builder.ROOT / "data")
    return parser.parse_args()


def row_text(row: Dict[str, Any]) -> str:
    messages = builder.flatten_messages(row.get("input") or row.get("prompt") or row.get("messages"))
    return "\n".join(str(item.get("content") or "") for item in messages)


def main() -> None:
    args = parse_args()
    data_dir = args.data_dir
    train_ids, train_fingerprints = builder.load_training_fingerprints(
        [data_dir / "sft_v1.jsonl", data_dir / "rl_v1.jsonl"]
    )

    report: Dict[str, Any] = {
        "training_id_count": len(train_ids),
        "training_fingerprint_count": len(train_fingerprints),
        "splits": {},
    }

    for split in ("id", "ood"):
        source_path = data_dir / (split + "_v1_extended.jsonl")
        rows: List[Dict[str, Any]] = []
        removed: Counter = Counter()
        seen_fingerprints = set()
        total = 0
        for row in builder.read_jsonl(source_path):
            total += 1
            benchmark = str(row.get("benchmark") or "")
            if builder.source_id(row) in train_ids:
                removed[benchmark] += 1
                continue
            if builder.row_fingerprint(row) in train_fingerprints:
                removed[benchmark] += 1
                continue
            fingerprint = builder.row_fingerprint(row)
            if fingerprint in seen_fingerprints:
                removed[benchmark] += 1
                continue
            seen_fingerprints.add(fingerprint)
            rows.append({key: value for key, value in row.items() if key != "_source_line_number"})

        counts = Counter(str(row.get("benchmark") or "") for row in rows)
        baseline_rows = list(builder.read_jsonl(data_dir / (split + "_v1.jsonl")))
        baseline_benchmarks = {str(row.get("benchmark") or "") for row in baseline_rows}
        unexpected = sorted(set(counts) - baseline_benchmarks)
        overflows = {name: count for name, count in sorted(counts.items()) if count > MAX_CASES}
        shortfalls = {
            name: count
            for name, count in sorted(counts.items())
            if count < MIN_CASES
            and not any(name.startswith(prefix) for prefix in ALLOWED_SHORTFALL_PREFIXES)
        }
        baseline_system = (
            (baseline_rows[0].get("input") or [{}])[0].get("content")
            if baseline_rows
            else None
        )
        schema_errors = []
        for index, row in enumerate(rows, start=1):
            messages = row.get("input")
            if (
                not isinstance(messages, list)
                or len(messages) < 2
                or (messages[0].get("content") if isinstance(messages[0], dict) else None)
                != baseline_system
            ):
                schema_errors.append({"line": index, "reason": "system_or_input_schema"})
                continue
            text = row_text(row)
            user_text = "\n".join(
                m["content"] for m in builder.flatten_messages(row.get("input"))
                if m.get("role") == "user"
            )
            outer_question = re.match(r"(?s)\A\[question\]\s*.+?\s*", user_text)
            outer_doc = re.search(r"(?m)^\[Doc\]\s*$", user_text)
            if not outer_question or not outer_doc or outer_doc.start() <= outer_question.start():
                schema_errors.append({"line": index, "reason": "missing_question_or_doc"})
            if BLOCK_CLOSE_RE.search(text):
                schema_errors.append({"line": index, "reason": "closed_block_marker"})
            if not BLOCK_OPEN_RE.search(text):
                schema_errors.append({"line": index, "reason": "missing_block_marker"})

        if unexpected or overflows or shortfalls or schema_errors:
            raise RuntimeError(
                json.dumps(
                    {
                        "split": split,
                        "unexpected": unexpected,
                        "overflows": overflows,
                        "blocking_shortfalls": shortfalls,
                        "schema_errors": schema_errors[:20],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )

        output_path = data_dir / (split + "_v1_extended.jsonl")
        builder.write_jsonl(output_path, rows)
        report["splits"][split] = {
            "source_total": total,
            "output_total": len(rows),
            "removed_total": sum(removed.values()),
            "removed_by_benchmark": dict(sorted(removed.items())),
            "by_benchmark": dict(sorted(counts.items())),
            "allowed_shortfalls": {
                name: count
                for name, count in sorted(counts.items())
                if count < MIN_CASES
            },
            "schema_errors": 0,
        }

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
