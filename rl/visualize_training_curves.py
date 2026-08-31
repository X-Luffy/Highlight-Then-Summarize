#!/usr/bin/env python3
"""Generate training curves from exported JSONL training logs."""

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def read_jsonl(path):
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def step_value(row, fallback):
    value = row.get("global_step/max_steps")
    if isinstance(value, str) and "/" in value:
        try:
            return int(value.split("/", 1)[0])
        except ValueError:
            pass
    value = row.get("step")
    if isinstance(value, (int, float)):
        return value
    return fallback


def numeric_series(rows, key):
    values = []
    for index, row in enumerate(rows, 1):
        value = row.get(key)
        if isinstance(value, (int, float)) and math.isfinite(value):
            values.append((step_value(row, index), value))
    return values


def setup_axes(ax, title, ylabel):
    ax.set_title(title)
    ax.set_xlabel("Training step")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)


def save_sft_curves(run_dir):
    log_path = run_dir / "logging.jsonl"
    if not log_path.exists():
        return False

    rows = read_jsonl(log_path)
    if not rows:
        return False

    out_dir = run_dir / "curves"
    out_dir.mkdir(parents=True, exist_ok=True)
    title = run_dir.name

    figures = [
        (
            "loss_and_accuracy.png",
            [("loss", "Loss"), ("token_acc", "Token accuracy")],
            "SFT loss and token accuracy",
        ),
        (
            "optimization.png",
            [("grad_norm", "Gradient norm"), ("learning_rate", "Learning rate")],
            "SFT optimization metrics",
        ),
    ]
    for filename, series, chart_title in figures:
        fig, axes = plt.subplots(len(series), 1, figsize=(10, 6), sharex=True)
        if len(series) == 1:
            axes = [axes]
        for ax, (key, label) in zip(axes, series):
            values = numeric_series(rows, key)
            if values:
                x, y = zip(*values)
                ax.plot(x, y, linewidth=1.5, label=label)
            setup_axes(ax, label, label)
        fig.suptitle("%s: %s" % (title, chart_title), fontsize=12)
        fig.tight_layout()
        fig.savefig(out_dir / filename, dpi=150)
        plt.close(fig)
    return True


def component_rows(run_dir):
    paths = sorted((run_dir / "logs").glob("reward_components_rank_*.jsonl"))
    if not paths:
        return []

    by_step = {}
    for path in paths:
        for index, row in enumerate(read_jsonl(path), 1):
            samples = row.get("samples") or []
            if not samples:
                continue
            aggregate = {}
            counts = {}
            for sample in samples:
                components = sample.get("components") or {}
                for key, value in components.items():
                    if isinstance(value, (int, float)) and math.isfinite(value):
                        aggregate[key] = aggregate.get(key, 0.0) + value
                        counts[key] = counts.get(key, 0) + 1
            step = index
            target = by_step.setdefault(step, {})
            for key, value in aggregate.items():
                target.setdefault(key, []).append(value / counts[key])
            reward_mean = row.get("reward_mean")
            if isinstance(reward_mean, (int, float)):
                target.setdefault("_reward_mean", []).append(reward_mean)

    result = []
    for step in sorted(by_step):
        values = {}
        for key, items in by_step[step].items():
            if items:
                values[key] = sum(items) / len(items)
        result.append((step, values))
    return result


def save_rl_curves(run_dir):
    log_path = run_dir / "logs" / "logging.jsonl"
    if not log_path.exists():
        log_path = run_dir / "logging.jsonl"
    if not log_path.exists():
        return False

    rows = read_jsonl(log_path)
    if not rows:
        return False

    out_dir = run_dir / "curves"
    out_dir.mkdir(parents=True, exist_ok=True)
    title = run_dir.name

    charts = [
        (
            "reward_and_length.png",
            [
                ("reward", "Reward mean"),
                ("reward_std", "Reward std"),
                ("completions/mean_length", "Response mean length"),
                ("completions/min_length", "Response min length"),
                ("completions/max_length", "Response max length"),
            ],
            "RL reward and response length",
        ),
        (
            "optimization.png",
            [
                ("loss", "Loss"),
                ("grad_norm", "Gradient norm"),
                ("learning_rate", "Learning rate"),
                ("completions/clipped_ratio", "Clipped ratio"),
            ],
            "RL optimization metrics",
        ),
    ]
    for filename, series, chart_title in charts:
        fig, axes = plt.subplots(len(series), 1, figsize=(10, 11), sharex=True)
        for ax, (key, label) in zip(axes, series):
            values = numeric_series(rows, key)
            if values:
                x, y = zip(*values)
                ax.plot(x, y, linewidth=1.3, label=label)
            setup_axes(ax, label, label)
        fig.suptitle("%s: %s" % (title, chart_title), fontsize=12)
        fig.tight_layout()
        fig.savefig(out_dir / filename, dpi=150)
        plt.close(fig)

    components = component_rows(run_dir)
    component_keys = sorted(
        {
            key
            for _, values in components
            for key in values
            if not key.startswith("_")
        }
    )
    if components and component_keys:
        fig, ax = plt.subplots(figsize=(10, 6))
        for key in component_keys:
            points = [
                (step, values[key])
                for step, values in components
                if key in values
            ]
            if points:
                x, y = zip(*points)
                ax.plot(x, y, linewidth=1.2, label=key)
        setup_axes(ax, "Reward component means", "Component score")
        fig.suptitle("%s: reward components" % title, fontsize=12)
        fig.tight_layout()
        fig.savefig(out_dir / "reward_components.png", dpi=150)
        plt.close(fig)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sft", action="append", default=[])
    parser.add_argument("--rl", action="append", default=[])
    args = parser.parse_args()

    for path in args.sft:
        print("SFT %s: %s" % (path, "ok" if save_sft_curves(Path(path)) else "no log"))
    for path in args.rl:
        print("RL %s: %s" % (path, "ok" if save_rl_curves(Path(path)) else "no log"))


if __name__ == "__main__":
    main()
