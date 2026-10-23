# L5 process layer: shared math kernels, snapshot-key contract, and funnels.

import unittest

from reproduce.eval.sample.process import (
    canonical_role,
    find_before_key,
    gate_funnel,
    gate_outcome,
    linking_outcome,
    process_summary,
    refinement_outcome,
    selection_outcome,
    stage_survival_funnel,
)
from reproduce.metrics.pipeline_delta import compute_pipeline_delta


class RoleMappingTests(unittest.TestCase):
    def test_task_types_map_to_canonical_roles(self):
        self.assertEqual(canonical_role("ReduceTask"), "linking")
        self.assertEqual(canonical_role("ParseTask"), "linking")
        self.assertEqual(canonical_role("GenerateTask"), "generation")
        self.assertEqual(canonical_role("ScaleTask"), "generation")
        self.assertEqual(canonical_role("OptimizeTask"), "refinement")
        self.assertEqual(canonical_role("SelectTask"), "selection")
        self.assertEqual(canonical_role("DecomposeTask"), "decomposition")
        self.assertIsNone(canonical_role("ComplexTask"))


class SnapshotContractTests(unittest.TestCase):
    def test_exact_stage_id_wins_over_substring(self):
        row = {
            "pred_sql_before_finsql_selector": "SELECT 1",
            "pred_sql_before_reranker_select": "SELECT 2",
        }
        self.assertEqual(
            find_before_key(row, stage_id="finsql_selector", needles=("select",)),
            "pred_sql_before_finsql_selector",
        )

    def test_legacy_substring_fallback(self):
        row = {"pred_sql_before_FINSQLSelector": "SELECT 1"}
        self.assertEqual(
            find_before_key(row, stage_id="missing_stage", needles=("select",)),
            "pred_sql_before_FINSQLSelector",
        )

    def test_no_match_returns_none(self):
        self.assertIsNone(find_before_key({"pred_sql": "SELECT 1"}, needles=("select",)))
        self.assertIsNone(find_before_key(None, needles=("select",)))


class KernelTests(unittest.TestCase):
    def test_linking_outcome_recall_precision(self):
        gold = {"db.users.name", "db.users.id"}
        pred = {"db.users.name", "db.orders.total"}
        outcome = linking_outcome(gold, pred)
        self.assertAlmostEqual(outcome["recall"], 0.5)
        self.assertAlmostEqual(outcome["precision"], 0.5)
        self.assertEqual(outcome["missing"], ["db.users.id"])

    def test_linking_outcome_containment_semantics(self):
        # a predicted table name counts toward the gold column it appears in
        gold = {"users.name"}
        pred = {"users"}
        outcome = linking_outcome(gold, pred)
        self.assertEqual(outcome["recall"], 1.0)
        self.assertEqual(outcome["precision"], 1.0)

    def test_selection_outcome_regret(self):
        outcome = selection_outcome([0, 1, 0], 0)
        self.assertEqual(outcome["oracle_ex"], 1)
        self.assertEqual(outcome["selection_loss"], 1)
        self.assertTrue(outcome["missed_correct"])

    def test_selection_outcome_first_numeric(self):
        outcome = selection_outcome([None, 1], 1)
        self.assertEqual(outcome["first_ex"], 1)
        self.assertEqual(outcome["selection_gain"], 0)

    def test_refinement_outcome(self):
        self.assertTrue(refinement_outcome(0, 1)["fix_success"])
        self.assertTrue(refinement_outcome(1, 0)["degradation"])
        self.assertIsNone(refinement_outcome(None, 1)["fix_success"])
        self.assertIsNone(refinement_outcome(1, None)["degradation"])


