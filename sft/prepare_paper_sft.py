#!/usr/bin/env python3
"""Build the paper SFT messages dataset from Stage-6 and frozen answers.

The training target is deliberately rendered as:

    <evidence>...</evidence><summary>...</summary><answer>...</answer>

Native benchmark gold is metadata only.  It is never inserted into the
assistant message, which prevents evaluation labels from leaking into SFT.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional


if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from block_input import (  # noqa: E402
    count_prompt_tokens,
    load_stage2_blocks,
    load_stage2_rendered,
    render_training_prompt,
)
from sft.system_prompt import prepend_system_prompt  # noqa: E402


CLAIM_REF_RE = re.compile(r"\[(clm_[A-Za-z0-9_-]+)\]")
SUMMARY_BUDGETS = {
    "CNNSum": {"evidence_tokens": 2048, "summary_tokens": 512},
    "GovReport": {"evidence_tokens": 2048, "summary_tokens": 768},
}
SCRIPT_DIR = Path(__file__).resolve().parent
TRAIN_ROOT = SCRIPT_DIR.parent
CODE_V3_ROOT = TRAIN_ROOT.parent
ERNIE_ROOT = CODE_V3_ROOT.parent.parent
DATA_V3_ROOT = ERNIE_ROOT / "data" / "v3"


def json_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected object")
            yield row


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def prompt_content(prompt: Any) -> str:
    if isinstance(prompt, list):
        parts: list[str] = []
        for message in prompt:
            if not isinstance(message, Mapping):
                parts.append(str(message or ""))
                continue
            content = message.get("content")
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, Mapping):
                        text_parts.append(str(item.get("text") or ""))
                    else:
                        text_parts.append(str(item or ""))
                parts.append("".join(text_parts))
            else:
                parts.append(str(content or ""))
        return "\n".join(part for part in parts if part)
    if isinstance(prompt, Mapping):
        return str(prompt.get("content") or prompt.get("text") or "")
    return str(prompt or "")


def stable_support_key(support: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(support.get("block_id") or ""),
        int(support.get("start_offset") or 0),
        int(support.get("end_offset") or 0),
        str(support.get("exact_span") or ""),
    )


class TokenCounter:
    def __init__(self, tokenizer_path: Optional[Path] = None) -> None:
        self.tokenizer = None
        if tokenizer_path and tokenizer_path.exists():
            try:
                from transformers import AutoTokenizer

                self.tokenizer = AutoTokenizer.from_pretrained(
                    str(tokenizer_path),
                    local_files_only=True,
                    use_fast=True,
                )
            except Exception:
                self.tokenizer = None

    def count(self, text: str) -> int:
        if self.tokenizer is not None:
            return len(self.tokenizer.encode(text, add_special_tokens=False))
        return max(1, (len(text) + 3) // 4) if text else 0


def trim_complete_sentences(text: str, budget: int, counter: TokenCounter) -> str:
    if counter.count(text) <= budget:
        return text.strip()
    # Chinese summaries commonly have no whitespace after sentence-ending
    # punctuation. Split those boundaries explicitly so the token budget is
    # meaningful for both Chinese and English summaries.
    pieces = [
        piece.strip()
        for piece in re.split(
            r"(?<=[。！？])\s*|(?<=[.!?])\s+|\n+",
            text,
        )
        if piece.strip()
    ]
    selected: list[str] = []
    for piece in pieces:
        candidate = " ".join(selected + [piece])
        if counter.count(candidate) > budget:
            break
        selected.append(piece)
    return " ".join(selected).strip()


def select_bounded_evidence(
    evidence: list[dict[str, Any]],
    claim_to_eids: Mapping[str, list[str]],
    budget: int,
    counter: TokenCounter,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Keep complete spans and prioritize one support per claim."""
    by_id = {str(item["evidence_id"]): item for item in evidence}
    ordered_ids: list[str] = []
    for eids in claim_to_eids.values():
        if eids and eids[0] in by_id and eids[0] not in ordered_ids:
            ordered_ids.append(eids[0])
    for item in evidence:
        eid = str(item["evidence_id"])
        if eid not in ordered_ids:
            ordered_ids.append(eid)

    selected: list[dict[str, Any]] = []
    selected_ids: list[str] = []
    used = 0
    for eid in ordered_ids:
        item = by_id[eid]
        line = (
            f"[{eid}] block_id={item.get('block_id') or ''}: "
            f"{item.get('exact_span') or ''}"
        )
        cost = counter.count(line)
        if selected and used + cost > budget:
            continue
        selected.append(item)
        selected_ids.append(eid)
        used += cost

    old_to_new = {
        old: f"E{index + 1:04d}" for index, old in enumerate(selected_ids)
    }
    normalized: list[dict[str, Any]] = []
    for item in selected:
        copied = dict(item)
        copied["evidence_id"] = old_to_new[str(item["evidence_id"])]
        normalized.append(copied)
    return normalized, old_to_new


