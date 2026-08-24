#!/usr/bin/env python3
"""Shared helpers for the incremental train/test data pipeline."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def iter_jsonl(path: Path) -> Iterator[Tuple[int, Dict[str, Any]]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("{}:{} is not a JSON object".format(path, line_number))
                yield line_number, value


def read_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    for _, value in iter_jsonl(path):
        yield value


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    count = 0
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
            count += 1
    temporary.replace(path)
    return count


def sha256_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def canonical_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u0000", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def prompt_text(value: Any) -> str:
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, Mapping):
                parts.append(str(item.get("content") or ""))
            else:
                parts.append(str(item or ""))
        return "\n".join(parts)
    if isinstance(value, Mapping):
        return str(value.get("content") or value.get("text") or "")
    return str(value or "")


def message_list(value: Any) -> List[Dict[str, str]]:
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, Mapping):
                result.append(
                    {
                        "role": str(item.get("role") or "user"),
                        "content": str(item.get("content") or ""),
                    }
                )
        return result
    return [{"role": "user", "content": prompt_text(value)}] if value else []


def last_nonempty_match(text: str, patterns: Iterable[str]) -> Optional[re.Match]:
    matches = []
    for pattern in patterns:
        matches.extend(re.finditer(pattern, text, flags=re.I | re.S))
    return max(matches, key=lambda match: match.start()) if matches else None


def question_from_prompt(benchmark: str, prompt: Any, problem: str = "") -> str:
    """Extract the actual question, excluding document text and output protocol."""
    text = prompt_text(prompt)
    if problem and not text:
        text = str(problem)
    benchmark = str(benchmark or "")

    if benchmark == "AA-LCR":
        match = last_nonempty_match(
            text,
            (r"--Question--\s*(.*?)\s*--End of Question--",),
        )
        if match:
            return canonical_text(match.group(1))

    if benchmark in {"QwenLong-Test-DocMath", "LongBenchV2"}:
        match = last_nonempty_match(
            text,
            (
                r"</text>\s*(.*?)\s*(?:Format your response|Let's think step by step|Let[’']s think step by step)",
                r"</text>\s*(.*?)\s*$",
            ),
        )
        if match:
            question = canonical_text(match.group(1))
            if question:
                return question

    if benchmark in {"HELMET-LongQA"}:
        matches = list(re.finditer(r"Question:\s*(.*?)\s*Answer:", text, flags=re.I | re.S))
        if matches:
            return canonical_text(matches[-1].group(1))

    if benchmark in {"HELMET-Rerank"}:
        # A single HELMET-Rerank prompt may concatenate many ranking tasks.
        # The old DOTALL pattern matched from the first Query marker through
        # the end of the prompt, so intermediate rankings and documents were
        # treated as part of the question.  The target query is the last
        # line-level Query marker.
        matches = re.findall(
            r"(?im)^[ \t]*Query\s*:\s*([^\r\n]*)[ \t]*$",
            text,
        )
        if matches:
            question = canonical_text(matches[-1])
            if question:
                return question

    if benchmark == "HELMET-Cite":
        matches = list(re.finditer(r"(?:^|\n)Question\s*:\s*(.*?)(?=\nDocument\s+\[|\Z)", text, flags=re.I | re.S))
        if matches:
            return canonical_text(matches[-1].group(1))

    if benchmark == "HELMET-Summ":
        for marker in ("Now summarize the book.", "Now please summarize the case.", "Summary:"):
            position = text.rfind(marker)
            if position >= 0:
                prefix = text[:position]
                match = re.search(
                    r"(?:Write a summary.*?|Only write about.*?|Now, write a summary.*?)\s*$",
                    prefix,
                    flags=re.I | re.S,
                )
                if match:
                    return canonical_text(match.group(0))
        return (
            "Summarize the plot and characters of the provided story, "
            "without discussing themes or background."
        )

    if benchmark.startswith("LongBench-Pro-"):
        match = last_nonempty_match(
            text,
            (r"(.*?)(?:Output example|输出示例)\s*[:：]",),
        )
        if match:
            candidate = canonical_text(match.group(1))
            if candidate:
                return candidate

    if benchmark in {"HELMET-Cite", "HELMET-Rerank"}:
        return canonical_text(text.split("\n", 1)[0])

    # Canonical rows for several datasets already contain a clean problem.
    if problem:
        problem_text = str(problem)
        for marker in ("--Question--", "Question:", "Query:"):
            position = problem_text.rfind(marker)
            if position >= 0:
                candidate = problem_text[position + len(marker):]
                candidate = re.split(
                    r"--End of Question--|\\n(?:Your Answer|Answer:)",
                    candidate,
                    maxsplit=1,
                    flags=re.I,
                )[0]
                if canonical_text(candidate):
                    return canonical_text(candidate)
        return canonical_text(problem_text)
    return canonical_text(text)


def answer_value(value: Any) -> Any:
    if isinstance(value, Mapping) and "answer" in value:
        return value.get("answer")
    return value


def answer_text(value: Any) -> str:
    value = answer_value(value)
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False)


def gt_from_canonical(row: Mapping[str, Any]) -> Dict[str, Any]:
    benchmark = str(row.get("source_dataset") or "")
    if benchmark == "LongBenchV2":
        answer = row.get("gold")
        return {
            "answer": answer,
            "eval_type": row.get("eval_type") or "rule-based",
            "main_score_name": row.get("main_score_name") or "exact_match",
        }
    reward_model = row.get("reward_model")
    if isinstance(reward_model, Mapping) and reward_model.get("ground_truth") is not None:
        return {
            "answer": reward_model.get("ground_truth"),
            "eval_type": "rule-based",
            "main_score_name": "exact_match",
        }
    gold = row.get("gold")
    if gold is not None:
        if isinstance(gold, Mapping) and "answer" in gold:
            return dict(gold)
        return {
            "answer": gold,
            "eval_type": row.get("eval_type"),
            "main_score_name": row.get("main_score_name"),
        }
    return {
        "answer": row.get("answer"),
        "eval_type": row.get("eval_type"),
        "main_score_name": row.get("main_score_name"),
    }


def source_row_from_canonical(
    row: Mapping[str, Any],
    source_path: Path,
    source_line_number: int,
    split: str,
) -> Dict[str, Any]:
    benchmark = str(row.get("source_dataset") or "")
    prompt = row.get("prompt")
    question = question_from_prompt(
        benchmark,
        prompt,
        str(row.get("problem") or ""),
    )
    gt = gt_from_canonical(row)
    case_id = str(row.get("case_id") or "")
    return {
        "id": case_id,
        "case_id": case_id,
        "benchmark": benchmark,
        "ability": str(row.get("primary_ability") or row.get("ability") or ""),
        "question": question,
        "prompt": message_list(prompt),
        "gt": gt,
        "input_token_num": row.get("input_token_num"),
        "length_bucket": row.get("length_bucket"),
        "source_split": split,
        "source_dataset": benchmark,
        "original_data_id": row.get("original_data_id"),
        "source_file": str(source_path),
        "source_line_number": source_line_number,
        "source_row_type": "canonical_ood",
        "source_prompt_hash": row.get("prompt_hash"),
    }


def source_row_from_longbenchv2(
    row: Mapping[str, Any],
    source_path: Path,
    source_line_number: int,
    ordinal: int,
) -> Dict[str, Any]:
    problem = str(row.get("problem") or "")
    case_id = "v3_lbv2_{:04d}_{}".format(
        ordinal,
        sha256_text("LongBenchV2|" + str(row.get("data_id") or "") + "|" + problem)[:12],
    )
    question = question_from_prompt("LongBenchV2", [{"role": "user", "content": problem}])
    return {
        "id": case_id,
        "case_id": case_id,
        "benchmark": "LongBenchV2",
        "ability": "reasoning",
        "question": question,
        "prompt": [{"role": "user", "content": problem}],
        "gt": {
            "answer": row.get("gold"),
            "eval_type": row.get("eval_type") or "rule-based",
            "main_score_name": row.get("main_score_name") or "exact_match",
        },
        "input_token_num": None,
        "length_bucket": None,
        "source_split": "raw_longbenchv2_128k",
        "source_dataset": "LongBenchV2",
        "original_data_id": row.get("data_id"),
        "source_file": str(source_path),
        "source_line_number": source_line_number,
        "source_row_type": "raw_longbenchv2_128k",
        "raw_ordinal": ordinal,
    }


def stable_fraction(case_id: str) -> float:
    value = int(sha256_text(case_id)[:16], 16)
    return value / float(16 ** 16)


def safe_case_name(case_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", str(case_id))
    return value[:160] + "_" + sha256_text(case_id)[:12]


def strip_old_markers(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"\[/?(?:BLOCK_ID|Block ID):\s*[A-Za-z0-9_-]+\]", "", value)
    value = re.sub(r"\[/?block_id(?::|\])[^]]*\]?", "", value, flags=re.I)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()
