#!/usr/bin/env python3
"""Validate an SFT JSONL file."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

try:
    from .dataset import (
        iter_jsonl,
        validate_messages_sft_record,
        validate_sft_record,
    )
except ImportError:
    from dataset import (
        iter_jsonl,
        validate_messages_sft_record,
        validate_sft_record,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--max-error-examples", type=int, default=20)
    parser.add_argument(
        "--format",
        choices=("auto", "baseline", "messages"),
        default="auto",
    )
    args = parser.parse_args()

    count = 0
    errors = Counter()
    examples = []
    detected_format = None
    for record in iter_jsonl(args.data):
        count += 1
        if args.format == "messages" or (
            args.format == "auto" and "messages" in record
        ):
            detected_format = "messages"
            record_errors = validate_messages_sft_record(record)
        else:
            detected_format = "baseline"
            record_errors = validate_sft_record(record)
        errors.update(record_errors)
        if record_errors and len(examples) < args.max_error_examples:
            examples.append(
                {
                    "id": record.get("id") or record.get("case_id"),
                    "errors": record_errors,
                }
            )
    report = {
        "data": str(args.data),
        "case_count": count,
        "format": detected_format or args.format,
        "error_counts": dict(errors),
        "error_examples": examples,
        "valid": not errors,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
