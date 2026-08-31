"""Small parser for the block-id document format used by V3 rewards."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Dict


_BLOCK_OPEN_RE = re.compile(
    r"\[BLOCK_ID:\s*([^\]\n]+?)\]",
    re.IGNORECASE,
)
_BLOCK_CLOSE_RE = re.compile(
    r"\[/BLOCK_ID:\s*[^\]\n]+?\]",
    re.IGNORECASE,
)


def _parse_text(text: str) -> Dict[str, str]:
    """Parse legacy closed blocks and the current open-only block format."""
    matches = list(_BLOCK_OPEN_RE.finditer(text))
    result: Dict[str, str] = {}
    for index, match in enumerate(matches):
        block_id = match.group(1).strip()
        if block_id in {"...", "…"}:
            continue
        content_start = match.end()
        next_start = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(text)
        )
        content_end = next_start
        close_match = _BLOCK_CLOSE_RE.search(text, content_start, next_start)
        if close_match is not None:
            content_end = close_match.start()
        result[block_id] = text[content_start:content_end].strip()
    return result


def parse_blocks(value: Any) -> Dict[str, str]:
    """Extract block id -> source text from rendered prompts or messages."""
    if isinstance(value, str):
        return _parse_text(value)
    if isinstance(value, Mapping):
        result: Dict[str, str] = {}
        for key in ("prompt", "input", "content", "text"):
            if key in value:
                result.update(parse_blocks(value[key]))
        return result
    if isinstance(value, list):
        result: Dict[str, str] = {}
        for item in value:
            result.update(parse_blocks(item))
        return result
    return {}
