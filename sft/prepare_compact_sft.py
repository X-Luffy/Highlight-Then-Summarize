#!/usr/bin/env python3
"""Build a compact SFT variant from the current paper SFT messages.

The current target asks the model to copy full evidence spans, write a
summary, and then reproduce the native answer.  This converter keeps the
three-stage protocol but makes evidence an ID-only index and removes only
conservative answer duplication.  The original file is never modified.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

try:
    from .system_prompt import ANSWER_ONLY_SYSTEM_PROMPT, COMPACT_SYSTEM_PROMPT
except ImportError:
    from system_prompt import ANSWER_ONLY_SYSTEM_PROMPT, COMPACT_SYSTEM_PROMPT

TAG_RE = {
    name: re.compile(fr"<{name}>(.*?)</{name}>", re.IGNORECASE | re.DOTALL)
    for name in ("evidence", "summary", "answer")
}
EVIDENCE_LINE_RE = re.compile(
    r"\[(E\d+)\]\s+block_id=([A-Za-z0-9_-]+)\s*:", re.IGNORECASE
)
CLAIM_REF_RE = re.compile(r"\[(clm_[A-Za-z0-9_-]+)\]")
SPACE_RE = re.compile(r"[ \t]+")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Take the first N rows after loading. 0 means all rows.",
    )
    parser.add_argument(
        "--summary-char-limit",
        type=int,
        default=1600,
        help="Soft cap. Only complete sentences are retained when exceeded.",
    )
    parser.add_argument(
        "--task-aware-summary",
        action="store_true",
        help="Use a retrieval-specific non-redundant summary policy.",
    )
    parser.add_argument(
        "--answer-only",
        action="store_true",
        help="Build an answer-only control variant instead of the compact protocol.",
    )
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def section(text: str, name: str) -> str:
    match = TAG_RE[name].search(text)
    return match.group(1).strip() if match else ""


def compact_evidence(raw: str) -> tuple[str, int]:
    """Keep stable evidence IDs and block IDs, not copied source spans."""
    seen: set[tuple[str, str]] = set()
    items: list[str] = []
    for match in EVIDENCE_LINE_RE.finditer(raw):
        evidence_id, block_id = match.groups()
        key = (evidence_id.upper(), block_id)
        if key in seen:
            continue
        seen.add(key)
        items.append(f"[{key[0]}] block_id={key[1]}")
    return "\n".join(items), len(items)


def normalize_text(text: str) -> str:
    text = SPACE_RE.sub(" ", text.replace("\r\n", "\n")).strip()
    return text


def sentence_parts(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text)
    return [normalize_text(part) for part in parts if normalize_text(part)]


def compact_summary(text: str, char_limit: int) -> tuple[str, bool]:
    """Remove exact repeated sentences and apply a sentence-safe soft cap."""
    sentences: list[str] = []
    seen: set[str] = set()
    for sentence in sentence_parts(text):
        key = re.sub(r"\s+", "", sentence)
        if key and key not in seen:
            seen.add(key)
            sentences.append(sentence)

    result = " ".join(sentences)
    if len(result) <= char_limit:
        return result, False

    kept: list[str] = []
    current = 0
    for sentence in sentences:
        extra = len(sentence) + (1 if kept else 0)
        if current + extra > char_limit:
            break
        kept.append(sentence)
        current += extra
    # If a single sentence is longer than the cap, keep it intact rather than
    # creating an incomplete claim by cutting in the middle.
    return (" ".join(kept) if kept else sentences[0]), True


def split_paragraphs(text: str) -> list[str]:
    return [
        normalize_text(part)
        for part in re.split(r"\n\s*\n+", text)
        if normalize_text(part)
    ]


def paragraph_similarity(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", left.lower()))
    right_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", right.lower()))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def answer_containment(candidate: str, answer: str) -> float:
    candidate_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", candidate.lower()))
    answer_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", answer.lower()))
    if not candidate_tokens:
        return 0.0
    return len(candidate_tokens & answer_tokens) / len(candidate_tokens)


def retrieval_summary(
    summary: str,
    answer: str,
    evidence: str,
    question: str,
) -> tuple[str, bool]:
    """Keep retrieval summaries from copying the retrieved answer."""
    answer_sentences = sentence_parts(answer)
    kept: list[str] = []
    for candidate in sentence_parts(summary):
        max_containment = answer_containment(candidate, answer)
        if answer_sentences:
            max_containment = max(
                max_containment,
                max(answer_containment(candidate, reference) for reference in answer_sentences),
            )
        if max_containment < 0.35:
            kept.append(candidate)
        if len(kept) >= 2:
            break

    if kept:
        return " ".join(kept), False
    evidence_ids = re.findall(r"\[(E\d+)\]", evidence)
    refs = " ".join(f"[{item}]" for item in evidence_ids[:4])
    chinese = len(re.findall(r"[\u4e00-\u9fff]", question)) > len(
        re.findall(r"[A-Za-z]", question)
    )
    if chinese:
        return (
            f"相关证据位于 {refs}。最终答案严格遵循用户要求的检索、排序或格式协议。",
            True,
        )
    return (
        f"Relevant evidence is identified by {refs}. "
        "The final answer follows the user's requested extraction or ordering format.",
        True,
    )


def compact_answer(text: str, benchmark: str) -> tuple[str, int]:
    """Remove exact or very conservative semantic duplicates.

    The semantic rule is limited to numeric QA, where the existing native GT
    can contain multiple paraphrases of the same numeric answer.  It does not
    alter list answers, summaries, or general QA text.
    """
    paragraphs = split_paragraphs(text)
    kept: list[str] = []
    removed = 0
    for paragraph in paragraphs:
        if any(paragraph == prior for prior in kept):
            removed += 1
            continue
        if benchmark in {"FinGLM", "DocFinQA"} and kept:
            current_numbers = NUM_RE.findall(paragraph)
            for index, prior in enumerate(kept):
                prior_numbers = NUM_RE.findall(prior)
                if (
                    current_numbers
                    and current_numbers == prior_numbers
                    and paragraph_similarity(paragraph, prior) >= 0.72
                ):
                    # Keep the shorter canonical wording.
                    if len(paragraph) < len(prior):
                        kept[index] = paragraph
                    removed += 1
                    break
            else:
                kept.append(paragraph)
        else:
            kept.append(paragraph)
    return "\n\n".join(kept).strip(), removed


def convert(
    row: dict[str, Any],
    summary_char_limit: int,
    task_aware_summary: bool,
    answer_only: bool,
) -> tuple[dict[str, Any], dict[str, int]]:
    messages = row.get("messages") or []
    if len(messages) < 3:
        raise ValueError(f"{row.get('id')}: expected system/user/assistant messages")

    old_assistant = str(messages[-1].get("content") or "")
    evidence_raw = section(old_assistant, "evidence")
    summary_raw = section(old_assistant, "summary")
    answer_raw = section(old_assistant, "answer")
    if not evidence_raw or not summary_raw or not answer_raw:
        raise ValueError(f"{row.get('id')}: missing one of evidence/summary/answer")

    evidence, evidence_count = compact_evidence(evidence_raw)
    summary, summary_clipped = compact_summary(summary_raw, summary_char_limit)
    answer, answer_removed = compact_answer(
        answer_raw,
        str(row.get("benchmark") or ""),
    )
    if answer_only:
        assistant = f"<answer>\n{answer}\n</answer>"
        system_prompt = ANSWER_ONLY_SYSTEM_PROMPT
    else:
        assistant = (
            "<evidence>\n"
            f"{evidence}\n"
            "</evidence>\n"
            "<summary>\n"
            f"{summary}\n"
            "</summary>\n"
            "<answer>\n"
            f"{answer}\n"
            "</answer>"
        )
        system_prompt = COMPACT_SYSTEM_PROMPT
    retrieval_summary_fallback = 0
    if (
        not answer_only
        and task_aware_summary
        and row.get("ability") == "precise_retrieval"
    ):
        summary, retrieval_summary_fallback = retrieval_summary(
            summary,
            answer,
            evidence,
            str(row.get("question") or ""),
        )
        assistant = (
            "<evidence>\n"
            f"{evidence}\n"
            "</evidence>\n"
            "<summary>\n"
            f"{summary}\n"
            "</summary>\n"
            "<answer>\n"
            f"{answer}\n"
            "</answer>"
        )

    output = {
        "schema_version": (
            "v3_sft_answer_only_v1" if answer_only else "v3_sft_compact_v1"
        ),
        "id": row.get("id"),
        "benchmark": row.get("benchmark"),
        "ability": row.get("ability"),
        "question": row.get("question"),
        "input_token_num": row.get("input_token_num"),
        "input_token_num_mode": row.get("input_token_num_mode"),
        "messages": [
            {"role": "system", "content": system_prompt},
            messages[-2],
            {"role": "assistant", "content": assistant},
        ],
    }
    return output, {
        "evidence_count": evidence_count,
        "summary_clipped": int(summary_clipped),
        "retrieval_summary_fallback": int(retrieval_summary_fallback),
        "answer_paragraphs_removed": answer_removed,
        "old_assistant_chars": len(old_assistant),
        "new_assistant_chars": len(assistant),
    }


def main() -> None:
    args = parse_args()
    rows = read_jsonl(args.input)
    if args.limit > 0:
        rows = rows[: args.limit]

    output_rows: list[dict[str, Any]] = []
    stats = {
        "rows": 0,
        "summary_clipped": 0,
        "retrieval_summary_fallback": 0,
        "answer_paragraphs_removed": 0,
        "old_assistant_chars": 0,
        "new_assistant_chars": 0,
    }
    for row in rows:
        converted, row_stats = convert(
            row,
            args.summary_char_limit,
            args.task_aware_summary,
            args.answer_only,
        )
        output_rows.append(converted)
        stats["rows"] += 1
        for key in stats:
            if key != "rows":
                stats[key] += row_stats[key]

    write_jsonl(args.output, output_rows)
    report_path = args.output.with_suffix(".report.json")
    report_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
