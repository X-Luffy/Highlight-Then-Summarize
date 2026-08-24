#!/usr/bin/env python3
"""Analyze the v4 SFT, RL, and test delivery files."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            rows.append(value)
    return rows


def percentile(values: List[int], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def ratio_table(values: Iterable[Any], total: int) -> List[Dict[str, Any]]:
    counts = Counter(str(value or "<missing>") for value in values)
    return [
        {
            "name": name,
            "count": count,
            "share": round(100.0 * count / total, 2) if total else 0.0,
        }
        for name, count in sorted(counts.items())
    ]


def length_bucket(value: int) -> str:
    if value < 8_192:
        return "<8K"
    if value < 32_768:
        return "8K-32K"
    if value < 65_536:
        return "32K-64K"
    if value < 131_072:
        return "64K-128K"
    return ">=128K"


def analyze_split(rows: List[Dict[str, Any]], split: str) -> Dict[str, Any]:
    lengths = [
        int(row["input_token_num"])
        for row in rows
        if isinstance(row.get("input_token_num"), (int, float))
        and int(row["input_token_num"]) >= 0
    ]
    modes = Counter(str(row.get("input_token_num_mode") or "<not_recorded>") for row in rows)
    ids = [str(row.get("id") or "") for row in rows]
    questions_missing = sum(not str(row.get("question") or "").strip() for row in rows)
    if split == "sft":
        input_missing = sum(not isinstance(row.get("messages"), list) for row in rows)
    elif split == "rl":
        input_missing = sum(not isinstance(row.get("prompt"), list) for row in rows)
    else:
        input_missing = sum(not isinstance(row.get("input"), list) for row in rows)

    stats = {
        "count": len(rows),
        "token_count_recorded": len(lengths),
        "token_count_missing": len(rows) - len(lengths),
        "token_mode_counts": dict(sorted(modes.items())),
        "min": min(lengths) if lengths else None,
        "mean": round(sum(lengths) / len(lengths), 2) if lengths else None,
        "p50": round(percentile(lengths, 0.50), 2),
        "p90": round(percentile(lengths, 0.90), 2),
        "p95": round(percentile(lengths, 0.95), 2),
        "max": max(lengths) if lengths else None,
        "bucket_counts": dict(
            sorted(Counter(length_bucket(value) for value in lengths).items())
        ),
    }
    return {
        "split": split,
        "count": len(rows),
        "length": stats,
        "ability": ratio_table((row.get("ability") for row in rows), len(rows)),
        "benchmark": ratio_table((row.get("benchmark") for row in rows), len(rows)),
        "quality": {
            "duplicate_ids": len(ids) - len(set(ids)),
            "missing_ids": sum(not value for value in ids),
            "missing_question": questions_missing,
            "missing_input": input_missing,
        },
    }


def markdown_table(items: List[Mapping[str, Any]], name_header: str) -> str:
    lines = [
        f"| {name_header} | Case | Share |",
        "|---|---:|---:|",
    ]
    for item in items:
        lines.append(
            "| {name} | {count} | {share:.2f}% |".format(
                name=item["name"],
                count=item["count"],
                share=item["share"],
            )
        )
    return "\n".join(lines)


def build_report(analysis: Mapping[str, Any]) -> str:
    lines = [
        "# v4 SFT / RL / Test 分布分析",
        "",
        "本报告基于 `data/v4` 下最终交付文件统计。训练集不包含 "
        "`MSMARCO-Rerank`；test 保留原 ID，并追加未进入 SFT/RL 的旧 OOD "
        "和候选 case。",
        "",
        "## 总览",
        "",
        "| Split | Case | Token mean | P50 | P90 | P95 | Max | Missing token |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for split in ("sft", "rl", "test"):
        item = analysis["splits"][split]
        length = item["length"]
        lines.append(
            "| {split} | {count} | {mean} | {p50} | {p90} | {p95} | {max} | "
            "{missing} |".format(
                split=split.upper(),
                count=item["count"],
                mean=length["mean"],
                p50=length["p50"],
                p90=length["p90"],
                p95=length["p95"],
                max=length["max"],
                missing=length["token_count_missing"],
            )
        )

    lines.extend(["", "## 输入长度桶", ""])
    for split in ("sft", "rl", "test"):
        length = analysis["splits"][split]["length"]
        lines.extend(
            [
                f"### {split.upper()}",
                "",
                "| Token bucket | Case | Share |",
                "|---|---:|---:|",
            ]
        )
        total = analysis["splits"][split]["count"]
        for name, count in length["bucket_counts"].items():
            share = 100.0 * count / total if total else 0.0
            lines.append(f"| {name} | {count} | {share:.2f}% |")
        lines.append("")

    for dimension, title in (
        ("ability", "能力维度"),
        ("benchmark", "Benchmark 维度"),
    ):
        lines.extend([f"## {title}", ""])
        for split in ("sft", "rl", "test"):
            lines.extend(
                [
                    f"### {split.upper()}",
                    "",
                    markdown_table(
                        analysis["splits"][split][dimension],
                        "Ability" if dimension == "ability" else "Benchmark",
                    ),
                    "",
                ]
            )

    lines.extend(["## 质量检查", ""])
    lines.extend(
        [
            "| Split | Duplicate ID | Missing ID | Missing question | Missing input |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for split in ("sft", "rl", "test"):
        quality = analysis["splits"][split]["quality"]
        lines.append(
            "| {split} | {duplicate} | {missing_id} | {missing_question} | "
            "{missing_input} |".format(
                split=split.upper(),
                duplicate=quality["duplicate_ids"],
                missing_id=quality["missing_ids"],
                missing_question=quality["missing_question"],
                missing_input=quality["missing_input"],
            )
        )
    lines.extend(
        [
            "",
            "## 解释",
            "",
            "- SFT/RL 的 `input_token_num` 沿用最终构造文件中的记录。",
            "- 新增 test 候选优先沿用候选源 token 数；原始 LongBenchV2 "
            "没有源 token 字段时使用字符长度近似，并在 "
            "`input_token_num_mode` 中标记为 `approx_chars_per_4`。",
            "- test 的 benchmark 数量不再与原始 500 条 ID 相同，因为它现在还"
            "包含未进入训练的 OOD/candidate case。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--report-md", type=Path, required=True)
    args = parser.parse_args()

    paths = {
        "sft": args.data_dir / "sft.jsonl",
        "rl": args.data_dir / "rl.jsonl",
        "test": args.data_dir / "test.jsonl",
    }
    analysis = {
        "schema_version": "v4_distribution_report_v1",
        "data_dir": str(args.data_dir),
        "splits": {
            split: analyze_split(read_jsonl(path), split)
            for split, path in paths.items()
        },
    }
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_md.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.report_md.write_text(build_report(analysis), encoding="utf-8")
    print(json.dumps(analysis, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
