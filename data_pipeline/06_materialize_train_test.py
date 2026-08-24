#!/usr/bin/env python3
"""Build the final two training files and one test file.

Existing SFT/RL rows and the original ID test rows are copied logically, not
reprocessed. Legacy OOD rows are migrated to train using their existing
Stage-6 records. Only newly processed Stage-6 cases are materialized from the
incremental pipeline output.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Set

from pipeline_common import answer_text, iter_jsonl, read_json, write_json, write_jsonl


PIPELINE_DIR = Path(__file__).resolve().parent
DATA_STRUCTION_ROOT = PIPELINE_DIR.parent
V3_ROOT = DATA_STRUCTION_ROOT.parent
TRAIN_ROOT = V3_ROOT / "train"
DEFAULT_CONFIG = PIPELINE_DIR / "configs/incremental_train_test.json"
EXCLUDED_TRAIN_BENCHMARKS = {"MSMARCO-Rerank"}

for import_root in (TRAIN_ROOT, TRAIN_ROOT / "sft"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from block_input import count_prompt_tokens  # noqa: E402
from sft.system_prompt import V1_SYSTEM_PROMPT  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, default=PIPELINE_DIR / "output/incremental")
    parser.add_argument("--final-data-dir", type=Path, default=TRAIN_ROOT / "data/final")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=None,
        help="New source split root; defaults to <output-root>/source.",
    )
    parser.add_argument(
        "--legacy-source-root",
        type=Path,
        default=None,
        help="Root containing legacy_ood_{sft,rl}_source.jsonl.",
    )
    parser.add_argument("--prepare-python", default=sys.executable)
    parser.add_argument("--skip-prepare", action="store_true")
    return parser.parse_args()


def rows(path: Path) -> Iterable[Dict[str, Any]]:
    return (row for _, row in iter_jsonl(path))


def collect_stage6(path: Path, ids: Set[str]) -> List[Dict[str, Any]]:
    result = []
    if not path.exists():
        return result
    for row in rows(path):
        if str(row.get("case_id") or "") in ids:
            result.append(row)
    return result


def stage6_rejection_reason(row: Mapping[str, Any]) -> str:
    if str(row.get("status") or "") != "PASS":
        return "stage6_status_{}".format(str(row.get("status") or "MISSING"))
    summary_validation = (row.get("summary") or {}).get("validation") or {}
    if summary_validation.get("status") != "PASS":
        return "summary_validation_not_pass"
    answerability = row.get("answerability") or {}
    subquery_probe = answerability.get("subquery_probe") or {}
    if subquery_probe:
        subquery_validation = subquery_probe.get("validation") or {}
        if subquery_validation.get("status") != "PASS":
            return "subquery_answerability_not_pass"
        if subquery_validation.get("all_subqueries_answerable") is not True:
            return "subquery_coverage_incomplete"
    else:
        # Earlier Stage-6 shards predate the LLM subquery probe. Their
        # deterministic claim-group union still records whether every
        # subquery has at least one valid bound claim.
        claim_groups = answerability.get("claim_groups") or {}
        if claim_groups.get("claim_group_status") != "PASS":
            return "legacy_claim_group_coverage_not_pass"
        total = int(claim_groups.get("total_subquery_count") or 0)
        nonempty = int(claim_groups.get("nonempty_subquery_count") or 0)
        if total <= 0 or total != nonempty or claim_groups.get("empty_subquery_ids"):
            return "legacy_subquery_coverage_incomplete"
    final_validation = answerability.get("validation") or {}
    if final_validation.get("status") != "PASS":
        return "summary_answerability_not_pass"
    if final_validation.get("answerable") is not True:
        return "summary_not_answerable"
    canonical_claims = (row.get("canonical") or {}).get("claims") or []
    if not canonical_claims:
        return "canonical_claims_empty"
    return ""


def load_stage2_block_inputs(stage2_root: Path, split: str) -> Dict[str, str]:
    """Rebuild parser-safe input from Stage-2's authoritative block store.

    The benchmark-rendered prompt can contain repeated questions and trailing
    instructions after the final block.  The block store contains the exact
    block text used by retrieval and evidence extraction, so it is the stable
    source for the v1 training input.
    """
    path = stage2_root / split / "2_blocks.jsonl"
    if not path.exists():
        return {}
    grouped: Dict[str, List[str]] = defaultdict(list)
    for row in rows(path):
        identifier = str(row.get("case_id") or "")
        block_id = str(row.get("block_id") or "")
        source_text = str(row.get("source_text") or "")
        if identifier and block_id and source_text:
            grouped[identifier].append(
                "[BLOCK_ID: {block_id}]\n{source_text}".format(
                    block_id=block_id,
                    source_text=source_text.strip(),
                )
            )
    return {
        identifier: "\n".join(parts).strip()
        for identifier, parts in grouped.items()
    }


def is_excluded_train_benchmark(value: Any) -> bool:
    return str(value or "").strip() in EXCLUDED_TRAIN_BENCHMARKS


def build_candidate_test_row(
    row: Mapping[str, Any],
    rendered_blocks: str,
    tokenizer: Any,
) -> Dict[str, Any]:
    question = str(row.get("question") or "").strip()
    user_input = "[question]\n{}\n[Doc]\n{}".format(
        question,
        str(rendered_blocks or "").strip(),
    ).strip()
    messages = [
        {"role": "system", "content": V1_SYSTEM_PROMPT},
        {"role": "user", "content": user_input},
    ]
    source_token_count = row.get("input_token_num")
    if source_token_count is not None:
        input_token_num = int(source_token_count)
        input_token_num_mode = "source_record"
    else:
        input_token_num = count_prompt_tokens(None, messages)
        input_token_num_mode = "approx_chars_per_4"
    return {
        "schema_version": "v4_eval_v1",
        "id": row.get("id"),
        "benchmark": row.get("benchmark"),
        "ability": row.get("ability"),
        "question": question,
        "gt": row.get("gt") if isinstance(row.get("gt"), Mapping) else {},
        "input_token_num": input_token_num,
        "input_token_num_original": row.get("input_token_num"),
        "input_token_num_mode": input_token_num_mode,
        "input": messages,
        "split": "test",
        "source_split": "incremental_unused_candidate",
    }


def attach_stage2_rendering(
    source_rows: List[Dict[str, Any]],
    split: str,
    rendered_by_id: Mapping[str, str],
) -> List[Dict[str, Any]]:
    output = []
    for row in source_rows:
        item = dict(row)
        if str(item.get("source_row_type") or "") == "legacy_ood_delivery":
            output.append(item)
            continue
        identifier = str(item.get("id") or "")
        rendered = str(rendered_by_id.get(identifier) or "")
        if not rendered:
            item["materialization_error"] = "missing_stage2_rendered_input"
            output.append(item)
            continue
        if split == "sft":
            item["messages"] = [
                {"role": "user", "content": rendered},
                {
                    "role": "assistant",
                    "content": answer_text(item.get("gt")),
                },
            ]
        else:
            item.pop("messages", None)
            item["prompt"] = [{"role": "user", "content": rendered}]
        output.append(item)
    return output


def dedupe_append(
    sources: Iterable[Path],
    extra: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for path in sources:
        if not path.exists():
            continue
        for row in rows(path):
            identifier = str(row.get("id") or row.get("case_id") or "")
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            result.append(dict(row))
    for row in extra:
        identifier = str(row.get("id") or row.get("case_id") or "")
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        result.append(dict(row))
    return result


def main() -> None:
    args = parse_args()
    config = read_json(args.config)
    output_root = args.output_root.resolve()
    source_root = (
        args.source_root.resolve()
        if args.source_root
        else output_root / "source"
    )
    final_dir = args.final_data_dir.resolve()
    final_dir.mkdir(parents=True, exist_ok=True)
    train_data = Path(config["train_data_root"])

    new_sft_source = source_root / "sft.jsonl"
    new_rl_source = source_root / "rl.jsonl"
    legacy_source_root = (
        args.legacy_source_root.resolve()
        if args.legacy_source_root
        else output_root
    )
    legacy_sft_source = legacy_source_root / "legacy_ood_sft_source.jsonl"
    legacy_rl_source = legacy_source_root / "legacy_ood_rl_source.jsonl"
    stage6_new = Path(config["new_stage6_file"])
    stage6_existing = Path(config["existing_stage6_file"])
    stage2_root = Path(config["stage2_output_root"])
    # Candidate rows already carry the authoritative token count for the
    # canonical OOD sources. Raw LongBenchV2 rows have no such field, so the
    # test-only fallback is a documented character approximation.
    tokenizer = None

    material_source_root = output_root / "materialization"
    material_source_root.mkdir(parents=True, exist_ok=True)
    combined_sft_source = material_source_root / "sft_source.jsonl"
    combined_rl_source = material_source_root / "rl_source.jsonl"
    combined_stage6 = material_source_root / "stage6_for_materialization.jsonl"

    stage2_rendered_inputs: Dict[str, str] = {}
    for split in ("sft", "rl"):
        stage2_rendered_inputs.update(
            load_stage2_block_inputs(stage2_root, split)
        )
    proposed_sft = list(rows(legacy_sft_source)) + attach_stage2_rendering(
        list(rows(new_sft_source)),
        "sft",
        stage2_rendered_inputs,
    )
    proposed_rl = list(rows(legacy_rl_source)) + attach_stage2_rendering(
        list(rows(new_rl_source)),
        "rl",
        stage2_rendered_inputs,
    )
    proposed_sft = [
        row for row in proposed_sft
        if not is_excluded_train_benchmark(row.get("benchmark"))
    ]
    proposed_rl = [
        row for row in proposed_rl
        if not is_excluded_train_benchmark(row.get("benchmark"))
    ]
    target_ids = {
        str(row.get("id") or "")
        for row in proposed_sft + proposed_rl
    }
    stage6_rows = collect_stage6(stage6_existing, target_ids)
    stage6_rows.extend(collect_stage6(stage6_new, target_ids))
    stage6_by_id = {}
    for row in stage6_rows:
        stage6_by_id[str(row.get("case_id") or "")] = row
    accepted_stage6 = {}
    source_by_id = {
        str(row.get("id") or ""): row
        for row in proposed_sft + proposed_rl
    }
    rejected_stage6 = []
    for identifier in sorted(target_ids):
        stage6 = stage6_by_id.get(identifier)
        reason = (
            "missing_stage6"
            if stage6 is None
            else stage6_rejection_reason(stage6)
        )
        if reason:
            rejected_stage6.append(
                {
                    "case_id": identifier,
                    "benchmark": (stage6 or {}).get("benchmark"),
                    "split": (stage6 or {}).get("split"),
                    "reason": reason,
                }
            )
            continue
        source_row = source_by_id.get(identifier, {})
        if source_row.get("materialization_error"):
            rejected_stage6.append(
                {
                    "case_id": identifier,
                    "benchmark": stage6.get("benchmark"),
                    "split": stage6.get("split"),
                    "reason": source_row["materialization_error"],
                }
            )
            continue
        accepted_stage6[identifier] = stage6

    accepted_ids = set(accepted_stage6)
    accepted_sft_source = [
        row for row in proposed_sft if str(row.get("id") or "") in accepted_ids
    ]
    accepted_rl_source = [
        row for row in proposed_rl if str(row.get("id") or "") in accepted_ids
    ]
    write_jsonl(combined_sft_source, accepted_sft_source)
    write_jsonl(combined_rl_source, accepted_rl_source)
    write_jsonl(combined_stage6, accepted_stage6.values())
    rejected_stage6_path = material_source_root / "rejected_stage6_cases.jsonl"
    write_jsonl(rejected_stage6_path, rejected_stage6)

    prepared_sft = material_source_root / "prepared_sft.jsonl"
    prepared_rl = material_source_root / "prepared_rl.jsonl"
    prepare_report = material_source_root / "prepare_report.json"
    if not args.skip_prepare:
        prepare_script = TRAIN_ROOT / "prepare_v1_data.py"
        command = [
            str(args.prepare_python),
            str(prepare_script),
            "--sft-source",
            str(combined_sft_source),
            "--rl-source",
            str(combined_rl_source),
            "--stage6",
            str(combined_stage6),
            "--sft-output",
            str(prepared_sft),
            "--rl-output",
            str(prepared_rl),
            "--report",
            str(prepare_report),
        ]
        subprocess.run(command, check=True)

    extra_sft = list(rows(prepared_sft)) if prepared_sft.exists() else []
    extra_rl = list(rows(prepared_rl)) if prepared_rl.exists() else []
    final_sft = [
        row
        for row in dedupe_append([train_data / "sft_v1.jsonl"], extra_sft)
        if not is_excluded_train_benchmark(row.get("benchmark"))
    ]
    final_rl = [
        row
        for row in dedupe_append([train_data / "rl_v1.jsonl"], extra_rl)
        if not is_excluded_train_benchmark(row.get("benchmark"))
    ]
    accepted_incremental_by_source = Counter(
        str(row.get("source_row_type") or "unknown")
        for row in accepted_sft_source + accepted_rl_source
    )

    train_ids = {
        str(row.get("id") or "")
        for row in final_sft + final_rl
        if str(row.get("id") or "")
    }
    test_rows = []
    test_ids: Set[str] = set()
    for row in rows(train_data / "id_v1.jsonl"):
        item = dict(row)
        item["split"] = "test"
        item["source_split"] = "id"
        identifier = str(item.get("id") or "")
        if identifier and identifier not in test_ids:
            test_ids.add(identifier)
            test_rows.append(item)

    legacy_ood_test_rows = []
    for row in rows(train_data / "ood_v1.jsonl"):
        item = dict(row)
        identifier = str(item.get("id") or "")
        if not identifier or identifier in train_ids or identifier in test_ids:
            continue
        item["split"] = "test"
        item["source_split"] = "legacy_ood_unselected"
        test_ids.add(identifier)
        test_rows.append(item)
        legacy_ood_test_rows.append(item)

    candidate_test_rows = []
    candidate_test_errors = []
    candidate_pool_path = output_root / "candidate_pool.jsonl"
    if candidate_pool_path.exists():
        candidate_rows = list(rows(candidate_pool_path))
        for row in candidate_rows:
            identifier = str(row.get("id") or "")
            if not identifier or identifier in train_ids or identifier in test_ids:
                continue
            rendered = stage2_rendered_inputs.get(identifier)
            if not rendered:
                candidate_test_errors.append(
                    {
                        "id": identifier,
                        "benchmark": row.get("benchmark"),
                        "reason": "missing_stage2_blocks",
                    }
                )
                continue
            item = build_candidate_test_row(row, rendered, tokenizer)
            test_ids.add(identifier)
            test_rows.append(item)
            candidate_test_rows.append(item)

    sft_path = final_dir / "sft_final.jsonl"
    rl_path = final_dir / "rl_final.jsonl"
    test_path = final_dir / "test.jsonl"
    write_jsonl(sft_path, final_sft)
    write_jsonl(rl_path, final_rl)
    write_jsonl(test_path, test_rows)

    manifest = {
        "schema_version": "v3_train_test_delivery_manifest_v1",
        "split_policy": {
            "train": [
                "existing sft_v1",
                "existing rl_v1",
                "legacy ood_v1 migrated by stable hash",
                "new unused OOD candidates after Stage-2 to Stage-6",
            ],
            "test": [
                "existing id_v1 renamed to test",
                "unselected legacy ood_v1 cases",
                "unused candidate cases rendered from Stage-2 blocks",
            ],
            "ood_split": "removed",
            "excluded_train_benchmarks": sorted(EXCLUDED_TRAIN_BENCHMARKS),
        },
        "output_files": {
            "sft": str(sft_path),
            "rl": str(rl_path),
            "test": str(test_path),
        },
        "counts": {
            "sft": len(final_sft),
            "rl": len(final_rl),
            "test": len(test_rows),
            "proposed_incremental_train": len(target_ids),
            "accepted_incremental_train": len(accepted_stage6),
            "rejected_incremental_train": len(rejected_stage6),
            "accepted_incremental_sft": len(extra_sft),
            "accepted_incremental_rl": len(extra_rl),
            "accepted_incremental_by_source": dict(
                sorted(accepted_incremental_by_source.items())
            ),
            "new_sft_prepared": sum(
                str(row.get("source_row_type") or "") != "legacy_ood_delivery"
                for row in accepted_sft_source
            ),
            "new_rl_prepared": sum(
                str(row.get("source_row_type") or "") != "legacy_ood_delivery"
                for row in accepted_rl_source
            ),
            "excluded_train_benchmark_rows": sum(
                is_excluded_train_benchmark(row.get("benchmark"))
                for row in extra_sft + extra_rl
            ),
            "test_added_legacy_ood": len(legacy_ood_test_rows),
            "test_added_unused_candidates": len(candidate_test_rows),
            "test_candidate_errors": len(candidate_test_errors),
        },
        "benchmark_counts": {
            "sft": dict(sorted(Counter(str(row.get("benchmark") or "") for row in final_sft).items())),
            "rl": dict(sorted(Counter(str(row.get("benchmark") or "") for row in final_rl).items())),
            "test": dict(sorted(Counter(str(row.get("benchmark") or "") for row in test_rows).items())),
        },
        "prepare_report": str(prepare_report),
        "stage6_rejection_counts": dict(
            sorted(Counter(row["reason"] for row in rejected_stage6).items())
        ),
        "stage6_rejected_cases": str(rejected_stage6_path),
        "test_candidate_errors": candidate_test_errors,
    }
    write_json(final_dir / "FINAL_MANIFEST.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
