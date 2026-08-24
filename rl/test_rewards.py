#!/usr/bin/env python3

import unittest

try:
    from .rewards import compute_programmatic_reward, final_answer_reward, resolve_quote, span_f1
except ImportError:
    from rewards import compute_programmatic_reward, final_answer_reward, resolve_quote, span_f1


class RewardTest(unittest.TestCase):
    def test_quote_resolution_allows_wrapping_quotes_and_whitespace(self) -> None:
        source = "alpha line one\nalpha line two"
        self.assertEqual(resolve_quote(source, "“alpha line one alpha line two”"), (0, len(source)))

    def test_span_segmentation_invariance(self) -> None:
        reference = [
            {"block_id": "b1", "start_offset": 0, "end_offset": 5},
            {"block_id": "b1", "start_offset": 5, "end_offset": 10},
        ]
        generated = [
            {"block_id": "b1", "start_offset": 0, "end_offset": 10}
        ]
        self.assertEqual(span_f1(generated, reference)["f1"], 1.0)

    def test_stale_reference_offset_is_repaired_from_unique_exact_span(self) -> None:
        record = {
            "id": "shifted-span",
            "benchmark": "Frames",
            "gt": {"answer": "alpha"},
            "block_store": {"b1": "alpha beta gamma"},
            "reference_spans": [
                {
                    "block_id": "b1",
                    "start_offset": 1,
                    "end_offset": 6,
                    "exact_span": "alpha",
                }
            ],
            "reference_summary": "alpha",
        }
        response = {
            "evidence": [
                {"id": "E1", "block_id": "b1", "quote": "alpha"}
            ],
            "summary": "alpha [E1]",
            "answer": "alpha",
        }
        result = compute_programmatic_reward(record, response)
        self.assertEqual(result["components"]["span_f1"], 1.0)
        self.assertEqual(result["reward"], 1.0)

    def test_final_answer_reuses_evaluator(self) -> None:
        record = {
            "id": "numeric",
            "benchmark": "DocFinQA",
            "ability": "numerical",
            "gt": {"answer": "2.33"},
        }
        self.assertEqual(final_answer_reward(record, "2.330"), 1.0)

    def test_complete_programmatic_reward(self) -> None:
        record = {
            "id": "qa",
            "benchmark": "Frames",
            "ability": "reasoning",
            "gt": {"answer": "alpha"},
            "block_store": {"b1": "alpha beta gamma"},
            "reference_spans": [
                {"block_id": "b1", "start_offset": 0, "end_offset": 5}
            ],
        }
        response = {
            "evidence": [
                {"id": "E1", "block_id": "b1", "quote": "alpha"}
            ],
            "summary": "alpha [E1]",
            "answer": "alpha",
        }
        result = compute_programmatic_reward(record, response)
        self.assertEqual(result["components"]["format"], 1.0)
        self.assertEqual(result["components"]["span_f1"], 1.0)
        self.assertGreater(result["reward"], 0.9)

    def test_missing_summary_citation_receives_partial_format_credit(self) -> None:
        record = {
            "id": "qa",
            "benchmark": "Frames",
            "ability": "reasoning",
            "gt": {"answer": "alpha"},
            "block_store": {"b1": "alpha beta gamma"},
            "reference_spans": [
                {"block_id": "b1", "start_offset": 0, "end_offset": 5}
            ],
        }
        response = {
            "evidence": [
                {"id": "E1", "block_id": "b1", "quote": "alpha"}
            ],
            "summary": "alpha",
            "answer": "alpha",
        }
        result = compute_programmatic_reward(record, response)
        self.assertEqual(result["components"]["format"], 0.5)
        self.assertIn("missing_summary_citation", result["format_errors"])
        self.assertEqual(result["reward"], 0.5)

    def test_missing_core_section_remains_hard_gated(self) -> None:
        record = {
            "id": "qa",
            "benchmark": "Frames",
            "ability": "reasoning",
            "gt": {"answer": "alpha"},
            "block_store": {"b1": "alpha beta gamma"},
            "reference_spans": [
                {"block_id": "b1", "start_offset": 0, "end_offset": 5}
            ],
        }
        response = {
            "evidence": [
                {"id": "E1", "block_id": "b1", "quote": "alpha"}
            ],
            "summary": "alpha [E1]",
            "answer": "",
        }
        result = compute_programmatic_reward(record, response)
        self.assertEqual(result["components"]["format"], 0.0)
        self.assertIn("missing_answer", result["format_errors"])
        self.assertEqual(result["reward"], 0.0)

    def test_reference_summary_scores_independently_of_citation_ids(self) -> None:
        record = {
            "id": "qa",
            "benchmark": "Frames",
            "ability": "reasoning",
            "gt": {"answer": "alpha"},
            "reference_summary": "alpha is supported by the document [clm_0001]",
            "block_store": {"b1": "alpha is supported by the document"},
            "reference_spans": [
                {"block_id": "b1", "start_offset": 0, "end_offset": 34}
            ],
        }
        response = {
            "evidence": [
                {
                    "id": "E1",
                    "block_id": "b1",
                    "quote": "alpha is supported by the document",
                }
            ],
            "summary": "alpha is supported by the document [E1]",
            "answer": "alpha",
        }
        result = compute_programmatic_reward(record, response)
        self.assertEqual(result["components"]["summary_task_score"], 1.0)
        self.assertEqual(
            result["component_sources"]["summary_task_score"],
            "reference_summary_rouge_l",
        )

    def test_actual_sft_tag_format_and_prompt_blocks(self) -> None:
        record = {
            "id": "self-contained-qa",
            "benchmark": "Frames",
            "ability": "reasoning",
            "gt": {"answer": "alpha"},
            "prompt": [
                {"role": "system", "content": "Answer carefully."},
                {
                    "role": "user",
                    "content": (
                        "[BLOCK_ID: b1]\nalpha beta gamma\n"
                        "[/BLOCK_ID: b1]\n\nQuestion"
                    ),
                },
            ],
            "reference_spans": [
                {"block_id": "b1", "start_offset": 0, "end_offset": 5}
            ],
            "reference_summary": "alpha",
        }
        response = """<evidence>
[E0001] block_id=b1: alpha
</evidence>
<summary>alpha [E0001]</summary>
<answer>alpha</answer>"""
        result = compute_programmatic_reward(record, response)
        self.assertEqual(result["components"]["format"], 1.0)
        self.assertEqual(result["components"]["evidence_validity"], 1.0)
        self.assertEqual(result["components"]["span_f1"], 1.0)
        self.assertEqual(result["components"]["summary_task_score"], 1.0)
        self.assertEqual(result["components"]["answer_score"], 1.0)
        self.assertEqual(result["reward"], 1.0)

    def test_actual_sft_tag_format_supports_multiple_multiline_quotes(self) -> None:
        record = {
            "id": "multiline-evidence",
            "benchmark": "Frames",
            "gt": {"answer": "alpha"},
            "block_store": {
                "b1": "alpha line one\nalpha line two",
                "b2": "beta evidence",
            },
            "reference_spans": [
                {"block_id": "b1", "start_offset": 0, "end_offset": 29},
                {"block_id": "b2", "start_offset": 0, "end_offset": 13},
            ],
            "reference_summary": "alpha and beta",
        }
        response = """<evidence>
[E0001] block_id=b1: alpha line one
alpha line two
[E0002] block_id=b2: beta evidence
</evidence>
<summary>alpha and beta [E0001] [E0002]</summary>
<answer>alpha</answer>"""
        result = compute_programmatic_reward(record, response)
        self.assertEqual(result["components"]["format"], 1.0)
        self.assertEqual(result["components"]["evidence_validity"], 1.0)
        self.assertEqual(result["components"]["span_f1"], 1.0)

    def test_legacy_text_evidence_format_remains_supported(self) -> None:
        record = {
            "id": "legacy",
            "benchmark": "Frames",
            "gt": {"answer": "alpha"},
            "block_store": {"b1": "alpha"},
            "reference_spans": [
                {"block_id": "b1", "start_offset": 0, "end_offset": 5}
            ],
            "reference_summary": "alpha",
        }
        response = """<evidence>
[E1] block=b1; quote="alpha"
</evidence>
<summary>alpha [E1]</summary>
<answer>alpha</answer>"""
        result = compute_programmatic_reward(record, response)
        self.assertEqual(result["components"]["format"], 1.0)
        self.assertEqual(result["components"]["evidence_validity"], 1.0)


if __name__ == "__main__":
    unittest.main()
