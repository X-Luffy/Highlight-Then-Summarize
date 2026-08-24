#!/usr/bin/env python3
"""Materialize Stage-2 source rows and migrate the old OOD delivery to train."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Set

from pipeline_common import (
    answer_text,
    iter_jsonl,
    message_list,
    read_json,
    stable_fraction,
    write_json,
    write_jsonl,
)


PIPELINE_DIR = Path(__file__).resolve().parent
CODE_ROOT = PIPELINE_DIR.parent
DEFAULT_CONFIG = PIPELINE_DIR / "configs/incremental_train_test.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--candidate-pool", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=PIPELINE_DIR / "output/incremental")
    parser.add_argument(
        "--smoke-per-benchmark",
        type=int,
        default=0,
        help="Keep at most this many new source rows per benchmark and split.",
    )
    return parser.parse_args()


def read_rows(path: Path) -> Iterable[Dict[str, Any]]:
    return (row for _, row in iter_jsonl(path))


def copy_old_ood_as_source(
    path: Path,
    split: str,
    rl_ratio: float,
) -> List[Dict[str, Any]]:
    rows = []
    for _, row in iter_jsonl(path):
        identifier = str(row.get("id") or "")
        if not identifier:
            continue
        messages = row.get("input")
        if not isinstance(messages, list):
            messages = row.get("messages") or row.get("prompt") or []
        normalized = {
            "id": identifier,
            "case_id": identifier,
            "benchmark": row.get("benchmark"),
            "ability": row.get("ability"),
            "question": row.get("question"),
            "prompt": message_list(messages),
            "gt": row.get("gt") if isinstance(row.get("gt"), Mapping) else {},
            "input_token_num": row.get("input_token_num"),
            "length_bucket": row.get("length_bucket"),
            "source_split": "legacy_ood",
            "source_dataset": row.get("benchmark"),
            "source_row_type": "legacy_ood_delivery",
        }
        normalized["materialize_split"] = "rl" if stable_fraction(identifier) < rl_ratio else "sft"
        normalized["assistant"] = {"answer": answer_text(normalized["gt"])}
        if normalized["materialize_split"] == "sft":
            user_messages = [
                item
                for item in normalized["prompt"]
                if str(item.get("role") or "") == "user"
            ]
            normalized["messages"] = user_messages + [
                {
                    "role": "assistant",
                    "content": answer_text(normalized["gt"]),
                }
            ]
        rows.append(normalized)
    return rows


def main() -> None:
    args = parse_args()
    config = read_json(args.config)
    output_root = args.output_root.resolve()
    source_root = output_root / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    candidate_path = args.candidate_pool or output_root / "candidate_pool.jsonl"

    new_sft: List[Dict[str, Any]] = []
    new_rl: List[Dict[str, Any]] = []
    candidate_rows = list(read_rows(candidate_path))
    ratio = float(config.get("new_rl_ratio") or 0.2)
    for row in candidate_rows:
        row = dict(row)
        row["materialize_split"] = "rl" if stable_fraction(str(row["id"])) < ratio else "sft"
        row["assistant"] = {"answer": answer_text(row.get("gt"))}
        if row["materialize_split"] == "sft":
            row["messages"] = list(row.get("prompt") or []) + [
                {
                    "role": "assistant",
                    "content": answer_text(row.get("gt")),
                }
            ]
        if row["materialize_split"] == "rl":
            new_rl.append(row)
        else:
            new_sft.append(row)

    if args.smoke_per_benchmark > 0:
        def limit_by_benchmark(values: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            counts = Counter()
            limited = []
            for value in values:
                benchmark = str(value.get("benchmark") or "")
                if counts[benchmark] >= args.smoke_per_benchmark:
                    continue
                counts[benchmark] += 1
                limited.append(value)
            return limited

        new_sft = limit_by_benchmark(new_sft)
        new_rl = limit_by_benchmark(new_rl)

    train_data = Path(config["train_data_root"])
    legacy = copy_old_ood_as_source(
        train_data / "ood_v1.jsonl",
        "legacy_ood",
        float(config.get("legacy_ood_rl_ratio") or ratio),
    )
    legacy_sft = [row for row in legacy if row["materialize_split"] == "sft"]
    legacy_rl = [row for row in legacy if row["materialize_split"] == "rl"]

    # Stage-2 only consumes new cases. Legacy OOD is already processed and is
    # passed directly to the materializer as a source sidecar.
    write_jsonl(source_root / "sft.jsonl", new_sft)
    write_jsonl(source_root / "rl.jsonl", new_rl)
    write_jsonl(output_root / "legacy_ood_sft_source.jsonl", legacy_sft)
    write_jsonl(output_root / "legacy_ood_rl_source.jsonl", legacy_rl)

    by_benchmark = Counter(str(row.get("benchmark") or "") for row in candidate_rows)
    manifest = {
        "schema_version": "v3_incremental_source_manifest_v1",
        "candidate_count": len(candidate_rows),
        "new_sft_count": len(new_sft),
        "new_rl_count": len(new_rl),
        "legacy_ood_count": len(legacy),
        "legacy_ood_sft_count": len(legacy_sft),
        "legacy_ood_rl_count": len(legacy_rl),
        "new_rl_ratio": ratio,
        "legacy_ood_rl_ratio": float(config.get("legacy_ood_rl_ratio") or ratio),
        "candidate_by_benchmark": dict(sorted(by_benchmark.items())),
        "stage2_data_root": str(source_root),
        "legacy_ood_sft_source": str(output_root / "legacy_ood_sft_source.jsonl"),
        "legacy_ood_rl_source": str(output_root / "legacy_ood_rl_source.jsonl"),
    }
    write_json(output_root / "source_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
