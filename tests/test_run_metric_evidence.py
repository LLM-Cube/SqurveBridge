"""Local run evidence projection never substitutes unknown signals with zero."""
import unittest
from reproduce.eval.sample.process import process_summary
from demo.run_evidence import run_evidence


class RunEvidenceTests(unittest.TestCase):
    def test_outcomes_have_explicit_denominator_and_unknown_coverage(self):
        rows = [{"ex": 1}, {"ex": 0, "exec_error": None}, {"ex": 0, "exec_error": "synthetic error"}, {"ex": None}]
        process = process_summary(rows, {})
        self.assertEqual(process["outcome_observed"], 3)
        self.assertEqual(process["outcome_coverage"], .75)
        for name in ("correct_ratio", "incorrect_ratio", "error_ratio"):
            self.assertAlmostEqual(process[name], 1 / 3)
        self.assertIsNone(process_summary([], {})["correct_ratio"])

    def test_projection_keeps_absence_and_hides_sql_and_hosted_questions(self):
        scores = {"per_sample": [{"question": "synthetic question", "pred_sql": "secret", "ex": 0, "exec_error": "private path"}], "aggregate": {}}
        local = run_evidence(scores, include_questions=True)
        hosted = run_evidence(scores, include_questions=False)
        self.assertEqual(local["sample_evidence"][0]["question"], "synthetic question")
        self.assertIsNone(hosted["sample_evidence"][0]["question"])
        self.assertNotIn("private path", str(local))
        self.assertNotIn("secret", str(local))
        self.assertIsNone(local["sample_evidence"][0]["c2i"])
        self.assertEqual(local["ex_variant"], "custom")

    def test_unknown_error_and_arbitrary_public_metric_keys_are_not_exported(self):
        data = run_evidence({"aggregate": {"process": {"synthetic-private-token": .2}, "cf1": {"synthetic-private-token": .2}}, "per_sample": [{"ex": 0}]})
        self.assertIsNone(data["sample_evidence"][0]["error"])
        self.assertNotIn("synthetic-private-token", str(data))
        self.assertIsNone(process_summary([{"ex": 0}], {})["incorrect_ratio"])

    def test_variant_is_declared_never_inferred_from_dataset(self):
        self.assertEqual(run_evidence({"dataset": "bird"})["ex_variant"], "custom")
        self.assertEqual(run_evidence({"ex_variant": "bird"})["ex_variant"], "bird")
