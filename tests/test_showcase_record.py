"""Showcase metrics record: archived real runs condensed into a publishable, validated record."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from demo import showcase_record as record

# A workstation-style path assembled at run time, so this file itself holds no literal user path.
PRIVATE_PATH = "/" + "Users" + "/someone/private/path"

QUESTIONS = [
    ("easy", "sqlite", "What is the secret question alpha?", "SELECT secret_alpha FROM t"),
    ("easy", "sqlite", "What is the secret question beta?", "SELECT secret_beta FROM t"),
    ("medium", "sqlite", "What is the secret question gamma?", "SELECT secret_gamma FROM t"),
    ("hard", "sqlite", "What is the secret question delta?", "SELECT secret_delta FROM t"),
    ("hard", "sqlite", "What is the secret question epsilon?", "SELECT secret_epsilon FROM t"),
]


def _metric(avg, valid, total, **extra):
    return {"avg": avg, "valid": valid, "total": total, **extra}


def write_run(root: Path, group: str, method: str, *, ex: list[int], degenerate: int = 0, connection_errors: int = 0,
              limit: int = 5, with_sc: bool = False, exhausted: int = 0, fallbacks: int = 0) -> None:
    """Write one archived run (job record, score bundle, log) shaped like the Demo's own output."""
    directory = root / group / method
    (directory / "score-bundle").mkdir(parents=True)
    samples = []
    for i, ((hardness, db_type, question, gold), hit) in enumerate(zip(QUESTIONS, ex)):
        bad = i < degenerate
        samples.append({
            "index": i, "instance_id": f"dev_{i}", "db_id": "private_db", "db_type": db_type, "hardness": hardness,
            "question": question, "gold_sql": gold, "pred_sql": "SELECT" if bad else gold, "ex": hit,
            "em": hit, "sf1": 1.0 if hit else 0.0, "ves": 0.9 if hit else 0.0, "rves": 0.9 if hit else 0.0,
            "error_root": None if hit else "wrong_join", "exec_error": None,
        })
    n = len(samples)
    passed = sum(ex)
    scores = {
        "run_id": f"spider-{method}-abc", "method": method, "dataset": "spider", "split": "dev",
        "generate_num": 2 if with_sc else 1, "sample_count": n, "timestamp": "2026-10-25T12:00:00+00:00",
        "config_path": f"{PRIVATE_PATH}/config.json",
        "aggregate": {
            "ex": _metric(passed / n, n, n, pass_count=passed, interval=[0.2, 0.9], interval_kind="wilson95"),
            "em": _metric(passed / n, n, n),
            "sf1": _metric(passed / n, n, n),
            "sc": _metric(0.5, n, n) if with_sc else _metric(None, 0, n, note="require generate_num>=2"),
            "ves": _metric(0.9 * passed / n, n, n),
            "rves": _metric(0.9 * passed / n, n, n),
            "cf1": {"cf1_select": {"avg": 0.8}, "cf1_where": {"avg": 0.7}},
            "fd": {"delta_join": {"mean": 0.1, "std": 0.2}},
            "error_root_distribution": {"wrong_join": {"count": n - passed, "pct": 1.0, "sample_ids": ["dev_1"]}},
            "token": {"total_calls": 10, "total_prompt_tokens": 300, "total_completion_tokens": 700,
                      "total_tokens": 1000, "avg_per_sample": 200.0,
                      "by_step": {"sample:dev_0": {"calls": 2, "total_tokens": 100}, "sample:dev_1": {"calls": 2, "total_tokens": 300}}},
            "latency": {"sample_count": n, "avg_s": 12.0, "p50_s": 10.0, "p95_s": 30.0},
            "process": {"generation_pass1": passed / n, "refinement_fix_rate": None},
            "stage": {"gen": {"task_type": "GenerateTask", "metrics": {"execute_accuracy": _metric(passed / n, n, n)}}},
        },
        "by_hardness": {"easy": {"count": 2, "ex": 1.0, "em": 1.0, "error_dist": {}},
                        "hard": {"count": 2, "ex": 0.0, "em": 0.0, "error_dist": {}}},
        "by_db_type": {"sqlite": {"count": n, "ex": passed / n, "em": passed / n,
                                  "error_dist": {"wrong_join": {"count": 1, "pct": 1.0, "sample_ids": ["dev_1"]}}}},
        "by_sql_feature": {"join>0": {"count": 2, "ex": 0.5, "em": 0.5, "sf1": 0.5, "ves": 0.4, "bottlenecks": {"gen": 1}}},
        "by_scenario": {"nested_or_set": {"count": 1, "ex": 0.0, "em": 0.0, "sf1": 0.0, "ves": 0.0, "bottlenecks": {}}},
        "workflow_trace": {"aggregate": {"bottleneck_distribution": {"success": passed, "gen": n - passed},
                                         "stage_summary": {"gen": {"task_type": "GenerateTask", "actor_class": "X",
                                                                   "status_counts": {"pass": passed}, "metrics": {}, "signals": {}}}}},
        "config_snapshot": {"llm": {"provider": "acme", "model": "tiny-1", "temperature": 0.0}},
        "per_sample": samples,
    }
    (directory / "score-bundle" / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
    (directory / "job.json").write_text(json.dumps({
        "job_id": f"job-{method}", "comparison_id": "cmp-1", "dataset": "spider", "method": method,
        "sample_limit": limit, "sample_mode": "slice", "sample_seed": 42, "started_at": 1000.0, "finished_at": 1090.0,
    }), encoding="utf-8")
    log = "".join("WARNING LLM call failed: Connection error.\n" for _ in range(connection_errors))
    log += "".join("ERROR All 3 LLM attempts failed\n" for _ in range(exhausted))
    log += "".join("WARNING Candidate 1 gave up, falling back to 'SELECT'\n" for _ in range(fallbacks))
    (directory / "run.log").write_text(log + f"{PRIVATE_PATH}/run.log\n", encoding="utf-8")


class ShowcaseRecordTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_run_carries_its_quality_cost_and_breakdown_metrics(self):
        write_run(self.root, "n5", "alpha", ex=[1, 1, 0, 1, 0])
        built = record.build_record(self.root)

        run = built["batches"][0]["runs"]["alpha"]
        self.assertEqual(run["quality"]["ex"], {
            "status": "ok", "value": 0.6, "numerator": 3, "denominator": 5, "total": 5, "coverage": 1.0,
            "interval": [0.2, 0.9],
        })
        self.assertEqual(run["quality"]["em"]["value"], 0.6)
        self.assertEqual(run["cost"]["total_tokens"], 1000)
        self.assertEqual(run["cost"]["latency"], {"avg_s": 12.0, "p50_s": 10.0, "p95_s": 30.0})
        self.assertEqual(run["by_hardness"]["easy"], {"count": 2, "ex": 1.0, "em": 1.0})
        self.assertEqual(run["component_f1"], {"select": 0.8, "where": 0.7})
        self.assertEqual(run["error_roots"], {"wrong_join": {"count": 2, "pct": 1.0}})
        self.assertEqual(run["by_sql_feature"]["join>0"]["ex"], 0.5)
        self.assertEqual(run["stages"]["gen"]["metrics"]["execute_accuracy"]["value"], 0.6)
        self.assertEqual(run["bottlenecks"], {"success": 3, "gen": 2})
        self.assertEqual(run["sampling"], {"limit": 5, "mode": "slice", "seed": 42, "sample_count": 5})

    def test_a_metric_that_cannot_be_computed_stays_unavailable_with_its_reason(self):
        write_run(self.root, "n5", "alpha", ex=[1, 0, 1, 0, 1])
        run = record.build_record(self.root)["batches"][0]["runs"]["alpha"]

        self.assertEqual(run["quality"]["sc"]["status"], "unavailable")
        self.assertIsNone(run["quality"]["sc"]["value"])
        self.assertEqual(run["quality"]["sc"]["reason"], "require generate_num>=2")
        self.assertEqual(run["process"]["refinement_fix_rate"], {"status": "unavailable", "value": None, "reason": "not_observed"})
        self.assertEqual(run["process"]["generation_pass1"], {"status": "ok", "value": 0.6})

    def test_a_run_with_too_many_degenerate_predictions_is_invalid_and_left_out_of_comparisons(self):
        write_run(self.root, "n5", "alpha", ex=[1, 1, 0, 1, 0])
        write_run(self.root, "n5", "beta", ex=[1, 0, 1, 1, 0])
        write_run(self.root, "n5", "gamma", ex=[1, 1, 1, 1, 1], degenerate=2, connection_errors=7, exhausted=3, fallbacks=2)
        batch = record.build_record(self.root)["batches"][0]

        gamma = batch["runs"]["gamma"]
        self.assertEqual(gamma["quality"]["ex"]["value"], 1.0, "an invalid run keeps its numbers")
        self.assertEqual(gamma["diagnostics"], {
            "connection_errors": 7, "retries_exhausted": 3, "fallbacks_to_empty": 2, "model_calls": 10,
            "degenerate_predictions": 2, "degenerate_rate": 0.4, "valid": False,
        })
        self.assertTrue(batch["runs"]["alpha"]["diagnostics"]["valid"])
        self.assertEqual(batch["validity_rule"], {
            "max_degenerate_rate": 0.2, "degenerate_means": "an empty prediction or a bare SELECT",
            "invalid_when": "the degenerate rate reaches the maximum",
        })
        self.assertEqual(batch["ranking"], ["alpha", "beta"], "equal EX falls back to fewer tokens, then name")
        self.assertEqual(batch["invalid_runs"], ["gamma"])
        self.assertEqual(batch["pairwise"], [{"a": "alpha", "b": "beta", "both": 2, "only_a": 1, "only_b": 1, "neither": 1}])

    def test_the_question_matrix_uses_anonymous_ordinals_for_every_run(self):
        write_run(self.root, "n5", "alpha", ex=[1, 1, 0, 1, 0])
        write_run(self.root, "n5", "beta", ex=[1, 0, 1, 1, 0])
        matrix = record.build_record(self.root)["batches"][0]["question_matrix"]

        self.assertEqual(len(matrix), 5)
        self.assertEqual(matrix[1], {"q": "q2", "hardness": "easy", "ex": {"alpha": 1, "beta": 0}})

    def test_the_record_never_carries_questions_sql_identifiers_paths_or_logs(self):
        write_run(self.root, "n5", "alpha", ex=[1, 1, 0, 1, 0], connection_errors=3)
        text = record.render_module(record.build_record(self.root))

        for forbidden in ("secret question", "secret_alpha", "SELECT secret", "FROM t", "private_db", "dev_0", "dev_1",
                          PRIVATE_PATH, "run.log", "Connection error", "config_path"):
            self.assertNotIn(forbidden, text, forbidden)

    def test_building_twice_gives_byte_identical_output(self):
        write_run(self.root, "n5", "alpha", ex=[1, 1, 0, 1, 0])
        write_run(self.root, "n5", "beta", ex=[1, 0, 1, 1, 0], connection_errors=2)
        first = record.render_module(record.build_record(self.root))
        second = record.render_module(record.build_record(self.root))
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("// Generated by demo/showcase_record.py"))
        self.assertIn("export default", first)

    def test_a_run_says_where_its_numbers_came_from(self):
        write_run(self.root, "n5", "alpha", ex=[1, 1, 0, 1, 0])
        run = record.build_record(self.root)["batches"][0]["runs"]["alpha"]
        self.assertEqual(run["source"], {"run_ref": "n5/alpha", "llm": {"provider": "acme", "model": "tiny-1"},
                                         "recorded_on": "2026-10-25", "dataset": "spider", "split": "dev", "wall_seconds": 90.0})

    def test_a_bundle_missing_a_core_metric_is_rejected_by_name(self):
        write_run(self.root, "n5", "alpha", ex=[1, 1, 0, 1, 0])
        path = self.root / "n5" / "alpha" / "score-bundle" / "scores.json"
        scores = json.loads(path.read_text())
        del scores["aggregate"]["ex"]
        path.write_text(json.dumps(scores))
        with self.assertRaisesRegex(ValueError, "n5/alpha.*ex"):
            record.build_record(self.root)

    def test_runs_in_one_batch_must_have_answered_the_same_number_of_questions(self):
        write_run(self.root, "n5", "alpha", ex=[1, 1, 0, 1, 0])
        write_run(self.root, "n5", "beta", ex=[1, 0, 1, 1])
        with self.assertRaisesRegex(ValueError, "n5.*same"):
            record.build_record(self.root)

    def test_remaining_breakdowns_are_recorded_without_sample_identifiers(self):
        write_run(self.root, "n5", "alpha", ex=[1, 1, 0, 1, 0])
        run = record.build_record(self.root)["batches"][0]["runs"]["alpha"]

        self.assertEqual(run["by_db_type"], {"sqlite": {"count": 5, "ex": 0.6, "em": 0.6}})
        self.assertEqual(run["by_scenario"]["nested_or_set"]["count"], 1)
        self.assertEqual(run["structure_delta"], {"join": {"mean": 0.1, "std": 0.2}})
        self.assertEqual(run["cost"]["tokens_per_sample"], {"samples": 2, "min": 100, "median": 200.0, "max": 300})

    def test_a_run_exactly_at_the_threshold_is_invalid(self):
        write_run(self.root, "n5", "alpha", ex=[1, 1, 0, 1, 0], degenerate=1)
        run = record.build_record(self.root)["batches"][0]["runs"]["alpha"]
        self.assertEqual(run["diagnostics"]["degenerate_rate"], 0.2)
        self.assertFalse(run["diagnostics"]["valid"])

    def test_a_batch_where_every_run_is_invalid_has_no_ranking_and_no_pairs(self):
        write_run(self.root, "n5", "alpha", ex=[1, 1, 0, 1, 0], degenerate=2)
        write_run(self.root, "n5", "beta", ex=[1, 0, 1, 1, 0], degenerate=3)
        batch = record.build_record(self.root)["batches"][0]
        self.assertEqual((batch["ranking"], batch["pairwise"], batch["invalid_runs"]), ([], [], ["alpha", "beta"]))
        self.assertEqual(len(batch["runs"]), 2)

    def test_runs_in_a_batch_prove_they_answered_the_same_questions(self):
        write_run(self.root, "n5", "alpha", ex=[1, 1, 0, 1, 0])
        write_run(self.root, "n5", "beta", ex=[1, 0, 1, 1, 0])
        batch = record.build_record(self.root)["batches"][0]
        self.assertEqual(batch["sample_count"], 5)
        self.assertTrue(batch["alignment"]["aligned"])
        self.assertRegex(batch["alignment"]["fingerprint"], r"^[0-9a-f]{12}$")
        self.assertNotIn("dev_0", json.dumps(batch["alignment"]), "the fingerprint hides the identifiers")

        path = self.root / "n5" / "beta" / "score-bundle" / "scores.json"
        scores = json.loads(path.read_text())
        scores["per_sample"][0]["instance_id"] = "dev_99"
        path.write_text(json.dumps(scores))
        with self.assertRaisesRegex(ValueError, "n5.*same questions"):
            record.build_record(self.root)


if __name__ == "__main__":
    unittest.main()
