# 数据结构示例

这里放的是四个可直接阅读和解析的 JSONL case。根目录下的
`*_example.jsonl` 保留一个快速入口；`examples/` 下是按 benchmark
有意挑选的三个完整 case，便于检查数据结构、长上下文输入和目标格式：

| 文件 | 来源 | 用途 | 示例 benchmark |
|---|---|---|---|
| `sft_example.jsonl` | `sft_v1.jsonl` | SFT 对话格式 | LongBench-Pro-T6 |
| `rl_example.jsonl` | `rl_v1.jsonl` | RL prompt、参考 evidence、summary 和 reward metadata | LongBench-Pro-T5 |
| `id_example.jsonl` | `id.jsonl` | ID 评测输入和 gold | CUAD |
| `ood_example.jsonl` | `ood.jsonl` | OOD 评测输入和 gold | QwenLong-Test-DocMath |

## Curated examples

| 文件 | 来源 | 选择的 benchmark |
|---|---|---|
| `examples/sft_v1_sample.jsonl` | `sft_v1.jsonl` | LongBench-Pro-T1、MRCR、QwenLong-MultiHopRAG |
| `examples/rl_v1_sample.jsonl` | `rl_v1.jsonl` | LongBench-Pro-T1、LongCite、QwenLong-MultiHopRAG |
| `examples/id_v1_extended_sample.jsonl` | `id_v1_extended.jsonl` | CNNSum、DocFinQA、LongCite |
| `examples/ood_v1_extended_sample.jsonl` | `ood_v1_extended.jsonl` | AA-LCR、LongBenchV2、HELMET-Summ |

每个 curated 文件按表中顺序保留对应 benchmark 的第一条记录，选择规则是确定性的。
记录本身没有截断 `messages`、`prompt`、文档输入或参考字段。样例只用于说明字段结构
和调用方式，不代表完整训练集或评测集；完整数据、模型权重和预测文件不随仓库发布。
公开发布前仍需根据各上游数据集许可证检查是否允许重新分发原文。

## 字段说明

- SFT case：`schema_version`、`id`、`benchmark`、`ability`、`question`、token 数以及 `messages`。
- RL case：除基本字段外，还包含 `prompt`、`gt`、`reference_evidence`、`reference_spans`、`reference_claims`、`reference_subqueries`、`reference_summary` 和 `reward_metadata`。这些字段由 `rl/rewards.py` 使用。
- ID/OOD case：包含 `input`、`gt` 和评测所需的 benchmark/ability 元数据。

读取示例：

```python
import json

with open("data/rl_example.jsonl", encoding="utf-8") as handle:
    case = json.loads(handle.readline())
print(case["benchmark"], case["question"])
```
