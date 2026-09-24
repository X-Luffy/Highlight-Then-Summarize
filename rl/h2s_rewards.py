#!/usr/bin/env python3
"""Auditable programmatic rewards for Evidence -> Summary -> Answer."""

from __future__ import annotations

import difflib
import json
import math
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

_CANONICAL_EVALUATOR_ROOT = (
    Path(__file__).resolve().parents[2] / "paper_number" / "mainresult" / "evaluator"
)
if _CANONICAL_EVALUATOR_ROOT.is_dir():
    sys.path.insert(0, str(_CANONICAL_EVALUATOR_ROOT))

try:
    # The paper's mainresult evaluator is the canonical FinalAnswer route.
    from eval.h2s_evaluator import evaluate_record, rouge_l
except ImportError:
    from eval.h2s_evaluator import evaluate_record, rouge_l

try:
    from ..block_input import parse_blocks
except ImportError:
    TRAIN_ROOT = Path(__file__).resolve().parents[1]
    if str(TRAIN_ROOT) not in sys.path:
        sys.path.insert(0, str(TRAIN_ROOT))
    from block_input import parse_blocks


Interval = Tuple[int, int]
LOCATOR_SEPARATOR_RE = re.compile(r"\s+(?:…|\.\.\.)\s+")


def final_answer_reward(
    record: Mapping[str, Any],
    response: Any,
    protocol: str = "native",
) -> float:
    """Final-answer score shared with the H2S evaluator."""
    return evaluate_record(record, response, protocol=protocol).score


def parse_response(response: Any) -> Dict[str, Any]:
    if isinstance(response, Mapping):
        parsed = dict(response)
    else:
        text = str(response or "").strip()
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, Mapping):
            parsed = dict(loaded)
        else:
            parsed = {
                "evidence": _parse_evidence_tags(text),
                "summary": _extract_tag(text, "summary"),
                "answer": _extract_tag(text, "answer"),
            }
    evidence = _normalize_evidence(parsed.get("evidence"))

    def response_text(value: Any) -> str:
        if isinstance(value, list):
            return "\n".join(response_text(item) for item in value)
        if isinstance(value, Mapping):
            return json.dumps(value, ensure_ascii=False)
        return str(value or "")

    return {
        "evidence": evidence,
        "summary": response_text(parsed.get("summary")),
        "answer": response_text(parsed.get("answer")),
    }


