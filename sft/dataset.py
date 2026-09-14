#!/usr/bin/env python3
"""Framework-neutral SFT dataset helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

try:
    from .system_prompt import (
        ANSWER_ONLY_SYSTEM_PROMPT,
        COMPACT_SYSTEM_PROMPT,
        SYSTEM_PROMPT,
        V1_SYSTEM_PROMPT,
    )
except ImportError:
    from system_prompt import (
        ANSWER_ONLY_SYSTEM_PROMPT,
        COMPACT_SYSTEM_PROMPT,
        SYSTEM_PROMPT,
        V1_SYSTEM_PROMPT,
    )


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_number}: invalid JSON: {exc}"
                ) from exc


def validate_sft_record(record: Mapping[str, Any]) -> list[str]:
    errors = []
    if not (record.get("id") or record.get("case_id")):
        errors.append("missing_id")
    if not record.get("question"):
        errors.append("missing_question")
    if not isinstance(record.get("prompt"), list):
        errors.append("prompt_not_list")
    assistant = record.get("assistant")
    if not isinstance(assistant, Mapping):
        errors.append("assistant_not_object")
    else:
        if assistant.get("reasoning_content") is None:
            errors.append("missing_reasoning_content")
        if assistant.get("answer") in (None, ""):
            errors.append("missing_answer")
    if record.get("input_token_num") is None:
        errors.append("missing_input_token_num")
    return errors


def validate_messages_sft_record(record: Mapping[str, Any]) -> list[str]:
    """Validate the active paper SFT message schemas."""
    errors = []
    if not (record.get("id") or record.get("case_id")):
        errors.append("missing_id")
    if not record.get("question"):
        errors.append("missing_question")
    if record.get("input_token_num") is None:
        errors.append("missing_input_token_num")

    messages = record.get("messages")
    if not isinstance(messages, list) or len(messages) < 3:
        errors.append("messages_must_have_system_user_and_assistant")
        return errors
    if messages[0].get("role") != "system":
        errors.append("first_message_not_system")
    elif str(messages[0].get("content") or "") not in {
        SYSTEM_PROMPT,
        COMPACT_SYSTEM_PROMPT,
        ANSWER_ONLY_SYSTEM_PROMPT,
        V1_SYSTEM_PROMPT,
    }:
        errors.append("system_prompt_mismatch")
    if messages[1].get("role") != "user":
        errors.append("second_message_not_user")
    if messages[-1].get("role") != "assistant":
        errors.append("last_message_not_assistant")
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            errors.append(f"message_{index}_not_object")
            continue
        if not str(message.get("content") or "").strip():
            errors.append(f"message_{index}_empty_content")

    assistant = str(messages[-1].get("content") or "")
    system_content = str(messages[0].get("content") or "")
    required_tags = (
        ("<answer>", "</answer>")
        if system_content == ANSWER_ONLY_SYSTEM_PROMPT
        else (
            "<evidence>",
            "</evidence>",
            "<summary>",
            "</summary>",
            "<answer>",
            "</answer>",
        )
    )
    for tag in required_tags:
        if tag not in assistant:
            errors.append(f"missing_tag:{tag}")
    answer = assistant.split("<answer>", 1)[-1].split("</answer>", 1)[0]
    if not answer.strip():
        errors.append("empty_tagged_answer")
    return errors


def to_training_example(record: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a framework-neutral prompt/response object.

    The caller can adapt this object to ERNIE, Transformers, or another
    trainer without changing the underlying dataset.
    """
    errors = validate_sft_record(record)
    if errors:
        raise ValueError(
            f"invalid SFT record {record.get('id')}: {', '.join(errors)}"
        )
    assistant = record["assistant"]
    return {
        "id": record.get("id") or record.get("case_id"),
        "prompt": record["prompt"],
        "response": {
            "reasoning_content": assistant.get("reasoning_content") or "",
            "answer": assistant.get("answer") or "",
        },
        "metadata": {
            "benchmark": record.get("benchmark"),
            "ability": record.get("ability"),
            "question": record.get("question"),
            "input_token_num": record.get("input_token_num"),
        },
    }
