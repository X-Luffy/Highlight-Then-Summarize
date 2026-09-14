#!/usr/bin/env python3
"""Add the canonical system prompt and recompute exact input token counts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


TRAIN_ROOT = Path(__file__).resolve().parents[1]
if str(TRAIN_ROOT) not in sys.path:
    sys.path.insert(0, str(TRAIN_ROOT))

from sft.system_prompt import prepend_system_prompt  # noqa: E402


def build(
    source: Path,
    output: Path,
    report: Path,
    tokenizer_path: Path,
    batch_size: int,
) -> dict[str, Any]:
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        str(tokenizer_path),
        local_files_only=True,
        use_fast=True,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    pending: list[dict[str, Any]] = []
    count = 0
    maximum = 0

    with source.open("r", encoding="utf-8") as source_handle, output.open(
        "w", encoding="utf-8"
    ) as output_handle:

        def flush() -> None:
            nonlocal count, maximum
            if not pending:
                return
            texts = [
                "\n".join(
                    str(message.get("content") or "")
                    for message in row["messages"]
                    if message.get("role") != "assistant"
                )
                for row in pending
            ]
            encoded = tokenizer(
                texts,
                add_special_tokens=False,
                padding=False,
            )
            for row, input_ids in zip(pending, encoded["input_ids"]):
                token_count = len(input_ids)
                row["input_token_num"] = token_count
                row["input_token_num_mode"] = "qwen2.5-7b-instruct-fast"
                maximum = max(maximum, token_count)
                output_handle.write(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                count += 1
            pending.clear()

        for line in source_handle:
            if not line.strip():
                continue
            row = json.loads(line)
            row["messages"] = prepend_system_prompt(row.get("messages") or [])
            pending.append(row)
            if len(pending) >= batch_size:
                flush()
        flush()

    result = {
        "source": str(source),
        "output": str(output),
        "count": count,
        "max_input_token_num": maximum,
        "tokenizer": str(tokenizer_path),
        "batch_size": batch_size,
        "system_prompt_added": True,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--tokenizer-path", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()
    print(json.dumps(build(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