def _extract_tag(text: str, tag: str) -> str:
    match = re.search(
        rf"<{tag}>\s*(.*?)\s*</{tag}>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _normalize_evidence(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    if not isinstance(value, Mapping):
        return []
    if "evidence" in value:
        return _normalize_evidence(value.get("evidence"))

    result: List[Dict[str, Any]] = []
    for claim, supports in value.items():
        support_rows = supports if isinstance(supports, list) else [supports]
        for support in support_rows:
            if not isinstance(support, Mapping):
                continue
            result.append(
                {
                    "claim": str(claim),
                    **dict(support),
                }
            )
    return result


def _parse_evidence_tags(text: str) -> List[Dict[str, Any]]:
    content = _extract_tag(text, "evidence")
    try:
        loaded = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        loaded = None
    normalized = _normalize_evidence(loaded)
    if normalized:
        return normalized

    result: List[Dict[str, str]] = []
    tagged_pattern = re.compile(
        r"\[(?P<id>E\d+)\]\s*"
        r"block_id\s*=\s*(?P<block>[^:\s;]+)\s*:\s*"
        r"(?P<quote>.*?)"
        r"(?=\s*\[E\d+\]\s*block_id\s*=|\s*$)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in tagged_pattern.finditer(content):
        result.append(
            {
                "id": match.group("id").upper(),
                "block_id": match.group("block").strip(),
                "quote": match.group("quote").strip(),
            }
        )
    if result:
        return result

    pattern = re.compile(
        r"\[(?P<id>E\d+)\]\s*"
        r"block(?:_id)?\s*=\s*(?P<block>[^;\n]+?)\s*;\s*"
        r"(?:quote|head)\s*=\s*[\"“](?P<head>.*?)[\"”]"
        r"(?:\s*;\s*tail\s*=\s*[\"“](?P<tail>.*?)[\"”])?"
        r"\s*(?:\n|$)",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(content):
        item = {
            "id": match.group("id"),
            "block_id": match.group("block").strip(),
            "head": match.group("head").strip(),
        }
        if match.group("tail"):
            item["tail"] = match.group("tail").strip()
        result.append(item)
    return result


def format_score(parsed: Mapping[str, Any]) -> Tuple[float, List[str]]:
    errors: List[str] = []
    evidence = parsed.get("evidence")
    summary = str(parsed.get("summary") or "").strip()
    answer = str(parsed.get("answer") or "").strip()
    if not isinstance(evidence, list):
        errors.append("evidence_not_list")
        evidence = []
    if not evidence:
        errors.append("missing_evidence")
    if not summary:
        errors.append("missing_summary")
    if not answer:
        errors.append("missing_answer")

    ids = []
    claim_schema = False
    for item in evidence:
        evidence_id = str(item.get("id") or "").strip()
        claim = str(item.get("claim") or "").strip()
        if claim:
            claim_schema = True
        elif not evidence_id:
            errors.append("missing_claim_or_evidence_id")
        if evidence_id:
            ids.append(evidence_id)
        if not str(item.get("block_id") or "").strip():
            errors.append("missing_block_id")
        if not (
            str(item.get("span") or "").strip()
            or
            str(item.get("quote") or "").strip()
            or str(item.get("head") or "").strip()
            or str(item.get("tail") or "").strip()
        ):
            errors.append("missing_evidence_fragment")
    if ids and len(ids) != len(set(ids)):
        errors.append("duplicate_evidence_id")
    citations = re.findall(r"\[(E\d+)\]", summary)
    if evidence and ids and not claim_schema and not citations:
        errors.append("missing_summary_citation")
    for citation in citations:
        if citation not in ids:
            errors.append(f"unknown_summary_citation:{citation}")
    if not errors:
        return 1.0, errors
    critical_errors = {
        "evidence_not_list",
        "missing_evidence",
        "missing_summary",
        "missing_answer",
    }
    return (0.0 if critical_errors.intersection(errors) else 0.5), errors


def _block_texts(record: Mapping[str, Any]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    block_store = record.get("block_store")
    if isinstance(block_store, Mapping):
        result.update({str(key): str(value) for key, value in block_store.items()})
    for key in ("blocks", "retrieved_blocks", "selected_blocks"):
        blocks = record.get(key)
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, Mapping):
                continue
            block_id = block.get("block_id") or block.get("id")
            text = block.get("source_text") or block.get("text")
            if block_id not in (None, "") and text is not None:
                result[str(block_id)] = str(text)
    if not result:
        for key in ("prompt", "input", "messages"):
            rendered = record.get(key)
            if rendered not in (None, "", [], {}):
                result.update(parse_blocks(rendered))
                if result:
                    break
    return result


def _nfkc(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


def resolve_quote(
    source: str,
    quote: str,
    fuzzy_threshold: float = 0.95,
) -> Optional[Interval]:
    if not source or not quote:
        return None
    text = quote.strip()
    candidates = [text]
    for left, right in (("\"", "\""), ("'", "'"), ("“", "”"), ("‘", "’")):
        if len(text) >= 2 and text.startswith(left) and text.endswith(right):
            candidates.append(text[len(left) : -len(right)].strip())

    for candidate in dict.fromkeys(candidates):
        exact_matches = [
            match.start() for match in re.finditer(re.escape(candidate), source)
        ]
        if exact_matches:
            start = exact_matches[0]
            return start, start + len(candidate)

        parts = [re.escape(part) for part in re.split(r"\s+", candidate) if part]
        if len(parts) > 1:
            whitespace_match = re.search(r"\s+".join(parts), source)
            if whitespace_match:
                return whitespace_match.start(), whitespace_match.end()

    normalized_source = _nfkc(source)
    normalized_candidates = list(dict.fromkeys(_nfkc(candidate) for candidate in candidates))
    for normalized_quote in normalized_candidates:
        normalized_matches = [
            match.start()
            for match in re.finditer(
                re.escape(normalized_quote),
                normalized_source,
            )
        ]
        if normalized_matches:
            start = normalized_matches[0]
            return start, start + len(normalized_quote)

    normalized_quote = normalized_candidates[-1]
    match = difflib.SequenceMatcher(
        None,
        normalized_source,
        normalized_quote,
        autojunk=False,
    ).find_longest_match(
        0,
        len(normalized_source),
        0,
        len(normalized_quote),
    )
    similarity = match.size / max(1, len(normalized_quote))
    if similarity >= fuzzy_threshold:
        return match.a, match.a + match.size
    return None


def _reference_spans(
    record: Mapping[str, Any],
    block_texts: Optional[Mapping[str, str]] = None,
) -> List[Dict[str, Any]]:
    direct = record.get("reference_spans")
    if isinstance(direct, list):
        result = [dict(item) for item in direct if isinstance(item, Mapping)]
    else:
        result = []
        canonical = record.get("canonical")
        if isinstance(canonical, Mapping):
            for claim in canonical.get("claims") or []:
                if not isinstance(claim, Mapping):
                    continue
                for support in claim.get("supports") or []:
                    if isinstance(support, Mapping):
                        result.append(dict(support))

    if block_texts:
        for span in result:
            block_id = str(span.get("block_id") or "")
            source = block_texts.get(block_id)
            exact = span.get("exact_span")
            if source is None or exact in (None, ""):
                continue
            exact_text = str(exact)
            try:
                start = int(span.get("start_offset"))
                end = int(span.get("end_offset"))
            except (TypeError, ValueError):
                start = end = -1
            if 0 <= start < end <= len(source) and source[start:end] == exact_text:
                continue
            repaired_start = source.find(exact_text)
            if repaired_start >= 0 and source.find(exact_text, repaired_start + 1) < 0:
                span["start_offset"] = repaired_start
                span["end_offset"] = repaired_start + len(exact_text)
    return result


def merge_intervals(intervals: Iterable[Interval]) -> List[Interval]:
    ordered = sorted(
        (int(start), int(end))
        for start, end in intervals
        if int(end) > int(start)
    )
    merged: List[Interval] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _interval_length(intervals: Sequence[Interval]) -> int:
    return sum(end - start for start, end in intervals)


def _intersection_length(
    left: Sequence[Interval], right: Sequence[Interval]
) -> int:
    left_index = 0
    right_index = 0
    overlap = 0
    while left_index < len(left) and right_index < len(right):
        start = max(left[left_index][0], right[right_index][0])
        end = min(left[left_index][1], right[right_index][1])
        if end > start:
            overlap += end - start
        if left[left_index][1] <= right[right_index][1]:
            left_index += 1
        else:
            right_index += 1
    return overlap


def span_f1(
    generated: Sequence[Mapping[str, Any]],
    references: Sequence[Mapping[str, Any]],
) -> Dict[str, float]:
    generated_by_block: Dict[str, List[Interval]] = defaultdict(list)
    reference_by_block: Dict[str, List[Interval]] = defaultdict(list)
    for item in generated:
        intervals = item.get("intervals")
        if isinstance(intervals, list) and intervals:
            generated_by_block[str(item["block_id"])].extend(
                (int(pair[0]), int(pair[1]))
                for pair in intervals
                if isinstance(pair, (list, tuple)) and len(pair) == 2
            )
        else:
            generated_by_block[str(item["block_id"])].append(
                (int(item["start_offset"]), int(item["end_offset"]))
            )
    for item in references:
        block_id = item.get("block_id")
        start = item.get("start_offset")
        end = item.get("end_offset")
        if block_id in (None, "") or start is None or end is None:
            continue
        reference_by_block[str(block_id)].append((int(start), int(end)))

    generated_length = 0
    reference_length = 0
    overlap = 0
    for block_id in set(generated_by_block) | set(reference_by_block):
        generated_intervals = merge_intervals(generated_by_block[block_id])
        reference_intervals = merge_intervals(reference_by_block[block_id])
        generated_length += _interval_length(generated_intervals)
        reference_length += _interval_length(reference_intervals)
        overlap += _intersection_length(generated_intervals, reference_intervals)
    precision = overlap / generated_length if generated_length else 0.0
    recall = overlap / reference_length if reference_length else 0.0
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "generated_chars": float(generated_length),
        "reference_chars": float(reference_length),
        "overlap_chars": float(overlap),
    }


def locator_matches_reference(
    intervals: Sequence[Interval],
    ref_start: int,
    ref_end: int,
) -> bool:
    normalized = sorted(
        (int(start), int(end))
        for start, end in intervals
        if int(end) > int(start)
    )
    if not normalized:
        return False
    if len(normalized) == 1:
        start, end = normalized[0]
        overlap = max(0, min(end, ref_end) - max(start, ref_start))
        reference_length = max(1, ref_end - ref_start)
        return (
            start <= ref_start and end >= ref_end
        ) or (
            overlap >= min(8, reference_length)
            and overlap / reference_length >= 0.5
        )
    first_start, first_end = normalized[0]
    last_start, last_end = normalized[-1]
    return (
        first_end >= ref_start
        and last_start <= ref_end
        and max(0, min(first_end, ref_end) - max(first_start, ref_start)) > 0
        and max(0, min(last_end, ref_end) - max(last_start, ref_start)) > 0
    )


def fragment_span_f1(
    generated: Sequence[Mapping[str, Any]],
    references: Sequence[Mapping[str, Any]],
) -> Dict[str, float]:
    """Match bounded head/tail fragments to their containing reference span."""
    reference_rows = [
        (
            str(item.get("block_id") or ""),
            int(item.get("start_offset") or 0),
            int(item.get("end_offset") or 0),
        )
        for item in references
        if item.get("block_id") not in (None, "")
        and item.get("start_offset") is not None
        and item.get("end_offset") is not None
    ]
    # Canonical references can contain nested spans, such as a complete
    # clause and a shorter phrase inside that clause. A first-candidate
    # greedy match lets multiple valid evidence items consume the same broad
    # reference and artificially lowers recall. Build a bipartite graph and
    # find a maximum one-to-one matching instead.
    candidate_map: List[List[int]] = []
    for item in generated:
        block_id = str(item.get("block_id") or "")
        intervals = item.get("intervals")
        if not isinstance(intervals, list) or not intervals:
            intervals = [
                (
                    int(item.get("start_offset") or 0),
                    int(item.get("end_offset") or 0),
                )
            ]
        normalized = [
            (int(pair[0]), int(pair[1]))
            for pair in intervals
            if isinstance(pair, (list, tuple)) and len(pair) == 2
        ]
        normalized.sort()
        candidates = []
        for index, (ref_block, ref_start, ref_end) in enumerate(
            reference_rows
        ):
            if ref_block != block_id or not normalized:
                continue
            # Context may extend outside the canonical span; the locator
            # helper ensures it still covers or brackets the reference.
            matches = locator_matches_reference(
                normalized,
                ref_start,
                ref_end,
            )
            if matches:
                candidates.append(index)
        candidates.sort(
            key=lambda index: (
                reference_rows[index][2] - reference_rows[index][1],
                index,
            )
        )
        candidate_map.append(candidates)

    # Kuhn-style augmenting paths maximize the number of matched generated
    # items while retaining deterministic shortest-span preference.
    reference_to_generated: Dict[int, int] = {}

    def augment(generated_index: int, seen: set[int]) -> bool:
        for reference_index in candidate_map[generated_index]:
            if reference_index in seen:
                continue
            seen.add(reference_index)
            previous = reference_to_generated.get(reference_index)
            if previous is None or augment(previous, seen):
                reference_to_generated[reference_index] = generated_index
                return True
        return False

    for generated_index in range(len(candidate_map)):
        augment(generated_index, set())

    matched_generated = len(reference_to_generated)
    matched_references = set(reference_to_generated)
    precision = matched_generated / len(generated) if generated else 0.0
    recall = (
        len(matched_references) / len(reference_rows)
        if reference_rows
        else 0.0
    )
    f1 = (
        0.0
        if precision + recall == 0
        else 2 * precision * recall / (precision + recall)
    )
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "generated_chars": float(
            sum(
                end - start
                for item in generated
                for start, end in item.get("intervals") or []
            )
        ),
        "reference_chars": float(
            sum(end - start for _, start, end in reference_rows)
        ),
        "overlap_chars": float(matched_generated),
    }


def deduplicate_resolved(
    resolved: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    unique: Dict[Tuple[str, Tuple[Interval, ...]], Dict[str, Any]] = {}
    for item in resolved:
        intervals = tuple(
            (int(pair[0]), int(pair[1]))
            for pair in item.get("intervals") or []
            if isinstance(pair, (list, tuple)) and len(pair) == 2
        )
        key = (str(item.get("block_id") or ""), intervals)
        unique.setdefault(key, dict(item))
    return list(unique.values())


def _harmonic_mean(values: Sequence[float]) -> float:
    if not values or any(value <= 0 for value in values):
        return 0.0
    return len(values) / sum(1.0 / value for value in values)


def _summary_references(record: Mapping[str, Any]) -> List[str]:
    value = record.get("reference_summary")
    if value in (None, "", [], {}):
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _strip_summary_citations(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\[(?:E\d+|clm_\d+)\]", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def summary_task_score(
    record: Mapping[str, Any],
    summary: str,
) -> Tuple[float, str]:
    references = _summary_references(record)
    if references:
        prediction = _strip_summary_citations(summary)
        score = max(
            (
                rouge_l(prediction, _strip_summary_citations(reference))
                for reference in references
            ),
            default=0.0,
        )
        return score, "reference_summary_rouge_l"
    return (
        evaluate_record(record, summary, protocol="native").score,
        "h2s_evaluator_native_final_answer_metric_fallback",
    )


def summary_citation_ids(summary: Any) -> set[str]:
    return set(re.findall(r"\[(E\d+)\]", str(summary or ""), flags=re.I))


def citation_coverage(
    evidence: Sequence[Mapping[str, Any]],
    summary: Any,
) -> float:
    evidence_ids = {
        str(item.get("id") or "")
        for item in evidence
        if str(item.get("id") or "")
    }
    if not evidence_ids:
        return 1.0
    return len(evidence_ids & summary_citation_ids(summary)) / len(evidence_ids)


def _overlap_ratio(
    generated: Sequence[Interval],
    reference: Interval,
) -> float:
    reference_length = max(0, reference[1] - reference[0])
    if reference_length <= 0:
        return 0.0
    overlap = _intersection_length(
        merge_intervals(generated),
        [reference],
    )
    return overlap / reference_length


def subquery_coverage(
    record: Mapping[str, Any],
    resolved_evidence: Sequence[Mapping[str, Any]],
    summary: Any,
    claim_overlap_threshold: float = 0.5,
) -> Dict[str, Any]:
    cited_ids = summary_citation_ids(summary)
    has_legacy_ids = any(
        str(item.get("id") or "")
        for item in resolved_evidence
    )
    intervals_by_block: Dict[str, List[Interval]] = defaultdict(list)
    bounded_by_block: Dict[str, List[List[Interval]]] = defaultdict(list)
    for item in resolved_evidence:
        if (
            has_legacy_ids
            and cited_ids
            and str(item.get("id") or "") not in cited_ids
        ):
            continue
        item_intervals = item.get("intervals")
        if isinstance(item_intervals, list) and item_intervals:
            normalized = [
                (int(pair[0]), int(pair[1]))
                for pair in item_intervals
                if isinstance(pair, (list, tuple)) and len(pair) == 2
            ]
            intervals_by_block[str(item.get("block_id") or "")].extend(normalized)
            if item.get("bounded_fragments"):
                bounded_by_block[str(item.get("block_id") or "")].append(
                    normalized
                )
        else:
            intervals_by_block[str(item.get("block_id") or "")].append(
                (
                    int(item.get("start_offset") or 0),
                    int(item.get("end_offset") or 0),
                )
            )

    covered_claims: set[str] = set()
    claim_scores: Dict[str, float] = {}
    for claim in record.get("reference_claims") or []:
        if not isinstance(claim, Mapping):
            continue
        claim_id = str(claim.get("claim_id") or "")
        support_scores = []
        for support in claim.get("supports") or []:
            if not isinstance(support, Mapping):
                continue
            block_id = str(support.get("block_id") or "")
            start = support.get("start_offset")
            end = support.get("end_offset")
            if start is None or end is None:
                continue
            bounded_match = any(
                locator_matches_reference(
                    intervals,
                    int(start),
                    int(end),
                )
                for intervals in bounded_by_block.get(block_id, [])
            )
            support_scores.append(
                1.0
                if bounded_match
                else _overlap_ratio(
                    intervals_by_block.get(block_id, []),
                    (int(start), int(end)),
                )
            )
        score = max(support_scores, default=0.0)
        claim_scores[claim_id] = score
        if score >= claim_overlap_threshold:
            covered_claims.add(claim_id)

    group_scores = []
    group_details = []
    for subquery in record.get("reference_subqueries") or []:
        if not isinstance(subquery, Mapping):
            continue
        claim_ids = [
            str(item)
            for item in subquery.get("claim_ids") or []
            if str(item)
        ]
        if not claim_ids:
            continue
        score = sum(item in covered_claims for item in claim_ids) / len(claim_ids)
        group_scores.append(score)
        group_details.append(
            {
                "subquery_id": subquery.get("subquery_id"),
                "claim_count": len(claim_ids),
                "covered_claim_count": sum(
                    item in covered_claims for item in claim_ids
                ),
                "score": score,
            }
        )
    return {
        "score": (
            sum(group_scores) / len(group_scores)
            if group_scores
            else citation_coverage(resolved_evidence, summary)
        ),
        "covered_claim_ids": sorted(covered_claims),
        "claim_scores": claim_scores,
        "groups": group_details,
        "source": (
            "reference_subquery_claim_support_coverage"
            if group_scores
            else "generated_evidence_citation_coverage"
        ),
    }


def compute_programmatic_reward(
    record: Mapping[str, Any],
    response: Any,
    fuzzy_threshold: float = 0.95,
) -> Dict[str, Any]:
    parsed = parse_response(response)
    r_format, format_errors = format_score(parsed)
    block_texts = _block_texts(record)

    resolved: List[Dict[str, Any]] = []
    invalid_evidence: List[Dict[str, Any]] = []
    for item in parsed["evidence"]:
        block_id = str(item.get("block_id") or "")
        source = block_texts.get(block_id, "")
        fragments = []
        span_locator = str(item.get("span") or "").strip()
        if span_locator:
            locator_parts = [
                part.strip()
                for part in LOCATOR_SEPARATOR_RE.split(
                    span_locator,
                    maxsplit=1,
                )
                if part.strip()
            ]
            fragments.extend(
                (f"locator_{index}", part)
                for index, part in enumerate(locator_parts)
            )
        elif str(item.get("quote") or "").strip():
            fragments.append(("quote", str(item.get("quote") or "")))
        else:
            for name in ("head", "tail"):
                fragment = str(item.get(name) or "").strip()
                if fragment:
                    fragments.append((name, fragment))
        intervals = []
        fragment_errors = []
        for name, fragment in fragments:
            if name.startswith("locator_") and source.count(fragment) > 1:
                fragment_errors.append(f"{name}_not_unique")
                continue
            interval = resolve_quote(
                source,
                fragment,
                fuzzy_threshold=fuzzy_threshold,
            )
            if interval is None:
                fragment_errors.append(name)
            else:
                intervals.append(interval)
        if len(intervals) > 1 and any(
            intervals[index][0] > intervals[index + 1][0]
            for index in range(len(intervals) - 1)
        ):
            fragment_errors.append("locator_order")
        if not fragments or fragment_errors:
            invalid_evidence.append(dict(item))
            continue
        resolved.append(
            {
                **dict(item),
                "block_id": block_id,
                "start_offset": intervals[0][0],
                "end_offset": intervals[0][1],
                "intervals": intervals,
                "bounded_fragments": len(intervals) > 1
                or (
                    not bool(str(item.get("quote") or "").strip())
                    and not span_locator
                ),
            }
        )

    evidence_count = len(parsed["evidence"])
    r_valid = len(resolved) / evidence_count if evidence_count else 0.0
    reference_spans = _reference_spans(record, block_texts)
    cited_ids = summary_citation_ids(parsed["summary"])
    has_legacy_ids = any(str(item.get("id") or "") for item in resolved)
    cited_resolved = (
        [
            item
            for item in resolved
            if str(item.get("id") or "") in cited_ids
        ]
        if has_legacy_ids and cited_ids
        else resolved
    )
    new_locator_schema = any(
        str(item.get("claim") or "").strip()
        and str(item.get("span") or "").strip()
        for item in parsed["evidence"]
    )
    bounded = new_locator_schema or any(
        item.get("bounded_fragments") for item in cited_resolved
    )
    span_inputs = (
        deduplicate_resolved(cited_resolved)
        if new_locator_schema
        else cited_resolved
    )
    span_scores = (
        fragment_span_f1(span_inputs, reference_spans)
        if bounded
        else span_f1(span_inputs, reference_spans)
    )
    r_span = span_scores["f1"]
    coverage_details = subquery_coverage(
        record,
        resolved,
        parsed["summary"],
    )
    r_summary_task, summary_score_source = summary_task_score(
        record,
        parsed["summary"],
    )
    r_citation_coverage = citation_coverage(
        parsed["evidence"],
        parsed["summary"],
    )
    # Actual GRPO completions are tagged text.  Score the complete response so
    # missing, unclosed, or duplicate <answer> tags are rejected exactly as in
    # the offline H2S evaluation report. Mapping responses are retained only as
    # a convenient structured API for tests/tools and are already parsed, so
    # their answer field uses V2's native normalization.
    answer_protocol = "native" if isinstance(response, Mapping) else "tagged"
    answer_input = parsed["answer"] if answer_protocol == "native" else response
    answer_evaluation = evaluate_record(
        record,
        answer_input,
        protocol=answer_protocol,
    )
    r_answer = answer_evaluation.score
    r_path = _harmonic_mean([r_valid, r_span, r_summary_task])
    total = r_format * (0.60 * r_answer + 0.40 * r_path)

    return {
        "reward": total,
        "components": {
            "format": r_format,
            "evidence_validity": r_valid,
            "span_f1": r_span,
            "summary_task_score": r_summary_task,
            "citation_coverage": r_citation_coverage,
            "subquery_coverage": float(coverage_details["score"]),
            "answer_score": r_answer,
            "path_score": r_path,
        },
        "component_sources": {
            "answer_score": f"h2s_evaluator_{answer_protocol}",
            "summary_task_score": summary_score_source,
            "span_f1": (
                "bounded_fragment_reference_match"
                if bounded
                else "character_interval_overlap"
            ),
        },
        "span_details": span_scores,
        "coverage_details": coverage_details,
        "format_errors": format_errors,
        "answer_evaluation": answer_evaluation.to_dict(),
        "resolved_evidence": resolved,
        "invalid_evidence": invalid_evidence,
        "reference_span_count": len(reference_spans),
    }


# VNext deliberately keeps the V3 parser and evidence resolvers above.  The
# change is only in aggregation and in response-shape diagnostics, so old
# reward logs remain directly comparable with new logs.
def _response_raw_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, Mapping):
        return json.dumps(response, ensure_ascii=False, sort_keys=True)
    return str(response or "")


def _estimate_completion_tokens(text: str) -> int:
    """Conservative token estimate without loading a tokenizer in every rank."""
    cjk = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text))
    non_space = len(re.findall(r"\S", text))
    return max(1, int(round(cjk + max(0, non_space - cjk) / 4.0)))


