# H2S Release Manifest

## Included

- block-aware input rendering and token-counting utilities;
- canonical SFT prompts and data validation helpers;
- deterministic process rewards and the Swift/GRPO adapter;
- the task-specific H2S evaluator and its command-line runner;
- unit tests;
- small SFT, RL, ID, and OOD schema examples;
- curated case-level evaluation examples for seven benchmarks;
- data-construction orchestration and materialization utilities;
- 7B/14B SFT and RL configuration templates;
- reproducibility and paper-facing implementation notes.

## Excluded

- model weights and checkpoints;
- optimizer states and training caches;
- complete training/evaluation JSONL data;
- full prediction directories and raw API responses;
- TensorBoard files, runtime logs, locks, and process IDs;
- API credentials and private environment files;
- private Stage-2/4/5/6 executors;
- machine-specific absolute paths;
- internal knowledge-base exports and unrelated figures.

## Paper result provenance

The paper result tables are backed by separate experiment artifacts. The
public repository provides the evaluator and schemas needed to inspect or
recompute results when those artifacts are available, but it does not embed
the private artifact-store paths.
