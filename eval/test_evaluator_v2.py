import unittest

try:
    from .evaluator_v2 import evaluate_record, metric_name_for_record
except ImportError:
    from evaluator_v2 import evaluate_record, metric_name_for_record


class EvaluatorV2RoutingTests(unittest.TestCase):
    def test_t8_structured_judgement_is_not_numeric(self) -> None:
        record = {
            "id": "t8-structured",
            "benchmark": "LongBench-Pro-T8",
            "ability": "numerical",
            "question": (
                "Output each date number followed by balanced or unbalanced."
            ),
            "gt": {"answer": ["1 unbalanced", "2 balanced", "3 balanced"]},
        }
        self.assertEqual(metric_name_for_record(record), "set")
        result = evaluate_record(
            record,
            "1 balanced\n2 unbalanced\n3 balanced",
            protocol="native",
        )
        self.assertEqual(result.details["selected_evaluator"], "set")
        self.assertAlmostEqual(result.score, 1.0 / 3.0)

    def test_t8_numeric_case_remains_numeric(self) -> None:
        record = {
            "id": "t8-numeric",
            "benchmark": "LongBench-Pro-T8",
            "ability": "numerical",
            "question": "Calculate the percentage.",
            "gt": {"answer": ["-11.9%", "-17.6%"]},
        }
        self.assertEqual(metric_name_for_record(record), "numeric")
        result = evaluate_record(
            record,
            "-11.9%\n-17.6%",
            protocol="native",
        )
        self.assertEqual(result.details["selected_evaluator"], "numeric")
        self.assertEqual(result.score, 1.0)

    def test_t10_keyword_extraction_is_a_set(self) -> None:
        record = {
            "id": "t10-keywords",
            "benchmark": "LongBench-Pro-T10",
            "ability": "precise_retrieval",
            "question": (
                "Extract keywords from the new paper abstract and output them."
            ),
            "gt": {"answer": ["labor alienation", "algorithm", "labor justice"]},
        }
        self.assertEqual(metric_name_for_record(record), "set")
        result = evaluate_record(
            record,
            "labor alienation\nalgorithm\nlabor justice",
            protocol="native",
        )
        self.assertEqual(result.details["selected_evaluator"], "set")
        self.assertEqual(result.score, 1.0)

    def test_t10_numeric_prompt_is_numeric_even_with_summarize_word(self) -> None:
        record = {
            "id": "t10-numeric",
            "benchmark": "LongBench-Pro-T10",
            "ability": "precise_retrieval",
            "question": (
                "Summarize the hidden pattern, then find the smallest positive "
                "integer n."
            ),
            "gt": {"answer": ["36"]},
        }
        self.assertEqual(metric_name_for_record(record), "numeric")
        result = evaluate_record(record, "36", protocol="native")
        self.assertEqual(result.details["selected_evaluator"], "numeric")
        self.assertEqual(result.score, 1.0)

    def test_t10_metadata_question_is_qa_not_summary(self) -> None:
        record = {
            "id": "t10-metadata",
            "benchmark": "LongBench-Pro-T10",
            "ability": "precise_retrieval",
            "question": (
                "Generate a summary of the metadata pattern, then output the "
                "arXiv identifier and word count."
            ),
            "gt": {"answer": ["arXiv:2509.06789: 3, 13"]},
        }
        self.assertEqual(metric_name_for_record(record), "qa")
        result = evaluate_record(
            record,
            "arXiv:2509.06789: 3, 13",
            protocol="native",
        )
        self.assertEqual(result.details["selected_evaluator"], "qa")
        self.assertEqual(result.score, 1.0)


if __name__ == "__main__":
    unittest.main()
