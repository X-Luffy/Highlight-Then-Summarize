# Evaluation Config Templates

This directory contains the historical SFT/RL evaluation YAML configurations
used by the project. The settings are preserved, while machine-specific paths
and cluster addresses are represented by:

- `${PROJECT_ROOT}` for local project and checkpoint roots
- `${MASTER_ADDR}` for the distributed evaluation master address

Replace these placeholders before running an evaluation. The cluster-specific
AFS finalization launcher is intentionally kept outside the public repository.
