# Curated Data Cases

These files contain three complete records selected from each major public
entry point:

- `sft_v1_sample.jsonl`: LongBench-Pro-T1, MRCR, and QwenLong-MultiHopRAG
- `rl_v1_sample.jsonl`: LongBench-Pro-T1, LongCite, and QwenLong-MultiHopRAG
- `id_v1_extended_sample.jsonl`: CNNSum, DocFinQA, and LongCite
- `ood_v1_extended_sample.jsonl`: AA-LCR, LongBenchV2, and HELMET-Summ

Selection is deterministic: the first record for each listed benchmark is
kept in the listed order. The records are complete JSON objects on one line;
long document fields are intentionally retained so readers can inspect the
actual input shape.

These are examples only. They are not a substitute for the full training or
evaluation data, and redistribution must be checked against every upstream
dataset license.