def _repeat_stats(text: str) -> Dict[str, float]:
    tokens = re.findall(r"[\w]+|[\u3400-\u9fff]", text.casefold())
    ngram_repeat = 0.0
    for size in (3, 4):
        if len(tokens) < size:
            continue
        counts: Dict[Tuple[str, ...], int] = defaultdict(int)
        for index in range(len(tokens) - size + 1):
            counts[tuple(tokens[index : index + size])] += 1
        total = sum(counts.values())
        repeated = sum(max(0, count - 1) for count in counts.values())
        ngram_repeat = max(ngram_repeat, repeated / max(1, total))

    lines = [
        " ".join(line.split()).casefold()
        for line in text.splitlines()
        if len(line.strip()) >= 16
    ]
    line_counts: Dict[str, int] = defaultdict(int)
    for line in lines:
        line_counts[line] += 1
    duplicate_lines = sum(max(0, count - 1) for count in line_counts.values())
    line_repeat = duplicate_lines / max(1, len(lines))
    return {
        "ngram_repeat_ratio": ngram_repeat,
        "line_repeat_ratio": line_repeat,
    }


def _enumeration_excess(text: str) -> float:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 6:
        return 0.0
    bullet = [
        line
        for line in lines
        if re.match(r"^(?:[-*+•]|\d+[.)]|[A-Z][.)])\s+", line)
    ]
    if len(bullet) < 6:
        return 0.0
    density = len(bullet) / len(lines)
    # A real answer may be a list.  Penalize only dense, long lists in the
    # narrative portion, where mechanical enumeration is usually a failure.
    return min(1.0, max(0.0, (density - 0.65) / 0.35) * min(1.0, len(lines) / 12.0))