def render_target(
    stage6: Mapping[str, Any],
    frozen_answer: str,
    benchmark: str = "",
    counter: Optional[TokenCounter] = None,
) -> tuple[str, dict[str, Any]]:
    canonical = stage6.get("canonical") or {}
    claims = canonical.get("claims") or []
    evidence: list[dict[str, Any]] = []
    support_to_eid: dict[tuple[Any, ...], str] = {}
    claim_to_eids: dict[str, list[str]] = {}

    for claim in claims:
        if not isinstance(claim, Mapping):
            continue
        claim_id = str(claim.get("claim_id") or "")
        if not claim_id:
            continue
        eids: list[str] = []
        for support in claim.get("supports") or []:
            if not isinstance(support, Mapping):
                continue
            key = stable_support_key(support)
            evidence_id = support_to_eid.get(key)
            if evidence_id is None:
                evidence_id = f"E{len(evidence) + 1:04d}"
                support_to_eid[key] = evidence_id
                evidence.append(
                    {
                        "evidence_id": evidence_id,
                        "claim_ids": [],
                        "block_id": support.get("block_id"),
                        "exact_span": support.get("exact_span"),
                        "start_offset": support.get("start_offset"),
                        "end_offset": support.get("end_offset"),
                        "document_id": support.get("document_id"),
                        "absolute_start_offset": support.get(
                            "absolute_start_offset"
                        ),
                    }
                )
            eids.append(evidence_id)
            if evidence_id not in claim_to_eids.setdefault(claim_id, []):
                claim_to_eids[claim_id].append(evidence_id)
            evidence_item = evidence[int(evidence_id[1:]) - 1]
            if claim_id not in evidence_item["claim_ids"]:
                evidence_item["claim_ids"].append(claim_id)

    summary = str((stage6.get("summary") or {}).get("summary") or "").strip()
    full_evidence_count = len(evidence)
    length_config = SUMMARY_BUDGETS.get(benchmark)
    counter = counter or TokenCounter()
    old_to_new = {str(item["evidence_id"]): str(item["evidence_id"]) for item in evidence}
    if length_config:
        evidence, old_to_new = select_bounded_evidence(
            evidence,
            claim_to_eids,
            length_config["evidence_tokens"],
            counter,
        )

    def replace_claim_ref(match: re.Match[str]) -> str:
        claim_id = match.group(1)
        refs = [
            old_to_new[eid]
            for eid in claim_to_eids.get(claim_id) or []
            if eid in old_to_new
        ]
        return "".join(f"[{eid}]" for eid in refs)

    summary = CLAIM_REF_RE.sub(replace_claim_ref, summary)
    full_summary_tokens = counter.count(summary)
    if length_config:
        summary = trim_complete_sentences(
            summary,
            length_config["summary_tokens"],
            counter,
        )

    evidence_payload = []
    for item in evidence:
        span = str(item.get("exact_span") or "").strip()
        block_id = str(item.get("block_id") or "")
        evidence_payload.append(
            {
                "id": str(item["evidence_id"]),
                "block_id": block_id,
                "quote": span,
            }
        )
    evidence_content = json.dumps(
        evidence_payload,
        ensure_ascii=False,
        indent=2,
    )

    assistant = (
        "<evidence>\n"
        + evidence_content
        + "\n</evidence>\n"
        "<summary>\n"
        + summary
        + "\n</summary>\n"
        "<answer>\n"
        + frozen_answer.strip()
        + "\n</answer>"
    )
    details = {
        "evidence": evidence,
        "claim_count": len(claims),
        "evidence_count": len(evidence),
        "full_evidence_count": full_evidence_count,
        "summary_char_count": len(summary),
        "summary_tokens": counter.count(summary),
        "full_summary_tokens": full_summary_tokens,
        "frozen_answer_char_count": len(frozen_answer),
        "claim_to_evidence": claim_to_eids,
        "length_control": length_config,
    }
    return assistant, details