class GateFunnelTests(unittest.TestCase):
    def test_gate_outcome_orders_failures(self):
        no_sql = gate_outcome({"pred_sql": None, "ex": None, "exec_error": None})
        self.assertEqual(no_sql["failed_at"], "parseable")
        timeout = gate_outcome({"pred_sql": "SELECT 1", "exec_error": "query timed out", "ex": 0})
        self.assertEqual(timeout["failed_at"], "timely")
        exec_fail = gate_outcome({"pred_sql": "SELECT", "exec_error": "syntax error", "ex": 0})
        self.assertEqual(exec_fail["failed_at"], "executable")
        wrong = gate_outcome({"pred_sql": "SELECT 1", "exec_error": None, "ex": 0})
        self.assertEqual(wrong["failed_at"], "correct")

    def test_gate_funnel_rates(self):
        samples = [
            {"pred_sql": "SELECT 1", "exec_error": None, "ex": 1, "act_elapsed_s": 1.0},
            {"pred_sql": "SELECT 1", "exec_error": None, "ex": 1, "act_elapsed_s": 9.0},
            {"pred_sql": "SELECT 1", "exec_error": "no such table", "ex": 0},
            {"pred_sql": "SELECT 1", "exec_error": "execution timeout", "ex": 0},
            {"pred_sql": None, "ex": None},
        ]
        funnel = gate_funnel(samples, timeout_s=5.0)
        self.assertEqual(funnel["survival"]["parseable"], 0.8)
        self.assertEqual(funnel["survival"]["timely"], 0.6)
        self.assertEqual(funnel["survival"]["executable"], 0.4)
        self.assertEqual(funnel["survival"]["correct"], 0.4)
        self.assertEqual(funnel["survival"]["efficient"], 0.2)
        self.assertEqual(
            funnel["failed_at"],
            {"parseable": 1, "timely": 1, "executable": 1, "efficient": 1},
        )

    def test_gate_funnel_without_budget_skips_efficiency(self):
        funnel = gate_funnel([{"pred_sql": "SELECT 1", "exec_error": None, "ex": 1}])
        self.assertIsNone(funnel["survival"]["efficient"])


def _traced_sample(*, ex, fatal_miss=False, oracle=None, missed_correct=False):
    return {
        "ex": ex,
        "workflow": {
            "stages": {
                "m_parse": {
                    "task_type": "ParseTask",
                    "metrics": {"parse_recall": 0.9, "parse_precision": 0.8},
                    "signals": {"fatal_schema_miss": fatal_miss},
                },
                "m_generate": {
                    "task_type": "GenerateTask",
                    "signals": {
                        "oracle_ex": oracle, "first_ex": oracle,
                        "candidate_count": 2, "valid_sql_count": 2,
                    },
                },
                "m_select": {
                    "task_type": "SelectTask",
                    "signals": {"missed_correct_candidate": missed_correct},
                },
            },
        },
    }


class StageFunnelTests(unittest.TestCase):
    def test_survival_decreases_monotonically(self):
        samples = [
            _traced_sample(ex=1, oracle=1),
            _traced_sample(ex=0, fatal_miss=True, oracle=None),
            _traced_sample(ex=0, oracle=0),
            _traced_sample(ex=0, oracle=1, missed_correct=True),
        ]
        funnel = stage_survival_funnel(samples)
        survival = funnel["survival"]
        self.assertEqual(survival["linking"], 0.75)
        self.assertEqual(survival["generation"], 0.5)
        self.assertEqual(survival["selection"], 0.25)
        self.assertEqual(survival["final"], 0.25)


class ProcessSummaryTests(unittest.TestCase):
    def test_summary_reads_stage_metrics_and_pipeline(self):
        samples = [
            _traced_sample(ex=1, oracle=1),
            _traced_sample(ex=0, oracle=0),
        ]
        aggregate = {
            "pipeline": {
                "scaler": {"pass_1": 0.5, "pass_k": 0.7, "avg_candidate_diversity": 0.9},
                "optimizer": {"fix_success_rate": 0.2, "degradation_rate": 0.1,
                              "net_gain": 1, "avg_debug_turns": 1.5},
                "selector": {"selection_accuracy": 0.6, "selection_loss": 0.1},
                "decomposer": {"trigger_rate": 0.25, "trigger_accuracy": 0.8},
            },
        }
        summary = process_summary(samples, aggregate)
        self.assertAlmostEqual(summary["linking_recall"], 0.9)
        self.assertAlmostEqual(summary["linking_precision"], 0.8)
        self.assertEqual(summary["linking_fatal_miss_rate"], 0.0)
        self.assertEqual(summary["generation_pass1"], 0.5)
        self.assertEqual(summary["generation_oracle_k"], 0.7)
        self.assertEqual(summary["generation_exec_validity"], 1.0)
        self.assertEqual(summary["refinement_fix_rate"], 0.2)
        self.assertEqual(summary["selection_regret"], 0.1)
        self.assertEqual(summary["selection_missed_correct_rate"], 0.0)
        self.assertEqual(summary["decomposition_trigger_rate"], 0.25)


class PipelineDeltaIntegrationTests(unittest.TestCase):
    def test_snapshot_keys_resolve_without_substring_guessing(self):
        row = {
            "pred_sql_before_chess_optimizer": "SELECT 1",
            "pred_sql": "SELECT 2",
        }
        delta = compute_pipeline_delta(row, dataset=None)
        self.assertTrue(delta["optimizer"]["has_optimizer"])
        self.assertFalse(delta["selector"]["has_selector"])
        self.assertFalse(delta["scaler"]["has_scaler"])


if __name__ == "__main__":
    unittest.main()
