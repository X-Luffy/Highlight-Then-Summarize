#!/usr/bin/env python3
"""Canonical block-aware input rendering shared by SFT and RL.

Stage-2 is the only source of block IDs.  Legacy helpers can render explicit
closed boundaries, while the V1 data path uses open-only markers; both forms
are parsed here so training and reward code share the same block boundaries.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


RENDERER_VERSION = "stage2_closed_block_v1"
BLOCK_START = "[BLOCK_ID: {block_id}]"
BLOCK_END = "[/BLOCK_ID: {block_id}]"
OLD_BLOCK_RE = re.compile(r"\[Block ID:\s*([A-Za-z0-9_-]+)\]")
BLOCK_RE = re.compile(
    r"\[BLOCK_ID:\s*([A-Za-z0-9_-]+)\]\n"
    r"(.*?)\n\[/BLOCK_ID:\s*\1\]",
    flags=re.DOTALL,
)
OPEN_BLOCK_RE = re.compile(
    r"\[BLOCK_ID:\s*([A-Za-z0-9_-]+)\]\s*\n",
    flags=re.IGNORECASE,
)

OUTPUT_PROTOCOL = """\
[TRAINING_OUTPUT_PROTOCOL]
Use only the block-tagged context above. Return exactly these three sections:
<evidence>
[{"id":"E0001","block_id":"the cited block ID","quote":"a verbatim substring from that block"}]
</evidence>
<summary>
A query-aware summary. Every factual statement must cite one or more evidence IDs such as [E0001].
</summary>
<answer>
The final answer in the format requested by the original question.
</answer>
Do not output text outside these three sections."""


def json_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            yield value


def load_stage2_rendered(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in json_rows(path):
        identifier = str(row.get("case_id") or row.get("id") or "")
        rendered = str(row.get("rendered_input") or "")
        if not identifier or not rendered:
            raise ValueError(f"invalid Stage-2 rendered row in {path}")
        if identifier in result:
            raise ValueError(f"duplicate Stage-2 rendered case: {identifier}")
        result[identifier] = rendered
    return result


def load_stage2_blocks(path: Path) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in json_rows(path):
        identifier = str(row.get("case_id") or "")
        block_id = str(row.get("block_id") or "")
        source = str(row.get("source_text") or "")
        if not identifier or not block_id or not source:
            raise ValueError(f"invalid Stage-2 block row in {path}")
        grouped[identifier].append(dict(row))
    for identifier, blocks in grouped.items():
        blocks.sort(
            key=lambda row: (
                int(row.get("document_ordinal") or 0),
                int(row.get("char_start") or 0),
                str(row.get("block_id") or ""),
            )
        )
        ids = [str(row["block_id"]) for row in blocks]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate block ID within case {identifier}")
    return dict(grouped)


def close_stage2_blocks(
    rendered_input: str,
    blocks: list[Mapping[str, Any]],
) -> str:
    """Replace Stage-2 open markers with explicit start/end block boundaries."""
    text = str(rendered_input or "")
    marker_positions: dict[str, int] = {}
    for block in blocks:
        block_id = str(block.get("block_id") or "")
        matches = list(
            re.finditer(
                rf"\[Block ID:\s*{re.escape(block_id)}\]",
                text,
            )
        )
        if len(matches) != 1:
            raise ValueError(
                f"expected one Stage-2 marker for block {block_id}, "
                f"found {len(matches)}"
            )
        marker_positions[block_id] = matches[0].start()
    ordered_blocks = sorted(
        blocks,
        key=lambda block: marker_positions[str(block.get("block_id") or "")],
    )
    output: list[str] = []
    cursor = 0
    seen: set[str] = set()
    for block in ordered_blocks:
        block_id = str(block.get("block_id") or "")
        source = str(block.get("source_text") or "")
        marker_match = re.search(
            rf"\[Block ID:\s*{re.escape(block_id)}\]",
            text[cursor:],
        )
        if marker_match is None:
            raise ValueError(f"Stage-2 marker missing for block {block_id}")
        marker_start = cursor + marker_match.start()
        marker_end = cursor + marker_match.end()
        source_start = text.find(source, marker_end)
        if source_start < 0:
            raise ValueError(f"Stage-2 source missing after marker {block_id}")
        between = text[marker_end:source_start]
        if between.strip():
            raise ValueError(
                f"unexpected content between marker and source for {block_id}"
            )
        source_end = source_start + len(source)
        output.append(text[cursor:marker_start])
        output.append(BLOCK_START.format(block_id=block_id))
        output.append("\n")
        output.append(source)
        output.append("\n")
        output.append(BLOCK_END.format(block_id=block_id))
        cursor = source_end
        seen.add(block_id)
    output.append(text[cursor:])
    rendered = "".join(output).strip()
    expected = {str(block.get("block_id") or "") for block in blocks}
    parsed = parse_blocks(rendered)
    if set(parsed) != expected or seen != expected:
        raise ValueError("closed block rendering lost or added block IDs")
    return rendered


def render_training_prompt(
    rendered_input: str,
    blocks: list[Mapping[str, Any]],
    include_output_protocol: bool = False,
) -> list[dict[str, str]]:
    content = close_stage2_blocks(rendered_input, blocks)
    if include_output_protocol:
        content = content.rstrip() + "\n\n" + OUTPUT_PROTOCOL
    return [{"role": "user", "content": content}]


def parse_blocks(value: Any) -> dict[str, str]:
    if isinstance(value, list):
        text = "\n".join(
            str(item.get("content") or "")
            for item in value
            if isinstance(item, Mapping)
        )
    elif isinstance(value, Mapping):
        text = str(value.get("content") or value.get("text") or "")
    else:
        text = str(value or "")
    result: dict[str, str] = {}
    for match in BLOCK_RE.finditer(text):
        block_id = match.group(1)
        if block_id in result:
            raise ValueError(f"duplicate rendered block ID: {block_id}")
        result[block_id] = match.group(2)
    if result:
        return result

    # V1 intentionally omits closing block tags. Each block ends immediately
    # before the next block marker or the end of the document.
    matches = list(OPEN_BLOCK_RE.finditer(text))
    for index, match in enumerate(matches):
        block_id = match.group(1)
        if block_id in result:
            raise ValueError(f"duplicate rendered block ID: {block_id}")
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block_text = text[start:end].strip()
        result[block_id] = block_text
    return result


def count_prompt_tokens(tokenizer: Any, messages: list[Mapping[str, Any]]) -> int:
    if tokenizer is None:
        text = "\n".join(str(item.get("content") or "") for item in messages)
        return max(1, (len(text) + 3) // 4) if text else 0
    if hasattr(tokenizer, "apply_chat_template"):
        rendered = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
        )
        # Fast tokenizers may return a BatchEncoding rather than a plain
        # input-id list. Counting the mapping keys would return 2.
        if isinstance(rendered, Mapping):
            input_ids = rendered.get("input_ids", [])
            if input_ids and isinstance(input_ids[0], list):
                return len(input_ids[0])
            return len(input_ids)
        return len(rendered)
    text = "\n".join(str(item.get("content") or "") for item in messages)
    return len(tokenizer.encode(text, add_special_tokens=False))