def extract_native_gold(master: Mapping[str, Any]) -> Any:
    meta = master.get("meta")
    if isinstance(meta, Mapping):
        for key in ("original_answer", "reference", "gold"):
            answer = meta.get(key)
            if answer not in (None, ""):
                return {
                    "answer": answer,
                    "program": meta.get("original_program"),
                    "source": f"benchmark_gate_master.meta.{key}",
                }
    verifier = master.get("verifier")
    qrel = find_nested_key(verifier, "qrel_pos")
    if qrel:
        return {
            "qrel_pos": qrel,
            "source": "benchmark_gate_master.verifier.qrel_pos",
        }
    for key in ("gt", "gold", "tgt"):
        if master.get(key) not in (None, "", [], {}):
            return {
                "answer": master.get(key),
                "source": f"benchmark_gate_master.{key}",
            }
    return None


def find_nested_key(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        if key in value and value[key] not in (None, "", [], {}):
            return value[key]
        for child in value.values():
            found = find_nested_key(child, key)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_nested_key(child, key)
            if found not in (None, "", [], {}):
                return found
    return None


def load_master_gold(
    path: Path,
    target_ids: set[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return result
    for row in json_rows(path):
        case_id = str(row.get("case_id") or "")
        if case_id not in target_ids:
            continue
        native_gold = extract_native_gold(row)
        if native_gold is not None:
            result[case_id] = {
                "native_gt": native_gold,
                "source_file": row.get("source_file"),
                "source_line_number": row.get("source_line_number"),
                "original_data_id": row.get("original_data_id"),
                "source_dataset": row.get("source_dataset"),
            }
    return result


def load_allocation_sources(
    path: Path,
    target_ids: set[str],
) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for row in json_rows(path):
        case_id = str(row.get("id") or row.get("case_id") or "")
        source = str(row.get("source") or "")
        if case_id in target_ids and source:
            result[case_id] = source
    return result


def raw_gold_value(row: Mapping[str, Any]) -> Any:
    if row.get("gold") not in (None, ""):
        return row.get("gold")
    if row.get("summary") not in (None, ""):
        return row.get("summary")
    meta = row.get("meta")
    if isinstance(meta, Mapping):
        for key in ("original_answer", "reference", "gold"):
            if meta.get(key) not in (None, ""):
                return {
                    "answer": meta.get(key),
                    "answers": meta.get("original_answers"),
                }
    verifier = row.get("verifier")
    if verifier not in (None, ""):
        return verifier
    return None


def canonical_pointer(source: str) -> tuple[Optional[Path], Optional[int]]:
    match = re.match(r"^canonical//(.+):([0-9]+)$", source or "")
    if not match:
        return None, None
    return Path("/" + match.group(1).lstrip("/")), int(match.group(2))


def extract_source_reference(row: Mapping[str, Any]) -> Any:
    meta = row.get("meta")
    if isinstance(meta, Mapping):
        for key in ("reference", "original_answer", "gold"):
            if meta.get(key) not in (None, ""):
                return {
                    "answer": meta.get(key),
                    "source": f"canonical.meta.{key}",
                }
    reward_model = row.get("reward_model")
    if isinstance(reward_model, Mapping):
        ground_truth = reward_model.get("ground_truth")
        if ground_truth not in (None, ""):
            return {
                "answer": ground_truth,
                "source": "canonical.reward_model.ground_truth",
            }
    qrel = find_nested_key(row.get("verifier"), "qrel_pos")
    if qrel:
        return {
            "qrel_pos": qrel,
            "source": "canonical.verifier.qrel_pos",
        }
    return None


def load_canonical_references(
    source_hints: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    by_path: dict[Path, list[tuple[int, str]]] = {}
    result: dict[str, dict[str, Any]] = {}
    for case_id, source in source_hints.items():
        path, line_number = canonical_pointer(source)
        if path is not None and line_number is not None:
            by_path.setdefault(path, []).append((line_number, case_id))

    for path, targets in by_path.items():
        wanted = {line_number: case_id for line_number, case_id in targets}
        if path.suffix == ".parquet":
            try:
                import pyarrow.parquet as pq

                table = pq.read_table(path)
                rows = table.to_pylist()
                for line_number, case_id in targets:
                    index = line_number - 1
                    if 0 <= index < len(rows):
                        reference = extract_source_reference(rows[index])
                        if reference is not None:
                            result[case_id] = {
                                "native_gt": reference,
                                "source_file": str(path),
                                "source_line_number": line_number,
                                "source": "canonical_pointer",
                            }
            except Exception:
                continue
            continue

        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    case_id = wanted.get(line_number)
                    if case_id is None or not line.strip():
                        continue
                    reference = extract_source_reference(json.loads(line))
                    if reference is not None:
                        result[case_id] = {
                            "native_gt": reference,
                            "source_file": str(path),
                            "source_line_number": line_number,
                            "source": "canonical_pointer",
                        }
        except Exception:
            continue
    return result


def render_native_answer(native_gt: Any, question: str) -> str:
    """Convert native benchmark gold into the requested answer surface."""
    if native_gt in (None, ""):
        return ""
    value = native_gt
    if isinstance(native_gt, Mapping) and "answer" in native_gt:
        value = native_gt.get("answer")
    if isinstance(native_gt, Mapping) and "qrel_pos" in native_gt:
        return ""
    if isinstance(value, list):
        marker = "[答案]" if "[答案]" in question else "[Answer]"
        return marker + "\n" + "\n".join(str(item) for item in value)
    return str(value).strip()


def raw_problem_text(row: Mapping[str, Any]) -> str:
    problem = row.get("problem")
    if problem:
        return str(problem)
    for key in ("input", "prompt", "document"):
        value = row.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return prompt_content(value)
        if isinstance(value, Mapping):
            return prompt_content([value])
    return ""


def load_raw_gold(
    raw_root: Path,
    target_questions: Mapping[str, set[str]],
    source_hints: Mapping[str, str],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Recover high-confidence gold from supplemental strategy exports.

    Matching is benchmark-scoped and requires the normalized processed question
    to occur in the raw problem text.  The function keeps only unambiguous
    matches, so it never invents a gold answer from a fuzzy nearest neighbor.
    """
    candidates: dict[str, list[dict[str, Any]]] = {}
    direct_result: dict[str, dict[str, Any]] = {}
    source_index: dict[tuple[str, str], list[str]] = {}
    for case_id, source in source_hints.items():
        match = re.match(r"^raw/([^/]+)/(.+)$", source)
        if not match:
            continue
        source_index.setdefault((match.group(1), match.group(2)), []).append(
            case_id
        )
    file_specs = {
        "LongBench-Pro": [raw_root / "LongBench-Pro/strategy_details.jsonl"],
        "Frames": [raw_root / "Frames/strategy_details.jsonl"],
        "LongBenchV2": sorted((raw_root / "LongBenchV2").glob("*.jsonl")),
        "GovReport": [raw_root / "GovReport/govreport_summarization_600_seed41.jsonl"],
        "CUAD-QA": sorted((raw_root / "CUAD-QA").glob("*.jsonl")),
    }
    for benchmark, files in file_specs.items():
        if benchmark == "LongBench-Pro":
            questions = {
                question
                for name, values in target_questions.items()
                if name.startswith("LongBench-Pro")
                for question in values
            }
        elif benchmark == "LongBenchV2":
            questions = {
                question
                for name, values in target_questions.items()
                if name == "LongBenchV2"
                for question in values
            }
        elif benchmark == "CUAD-QA":
            questions = target_questions.get("CUAD") or set()
        else:
            questions = target_questions.get(benchmark) or set()
        if not questions:
            continue
        for path in files:
            if not path.exists():
                continue
            for row in json_rows(path):
                gold = raw_gold_value(row)
                data_id = str(row.get("data_id") or "")
                for case_id in source_index.get((benchmark, data_id), []):
                    direct_result[case_id] = {
                        "native_gt": gold,
                        "source_file": str(path),
                        "raw_data_id": data_id,
                        "source": "raw_benchmark_allocation_pointer",
                    }
                problem = normalize_text(raw_problem_text(row))
                if gold in (None, "") or not problem:
                    continue
                matched = [
                    question for question in questions
                    if normalize_text(question) in problem
                ]
                if len(matched) != 1:
                    continue
                question = matched[0]
                candidates.setdefault(question, []).append(
                    {
                        "gold": gold,
                        "path": str(path),
                        "data_id": row.get("data_id"),
                    }
                )

    question_result: dict[str, dict[str, Any]] = {}
    for question, matches in candidates.items():
        unique = {
            json.dumps(item, ensure_ascii=False, sort_keys=True)
            for item in matches
        }
        if len(unique) == 1:
            question_result[question] = {
                "native_gt": matches[0]["gold"],
                "source_file": matches[0]["path"],
                "raw_data_id": matches[0].get("data_id"),
                "source": "raw_benchmark_strategy_details",
            }
    return direct_result, question_result


def build(args: argparse.Namespace) -> dict[str, Any]:
    baseline_rows = list(json_rows(args.baseline))
    baseline_by_id = {
        str(row.get("id") or row.get("case_id")): row for row in baseline_rows
    }
    target_ids = set(baseline_by_id)

    stage6_by_id: dict[str, dict[str, Any]] = {}
    for row in json_rows(args.stage6):
        case_id = str(row.get("case_id") or "")
        if case_id in target_ids:
            stage6_by_id[case_id] = row

    target_questions = {}
    for row in baseline_rows:
        benchmark = str(row.get("benchmark") or "")
        target_questions.setdefault(benchmark, set()).add(
            str(row.get("question") or "")
        )

    master_gold = load_master_gold(args.master, target_ids)
    allocation_sources = load_allocation_sources(args.allocation, target_ids)
    canonical_gold = load_canonical_references(allocation_sources)
    raw_gold_by_case, raw_gold_by_question = load_raw_gold(
        args.raw_root,
        target_questions,
        allocation_sources,
    )
    counter = TokenCounter(args.tokenizer)
    stage2_rendered = load_stage2_rendered(
        args.stage2_root / "2_rendered_cases.jsonl"
    )
    stage2_blocks = load_stage2_blocks(
        args.stage2_root / "2_blocks.jsonl"
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.strict_output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.missing_gt.parent.mkdir(parents=True, exist_ok=True)
    args.skipped.parent.mkdir(parents=True, exist_ok=True)

    status_counts: Counter[str] = Counter()
    gt_source_counts: Counter[str] = Counter()
    answer_source_counts: Counter[str] = Counter()
    length_stats: dict[str, Counter[str]] = {}
    benchmark_counts: Counter[str] = Counter()
    output_count = 0
    strict_output_count = 0
    skipped: list[dict[str, Any]] = []
    missing_gt: list[dict[str, Any]] = []
    block_render_errors: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    with args.output.open("w", encoding="utf-8") as output, args.strict_output.open(
        "w", encoding="utf-8"
    ) as strict_output:
        for row in baseline_rows:
            case_id = str(row.get("id") or row.get("case_id"))
            stage6 = stage6_by_id.get(case_id)
            stage6_status = str((stage6 or {}).get("status") or "")
            summary_validation = str(
                ((stage6 or {}).get("summary") or {})
                .get("validation", {})
                .get("status")
                or ""
            )
            answerability = (
                (stage6 or {}).get("answerability") or {}
            ).get("validation") or {}
            answerable = answerability.get("answerable")
            frozen_answer = str(
                (row.get("assistant") or {}).get("answer") or ""
            ).strip()

            if (
                stage6 is None
                or stage6_status != "PASS"
                or summary_validation != "PASS"
                or answerable is not True
                or not frozen_answer
            ):
                skipped.append(
                    {
                        "id": case_id,
                        "benchmark": row.get("benchmark"),
                        "stage6_status": stage6_status,
                        "summary_status": summary_validation,
                        "answerable": answerable,
                        "has_frozen_answer": bool(frozen_answer),
                    }
                )
                continue

            question = str(row.get("question") or "")
            master = master_gold.get(case_id)
            raw = (
                raw_gold_by_case.get(case_id)
                or raw_gold_by_question.get(question)
            )
            native_gt = None
            gt_source = "unresolved"
            gt_provenance: dict[str, Any] = {}
            canonical = canonical_gold.get(case_id)
            if raw:
                native_gt = raw["native_gt"]
                gt_source = raw.get("source") or "raw_benchmark"
                gt_provenance = raw
            elif canonical:
                native_gt = canonical["native_gt"]
                gt_source = canonical.get("source") or "canonical_pointer"
                gt_provenance = canonical
            elif master:
                native_gt = master["native_gt"]
                gt_source = "benchmark_gate_master"
                gt_provenance = master

            if native_gt is None:
                missing_gt.append(
                    {
                        "id": case_id,
                        "benchmark": row.get("benchmark"),
                        "question": question,
                        "reason": "no_high_confidence_native_gt_indexed",
                        "source_hint": row.get("benchmark"),
                    }
                )
            else:
                gt_source_counts[gt_source] += 1

            native_answer = render_native_answer(native_gt, question)
            target_answer = native_answer or frozen_answer
            answer_source = (
                "native_gt" if native_answer else "frozen_sft_answer"
            )
            answer_source_counts[answer_source] += 1
            assistant_content, target_meta = render_target(
                stage6,
                target_answer,
                benchmark=str(row.get("benchmark") or ""),
                counter=counter,
            )
            length_record = length_stats.setdefault(
                str(row.get("benchmark") or ""),
                Counter(),
            )
            length_control = target_meta.get("length_control")
            if length_control:
                length_record["budgeted_cases"] += 1
                if target_meta["full_evidence_count"] != target_meta["evidence_count"]:
                    length_record["evidence_trimmed_cases"] += 1
                if target_meta["full_summary_tokens"] > target_meta["summary_tokens"]:
                    length_record["summary_trimmed_cases"] += 1

            record = {
                "schema_version": "v3_sft_messages_block_aware_v2",
                "id": case_id,
                "benchmark": row.get("benchmark"),
                "ability": row.get("ability"),
                "question": question,
                "input_token_num": 0,
                "messages": [
                    *[],
                    {
                        "role": "assistant",
                        "content": assistant_content,
                    },
                ],
                "target_answer": target_answer,
                "native_gt": native_gt,
                "provenance": {
                    "stage6_case_id": stage6.get("case_id"),
                    "stage6_status": stage6_status,
                    "answerability": answerability,
                    "gt_source": gt_source,
                    "answer_source": answer_source,
                    "frozen_answer": frozen_answer,
                    "gt_provenance": gt_provenance,
                    "target": target_meta,
                },
            }
            try:
                prompt_messages = render_training_prompt(
                    stage2_rendered[case_id],
                    stage2_blocks[case_id],
                )
                prompt_messages = prepend_system_prompt(prompt_messages)
                record["messages"] = prompt_messages + [
                    {
                        "role": "assistant",
                        "content": assistant_content,
                    }
                ]
                if counter.tokenizer is not None:
                    record["input_token_num"] = count_prompt_tokens(
                        counter.tokenizer,
                        prompt_messages,
                    )
                else:
                    record["input_token_num"] = counter.count(
                        prompt_messages[0]["content"]
                    )
                record["block_renderer"] = {
                    "version": "stage2_closed_block_v1",
                    "block_count": len(stage2_blocks[case_id]),
                    "block_ids": [
                        str(block["block_id"])
                        for block in stage2_blocks[case_id]
                    ],
                }
            except (KeyError, ValueError) as exc:
                block_render_errors.append(
                    {
                        "id": case_id,
                        "benchmark": row.get("benchmark"),
                        "reason": str(exc),
                    }
                )
                continue
            serialized = json.dumps(
                record, ensure_ascii=False, separators=(",", ":")
            ) + "\n"
            output.write(serialized)
            if answer_source == "native_gt":
                strict_output.write(serialized)
                strict_output_count += 1
            output_count += 1
            seen_ids.add(case_id)
            benchmark_counts[str(row.get("benchmark") or "<missing>")] += 1
            status_counts[stage6_status] += 1

    report = {
        "schema_version": "v3_sft_paper_build_report_v1",
        "baseline": str(args.baseline),
        "stage6": str(args.stage6),
        "output": str(args.output),
        "strict_output": str(args.strict_output),
        "baseline_count": len(baseline_rows),
        "stage6_intersection_count": len(stage6_by_id),
        "output_count": output_count,
        "strict_output_count": strict_output_count,
        "skipped_count": len(skipped),
        "block_render_error_count": len(block_render_errors),
        "skipped_reasons": dict(
            Counter(
                "|".join(
                    [
                        str(item["stage6_status"]),
                        str(item["summary_status"]),
                        str(item["answerable"]),
                        str(item["has_frozen_answer"]),
                    ]
                )
                for item in skipped
            )
        ),
        "duplicate_output_ids": output_count - len(seen_ids),
        "benchmark_counts": dict(sorted(benchmark_counts.items())),
        "native_gt_count": sum(gt_source_counts.values()),
        "native_gt_missing_count": len(missing_gt),
        "native_gt_sources": dict(sorted(gt_source_counts.items())),
        "answer_sources": dict(sorted(answer_source_counts.items())),
        "length_control": {
            benchmark: dict(counts)
            for benchmark, counts in sorted(length_stats.items())
        },
        "missing_gt_file": str(args.missing_gt),
        "notes": [
            "target_answer uses high-confidence native GT when available and otherwise falls back to the frozen SFT answer.",
            "native_gt is metadata only and is not included in messages.",
            "Only Stage-6 PASS cases with summary validation PASS and answerability=true are emitted.",
            "A missing native_gt is reported, never fabricated from a model answer.",
            "Input is rendered from Stage-2 2_rendered_cases.jsonl and 2_blocks.jsonl.",
            "Evidence is a JSON array inside <evidence>, matching the RL reward parser.",
        ],
    }
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with args.missing_gt.open("w", encoding="utf-8") as handle:
        for item in missing_gt:
            handle.write(
                json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
    with args.skipped.open("w", encoding="utf-8") as handle:
        for item in skipped:
            handle.write(
                json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
    block_error_path = args.report.with_name("sft_block_render_errors.jsonl")
    with block_error_path.open("w", encoding="utf-8") as handle:
        for item in block_render_errors:
            handle.write(
                json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--baseline",
        type=Path,
        default=TRAIN_ROOT / "data" / "sft_baseline.jsonl",
    )
    parser.add_argument(
        "--stage6",
        type=Path,
        default=CODE_V3_ROOT
        / "data_struction/output/6_canonical_summary_answerability_full_v2_20260815/"
        / "6_canonical_summary.jsonl",
    )
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=DATA_V3_ROOT / "raw",
    )
    parser.add_argument(
        "--master",
        type=Path,
        default=DATA_V3_ROOT
        / "processed/v3_benchmark_gate_20260812/master.jsonl",
    )
    parser.add_argument(
        "--allocation",
        type=Path,
        default=DATA_V3_ROOT
        / "processed/v3_balanced_20260814_framesfix/allocation.jsonl",
    )
    parser.add_argument(
        "--tokenizer",
        type=Path,
        default=CODE_V3_ROOT
        / "data_struction/model/tokenizer/Qwen2.5-7B-Instruct",
    )
    parser.add_argument(
        "--stage2-root",
        type=Path,
        default=CODE_V3_ROOT
        / "data_struction/output/2_full_v3_balanced_20260814_framesfix/sft",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=TRAIN_ROOT / "data" / "sft_paper_messages_full.jsonl",
    )
    parser.add_argument(
        "--strict-output",
        type=Path,
        default=TRAIN_ROOT / "data" / "sft_paper_messages.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=TRAIN_ROOT / "data" / "audit" / "sft_paper_build_report.json",
    )
    parser.add_argument(
        "--missing-gt",
        type=Path,
        default=TRAIN_ROOT / "data" / "audit" / "sft_paper_missing_gt.jsonl",
    )
    parser.add_argument(
        "--skipped",
        type=Path,
        default=TRAIN_ROOT
        / "data"
        / "audit"
        / "sft_paper_messages_skipped.jsonl",
    )
    return parser.parse_args()


if __name__ == "__main__":
    report = build(parse_args())
    print(json.dumps(report, ensure_ascii=False, indent=2))
