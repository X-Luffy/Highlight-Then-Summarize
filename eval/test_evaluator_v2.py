import unittest

try:
    from .evaluator_v2 import evaluate_record
except ImportError:
    from evaluator_v2 import evaluate_record


class EvaluatorV2Tests(unittest.TestCase):
    def test_mrcr_uses_fuzzy95_metric(self) -> None:
        record = {
            "id": "mrcr-fuzzy",
            "benchmark": "MRCR",
            "ability": "precise_retrieval",
            "question": "Return the requested string.",
            "gt": {"answer": ["42"]},
        }
        result = evaluate_record(
            record,
            "The answer is 42",
            protocol="native",
        )
        self.assertEqual(result.details["selected_evaluator"], "mrcr_fuzzy95")
        self.assertEqual(result.score, 1.0)

    def test_v2_t8_uses_numeric_protocol(self) -> None:
        record = {
            "id": "t8-numeric",
            "benchmark": "LongBench-Pro-T8",
            "ability": "numerical",
            "question": "Calculate the percentage.",
            "gt": {"answer": ["-11.9%", "-17.6%"]},
        }
        result = evaluate_record(
            record,
            "-11.9%\n-17.6%",
            protocol="native",
        )
        self.assertEqual(result.details["selected_evaluator"], "numeric")
        self.assertEqual(result.score, 1.0)

    def test_v2_t10_summary_routing_is_explicit(self) -> None:
        record = {
            "id": "t10-summary",
            "benchmark": "LongBench-Pro-T10",
            "ability": "precise_retrieval",
            "question": "Summarize the hidden pattern and report the result.",
            "gt": {"answer": ["36"]},
        }
        result = evaluate_record(record, "36", protocol="native")
        self.assertEqual(result.details["selected_evaluator"], "summary")

    def test_longbenchv2_native_answer_tag_is_parsed_as_final_choice(self) -> None:
        record = {
            "id": "lbv2-choice",
            "benchmark": "LongBenchV2",
            "ability": "choice",
            "question": "Choose the correct option.",
            "gt": {"answer": ["C"]},
        }
        result = evaluate_record(
            record,
            "<evidence>irrelevant mention of A</evidence>"
            "<summary>reasoning mentions B</summary>"
            "<answer>(C) final choice</answer>",
            protocol="native",
        )
        self.assertEqual(result.details["selected_evaluator"], "choice")
        self.assertEqual(result.score, 1.0)


if __name__ == "__main__":
    unittest.main()
