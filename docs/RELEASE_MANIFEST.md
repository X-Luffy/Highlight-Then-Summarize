# V3 Release Manifest

## Included

- deterministic reward and evaluator source;
- evaluator-V2 runner and benchmark evaluator;
- unit tests;
- one complete schema example for each SFT, RL, ID, and OOD entry point;
- incremental data-pipeline orchestration and materialization utilities;
- representative 7B/14B SFT and RL YAML configurations;
- documentation and paper-facing provenance notes.

## Excluded

- model weights and checkpoints;
- optimizer states;
- training/evaluation JSONL data;
- API credentials or private environment files;
- private Stage-2/4/5/6 API executors;
- machine-specific absolute paths.

## Provenance

This release was assembled from the V3 AFS pipeline snapshot and the final local reward/evaluator implementation. The paper reports the measured audit and evaluator-V2 results from the corresponding experiment artifacts. Hashes and storage paths for those artifacts should be recorded in the project's private experiment registry rather than embedded in a public repository.
