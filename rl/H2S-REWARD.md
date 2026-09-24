# H2S reward

This is the frozen initial RL reward design. It is intentionally simple and
auditable; later versions can iterate without changing the SFT/RL output
contract.

## Output Contract

```text
<evidence>
[E0001] block_id=block-id: An exact or fuzzy95 source span.
</evidence>
<summary>A query-aware summary citing [E0001].</summary>
<answer>The final task answer.</answer>
```

`E0001` is local to one rollout. It is never matched against a reference claim ID.
The stable interface is `block_id + quote`, which can be resolved to source
offsets and compared with reference span unions.

The parser also accepts the legacy JSON object and
`[E1] block=...; quote="..."` syntax for backward compatibility, but the
tagged form above is the training contract.

## Components

```text
R_format
  1.0 when the structured output and all local citation links are complete;
  0.5 when Evidence/Summary/Answer are all parseable but citation-link or
  evidence-ID consistency has an error; 0.0 when any core section is missing.

R_valid
  Fraction of generated quotes found in the cited source blocks.

R_span
  Character-interval F1 between generated and reference span unions.
  This is invariant to whether a model splits one span into several evidence
  items or merges several adjacent spans.
  If a rendered block makes a stored offset stale, a unique ``exact_span``
  match in that block repairs the reference interval at scoring time.

R_summary_task
  ROUGE-L against the offline Stage-6 canonical query-aware Summary after
  removing local citation IDs. When only reference claims/subqueries are
  attached, this becomes supported subquery coverage. If no summary or claim
  metadata is attached, a non-empty parseable summary is not penalized again;
  citation linkage is reported separately and remains part of R_format.

R_answer
  Benchmark-specific FinalAnswer score from train/eval/evaluators.py.
```

## Aggregation

```text
R_path = harmonic_mean(R_valid, R_span, R_summary_task)

R_total = R_format * (0.60 * R_answer + 0.40 * R_path)
```

The three-level format multiplier was adopted after the group-8 API audit.
A binary gate made one complete eight-sample group constant zero even though
five samples had the exact final ranking and differed in grounded-path
quality; all eight merely omitted the local citation in Summary. Partial
format credit preserves that preference signal while a complete response
still receives twice the multiplier and missing core sections remain zero.

When reference spans or block text are unavailable, `R_path` is zero and the
reward falls back to `0.60 * R_answer`. Missing evidence supervision is never
silently treated as perfect.

`train/data/rl.jsonl` does not duplicate a `block_store`; the reward function
reconstructs exact block text from `[BLOCK_ID: ...]...[/BLOCK_ID: ...]` in the
record's prompt before resolving quotes.

## Reviewer-Facing Properties

- No online LLM judge.
- Each score is reproducible from saved text, offsets, GT, and verifier rules.
- Every rollout stores a component-level reward breakdown.
- Claim IDs and subquery IDs remain construction metadata, not model outputs.
- Stage-6 LLM calls are offline data construction/QC, not online RL rewards.