def _response_shape_stats(response: Any, parsed: Mapping[str, Any]) -> Dict[str, float]:
    raw = _response_raw_text(response)
    if not isinstance(response, str):
        return {
            "trailing_after_answer_chars": 0.0,
            "duplicate_section_count": 0.0,
            "stop_score": 1.0,
        }
    answer_close = list(re.finditer(r"</answer>\s*", raw, flags=re.IGNORECASE))
    trailing = len(raw[answer_close[-1].end() :].strip()) if answer_close else 0
    section_counts = [
        len(re.findall(rf"<{tag}>", raw, flags=re.IGNORECASE))
        for tag in ("evidence", "summary", "answer")
    ]
    duplicate_sections = sum(max(0, count - 1) for count in section_counts)
    stop_score = math.exp(-min(6.0, trailing / 128.0 + duplicate_sections * 0.75))
    return {
        "trailing_after_answer_chars": float(trailing),
        "duplicate_section_count": float(duplicate_sections),
        "stop_score": stop_score,
    }


def compute_programmatic_reward_vnext(
    record: Mapping[str, Any],
    response: Any,
    fuzzy_threshold: float = 0.95,
    target_completion_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """VNext reward with explicit anti-length and anti-repetition costs.

    ``target_completion_tokens`` is normally supplied by the phase launcher via
    ``RL_VNEXT_TARGET_TOKENS``.  It is a soft target, not a hard truncation
    rule; phase 1/2/3 can therefore use 2K/4K/8K without changing the data
    contract.
    """
    base = compute_programmatic_reward(record, response, fuzzy_threshold)
    parsed = parse_response(response)
    raw = _response_raw_text(response)
    metadata = record.get("reward_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    target_value = (
        target_completion_tokens
        or metadata.get("reward_target_completion_tokens")
        or record.get("reward_target_completion_tokens")
        or os.environ.get("RL_VNEXT_TARGET_TOKENS", "2048")
    )
    try:
        target = max(256, int(target_value))
    except (TypeError, ValueError):
        target = 2048

    completion_tokens = _estimate_completion_tokens(raw)
    excess = max(0.0, (completion_tokens - target) / target)
    # One smooth budget cost is enough.  It is deliberately mild: the model
    # can spend extra tokens when they improve answer/evidence quality, but
    # unbounded generation no longer has a neutral cost.
    length_score = math.exp(-0.35 * excess)

    # Style checks focus on the model-authored narrative.  Evidence quotes are
    # still covered by the length budget, but repeated source text alone is not
    # treated as a hallucination.
    narrative = f"{parsed.get('summary', '')}\n{parsed.get('answer', '')}"
    repeat = _repeat_stats(narrative)
    repeat_excess = min(
        1.0,
        max(0.0, (repeat["ngram_repeat_ratio"] - 0.08) / 0.32)
        + max(0.0, (repeat["line_repeat_ratio"] - 0.05) / 0.30),
    )
    list_excess = _enumeration_excess(str(parsed.get("summary") or ""))
    shape = _response_shape_stats(response, parsed)

    r_answer = float(base["components"]["answer_score"])
    r_valid = float(base["components"]["evidence_validity"])
    r_span = float(base["components"]["span_f1"])
    r_summary = float(base["components"]["summary_task_score"])
    grounding = 0.60 * r_valid + 0.40 * r_span
    core = 0.65 * r_answer + 0.25 * grounding + 0.10 * r_summary

    # Missing core sections remain a hard failure.  A citation-link mistake is
    # a light gate, preserving ranking information inside a GRPO group.
    format_score_value = float(base["components"]["format"])
    format_gate = 0.0 if format_score_value <= 0 else 0.75 + 0.25 * format_score_value
    # Repetition, list density, and trailing text are logged for diagnosis but
    # intentionally do not create separate targeted penalties.  The only new
    # behavioral cost in VNext is the smooth completion-length factor.
    style_factor = length_score
    total = format_gate * core * length_score

    components = dict(base["components"])
    components.update(
        {
            "grounding_score": grounding,
            "length_score": length_score,
            "length_penalty": 1.0 - length_score,
            "style_factor": style_factor,
            "completion_tokens_est": float(completion_tokens),
            "target_completion_tokens": float(target),
        }
    )
    result = dict(base)
    result["reward"] = total
    result["schema_version"] = "vnext_anti_length_repeat_v1"
    result["components"] = components
    result["component_sources"] = {
        **base.get("component_sources", {}),
        "grounding_score": "weighted_evidence_validity_span_f1",
        "length_score": "soft_target_completion_budget",
        "length_penalty": "smooth_soft_target_completion_budget",
    }
    result["style_details"] = {
        **repeat,
        **shape,
        "enumeration_excess": list_excess,
        "completion_tokens_est": completion_tokens,
        "target_completion_tokens": target,
    }
    return result
