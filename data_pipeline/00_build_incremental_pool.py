#!/usr/bin/env python3
"""Build the unused OOD candidate pool for the new training split.

The script is metadata-only. It does not call an API and does not build blocks.
It removes already delivered cases, keeps LongBenchV2 on the raw 128K source,
and writes a compact manifest so later stages can be resumed deterministically.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

from pipeline_common import (
    canonical_text,
    iter_jsonl,
    read_json,
    sha256_text,
    source_row_from_canonical,
    source_row_from_longbenchv2,
    write_json,
    write_jsonl,
)


PIPELINE_DIR = Path(__file__).resolve().parent
CODE_ROOT = PIPELINE_DIR.parent
ERNIE_ROOT = CODE_ROOT.parent.parent
DATA_ROOT = ERNIE_ROOT / "data/v3"
TRAIN_DATA = CODE_ROOT / "train/data"
DEFAULT_CONFIG = PIPELINE_DIR / "configs/incremental_train_test.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--smoke-per-benchmark", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output-root", type=Path, default=PIPELINE_DIR / "output/incremental")
    return parser.parse_args()


def load_used_ids(paths: Iterable[Path]) -> Set[str]:
    result: Set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        for row in (value for _, value in iter_jsonl(path)):
            identifier = str(row.get("id") or row.get("case_id") or "")
            if identifier:
                result.add(identifier)
    return result


def load_longbenchv2_used_ordinals(path: Path) -> Set[int]:
    result: Set[int] = set()
    if not path.exists():
        return result
    pattern = re.compile(r"v3_lbv2_(\d{4})_")
    for _, row in iter_jsonl(path):
        if str(row.get("benchmark") or "") != "LongBenchV2":
            continue
        match = pattern.match(str(row.get("id") or ""))
        if match:
            result.add(int(match.group(1)))
    return result


def row_fingerprint(row: Dict[str, Any]) -> str:
    return sha256_text(
        "{}|{}|{}|{}".format(
            row.get("benchmark"),
            row.get("original_data_id"),
            row.get("question"),
            row.get("source_prompt_hash") or "",
        )
    )


def selected_smoke(rows: List[Dict[str, Any]], per_benchmark: int) -> List[Dict[str, Any]]:
    if per_benchmark <= 0:
        return rows
    counters = Counter()
    result = []
    for row in rows:
        benchmark = str(row.get("benchmark") or "")
        if counters[benchmark] >= per_benchmark:
            continue
        counters[benchmark] += 1
        result.append(row)
    return result


def main() -> None:
    args = parse_args()
    config = read_json(args.config)
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    train_data = Path(config.get("train_data_root") or TRAIN_DATA)
    canonical_path = Path(config["canonical_ood_path"])
    longbench_paths = [Path(value) for value in config["longbenchv2_raw_paths"]]
    current_paths = [
        train_data / name
        for name in ("sft_v1.jsonl", "rl_v1.jsonl", "id_v1.jsonl", "ood_v1.jsonl")
    ]
    used_ids = load_used_ids(current_paths)
    used_lbv2_ordinals = load_longbenchv2_used_ordinals(train_data / "ood_v1.jsonl")

    candidates: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    seen_fingerprints: Set[str] = set()
    source_counts = Counter()

    # Canonical OOD is authoritative for every OOD benchmark except LongBenchV2,
    # which is replaced by the raw 128K union below.
    for line_number, raw in iter_jsonl(canonical_path):
        benchmark = str(raw.get("source_dataset") or "")
        if benchmark == "LongBenchV2":
            continue
        row = source_row_from_canonical(raw, canonical_path, line_number, "OOD")
        source_counts[benchmark] += 1
        identifier = str(row.get("id") or "")
        fingerprint = row_fingerprint(row)
        if identifier in used_ids:
            excluded.append(
                {
                    "case_id": identifier,
                    "benchmark": benchmark,
                    "reason": "already_delivered_case_id",
                    "source_file": str(canonical_path),
                    "source_line_number": line_number,
                }
            )
            continue
        if fingerprint in seen_fingerprints:
            excluded.append(
                {
                    "case_id": identifier,
                    "benchmark": benchmark,
                    "reason": "duplicate_candidate_fingerprint",
                    "source_file": str(canonical_path),
                    "source_line_number": line_number,
                }
            )
            continue
        seen_fingerprints.add(fingerprint)
        row["candidate_fingerprint"] = fingerprint
        candidates.append(row)

    # LongBenchV2 has cumulative 32K/64K/128K files. Use 128K as the source of
    # truth; the ordinal in the existing OOD IDs is the historical sample key.
    longbench_path = max(
        longbench_paths,
        key=lambda path: int(re.search(r"(\d+)k", path.name).group(1)),
    )
    for line_number, raw in iter_jsonl(longbench_path):
        ordinal = line_number - 1
        row = source_row_from_longbenchv2(raw, longbench_path, line_number, ordinal)
        identifier = str(row["id"])
        fingerprint = row_fingerprint(row)
        source_counts["LongBenchV2"] += 1
        if ordinal in used_lbv2_ordinals or identifier in used_ids:
            excluded.append(
                {
                    "case_id": identifier,
                    "benchmark": "LongBenchV2",
                    "raw_ordinal": ordinal,
                    "reason": (
                        "already_delivered_longbenchv2_ordinal"
                        if ordinal in used_lbv2_ordinals
                        else "already_delivered_case_id"
                    ),
                    "source_file": str(longbench_path),
                    "source_line_number": line_number,
                }
            )
            continue
        if fingerprint in seen_fingerprints:
            excluded.append(
                {
                    "case_id": identifier,
                    "benchmark": "LongBenchV2",
                    "raw_ordinal": ordinal,
                    "reason": "duplicate_candidate_fingerprint",
                    "source_file": str(longbench_path),
                    "source_line_number": line_number,
                }
            )
            continue
        seen_fingerprints.add(fingerprint)
        row["candidate_fingerprint"] = fingerprint
        candidates.append(row)

    candidates.sort(
        key=lambda row: (
            str(row.get("benchmark") or ""),
            str(row.get("ability") or ""),
            str(row.get("case_id") or ""),
        )
    )
    candidates = selected_smoke(candidates, args.smoke_per_benchmark)
    if args.limit > 0:
        candidates = candidates[: args.limit]

    candidate_path = output_root / "candidate_pool.jsonl"
    excluded_path = output_root / "excluded_cases.jsonl"
    write_jsonl(candidate_path, candidates)
    write_jsonl(excluded_path, excluded)

    by_benchmark = Counter(str(row.get("benchmark") or "") for row in candidates)
    manifest = {
        "schema_version": "v3_incremental_candidate_pool_v1",
        "status": "PASS",
        "canonical_ood_path": str(canonical_path),
        "longbenchv2_source_path": str(longbench_path),
        "current_delivery_files": [str(path) for path in current_paths],
        "used_case_id_count": len(used_ids),
        "used_longbenchv2_ordinal_count": len(used_lbv2_ordinals),
        "candidate_count": len(candidates),
        "excluded_count": len(excluded),
        "candidate_by_benchmark": dict(sorted(by_benchmark.items())),
        "source_pool_counts_before_exclusion": dict(sorted(source_counts.items())),
        "smoke_per_benchmark": args.smoke_per_benchmark,
        "limit": args.limit,
        "candidate_file": str(candidate_path),
        "excluded_file": str(excluded_path),
        "question_empty_count": sum(not canonical_text(row.get("question")) for row in candidates),
        "gt_empty_count": sum(not canonical_text(row.get("gt", {}).get("answer")) for row in candidates),
    }
    write_json(output_root / "candidate_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
