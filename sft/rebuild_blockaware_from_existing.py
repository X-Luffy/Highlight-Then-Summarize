#!/usr/bin/env python3
"""Rewrite existing SFT targets with the canonical Stage-2 block-aware input.

The assistant targets are already finalized. This utility only replaces the
user message and preserves the existing full/strict split and target fields.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import sys

TRAIN_ROOT = Path(__file__).resolve().parents[1]
if str(TRAIN_ROOT) not in sys.path:
    sys.path.insert(0, str(TRAIN_ROOT))

from block_input import (  # noqa: E402
    count_prompt_tokens,
    load_stage2_blocks,
    load_stage2_rendered,
    render_training_prompt,
)
from sft.system_prompt import prepend_system_prompt  # noqa: E402


def rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected object")
            yield value


def write_rows(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(
                json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )


def rebuild(
    source: Path,
    output: Path,
    stage2_root: Path,
    report: Path,
) -> dict[str, Any]:
    rendered = load_stage2_rendered(stage2_root / "2_rendered_cases.jsonl")
    blocks = load_stage2_blocks(stage2_root / "2_blocks.jsonl")
    output_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    errors: Counter[str] = Counter()

    for row in rows(source):
        case_id = str(row.get("id") or "")
        if not case_id or case_id not in rendered or case_id not in blocks:
            reason = "missing_stage2_rendered_or_blocks"
            skipped.append({"id": case_id, "reason": reason})
            errors[reason] += 1
            continue
        try:
            prompt = render_training_prompt(rendered[case_id], blocks[case_id])
            prompt = prepend_system_prompt(prompt)
        except (KeyError, ValueError) as exc:
            reason = str(exc)
            skipped.append({"id": case_id, "reason": reason})
            errors["block_render_error"] += 1
            continue

        rebuilt = dict(row)
        rebuilt["messages"] = prompt + [
            message
            for message in (row.get("messages") or [])
            if isinstance(message, dict) and message.get("role") == "assistant"
        ]
        rebuilt["input_token_num_original"] = row.get("input_token_num")
        # This is a deterministic fallback count. Exact tokenizer accounting
        # can be recomputed offline without changing the rendered input.
        prompt_text = "\n".join(str(item.get("content") or "") for item in prompt)
        rebuilt["input_token_num"] = max(1, (len(prompt_text) + 3) // 4)
        rebuilt["block_renderer"] = {
            "version": "stage2_closed_block_v1",
            "block_count": len(blocks[case_id]),
            "block_ids": [str(item["block_id"]) for item in blocks[case_id]],
        }
        output_rows.append(rebuilt)

    write_rows(output, output_rows)
    write_rows(report.with_name(report.stem + "_skipped.jsonl"), skipped)
    result = {
        "source": str(source),
        "output": str(output),
        "stage2_root": str(stage2_root),
        "source_count": sum(1 for _ in rows(source)),
        "output_count": len(output_rows),
        "skipped_count": len(skipped),
        "skip_reasons": dict(errors),
        "renderer_version": "stage2_closed_block_v1",
        "token_count_mode": "deterministic_char_fallback",
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
    parser.add_argument("--stage2-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(rebuild(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
