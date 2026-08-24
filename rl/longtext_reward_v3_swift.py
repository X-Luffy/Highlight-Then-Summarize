#!/usr/bin/env python3
"""ms-swift ORM adapter for the V3 deterministic programmatic reward."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Mapping

from swift.plugin import ORM, orms


SCRIPT_DIR = Path(__file__).resolve().parent
TRAIN_ROOT = SCRIPT_DIR.parent
if str(TRAIN_ROOT) not in sys.path:
    sys.path.insert(0, str(TRAIN_ROOT))

from rl.rewards import compute_programmatic_reward  # noqa: E402


RECORD_KEYS = (
    "schema_version",
    "id",
    "benchmark",
    "ability",
    "question",
    "input_token_num",
    "input_token_num_original",
    "prompt",
    "messages",
    "gt",
    "reference_spans",
    "reference_claims",
    "reference_summary",
    "reference_subqueries",
    "reward_metadata",
)


def _as_list(value: Any, size: int, default: Any = None) -> list[Any]:
    if isinstance(value, list):
        if len(value) == size:
            return value
        if len(value) == 1 and size > 1:
            return value * size
        return value[:size] + [default] * max(0, size - len(value))
    return [default if value is None else value for _ in range(size)]


def _json_maybe(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


class LongTextRewardV3(ORM):
    """Thin batch wrapper; all metric behavior lives in ``rl/rewards.py``."""

    def __init__(self) -> None:
        self.fail_fast = _bool_env("RL_V3_REWARD_FAIL_FAST", True)
        raw_log_path = os.environ.get("RL_V3_REWARD_COMPONENT_LOG_PATH", "")
        try:
            self.component_log_path = raw_log_path.format(
                rank=os.environ.get("RANK", "0"),
                local_rank=os.environ.get("LOCAL_RANK", "0"),
                pid=os.getpid(),
            )
        except (KeyError, ValueError):
            self.component_log_path = raw_log_path
        self._write_lock = threading.Lock()
        print(
            "[longtext-reward-v3] deterministic=true "
            f"fail_fast={str(self.fail_fast).lower()}",
            flush=True,
        )

    def _record(self, index: int, kwargs: Mapping[str, Any], size: int) -> Dict[str, Any]:
        record: Dict[str, Any] = {}
        for key in RECORD_KEYS:
            value = _as_list(kwargs.get(key), size, None)[index]
            if value is not None:
                record[key] = _json_maybe(value)

        # ms-swift may expose chat messages under ``messages`` after consuming
        # the dataset's ``prompt`` column.  Prefer the structured version for
        # reconstructing BLOCK_ID sections.
        messages = record.pop("messages", None)
        if isinstance(messages, list):
            record["prompt"] = messages
        elif "prompt" not in record:
            record["prompt"] = messages
        return record

    def _write_log(self, results: list[Mapping[str, Any]]) -> None:
        if not self.component_log_path or not results:
            return
        row = {
            "kind": "v3_programmatic_reward_components",
            "count": len(results),
            "reward_mean": sum(float(item.get("reward") or 0.0) for item in results)
            / len(results),
            "samples": [
                {
                    "id": item.get("id"),
                    "benchmark": item.get("benchmark"),
                    "reward": item.get("reward"),
                    "components": item.get("components"),
                    "format_errors": item.get("format_errors"),
                    "error": item.get("error"),
                }
                for item in results
            ],
        }
        path = Path(self.component_log_path)
        with self._write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def __call__(self, completions: list[str], **kwargs: Any) -> list[float]:
        size = len(completions)
        results: list[Dict[str, Any]] = []
        scores: list[float] = []
        for index, completion in enumerate(completions):
            record = self._record(index, kwargs, size)
            try:
                result = compute_programmatic_reward(record, completion)
                result["id"] = record.get("id")
                result["benchmark"] = record.get("benchmark")
                score = float(result["reward"])
            except Exception as exc:  # Make failures explicit in training.
                if self.fail_fast:
                    raise RuntimeError(
                        f"V3 reward failed for index={index} id={record.get('id')!r}"
                    ) from exc
                score = 0.0
                result = {
                    "id": record.get("id"),
                    "benchmark": record.get("benchmark"),
                    "reward": 0.0,
                    "components": {},
                    "format_errors": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            results.append(result)
            scores.append(score)
        self._write_log(results)
        return scores


orms["longtext_reward_v3"] = LongTextRewardV3
