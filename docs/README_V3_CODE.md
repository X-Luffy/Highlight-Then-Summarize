# V3 长文本数据与过程奖励开源代码

这是 V3 长文本数据构建和过程感知强化学习研究的配套代码。仓库包含确定性的奖励实现、基准评测器、训练配置示例，以及用于增量式数据构建的流程编排代码。

论文将训练接口称为**压缩后推理（compression-after-reasoning）**：模型先生成有来源依据的证据和面向问题的摘要，再基于这份紧凑的中间表示生成答案。本仓库用于支撑论文中的代码和数据格式说明，不是完整私有训练环境的复制品。

## 仓库内容

- `data_pipeline/`：候选池构建、稳定切分与数据落盘工具、各阶段脚本和流程说明。
- `rl/rewards.py`：针对 `<evidence>`、`<summary>` 和 `<answer>` 输出的确定性过程奖励。
- `rl/longtext_reward_v3_swift.py`：Swift/GRPO 适配器，为每条生成结果返回奖励并记录各分项得分。
- `eval/`：奖励和评测脚本使用的离线、基准原生评测器。
- `data/`：SFT、RL、ID 和 OOD 的快速 case，以及按 benchmark 精选的完整 case。
- `configs/`：7B 和 14B 的 SFT/RL YAML 配置示例。
- `eval/configs/`：历史评估 YAML 模板；路径和集群地址使用公开占位符。
- `eval/examples/`：七个 benchmark 的 all-model 汇总和少量 per-case 评分。
- `docs/`：复现说明、发布清单和凭据文件示例。

仓库**不包含**模型权重、私有凭据，也不包含约 7.6K 条训练和测试 JSONL 全量数据。
`data/` 下的 case 只用于说明数据格式；`eval/examples/` 下的结果文件是小型快照，
不包含完整预测目录。完整数据和实验产物请从项目批准的存储位置获取。

## 奖励接口

模型输出格式如下：

```text
<evidence>
[E0001] block_id=block-1: an exact or fuzzy-95 source span
</evidence>
<summary>A query-aware summary citing [E0001].</summary>
<answer>The final answer.</answer>
```

该分数可以审计，不依赖在线 LLM 评委：

```text
R_path  = H(R_valid, R_span, R_summary_task)
R_total = R_format * (0.60 * R_answer + 0.40 * R_path)
```

其中，`R_valid` 检查引用是否能在所引用的文本块中解析，`R_span` 计算字符区间或有界片段相对于参考片段的 F1，`R_summary_task` 衡量摘要对参考摘要和问题的覆盖，`R_answer` 调用对应基准的原生评测器。详细定义见 [`rl/PROGRAMMATIC_REWARD_V0.md`](rl/PROGRAMMATIC_REWARD_V0.md)。

## V2 评测

最终对比使用 `eval/evaluator_v2.py` 和 `eval/run_eval_v2.py`。ID 评测不包含 `MSMARCO-Rerank`，OOD 评测不包含 `HELMET-Rerank`。Base/API 预测使用 `native` 模式，SFT/RL 预测使用 `tagged` 模式。报告中的 `task_macro_score` 对应结果表中的 Avg。

## 快速检查

```bash
python3 -m unittest discover -s rl -p 'test_*.py'
python3 -m unittest discover -s data_pipeline -p 'test_*.py'
python3 -m py_compile $(find . -name '*.py' -not -path './.git/*')
```

奖励测试不需要模型服务。完整数据流程依赖下文所述的 Stage-2/4/5/6 外部执行器。

## 数据流程边界

仓库中的流程是增量编排和数据落盘的代码快照。原始环境使用的 API 执行器（文本块构建、向量召回、证据抽取、摘要和 QC）属于外部或私有依赖，不在本次发布中。因此，本仓库不承诺一条命令复现全量数据。请准备兼容的执行器、本地配置和环境文件，再运行 `data_pipeline/` 下的封装脚本。

```bash
cd v3/github
MODE=smoke ./data_pipeline/run_all.sh
```

默认配置是不会指向具体机器路径的模板。请复制后，将 `data/...`、`configs/...`
和 `eval/configs/...` 替换为本地路径，并为 API 阶段设置 `ENVIRONMENT_FILE`。
不要提交包含凭据的环境文件。

## 数据集快照

论文实验使用的 V3 数据包包含 4,228 条严格 native-GT SFT 数据、2,419 条 active-pool RL 数据，
以及 500 条 ID 和 500 条 OOD 评测数据，总量约 7.6K 条。来源数据覆盖 21 个长文本基准。
发布的脚本保留了原始的可回答性和来源 QC 决策；七任务结果快照位于
`eval/examples/`，详见 [`docs/REPRODUCIBILITY.md`](REPRODUCIBILITY.md)。

## 引用

引用信息见 [`CITATION.cff`](CITATION.cff)，论文草稿位于 `../overleaf/`。
