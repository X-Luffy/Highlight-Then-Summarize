# 数据结构示例

这里放的是四个可直接阅读和解析的完整 JSONL case，每个文件对应一个数据入口：

| 文件 | 来源 | 用途 | 示例 benchmark |
|---|---|---|---|
| `sft_example.jsonl` | `sft_v1.jsonl` | SFT 对话格式 | LongBench-Pro-T6 |
| `rl_example.jsonl` | `rl_v1.jsonl` | RL prompt、参考 evidence、summary 和 reward metadata | LongBench-Pro-T5 |
| `id_example.jsonl` | `id.jsonl` | ID 评测输入和 gold | CUAD |
| `ood_example.jsonl` | `ood.jsonl` | OOD 评测输入和 gold | QwenLong-Test-DocMath |

示例行是从 V3 数据文件中完整抽取的原始 JSON 对象，没有截断 `messages`、`prompt`、文档输入或参考字段。它们只用于说明字段结构和调用方式，不代表完整训练集或评测集。完整数据、模型权重和预测文件不随仓库发布。

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
