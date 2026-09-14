#!/usr/bin/env python3
"""Create a clean baseline SFT file without modifying the canonical source."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    from .dataset import iter_jsonl, validate_sft_record
except ImportError:
    from dataset import iter_jsonl, validate_sft_record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rejected", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.rejected.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    accepted = 0
    rejected = 0
    error_counts = Counter()
    benchmark_rejections = Counter()
    with args.output.open("w", encoding="utf-8") as output_handle, args.rejected.open(
        "w", encoding="utf-8"
    ) as rejected_handle:
        for record in iter_jsonl(args.input):
            total += 1
            errors = validate_sft_record(record)
            if errors:
                rejected += 1
                error_counts.update(errors)
                benchmark_rejections[str(record.get("benchmark") or "")] += 1
                rejected_handle.write(
                    json.dumps(
                        {
                            "id": record.get("id") or record.get("case_id"),
                            "benchmark": record.get("benchmark"),
                            "ability": record.get("ability"),
                            "errors": errors,
                            "record": record,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                continue
            accepted += 1
            output_handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )

    report = {
        "schema_version": "v3_sft_baseline_clean_report_v1",
        "input": str(args.input),
        "output": str(args.output),
        "rejected_output": str(args.rejected),
        "total": total,
        "accepted": accepted,
        "rejected": rejected,
        "error_counts": dict(error_counts),
        "benchmark_rejections": dict(benchmark_rejections),
    }
    with args.report.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
