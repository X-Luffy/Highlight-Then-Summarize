#!/usr/bin/env python3
"""Deterministic benchmark evaluators for V3.

The module intentionally has no LLM dependency.  Every evaluator returns a
score in [0, 1] and an auditable metric breakdown.
"""

from __future__ import annotations

import json
import math
import re
import string
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence


@dataclass
class EvaluationResult:
    score: float
    metric: str
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _nfkc(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or ""))


def strip_answer_marker(value: Any) -> str:
    text = _nfkc(value).strip()
    text = re.sub(
        r"^\s*\[(?:answer|答案)\]\s*[:：]?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


def extract_prediction(value: Any) -> str:
    """Extract FinalAnswer from JSON, tagged output, or plain text."""
    if isinstance(value, Mapping):
        for key in ("answer", "final_answer", "prediction", "output"):
            if key in value:
                return extract_prediction(value[key])
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "\n".join(extract_prediction(item) for item in value)

    text = _nfkc(value).strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, (Mapping, list)):
        return extract_prediction(parsed)
    if isinstance(parsed, str):
        return parsed.strip()

    tag_match = re.search(
        r"<answer>\s*(.*?)\s*</answer>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if tag_match:
        return tag_match.group(1).strip()

    open_tag_match = re.search(
        r"<answer>\s*(.*)$",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if open_tag_match:
        return open_tag_match.group(1).strip()

    marker = re.search(
        r"\[(?:answer|答案)\]\s*[:：]?\s*(.*)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if marker:
        return marker.group(1).strip()

    # A structured response that never reached <answer> is a protocol
    # failure. Do not score its evidence/summary as a final answer.
    if re.search(r"<(?:evidence|summary)>", text, flags=re.IGNORECASE):
        return ""
    return text


def normalize_answer(value: Any) -> str:
    text = strip_answer_marker(value).casefold()
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    punctuation = string.punctuation + "，。！？；：、“”‘’（）【】《》"
    text = "".join(" " if character in punctuation else character for character in text)
    return " ".join(text.split())


def normalize_structured_item(value: Any) -> str:
    """Normalize a set/ranking item without deleting labels such as A."""
    text = strip_answer_marker(value).casefold()
    punctuation = string.punctuation + "，。！？；：、“”‘’（）【】《》"
    text = "".join(" " if character in punctuation else character for character in text)
    return " ".join(text.split())


def _has_cjk(text: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" for character in text)


def tokenize(value: Any) -> List[str]:
    text = normalize_answer(value)
    if not text:
        return []
    # Use the same character granularity on both sides of every CJK QA.
    # Punctuation normalization introduces spaces, so choosing granularity by
    # ``len(text.split())`` made a punctuated model answer word-level while an
    # unpunctuated reference stayed character-level, yielding a false F1=0.
    if _has_cjk(text):
        return [character for character in text if not character.isspace()]
    return text.split()


def exact_match(prediction: Any, reference: Any) -> float:
    return float(normalize_answer(prediction) == normalize_answer(reference))


def token_f1(prediction: Any, reference: Any) -> float:
    predicted_text = normalize_answer(prediction)
    expected_text = normalize_answer(reference)
    character_level = _has_cjk(predicted_text) or _has_cjk(expected_text)

    def paired_tokens(text: str) -> List[str]:
        if character_level:
            return [character for character in text if not character.isspace()]
        return text.split()

    # Pick granularity jointly.  A bilingual answer must not be characters on
    # one side and whitespace-delimited words on the other.
    predicted = Counter(paired_tokens(predicted_text))
    expected = Counter(paired_tokens(expected_text))
    if not predicted and not expected:
        return 1.0
    if not predicted or not expected:
        return 0.0
    overlap = sum((predicted & expected).values())
    if overlap == 0:
        return 0.0
    precision = overlap / sum(predicted.values())
    recall = overlap / sum(expected.values())
    return 2.0 * precision * recall / (precision + recall)


def _summary_checkpoint_reference(record: Mapping[str, Any]) -> Any:
    for verifier in record.get("verifier") or []:
        if not isinstance(verifier, Mapping):
            continue
        for group in verifier.get("check_list") or []:
            for item in group or []:
                if not isinstance(item, Mapping):
                    continue
                if item.get("verifier_name") != "UniSummaryRecallVerifier":
                    continue
                checkpoints = (item.get("verifier_para") or {}).get("checkpoint")
                if checkpoints not in (None, "", [], {}):
                    return checkpoints
    return None


def reference_payload(record: Mapping[str, Any]) -> Any:
    gt = record.get("gt")
    if isinstance(gt, Mapping) and "answer" in gt:
        return gt.get("answer")
    if gt not in (None, "", [], {}):
        return gt

    if str(record.get("benchmark") or "") == "LongBench-Pro-T4":
        checkpoint = _summary_checkpoint_reference(record)
        if checkpoint is not None:
            return checkpoint

    references: List[Any] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key == "reference":
                    if isinstance(item, list):
                        references.extend(item)
                    else:
                        references.append(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(record.get("verifier"))
    if references:
        return references
    assistant = record.get("assistant")
    if isinstance(assistant, Mapping) and assistant.get("answer") not in (None, ""):
        return assistant.get("answer")
    return None


def _reference_payload(record: Mapping[str, Any]) -> Any:
    """Backward-compatible alias for earlier callers."""
    return reference_payload(record)


def reference_answers(record: Mapping[str, Any]) -> List[str]:
    payload = reference_payload(record)
    if payload is None:
        return []
    if isinstance(payload, list):
        return [str(item) for item in payload]
    return [str(payload)]


def _best_reference_score(
    prediction: str,
    references: Sequence[str],
    scorer: Callable[[Any, Any], float],
) -> float:
    return max((scorer(prediction, reference) for reference in references), default=0.0)


def _qa_reference_answers(record: Mapping[str, Any]) -> List[str]:
    """Return QA references, expanding benchmark-native alias protocols.

    FinGLM canonical gold uses ``[Answer]`` followed by three newline-separated
    paraphrases.  They are alternatives, not one three-paragraph answer.  Keep
    the original payload as well so evaluating the serialized GT against itself
    remains exactly 1.0.
    """
    references = reference_answers(record)
    if str(record.get("benchmark") or "") != "FinGLM":
        return references
    payload = reference_payload(record)
    if not isinstance(payload, str):
        return references
    lines = [line.strip() for line in payload.splitlines() if line.strip()]
    if len(lines) < 2 or not re.fullmatch(
        r"\[(?:answer|答案)\]", lines[0], flags=re.IGNORECASE
    ):
        return references
    aliases = lines[1:]
    return list(dict.fromkeys([*references, *aliases]))


_FRAMES_ANSWER_RE = re.compile(
    r"(?:therefore,\s*)?(?:the\s+)?answer\s+is\s+(.+)",
    flags=re.IGNORECASE | re.DOTALL,
)

# These source rows failed the dataset's own answer validation
# (``gt.judge_result == "NO"``).  They must not enter Frames aggregates.
_FRAMES_EXCLUDED_CASE_IDS = frozenset(
    {
        "v3_frames_0783_1b81c86b51da",
        "v3_frames_0001_aa5ae5c4e754",
    }
)


def is_excluded_evaluation_record(record: Mapping[str, Any]) -> bool:
    """Return whether a record is excluded by a benchmark-native audit rule."""
    return (
        str(record.get("benchmark") or "") == "Frames"
        and str(record.get("id") or record.get("case_id") or "")
        in _FRAMES_EXCLUDED_CASE_IDS
    )


def _frames_answer_text(value: Any) -> str:
    text = strip_answer_marker(value).strip()
    matches = list(_FRAMES_ANSWER_RE.finditer(text))
    if not matches:
        return text
    return matches[-1].group(1).strip()


def evaluate_qa(record: Mapping[str, Any], prediction: str) -> EvaluationResult:
    references = _qa_reference_answers(record)
    if not references:
        return EvaluationResult(0.0, "no_reference", {"references": []})
    if str(record.get("benchmark") or "") == "Frames":
        prediction = _frames_answer_text(prediction)
        references = [_frames_answer_text(reference) for reference in references]
        em = _best_reference_score(prediction, references, exact_match)
        return EvaluationResult(
            em,
            "frames_exact_match",
            {"exact_match": em, "references": references},
        )
    em = _best_reference_score(prediction, references, exact_match)
    f1 = _best_reference_score(prediction, references, token_f1)
    return EvaluationResult(
        _clip(max(em, f1)),
        "qa_em_f1",
        {"exact_match": em, "token_f1": f1, "references": references},
    )


def evaluate_strict_exact(
    record: Mapping[str, Any], prediction: str
) -> EvaluationResult:
    references = reference_answers(record)
    score = _best_reference_score(prediction, references, exact_match)
    return EvaluationResult(
        score,
        "exact_match",
        {"exact_match": score, "references": references},
    )


_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:\.\d+)?(?:[eE][-+]?\d+)?%?(?![A-Za-z0-9])"
)


def _numbers(value: Any) -> List[Decimal]:
    result: List[Decimal] = []
    for match in _NUMBER_RE.findall(_nfkc(value)):
        cleaned = match.replace(",", "")
        is_percent = cleaned.endswith("%")
        if is_percent:
            cleaned = cleaned[:-1]
        try:
            number = Decimal(cleaned)
        except InvalidOperation:
            continue
        if is_percent:
            number /= Decimal(100)
        result.append(number)
    return result


_ANSWER_CUE_RE = re.compile(
    r"(?:final\s+answer|the\s+answer\s+is|answer\s*[:：]|"
    r"答案(?:是|为)?\s*[:：]?|结果(?:是|为)?\s*[:：]?|"
    r"因此(?:答案)?\s*[:：]?)",
    flags=re.IGNORECASE,
)


def _numeric_answer_region(value: Any) -> str:
    """Keep derivation numbers out of numeric scoring when possible.

    Baseline answers are often free-form explanations.  Scoring every number
    in the explanation lets an intermediate year, operand, or table index
    accidentally satisfy the gold value.  Prefer an explicit final-answer
    clause; otherwise use the last non-empty line containing a number.
    """
    text = _nfkc(value).strip()
    if not text:
        return ""
    matches = list(_ANSWER_CUE_RE.finditer(text))
    if matches:
        suffix = text[matches[-1].end() :].strip()
        if suffix:
            return suffix
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        for line in reversed(lines):
            if _numbers(line):
                return line
    return text


def _numeric_match_score(predicted: Decimal, expected: Decimal) -> float:
    difference = abs(predicted - expected)
    tolerance = max(Decimal("0.0001"), abs(expected) * Decimal("0.001"))
    normalized = difference / tolerance
    if normalized <= 1:
        return 1.0
    return float(max(Decimal(0), Decimal(1) / normalized))


def evaluate_numeric(
    record: Mapping[str, Any], prediction: str
) -> EvaluationResult:
    references = reference_answers(record)
    predicted_numbers = _numbers(_numeric_answer_region(extract_prediction(prediction)))
    reference_numbers = [
        number for reference in references for number in _numbers(reference)
    ]
    if not predicted_numbers or not reference_numbers:
        fallback = evaluate_qa(record, prediction)
        fallback.metric = "numeric_fallback_qa"
        return fallback

    # Match each gold number to at most one predicted number.  For the common
    # single-answer case this is equivalent to the old tolerance score.  For
    # multi-answer cases it prevents one correct number from masking missing
    # answers.
    remaining = list(predicted_numbers)
    matched_scores: List[float] = []
    best_pairs: List[List[str]] = []
    for expected in reference_numbers:
        if not remaining:
            matched_scores.append(0.0)
            continue
        candidate = max(
            remaining,
            key=lambda predicted: _numeric_match_score(predicted, expected),
        )
        score_for_expected = _numeric_match_score(candidate, expected)
        matched_scores.append(score_for_expected)
        best_pairs.append([str(candidate), str(expected)])
        remaining.remove(candidate)
    score = sum(matched_scores) / len(reference_numbers)
    return EvaluationResult(
        _clip(score),
        "numeric_tolerance",
        {
            "best_pairs": best_pairs,
            "predicted_numbers": [str(value) for value in predicted_numbers],
            "reference_numbers": [str(value) for value in reference_numbers],
            "matched_reference_count": sum(score == 1.0 for score in matched_scores),
        },
    )


def _split_items(value: Any) -> List[str]:
    if isinstance(value, list):
        return [
            normalize_structured_item(item)
            for item in value
            if normalize_structured_item(item)
        ]
    text = strip_answer_marker(value)
    citation_tags = re.findall(r"<cite>\s*([^<]+?)\s*</cite>", text, re.IGNORECASE)
    if citation_tags:
        return [normalize_structured_item(item) for item in citation_tags]

    compact = re.sub(r"\s+", "", text).upper()
    if re.fullmatch(r"[A-J]{2,10}", compact):
        return [item.casefold() for item in compact]

    parts = re.split(r"\s*(?:\n+|;|；|\|)\s*", text)
    if len(parts) == 1:
        comma_parts = re.split(r"\s*(?:,|，|、)\s*", text)
        if len(comma_parts) > 1 and all(
            re.fullmatch(
                r"(?:[A-J]|[APB]\d+|[A-D]\.\d+(?:\.\d+){1,3}|"
                r"article\s+\d+|paragraph\s+\d+)",
                part.strip(),
                flags=re.IGNORECASE,
            )
            for part in comma_parts
        ):
            parts = comma_parts

    normalized = [
        normalize_structured_item(re.sub(r"^\s*[-*]\s*", "", part))
        for part in parts
    ]
    return [item for item in normalized if item]


def _set_f1(predicted: Iterable[str], expected: Iterable[str]) -> Dict[str, float]:
    predicted_set = {item for item in predicted if item}
    expected_set = {item for item in expected if item}
    if not predicted_set and not expected_set:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    if not predicted_set or not expected_set:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    overlap = len(predicted_set & expected_set)
    precision = overlap / len(predicted_set)
    recall = overlap / len(expected_set)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {"precision": precision, "recall": recall, "f1": f1}


def _reference_set_groups(value: Any) -> List[List[str]]:
    """Return set items where each item can contain accepted aliases."""
    def aliases_for(raw_value: Any) -> List[str]:
        raw = " ".join(_nfkc(raw_value).split())
        candidates = [raw]
        match = re.fullmatch(
            r"(.*?)\s*\(\s*or\s+(.*?)\s*\)",
            raw,
            flags=re.IGNORECASE,
        )
        if match:
            candidates.append(match.group(1))
            candidates.extend(
                re.split(r"\s+or\s+", match.group(2), flags=re.IGNORECASE)
            )
        return sorted(
            {
                normalize_structured_item(candidate)
                for candidate in candidates
                if normalize_structured_item(candidate)
            }
        )

    if isinstance(value, list):
        groups: List[List[str]] = []
        seen_groups: set[tuple[str, ...]] = set()
        for item in value:
            if isinstance(item, list):
                aliases = sorted(
                    {
                        normalized
                        for alias in item
                        for normalized in aliases_for(alias)
                    }
                )
            else:
                aliases = aliases_for(item)
            key = tuple(aliases)
            if aliases and key not in seen_groups:
                seen_groups.add(key)
                groups.append(aliases)
        return groups
    return [[item] for item in _split_items(value)]


def _set_alias_f1(
    predicted: Sequence[str],
    expected_groups: Sequence[Sequence[str]],
) -> Dict[str, float]:
    predicted_items = list(dict.fromkeys(item for item in predicted if item))
    groups = [set(group) for group in expected_groups if group]
    if not predicted_items and not groups:
        return {
            "precision": 1.0,
            "recall": 1.0,
            "f1": 1.0,
            "matched_items": 0.0,
        }
    if not predicted_items or not groups:
        return {
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "matched_items": 0.0,
        }

    group_to_prediction: Dict[int, int] = {}

    def match(prediction_index: int, visited: set[int]) -> bool:
        prediction = predicted_items[prediction_index]
        for group_index, aliases in enumerate(groups):
            if group_index in visited or prediction not in aliases:
                continue
            visited.add(group_index)
            previous = group_to_prediction.get(group_index)
            if previous is None or match(previous, visited):
                group_to_prediction[group_index] = prediction_index
                return True
        return False

    matched = sum(
        match(prediction_index, set())
        for prediction_index in range(len(predicted_items))
    )
    precision = matched / len(predicted_items)
    recall = matched / len(groups)
    f1 = (
        0.0
        if precision + recall == 0
        else 2.0 * precision * recall / (precision + recall)
    )
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "matched_items": float(matched),
    }


def evaluate_set(record: Mapping[str, Any], prediction: str) -> EvaluationResult:
    payload = _reference_payload(record)
    expected_groups = _reference_set_groups(payload)
    predicted = _split_items(prediction)
    scores = _set_alias_f1(predicted, expected_groups)
    return EvaluationResult(
        scores["f1"],
        "set_f1",
        {
            **scores,
            "predicted_items": predicted,
            "reference_alias_groups": expected_groups,
        },
    )


def _ranked_items(value: Any) -> List[str]:
    if isinstance(value, list):
        return [
            normalize_structured_item(item)
            for item in value
            if normalize_structured_item(item)
        ]
    text = strip_answer_marker(value)
    text = re.sub(
        r"^\s*(?:ranking|rank|排序|答案)\s*[:：]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    if ">" in text:
        parts = text.split(">")
    elif "\n" in text:
        parts = re.split(r"\s*\n+\s*", text)
    else:
        parts = re.split(r"\s*(?:,|，|、|;|；|\|)\s*", text)
    result: List[str] = []
    for part in parts:
        item = re.sub(r"^\s*\d+[.)、]\s*", "", part)
        item = normalize_structured_item(item)
        if item:
            result.append(item)
    return result


def _ndcg(
    predicted: Sequence[str],
    expected: Sequence[str],
    cutoff: int = 10,
) -> float:
    if not predicted or not expected:
        return 0.0
    reference_rank = {item: index for index, item in enumerate(expected)}
    size = len(expected)

    def relevance(item: str) -> float:
        if item not in reference_rank:
            return 0.0
        return (size - reference_rank[item]) / size

    limit = min(max(1, cutoff), size)
    dcg = sum(
        relevance(item) / math.log2(index + 2)
        for index, item in enumerate(predicted[:limit])
    )
    idcg = sum(
        relevance(item) / math.log2(index + 2)
        for index, item in enumerate(expected[:limit])
    )
    return 0.0 if idcg == 0 else _clip(dcg / idcg)


def _pairwise_accuracy(predicted: Sequence[str], expected: Sequence[str]) -> float:
    predicted_rank = {item: index for index, item in enumerate(predicted)}
    common = [item for item in expected if item in predicted_rank]
    if len(common) < 2:
        return 0.0
    correct = 0
    total = 0
    for left_index, left in enumerate(common):
        for right in common[left_index + 1 :]:
            total += 1
            correct += int(predicted_rank[left] < predicted_rank[right])
    return correct / total if total else 0.0


def _ranking_qrel_pos(record: Mapping[str, Any]) -> Dict[str, float]:
    for verifier in record.get("verifier") or []:
        if not isinstance(verifier, Mapping):
            continue
        for group in verifier.get("check_list") or []:
            for item in group or []:
                if not isinstance(item, Mapping):
                    continue
                qrel = (item.get("verifier_para") or {}).get("qrel_pos")
                if not isinstance(qrel, Mapping) or not qrel:
                    continue
                result: Dict[str, float] = {}
                for document_id, grade in qrel.items():
                    try:
                        numeric_grade = float(grade)
                    except (TypeError, ValueError):
                        continue
                    if numeric_grade > 0:
                        result[normalize_structured_item(document_id)] = numeric_grade
                if result:
                    return result
    return {}


def _ranking_cutoff(record: Mapping[str, Any], default: int = 10) -> int:
    for verifier in record.get("verifier") or []:
        if not isinstance(verifier, Mapping):
            continue
        for group in verifier.get("check_list") or []:
            for item in group or []:
                if not isinstance(item, Mapping):
                    continue
                value = (item.get("verifier_para") or {}).get("k")
                if value is None:
                    continue
                try:
                    return max(1, int(value))
                except (TypeError, ValueError):
                    continue
    return default


def evaluate_ranking(
    record: Mapping[str, Any], prediction: str
) -> EvaluationResult:
    qrel_pos = _ranking_qrel_pos(record)
    if qrel_pos:
        predicted = _ranked_items(prediction)
        cutoff = _ranking_cutoff(record)
        dcg = sum(
            (2.0 ** qrel_pos.get(item, 0.0) - 1.0) / math.log2(rank + 2)
            for rank, item in enumerate(predicted[:cutoff])
        )
        ideal_grades = sorted(qrel_pos.values(), reverse=True)[:cutoff]
        idcg = sum(
            (2.0 ** grade - 1.0) / math.log2(rank + 2)
            for rank, grade in enumerate(ideal_grades)
        )
        ndcg = 0.0 if idcg == 0 else _clip(dcg / idcg)
        return EvaluationResult(
            ndcg,
            f"ndcg@{cutoff}",
            {
                "ndcg": ndcg,
                "dcg": dcg,
                "idcg": idcg,
                "predicted_ranking": predicted,
                "qrel_pos": qrel_pos,
                "reference_source": "verifier.qrel_pos",
            },
        )

    expected = _ranked_items(_reference_payload(record))
    predicted = _ranked_items(prediction)
    cutoff = _ranking_cutoff(record)
    ndcg = _ndcg(predicted, expected, cutoff)
    pairwise = _pairwise_accuracy(predicted, expected)
    coverage = (
        len(set(predicted) & set(expected)) / len(set(expected))
        if expected
        else 0.0
    )
    return EvaluationResult(
        ndcg,
        "ndcg",
        {
            "ndcg": ndcg,
            "pairwise_accuracy": pairwise,
            "item_recall": coverage,
            "predicted_count": len(predicted),
            "reference_count": len(expected),
            "cutoff": cutoff,
        },
    )


_FINAL_CHOICE_RE = re.compile(
    r"(?:"
    r"(?:the\s+)?(?:final\s+)?(?:correct\s+)?(?:answer|option|choice)"
    r"\s*(?:is|=|:)?"
    r"|(?:最终|正确)?(?:答案|选项)\s*(?:为|是|=|:)?"
    r")\s*[\(\[（【]?\s*([A-J])\s*[\)\]）】]?",
    re.IGNORECASE,
)


def _choice_letters(value: Any, *, final_only: bool = False) -> List[str]:
    """Extract choice labels without treating reasoning mentions as answers.

    LongBenchV2 is single-choice. Its responses often discuss several options
    before committing to one, so collecting every label makes a correct final
    answer a false negative. Other choice benchmarks keep the legacy parser.
    """
    if isinstance(value, Mapping):
        for key in ("answer", "reference", "choices"):
            if key in value:
                return _choice_letters(value[key], final_only=final_only)
    if isinstance(value, list):
        return sorted(
            {
                choice
                for item in value
                for choice in _choice_letters(item, final_only=final_only)
            }
        )

    text = strip_answer_marker(value).upper()
    if final_only:
        # The last declaration is the response's final commitment.
        final = _FINAL_CHOICE_RE.findall(text)
        if final:
            return [final[-1]]
        # Bare labels are valid answers; do not scan free-form reasoning.
        compact = re.sub(r"\s+", "", text)
        bare = re.fullmatch(r"[\(\[（【]?([A-J])[\)\]）】]?[.。!！]?", compact)
        return [bare.group(1)] if bare else []

    explicit = re.findall(
        r"(?:ANSWER|OPTION|CHOICE|答案|选项)\s*(?:IS|为|是)?\s*[:：]?\s*"
        r"[\(\[（【]?\s*([A-J])\s*[\)\]）】]?",
        text,
    )
    if explicit:
        return sorted(set(explicit))

    enclosed = re.findall(r"[\(\[（【]\s*([A-J])\s*[\)\]）】]", text)
    if enclosed:
        return sorted(set(enclosed))

    compact = re.sub(r"[\s,，;；、/|]+", "", text)
    if re.fullmatch(r"[A-J]{1,10}", compact):
        return sorted(set(compact))

    standalone = re.findall(r"(?<![A-Z0-9])([A-J])(?![A-Z0-9])", text)
    if standalone and len(text.split()) <= 8:
        return sorted(set(standalone))
    return []


def evaluate_choice(
    record: Mapping[str, Any], prediction: str
) -> EvaluationResult:
    expected = _choice_letters(_reference_payload(record))
    final_only = str(record.get("benchmark") or "") == "LongBenchV2"
    predicted = _choice_letters(prediction, final_only=final_only)
    score = float(bool(expected) and predicted == expected)
    return EvaluationResult(
        score,
        "choice_accuracy",
        {
            "predicted_choices": predicted,
            "reference_choices": expected,
            "choice_parser": "final_declaration" if final_only else "legacy",
        },
    )


def _rouge_tokens(value: Any) -> List[str]:
    text = normalize_answer(value)
    # Always use one tokenization granularity on both sides of a CJK summary.
    # Model answers often contain Markdown headings/newlines while references
    # are a single paragraph.  Basing the choice on whitespace made the model
    # answer word-level and the reference character-level, collapsing ROUGE-L.
    if _has_cjk(text):
        return [character for character in text if not character.isspace()][:4096]
    return text.split()[:4096]


def _lcs_length(left: Sequence[str], right: Sequence[str]) -> int:
    if len(left) > len(right):
        left, right = right, left
    previous = [0] * (len(left) + 1)
    for right_item in right:
        current = [0]
        for index, left_item in enumerate(left, start=1):
            if left_item == right_item:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(current[-1], previous[index]))
        previous = current
    return previous[-1]


def rouge_l(prediction: Any, reference: Any) -> float:
    predicted = _rouge_tokens(prediction)
    expected = _rouge_tokens(reference)
    if not predicted and not expected:
        return 1.0
    if not predicted or not expected:
        return 0.0
    lcs = _lcs_length(predicted, expected)
    precision = lcs / len(predicted)
    recall = lcs / len(expected)
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


def evaluate_summary(
    record: Mapping[str, Any], prediction: str
) -> EvaluationResult:
    references = reference_answers(record)
    score = _best_reference_score(prediction, references, rouge_l)
    return EvaluationResult(
        score,
        "rouge_l",
        {"rouge_l": score, "reference_count": len(references)},
    )


def _citation_ids(value: Any) -> List[str]:
    text = _nfkc(value)
    cite_contents = re.findall(
        r"<cite>\s*(.*?)\s*</cite>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    identifiers: List[str] = []
    for content in cite_contents:
        identifiers.extend(
            normalize_answer(item)
            for item in re.findall(r"\[([^\[\]]+)\]", content)
        )
    if not cite_contents:
        identifiers.extend(
            normalize_answer(item)
            for item in re.findall(r"\[(\d+(?:\s*-\s*\d+)?)\]", text)
        )
    return [item for item in identifiers if item]


def _strip_citation_markup(value: Any) -> str:
    text = _nfkc(value)
    text = re.sub(
        r"<cite>\s*.*?\s*</cite>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"</?statement>", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def evaluate_cited_answer(
    record: Mapping[str, Any], prediction: str
) -> EvaluationResult:
    references = reference_answers(record)
    if not references:
        return EvaluationResult(0.0, "no_reference", {"references": []})

    predicted_content = _strip_citation_markup(prediction)
    content_score = max(
        (
            max(
                token_f1(predicted_content, _strip_citation_markup(reference)),
                rouge_l(predicted_content, _strip_citation_markup(reference)),
            )
            for reference in references
        ),
        default=0.0,
    )
    predicted_citations = _citation_ids(prediction)
    reference_citations = [
        citation
        for reference in references
        for citation in _citation_ids(reference)
    ]
    citation_scores = _set_f1(predicted_citations, reference_citations)
    citation_f1 = citation_scores["f1"]

    if not reference_citations:
        return EvaluationResult(
            content_score,
            "citation_answer_f1",
            {
                "answer_content_score": content_score,
                "citation_reference_available": False,
                "predicted_citations": predicted_citations,
            },
        )

    joint_score = (
        0.0
        if content_score + citation_f1 == 0
        else 2.0 * content_score * citation_f1 / (content_score + citation_f1)
    )
    return EvaluationResult(
        joint_score,
        "cited_answer_hmean",
        {
            "answer_content_score": content_score,
            "citation_f1": citation_f1,
            "citation_precision": citation_scores["precision"],
            "citation_recall": citation_scores["recall"],
            "predicted_citations": predicted_citations,
            "reference_citations": reference_citations,
        },
    )


_SUMMARY_BENCHMARKS = {"CNNSum", "GovReport", "HELMET-Summ"}
_RANKING_BENCHMARKS = {"MSMARCO-Rerank", "HELMET-Rerank"}
_NUMERIC_BENCHMARKS = {"DocFinQA", "QwenLong-Test-DocMath"}
_CHOICE_BENCHMARKS = {"QwenLong-DocMC", "LongBenchV2"}
_CITED_ANSWER_BENCHMARKS = {"LongCite"}
_STRICT_EXACT_BENCHMARKS = {"MRCR"}
_SET_BENCHMARKS = {"AA-LCR"}
_QA_BENCHMARKS = {"HELMET-LongQA"}

_LONG_BENCH_PRO_METRICS = {
    1: "set",
    2: "ranking",
    3: "choice",
    4: "summary",
    5: "set",
    6: "set",
    7: "set",
    8: "numeric",
    9: "set",
    10: "qa",
    11: "choice",
}

_ORDER_MARKERS = (
    "chronological order",
    "ascending order",
    "descending order",
    "order them",
    "ordered identifiers",
    "sorted identifiers",
    "sort by",
    "sort the",
    "rank by",
    "arrange them",
    "arrange the",
    "reconstruct the order",
    "排序",
    "顺序",
    "重构",
    "由早到晚",
    "由高到低",
    "由低到高",
    "时间先后",
)

_T10_ORDER_MARKERS = (
    "chronological order",
    "ascending order",
    "descending order",
    "order them",
    "sort by",
    "sort the",
    "arrange them",
    "arrange the",
    "reconstruct the order",
    "排序后的",
    "进行排序",
    "重新排序",
    "重构顺序",
    "由早到晚",
    "由高到低",
    "由低到高",
    "时间先后",
)


def _is_numeric_reference(value: Any) -> bool:
    text = re.sub(r"\s+", "", strip_answer_marker(value)).replace(",", "")
    return bool(
        re.fullmatch(
            r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)%?",
            text,
        )
    )


def _is_choice_reference(value: Any) -> bool:
    text = re.sub(r"\s+", "", strip_answer_marker(value))
    return bool(re.fullmatch(r"[a-j]{1,10}", text, flags=re.IGNORECASE))


def _has_choice_options(question: str) -> bool:
    return bool(
        re.search(
            r"(?:选项|option|choices?)|(?:^|\s)[A-D][.)、:：]",
            question,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    )


def _is_identifier_reference(value: Any) -> bool:
    text = " ".join(strip_answer_marker(value).casefold().split())
    if text in {"y", "n", "no error", "error"}:
        return True
    return bool(
        re.fullmatch(
            r"[a-z0-9][a-z0-9_-]{2,}",
            text,
            flags=re.IGNORECASE,
        )
    )


def metric_name_for_record(record: Mapping[str, Any]) -> str:
    benchmark = str(record.get("benchmark") or "")
    ability = str(record.get("ability") or "")
    question = _nfkc(record.get("question")).casefold()
    references = reference_answers(record)

    match = re.fullmatch(r"LongBench-Pro-T(\d+)", benchmark)
    if match:
        task_id = int(match.group(1))
        if task_id in (1, 6) and any(marker in question for marker in _ORDER_MARKERS):
            return "ranking"
        if task_id != 10:
            if task_id == 8:
                # T8 is mostly numeric, but its packed benchmark also
                # contains identifier, yes/no, and structured judgement
                # protocols.  The expected output shape is the reliable
                # contract for these cases.
                if references and all(_is_numeric_reference(item) for item in references):
                    return "numeric"
                if references and all(_is_identifier_reference(item) for item in references):
                    return "exact"
                return "set" if len(references) > 1 else "qa"
            return _LONG_BENCH_PRO_METRICS.get(task_id, "qa")

        # T10 contains several output protocols. Restrict query heuristics to
        # this mixed task. Other T tasks have a fixed benchmark contract, so
        # ordinary words such as "order" in their question must not override
        # the protocol.
        if any(marker in question for marker in _T10_ORDER_MARKERS):
            return "ranking"
        if references and all(_is_choice_reference(item) for item in references):
            if _has_choice_options(question):
                return "choice"
        if references and all(_is_numeric_reference(item) for item in references):
            return "numeric"
        if len(references) > 1:
            return "set"
        if references and all(_is_identifier_reference(item) for item in references):
            return "exact"
        return "qa"

    if benchmark in _SUMMARY_BENCHMARKS:
        return "summary"
    if benchmark == "HELMET-Cite":
        return "set" if isinstance(reference_payload(record), list) else "qa"
    if benchmark in _RANKING_BENCHMARKS:
        return "ranking"
    if benchmark in _NUMERIC_BENCHMARKS:
        return "numeric"
    if benchmark in _CHOICE_BENCHMARKS:
        return "choice"
    if benchmark in _CITED_ANSWER_BENCHMARKS:
        return "cited_answer"
    if benchmark in _STRICT_EXACT_BENCHMARKS:
        return "exact"
    if benchmark in _SET_BENCHMARKS:
        return "set"
    if benchmark in _QA_BENCHMARKS:
        return "qa"
    return {
        "summarization": "summary",
        "ranking": "ranking",
        "numerical": "numeric",
        "citation": "qa",
    }.get(ability, "qa")


_EVALUATORS: Dict[str, Callable[[Mapping[str, Any], str], EvaluationResult]] = {
    "qa": evaluate_qa,
    "exact": evaluate_strict_exact,
    "numeric": evaluate_numeric,
    "set": evaluate_set,
    "ranking": evaluate_ranking,
    "choice": evaluate_choice,
    "summary": evaluate_summary,
    "cited_answer": evaluate_cited_answer,
}


def evaluate_record(
    record: Mapping[str, Any],
    prediction: Any,
) -> EvaluationResult:
    if is_excluded_evaluation_record(record):
        return EvaluationResult(
            0.0,
            "excluded_case",
            {
                "excluded": True,
                "exclusion_reason": "frames_source_judge_result_no",
            },
        )
    final_answer = extract_prediction(prediction)
    metric_name = metric_name_for_record(record)
    result = _EVALUATORS[metric_name](record, final_answer)
    result.details.update(
        {
            "benchmark": record.get("benchmark"),
            "ability": record.get("ability"),
            "case_id": record.get("id") or record.get("case_id"),
            "selected_evaluator": metric_name,
        }
    )
    return result
