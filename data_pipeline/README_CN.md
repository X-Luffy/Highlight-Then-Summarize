# H2S 数据构造 Pipeline（发布快照）

本目录包含增量数据构造的编排脚本、来源整理、物化工具和审计辅助代码。它记录了 H2S 的公开研究流程：语义 block 切分、长度窗口约束、BM25 与 embedding 混合召回、RRF 排序、evidence/claim 抽取、summary 构建和最终 QC。

## 重要的发布边界

当前快照不包含 API 驱动的 Stage-2/4/5/6 执行器（例如 `2_build_block_store.py`、`4_retrieve_blocks_api.py`、`5_query_diff_evidence.py` 和 `6_build_canonical_summary.py`）。这些执行器是原始训练环境中的外部或私有依赖；仓库中的 wrapper 会调用它们，但不会伪装成开箱即用的全量复现。请通过 `CONFIG`、`PYTHON` 和 `ENVIRONMENT_FILE` 指向本地实现及凭据文件。凭据不要提交到 Git。

## 阶段

1. `00_build_incremental_pool.py`：稳定去重、来源合并和候选池统计。
2. `01_build_incremental_source.py`：按稳定哈希分配新增 SFT/RL 来源。
3. `02_run_block_split.sh`：调用外部 block parser，并执行 block QC。
4. `03_run_retrieval.sh`：BM25 + Qwen embedding + RRF。
5. `04_run_evidence.sh`：query decomposition、claim/evidence 及 strict/fuzzy95 span 验证。
6. `05_run_summary_qc.sh`：canonical summary、覆盖性和 answerability QC。
7. `06_materialize_train_test.py`：将通过 QC 的记录物化为 SFT/RL/test 文件。

## 配置和运行

先复制 `configs/incremental_train_test.json`，把其中相对路径改成本地数据、外部配置和输出目录。示例配置不含训练数据、模型权重或访问凭据。

```bash
cd /path/to/Longtext-RL
MODE=smoke ./data_pipeline/run_all.sh
```

`run_all.sh` 默认只执行 smoke；全量运行前应检查每个阶段的报告，并显式使用 `--full`。API 阶段需要额外的外部执行器和环境文件，例如：

```bash
PYTHON=python3 ENVIRONMENT_FILE=/path/to/environment.json \
  MODE=smoke ./data_pipeline/04_run_evidence.sh
```

详细的输入输出约定见根目录 `README.md` 和 `docs/REPRODUCIBILITY.md`。
