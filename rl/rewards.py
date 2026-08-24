#!/usr/bin/env python3
"""Auditable programmatic rewards for Evidence -> Summary -> Answer."""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from ..eval.evaluators import evaluate_record, rouge_l
    from ..block_input import parse_blocks
except ImportError:
    import sys
    from pathlib import Path

    TRAIN_ROOT = Path(__file__).resolve().parents[1]
    if str(TRAIN_ROOT) not in sys.path:
        sys.path.insert(0, str(TRAIN_ROOT))
    from eval.evaluators import evaluate_record, rouge_l
    from block_input import parse_blocks


Interval = Tuple[int, int]
LOCATOR_SEPARATOR_RE = re.compile(r"\s+(?:…|\.\.\.)\s+")


def final_answer_reward(record: Mapping[str, Any], response: Any) -> float:
    """FinalAnswer score shared with offline evaluation."""
    return evaluate_record(record, response).score


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
    # Current tagged contract: one evidence item per line (the span may
    # continue over multiple lines until the next evidence id).
    tagged_pattern = re.compile(
        r"\[(?P<id>E\d+)\]\s*"
        r"block(?:_id)?\s*=\s*(?P<block>[^:\n]+?)\s*:\s*"
        r"(?P<span>.*?)(?=\n\s*\[E\d+\]\s*block(?:_id)?\s*=|\Z)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in tagged_pattern.finditer(content):
        result.append(
            {
                "id": match.group("id"),
                "block_id": match.group("block").strip(),
                "span": match.group("span").strip(),
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
    citation_only = {
        "missing_summary_citation",
        "duplicate_evidence_id",
    }
    if all(
        error in citation_only or error.startswith("unknown_summary_citation:")
        for error in errors
    ):
        return 0.5, errors
    return 0.0, errors


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


def _strip_wrapping_quotes(value: str) -> str:
    text = str(value or "").strip()
    pairs = (("\u201c", "\u201d"), ("\"", "\""), ("'", "'"))
    for left, right in pairs:
        if text.startswith(left) and text.endswith(right) and len(text) >= 2:
            return text[1:-1].strip()
    return text


def _whitespace_normalized(value: str) -> Tuple[str, List[int], List[int]]:
    """Collapse whitespace while retaining original character boundaries."""
    normalized: List[str] = []
    starts: List[int] = []
    ends: List[int] = []
    pending_space = False
    pending_start = 0
    for index, character in enumerate(_nfkc(value)):
        if character.isspace():
            if normalized and not pending_space:
                pending_space = True
                pending_start = index
            continue
        if pending_space:
            normalized.append(" ")
            starts.append(pending_start)
            ends.append(index)
            pending_space = False
        normalized.append(character)
        starts.append(index)
        ends.append(index + 1)
    return "".join(normalized), starts, ends


def resolve_quote(
    source: str,
    quote: str,
    fuzzy_threshold: float = 0.95,
) -> Optional[Interval]:
    if not source or not quote:
        return None
    quote = _strip_wrapping_quotes(quote)
    exact_matches = [
        match.start() for match in re.finditer(re.escape(quote), source)
    ]
    if exact_matches:
        start = exact_matches[0]
        return start, start + len(quote)

    normalized_source, source_starts, source_ends = _whitespace_normalized(source)
    normalized_quote, _, _ = _whitespace_normalized(quote)
    normalized_matches = [
        match.start()
        for match in re.finditer(re.escape(normalized_quote), normalized_source)
    ]
    if normalized_matches:
        start = normalized_matches[0]
        end = start + len(normalized_quote) - 1
        return source_starts[start], source_ends[end]

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
        start = match.a
        end = match.a + match.size - 1
        return source_starts[start], source_ends[end]
    return None


def _repair_reference_spans(
    references: Sequence[Mapping[str, Any]],
    block_texts: Mapping[str, str],
) -> List[Dict[str, Any]]:
    """Repair stale offsets when a unique stored exact span is available."""
    repaired: List[Dict[str, Any]] = []
    for reference in references:
        item = dict(reference)
        exact_span = str(item.get("exact_span") or "")
        block_id = str(item.get("block_id") or "")
        source = block_texts.get(block_id, "")
        if exact_span and source and source.count(exact_span) == 1:
            start = source.index(exact_span)
            item["start_offset"] = start
            item["end_offset"] = start + len(exact_span)
        repaired.append(item)
    return repaired


def _reference_spans(record: Mapping[str, Any]) -> List[Dict[str, Any]]:
    direct = record.get("reference_spans")
    if isinstance(direct, list):
        return [dict(item) for item in direct if isinstance(item, Mapping)]

    result: List[Dict[str, Any]] = []
    canonical = record.get("canonical")
    if isinstance(canonical, Mapping):
        for claim in canonical.get("claims") or []:
            if not isinstance(claim, Mapping):
                continue
            for support in claim.get("supports") or []:
                if isinstance(support, Mapping):
                    result.append(dict(support))
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
    reference_summaries = _summary_references(record)
    if reference_summaries:
        prediction = _strip_summary_citations(summary)
        score = max(
            (
                rouge_l(prediction, _strip_summary_citations(reference))
                for reference in reference_summaries
            ),
            default=0.0,
        )
        return {
            "score": score,
            "covered_claim_ids": [],
            "claim_scores": {},
            "groups": [],
            "source": "reference_summary_rouge_l",
        }
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
            # Citation-link errors are already reflected by R_format and the
            # separate citation_coverage diagnostic. Do not collapse the
            # entire path score when no reference claim metadata is attached.
            else float(bool(resolved_evidence and str(summary or "").strip()))
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
    reference_spans = _repair_reference_spans(
        _reference_spans(record),
        block_texts,
    )
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
    r_summary_task = float(coverage_details["score"])
    summary_score_source = str(coverage_details["source"])
    r_citation_coverage = citation_coverage(
        parsed["evidence"],
        parsed["summary"],
    )
    r_answer = evaluate_record(record, parsed["answer"]).score
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
            "subquery_coverage": r_summary_task,
            "answer_score": r_answer,
            "path_score": r_path,
        },
        "component_sources": {
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
        "resolved_evidence": resolved,
        "invalid_evidence": invalid_evidence,
        "reference_span_count": len(reference_spans),
    }
