"""Small parser for the block-id document format used by V3 rewards."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Dict


_BLOCK_RE = re.compile(
    r"\[BLOCK_ID:\s*([^\]\n]+?)\]\s*(.*?)\s*\[/BLOCK_ID:\s*[^\]\n]+?\]",
    re.IGNORECASE | re.DOTALL,
)


def _parse_text(text: str) -> Dict[str, str]:
    return {match.group(1).strip(): match.group(2) for match in _BLOCK_RE.finditer(text)}


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
