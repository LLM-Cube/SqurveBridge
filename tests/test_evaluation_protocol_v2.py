from __future__ import annotations

import json
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from reproduce.eval.protocol import (
    Candidate,
    CohortDescriptor,
    ComparisonPolicy,
    EvaluationBundleV2,
    MetricResult,
    NativeAdapterSpec,
    NativeEvaluatorRegistry,
    SQLExecutor,
    TokenObservation,
    aggregate_token_observations,
    attribute_execution,
    compare_bundles,
    compare_results,
    evaluate_checkpoint,
    evaluate_component_transition,
    evaluate_decomposition,
    evaluate_scaling,
    evaluate_selection,
    aggregate_component_transitions,
    self_consistency,
    soft_f1,
    structural_analysis,
    structural_match,
    register_builtin_spider_test_suite,
)
from reproduce.eval.bundle.schema import bundle_version, validate_bundle
from reproduce.eval.bundle.compare import compare_scores
from reproduce.eval.report import format_v2_report
from reproduce.eval.views.evidence import sanitized_v2_export
from reproduce.eval.views.store import load_v2_run_metric, persist_eval_store
from reproduce.eval.views.matrix import MethodMatrix, matrix_report
from reproduce.eval.views.fitness import bundle_fitness


class EvaluationProtocolV2Tests(unittest.TestCase):
    def test_mixed_bundle_versions_are_reported_as_incompatible(self):
        v1 = {"run_id": "legacy"}
        v2 = EvaluationBundleV2(
            run_id="typed", dataset="d", split="s", method="m", samples=[],
        ).model_dump(mode="json", by_alias=True)

        comparison = compare_scores(v1, v2)

        self.assertEqual(comparison["status"], "incompatible_bundle_schema")
        self.assertEqual(comparison["schema_versions"], {"previous": 1, "current": 2})

    def test_metric_contract_rejects_invalid_values_and_counts(self):
        with self.assertRaises(ValidationError):
            ComparisonPolicy(absolute_tolerance=float("inf"))
        with self.assertRaises(ValidationError):
            MetricResult(
                metric_id="ex_at_1", protocol_id="project.ex", protocol_version="2",
                status="available", value=1.2, numerator=1, denominator=1,
                coverage=1.0, candidate_ids=["c1"], value_domain="unit_interval",
            )
        with self.assertRaises(ValidationError):
            MetricResult(
                metric_id="ex_at_1", protocol_id="project.ex", protocol_version="2",
                status="available", value=1.0, numerator=0, denominator=1,
                coverage=1.0, candidate_ids=["c1"], value_domain="unit_interval",
            )
        with self.assertRaises(ValidationError):
            MetricResult(
                metric_id="ex_at_1", protocol_id="project.ex", protocol_version="2",
                status="available", value=1.0, coverage=0.2,
                candidate_ids=["c1"], value_domain="unit_interval",
            )
        with self.assertRaises(ValidationError):
            MetricResult(
                metric_id="ex_at_1", protocol_id="project.ex", protocol_version="2",
                status="available", value=1.0, numerator=2, denominator=1,
                coverage=1.0, candidate_ids=["c1"], value_domain="unit_interval",
            )

    def test_comparison_preserves_rows_duplicates_and_global_column_mapping(self):
        policy = ComparisonPolicy(row_order="ignore", column_order="map_one_to_one")
        self.assertTrue(compare_results([(1, "a"), (2, "b")], [("a", 1), ("b", 2)], policy).equal)
        self.assertFalse(compare_results([(1, "a"), (2, "b")], [("a", 1), ("a", 2)], policy).equal)
        self.assertFalse(compare_results([(1,), (1,)], [(1,)], policy).equal)

    def test_comparison_policy_covers_null_empty_tolerance_and_extra_columns(self):
        policy = ComparisonPolicy(row_order="ignore", column_order="preserve",
                                  absolute_tolerance=0.01, relative_tolerance=0,
                                  extra_columns="reject")
        self.assertTrue(compare_results([(None, 1.005)], [(None, 1.0)], policy).equal)
        self.assertTrue(compare_results([], [], policy).equal)
        self.assertFalse(compare_results([], [], policy, predicted_columns=["x", "y"],
                                         gold_columns=["x"]).equal)
        self.assertFalse(compare_results([(1, 2)], [(1,)], policy).equal)

    def test_column_mapping_search_bounds_partial_assignments(self):
        policy = ComparisonPolicy(column_order="map_one_to_one", max_column_mappings=2)
        result = compare_results([(1, 2, 3)], [(1, 2, 3)], policy)
        self.assertFalse(result.equal)
        self.assertEqual(result.reason, "column_mapping_search_limit")

    def test_checkpoint_sqlite_produces_three_distinct_candidate_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "db.sqlite"
            with sqlite3.connect(db) as conn:
                conn.execute("create table t(x integer)")
                conn.executemany("insert into t values (?)", [(1,), (2,)])
            checkpoint = root / "checkpoint.json"
            checkpoint.write_text(json.dumps({
                "run_id": "r1", "dataset": "synthetic", "split": "dev", "method": "m",
                "samples": [{
                    "instance_id": "s1", "db_path": str(db), "gold_sql": "select x from t order by x",
                    "candidates": [
                        {"candidate_id": "c1", "sql": "select x from t where x=9"},
                        {"candidate_id": "c2", "sql": "select x from t order by x"},
                    ],
                    "selected_candidate_id": "c2",
                }],
            }), encoding="utf-8")
            bundle = evaluate_checkpoint(checkpoint, {"comparison": {"row_order": "preserve"}})
            self.assertEqual(bundle.schema_version, 2)
            sample = bundle.samples[0]
            self.assertEqual(sample.metrics["ex_at_1"].value, 0.0)
            self.assertEqual(sample.metrics["selected_ex"].value, 1.0)
            self.assertEqual(sample.metrics["observed_oracle_at_k"].value, 1.0)
            self.assertEqual(sample.metrics["observed_oracle_at_k"].actual_k, 2)
            self.assertEqual(sample.metrics["selected_ex"].candidate_ids, ["c2"])
            payload = bundle.model_dump(mode="json", by_alias=True)
            self.assertEqual(validate_bundle(payload), [])
            store = root / "eval.sqlite"
            persist_eval_store(payload, store)
            self.assertEqual(load_v2_run_metric(store, "r1", "selected_ex"), {"s1": 1.0})
            exported = sanitized_v2_export(payload)
            serialized = json.dumps(exported)
            self.assertNotIn("select x", serialized.lower())
            self.assertNotIn(str(root), serialized)
            self.assertIn("coverage=1.000", format_v2_report(payload))
            self.assertGreater(bundle_fitness(payload), 0)

    def test_checkpoint_consumes_schema_runtime_and_component_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "db.sqlite"
            with sqlite3.connect(db) as conn:
                conn.execute("create table t(x integer)")
                conn.execute("insert into t values (1)")
            checkpoint = root / "checkpoint.json"
            checkpoint.write_text(json.dumps({
                "run_id": "rich", "dataset": "synthetic", "split": "dev", "method": "m",
                "samples": [{
                    "instance_id": "s1", "db_path": str(db), "gold_sql": "select x from t",
                    "schema": {"t": ["x"]},
                    "candidates": [
                        {"candidate_id": "c1", "sql": "select missing from t"},
                        {"candidate_id": "c2", "sql": "select x from t"},
                    ],
                    "selected_candidate_id": "c2",
                    "runtime_evidence": {
                        "actor_time_s": 99,
                        "timings": [
                            {"operation_id": "actor-1", "scope": "actor_inclusive",
                             "duration_s": 0.4, "inclusive": True, "clock": "monotonic",
                             "status": "complete"},
                            {"operation_id": "workflow-1", "scope": "workflow_end_to_end",
                             "duration_s": 0.8, "inclusive": True, "clock": "monotonic",
                             "status": "complete"},
                        ],
                        "token_usage": [{"stage_id": "generator", "logical_call_id": "call-1",
                                         "scope": "logical_call", "prompt_tokens": 10,
                                         "completion_tokens": 2, "coverage": 1,
                                         "source": "provider"}],
                    },
                    "component_observations": [
                        {"kind": "transition", "stage_id": "optimizer", "task_id": "task-1",
                         "iteration": 0, "candidate_id": "c2", "before_correct": False,
                         "after_correct": True, "before_checkpoint": "cp-1",
                         "after_checkpoint": "cp-2"},
                        {"kind": "selection", "stage_id": "selector", "task_id": "task-2",
                         "iteration": 1, "candidate_outcomes": {"c1": False, "c2": True},
                         "selected_candidate_id": "c2"},
                        {"kind": "decomposition", "stage_id": "decomposer", "task_id": "task-3",
                         "iteration": 2, "triggered": True, "plan_ref": "artifact:plan",
                         "runtime_task_ids": ["task-4"]},
                    ],
                }],
            }), encoding="utf-8")
            bundle = evaluate_checkpoint(checkpoint, {})
            sample = bundle.samples[0]
            self.assertEqual(sample.analyses["c2"]["schema_resolution"], "resolved")
            self.assertEqual(sample.analyses["c1"]["schema_resolution"], "unresolved")
            self.assertEqual(sample.metrics["token_total"].value, 12)
            self.assertEqual(sample.metrics["actor_time"].value, 0.4)
            self.assertEqual(sample.metrics["workflow_time"].value, 0.8)
            self.assertEqual(sample.metrics["refinement_fix_rate"].value, 1.0)
            self.assertEqual(sample.metrics["selection_regret"].value, 0.0)
            self.assertEqual(sample.metrics["decomposition_trigger_rate"].value, 1.0)
            self.assertEqual(len(sample.component_observations), 3)
            self.assertEqual(sample.execution_results[0]["stage_id"], "evaluation.gold")
            self.assertEqual(sample.execution_results[1]["stage_id"], "evaluation.prediction")
            self.assertNotEqual(sample.execution_results[1]["database_snapshot"], "unknown")
            self.assertIn("analysis:", sample.attribution[0]["supporting_evidence_refs"][1])
            summary = bundle.provenance["attribution_summary"]
            self.assertEqual(summary["outcome_distribution"], {"success": 1})
            self.assertEqual(summary["failure_only_bottleneck_distribution"], {})
            self.assertEqual(bundle.aggregate["error_root_distribution"].categorical_counts,
                             summary["failure_only_bottleneck_distribution"])
            from reproduce.eval.registry import default_registry
            self.assertTrue({spec.id for spec in default_registry().metrics()}.issubset(bundle.aggregate))
            report = format_v2_report(bundle.model_dump(mode="json", by_alias=True))
            self.assertIn("failure-only bottlenecks", report)
            self.assertIn("component observations", report)

    def test_unversioned_bundle_dispatches_to_v1(self):
        self.assertEqual(bundle_version({"run_id": "legacy"}), 1)

    def test_execution_failure_and_missing_gold_remain_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "db.sqlite"
            with sqlite3.connect(db) as conn:
                conn.execute("create table t(x)")
            checkpoint = root / "checkpoint.json"
            checkpoint.write_text(json.dumps({
                "run_id": "r", "dataset": "d", "split": "s", "method": "m",
                "samples": [{"instance_id": "i", "db_path": str(db),
                             "candidates": [{"candidate_id": "bad", "sql": "select nope from t"}],
                             "selected_candidate_id": "bad"}],
            }), encoding="utf-8")
            sample = evaluate_checkpoint(checkpoint, {}).samples[0]
            statuses = {item["candidate_id"]: item["status"] for item in sample.execution_results}
            self.assertEqual(statuses["__gold__"], "ground_truth_unavailable")
            self.assertEqual(statuses["bad"], "execution_error")
            self.assertEqual(sample.metrics["selected_ex"].status, "unavailable")
            self.assertEqual(sample.metrics["selected_ex"].failed_count, 1)

    def test_oracle_zero_requires_complete_candidate_outcomes(self):
        class PartialExecutor(SQLExecutor):
            def execute_sqlite(self, *args, **kwargs):
                result = super().execute_sqlite(*args, **kwargs)
                if kwargs.get("candidate_id") == "unknown":
                    return result.model_copy(update={"preservation_complete": False})
                return result

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "db.sqlite"
            with sqlite3.connect(db) as conn:
                conn.execute("create table t(x)")
                conn.execute("insert into t values (1)")
            checkpoint = root / "checkpoint.json"
            checkpoint.write_text(json.dumps({
                "run_id": "r", "dataset": "d", "split": "s", "method": "m",
                "samples": [{"instance_id": "i", "db_path": str(db),
                             "gold_sql": "select x from t",
                             "candidates": [{"candidate_id": "wrong", "sql": "select x from t where 0"},
                                            {"candidate_id": "unknown", "sql": "select x from t"}],
                             "selected_candidate_id": "wrong"}],
            }), encoding="utf-8")
            metric = evaluate_checkpoint(checkpoint, {}, executor=PartialExecutor()).samples[0].metrics[
                "observed_oracle_at_k"]
            self.assertEqual(metric.status, "partial")
            self.assertIsNone(metric.value)
            self.assertEqual(metric.coverage, 0.5)

    def test_partial_fetch_cannot_satisfy_strict_comparison(self):
        executor = SQLExecutor()
        result = executor.missing_input("r", "s", "c")
        self.assertEqual(result.status, "missing_input")
        self.assertFalse(result.comparable)

    def test_sc_keeps_failed_candidates_in_denominator(self):
        ok1 = SQLExecutor().missing_input("r", "s", "bad")
        ok1 = ok1.model_copy(update={"status": "success", "rows": [(1,)], "execution_complete": True,
                                     "fetch_complete": True, "preservation_complete": True,
                                     "candidate_id": "c1"})
        ok2 = ok1.model_copy(update={"candidate_id": "c2", "execution_id": "e2"})
        bad = SQLExecutor().missing_input("r", "s", "c3")
        metric = self_consistency([ok1, ok2, bad], ComparisonPolicy())
        self.assertEqual(metric.denominator, 3)
        self.assertEqual(metric.numerator, 1)
        self.assertAlmostEqual(metric.coverage, 1 / 3)

    def test_soft_f1_requires_complete_results(self):
        missing = SQLExecutor().missing_input("r", "s", "c")
        metric = soft_f1(missing, missing, candidate_id="c", policy=ComparisonPolicy())
        self.assertEqual(metric.status, "unavailable")
        self.assertEqual(metric.reason, "complete_results_required")

    def test_token_missing_is_not_zero(self):
        usage = TokenObservation(stage_id="g", logical_call_id="l", scope="logical_call",
                                 coverage=0, source="provider", reason="not_reported")
        self.assertIsNone(usage.total_tokens)

    def test_token_aggregation_does_not_double_count_attempt_and_logical_scope(self):
        observations = [
            TokenObservation(stage_id="g", logical_call_id="l", attempt_id="a1", scope="attempt",
                             prompt_tokens=10, completion_tokens=2, coverage=1, source="provider"),
            TokenObservation(stage_id="g", logical_call_id="l", scope="logical_call",
                             prompt_tokens=10, completion_tokens=2, coverage=1, source="provider"),
        ]
        total, coverage, provenance = aggregate_token_observations(observations)
        self.assertEqual(total, 12)
        self.assertEqual(coverage, 1)
        self.assertEqual(provenance["logical_call_scopes_used"], 1)

    def test_attribution_does_not_infer_success_from_missing_evidence(self):
        result = SQLExecutor().missing_input("r", "s", "c")
        attribution = attribute_execution(result, parse_success=None, schema_resolution=None)
        self.assertIsNone(attribution.primary_label)
        self.assertIn("parse_status", attribution.unknown_items)

        comparable = result.model_copy(update={"status": "success", "rows": [(0,)],
                                               "execution_complete": True, "fetch_complete": True,
                                               "preservation_complete": True})
        mismatch = attribute_execution(comparable, parse_success=True, schema_resolution="resolved",
                                       execution_correct=False)
        self.assertEqual(mismatch.primary_label, "result_mismatch")

    def test_analysis_preserves_structural_details(self):
        analysis = structural_analysis(
            "select a.x, count(*) from a left join b on a.id=b.aid "
            "where a.x >= 2 group by a.x order by a.x desc limit 3", "sqlite"
        )
        self.assertTrue(analysis.parse_success)
        self.assertEqual(analysis.joins[0].kind, "LEFT")
        self.assertEqual(analysis.order_by[0].direction, "DESC")
        self.assertEqual(analysis.limit, 3)
        self.assertIn("a.x", analysis.column_references)

    def test_schema_resolution_uses_alias_and_exact_column_identity(self):
        analysis = structural_analysis("select u.id, name from users u", "sqlite",
                                       schema={"users": {"id", "username"}, "teams": {"name"}})
        self.assertIn("users.id", analysis.column_references)
        self.assertIn("name", analysis.unresolved_references)
        self.assertNotIn("users.username", analysis.column_references)

    def test_nested_aliases_resolve_in_their_own_scope(self):
        analysis = structural_analysis(
            "select u.id from users u where exists (select 1 from teams u where u.id=1)",
            "sqlite", schema={"users": {"id"}, "teams": {"id"}},
        )
        self.assertEqual(analysis.column_references, ["teams.id", "users.id"])

    def test_set_operation_and_derived_scope_semantics_are_preserved(self):
        distinct = structural_analysis("select x from t union select x from t", "sqlite")
        all_rows = structural_analysis("select x from t union all select x from t", "sqlite")
        self.assertIn("UNION DISTINCT", distinct.set_operations)
        self.assertIn("UNION ALL", all_rows.set_operations)
        self.assertFalse(structural_match(distinct, all_rows))

        cte = structural_analysis("with q as (select x from t) select q.x from q", "sqlite",
                                  schema={"t": {"x"}})
        derived = structural_analysis("select s.x from (select x from t) s", "sqlite",
                                      schema={"t": {"x"}})
        self.assertEqual(cte.schema_resolution, "resolved")
        self.assertEqual(derived.schema_resolution, "resolved")
        self.assertNotIn("q.x", cte.unresolved_references)
        self.assertNotIn("s.x", derived.unresolved_references)

    def test_component_transition_is_bound_to_immediate_pair(self):
        metric = evaluate_component_transition(
            stage_id="opt-1", task_id="task-1", instance_id="sample-1",
            candidate_id="c1", iteration=0,
            before_correct=False, after_correct=True,
            before_checkpoint="cp1", after_checkpoint="cp2",
        )
        self.assertEqual(metric.wrong_to_right, 1)
        self.assertEqual(metric.net_ex_change, 1.0)
        unknown = evaluate_component_transition(
            stage_id="opt-2", task_id="task-1", instance_id="sample-1",
            candidate_id="c1", iteration=1,
            before_correct=False, after_correct=True, before_checkpoint=None, after_checkpoint="cp3",
        )
        self.assertEqual(unknown.status, "unknown")
        unknown_correctness = evaluate_component_transition(
            stage_id="opt-3", task_id="task-1", instance_id="sample-1",
            candidate_id="c1", iteration=2, before_correct=None, after_correct=True,
            before_checkpoint="cp3", after_checkpoint="cp4",
        )
        self.assertEqual(unknown_correctness.status, "unknown")
        self.assertIsNone(unknown_correctness.wrong_to_right)
        with self.assertRaises(ValueError):
            evaluate_component_transition(
                stage_id="opt-4", task_id="task-1", instance_id="sample-1",
                candidate_id="c1", iteration=3, before_correct="false", after_correct=True,
                before_checkpoint="cp4", after_checkpoint="cp5",
            )

    def test_component_transition_summary_uses_conditional_denominators(self):
        observations = [
            evaluate_component_transition(
                stage_id=f"opt-{index}", task_id="task", instance_id="sample",
                candidate_id="c", iteration=index, before_correct=before,
                after_correct=after, before_checkpoint=f"cp-{index}",
                after_checkpoint=f"cp-{index + 1}",
            )
            for index, (before, after) in enumerate(((False, True), (False, False), (True, False)))
        ]
        observations.append(evaluate_component_transition(
            stage_id="opt-unknown", task_id="task", instance_id="sample",
            candidate_id="c", iteration=3, before_correct=True, after_correct=True,
            before_checkpoint=None, after_checkpoint="cp-4",
        ))
        summary = aggregate_component_transitions(observations)
        self.assertEqual(summary.eligible_pairs, 3)
        self.assertEqual(summary.unknown_pairs, 1)
        self.assertEqual(summary.wrong_to_right, 1)
        self.assertEqual(summary.right_to_wrong, 1)
        self.assertEqual(summary.fix_rate_given_wrong, 0.5)
        self.assertEqual(summary.degradation_rate_given_correct, 1.0)
        self.assertEqual(summary.normalized_net_ex_change, 0.0)

    def test_selection_scaling_and_decomposition_keep_distinct_l5_facts(self):
        selection = evaluate_selection(
            {"c1": True, "c2": False, "c3": None}, "c2",
            stage_id="selector", task_id="task", instance_id="sample", iteration=0,
        )
        self.assertEqual(selection.input_oracle, 1.0)
        self.assertEqual(selection.regret, 1.0)
        self.assertEqual(selection.missed_correct, 1)
        self.assertEqual(selection.known_candidate_count, 2)
        self.assertEqual(selection.regret_denominator, 1)

        scaling = evaluate_scaling(
            {"c1": "select 1", "c2": "select 2"},
            {"c1": "select 1", "c3": "SELECT 1"},
            {"c1": True, "c2": False}, {"c1": True, "c3": True},
            structural_keys={"c1": "one", "c3": "one"},
            stage_id="scaler", task_id="task", instance_id="sample", iteration=1,
        )
        self.assertEqual(scaling.surviving_correct_count, 1)
        self.assertEqual(scaling.gained_correct_count, 1)
        self.assertEqual(scaling.textual_diversity, 1.0)
        self.assertEqual(scaling.structural_diversity, 0.5)

        decomposition = evaluate_decomposition(
            triggered=True, plan_ref="artifact:plan", runtime_task_ids=["subtask-1"],
            stage_id="decomposer", task_id="task", instance_id="sample", iteration=2,
        )
        self.assertEqual(decomposition.interpretation, "observed_trigger_only")
        self.assertFalse(hasattr(decomposition, "benefit"))

    def test_native_adapter_is_default_off_and_resources_are_verified(self):
        registry = NativeEvaluatorRegistry()
        registry.register(NativeAdapterSpec(
            adapter_id="known", benchmark="synthetic", dataset="synthetic", split="dev",
            upstream_revision="abc123", license_status="compatible",
            resource_checksums={"fixture": "deadbeef"},
        ), lambda _: 1.0)
        result = registry.evaluate("known", {}, enabled=False, available_resources={})
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.reason, "adapter_disabled")
        result = registry.evaluate("known", {}, enabled=True, available_resources={})
        self.assertEqual(result.reason, "resource_missing:fixture")

    def test_vendored_spider_test_suite_known_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, values in (("suite1.sqlite", [(0,), (1,)]), ("suite2.sqlite", [(0.5,), (2,)])):
                with sqlite3.connect(root / name) as conn:
                    conn.execute("create table t(x integer)")
                    conn.executemany("insert into t values (?)", values)
            registry = NativeEvaluatorRegistry()
            suite_checksums = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in root.glob("*.sqlite")
            }
            register_builtin_spider_test_suite(registry, resource_checksums=suite_checksums)
            result = registry.evaluate(
                "spider_test_suite",
                {"database_path": str(root / "suite1.sqlite"),
                 "dataset": "spider", "split": "dev",
                 "gold_sql": "select x from t where x > 0",
                 "predicted_sql": "select x from t where x > 0"},
                enabled=True,
                available_resources={path.name: str(path) for path in root.glob("*.sqlite")},
            )
            self.assertEqual(result.value, 1.0)
            self.assertEqual(result.protocol_version, "e97acc546ecbee8fa27fa8dbf025ef61493a876c")
            suite_only_failure = registry.evaluate(
                "spider_test_suite",
                {"database_path": str(root / "suite1.sqlite"),
                 "dataset": "spider", "split": "dev",
                 "gold_sql": "select x from t where x > 0",
                 "predicted_sql": "select x from t where x >= 1"},
                enabled=True,
                available_resources={path.name: str(path) for path in root.glob("*.sqlite")},
            )
            self.assertEqual(suite_only_failure.value, 0.0)
            incomplete = registry.evaluate(
                "spider_test_suite",
                {"database_path": str(root / "suite1.sqlite"),
                 "dataset": "spider", "split": "dev",
                 "gold_sql": "select x from t", "predicted_sql": "select x from t"},
                enabled=True, available_resources={"suite1.sqlite": str(root / "suite1.sqlite")},
            )
            self.assertEqual(incomplete.reason, "resource_missing:suite2.sqlite")
            wrong_dataset = registry.evaluate(
                "spider_test_suite",
                {"database_path": str(root / "suite1.sqlite"),
                 "dataset": "bird", "split": "dev",
                 "gold_sql": "select x from t", "predicted_sql": "select x from t"},
                enabled=True,
                available_resources={path.name: str(path) for path in root.glob("*.sqlite")},
            )
            self.assertEqual(wrong_dataset.reason, "dataset_mismatch")

    def test_l6_store_requires_explicit_compatible_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "db.sqlite"
            with sqlite3.connect(db) as conn:
                conn.execute("create table t(x)")
                conn.execute("insert into t values (1)")
            checkpoint = root / "checkpoint.json"
            checkpoint.write_text(json.dumps({
                "run_id": "r1", "dataset": "d", "split": "dev", "method": "a",
                "database_snapshot": "snap", "model_config_id": "model",
                "samples": [{"instance_id": "s", "db_path": str(db), "gold_sql": "select x from t",
                             "candidates": [{"candidate_id": "c", "sql": "select x from t"}],
                             "selected_candidate_id": "c"}],
            }), encoding="utf-8")
            first = evaluate_checkpoint(checkpoint, {"measure_efficiency": True})
            second = first.model_copy(update={"run_id": "r2", "method": "b"})
            store = root / "store.sqlite"
            persist_eval_store(first.model_dump(mode="json", by_alias=True), store)
            persist_eval_store(second.model_dump(mode="json", by_alias=True), store)
            matrix = MethodMatrix.from_v2_runs(store, ["r1", "r2"])
            report = matrix_report(matrix)
            self.assertEqual(report["cohort"]["common_set_size"], 1)
            self.assertEqual(report["cohort"]["latency_coverage"], {"a": 1.0, "b": 1.0})
            self.assertEqual(report["cohort"]["allowed_differences"], [])
            with sqlite3.connect(store) as conn:
                conn.execute("UPDATE metric_results_v2 SET protocol_version='different' "
                             "WHERE run_id='r2' AND metric_id='selected_ex'")
            with self.assertRaisesRegex(ValueError, "selected_ex_protocol_mismatch"):
                MethodMatrix.from_v2_runs(store, ["r1", "r2"])

    def test_execution_cache_never_crosses_database_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            for index in (1, 2):
                db = root / f"db{index}.sqlite"
                with sqlite3.connect(db) as conn:
                    conn.execute("create table t(x)")
                    conn.execute("insert into t values (?)", (index,))
                paths.append(db)
            checkpoint = root / "checkpoint.json"
            checkpoint.write_text(json.dumps({
                "run_id": "r", "dataset": "d", "split": "s", "method": "m",
                "samples": [{"instance_id": str(i), "db_path": str(db), "gold_sql": "select x from t",
                             "candidates": [{"candidate_id": f"c{i}", "sql": "select x from t"}],
                             "selected_candidate_id": f"c{i}"} for i, db in enumerate(paths)],
            }), encoding="utf-8")
            bundle = evaluate_checkpoint(checkpoint, {})
            second_rows = bundle.samples[1].execution_results[0]["rows"]
            self.assertEqual(second_rows, [[2]])

    def test_sanitized_export_drops_arbitrary_sensitive_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "db.sqlite"
            with sqlite3.connect(db) as conn:
                conn.execute("create table t(x)")
            checkpoint = root / "checkpoint.json"
            checkpoint.write_text(json.dumps({
                "run_id": "r", "dataset": "d", "split": "s", "method": "m",
                "samples": [{"instance_id": "i", "db_path": str(db), "gold_sql": "select x from t",
                             "candidates": [{"candidate_id": "c", "sql": "select x from t"}],
                             "selected_candidate_id": "c"}],
            }), encoding="utf-8")
            payload = evaluate_checkpoint(checkpoint, {}).model_dump(mode="json", by_alias=True)
            private_path = "/" + "Users" + "/example/private"
            payload["samples"][0]["metrics"]["selected_ex"]["provenance"] = {
                "raw_sql": "select secret from private", "path": private_path,
            }
            exported = json.dumps(sanitized_v2_export(payload)).lower()
            self.assertNotIn("select secret", exported)
            self.assertNotIn(private_path.lower(), exported)
            self.assertNotIn('"instance_id": "i"', exported)
            self.assertNotIn('"candidate_ids": ["c"]', exported)

    def test_correctness_execution_reuses_but_efficiency_bypasses_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite"
            with sqlite3.connect(db) as conn:
                conn.execute("create table t(x)")
            ticks = iter(float(i) for i in range(100))
            executor = SQLExecutor(clock=lambda: next(ticks))
            executor.execute_sqlite(str(db), "select x from t", run_id="r", instance_id="s",
                                    candidate_id="c1", database_snapshot="snap")
            reused = executor.execute_sqlite(str(db), "select x from t", run_id="r", instance_id="s",
                                             candidate_id="c2", database_snapshot="snap")
            measured = executor.execute_sqlite(str(db), "select x from t", run_id="r", instance_id="s",
                                               candidate_id="c3", database_snapshot="snap",
                                               efficiency_measurement=True)
            self.assertIn("reused", reused.execution_id)
            self.assertNotIn("reused", measured.execution_id)

    def test_execution_cache_excludes_failures_and_nondeterministic_sql(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "db.sqlite"
            with sqlite3.connect(db) as conn:
                conn.execute("create table t(x)")
            executor = SQLExecutor()
            first = executor.execute_sqlite(
                str(db), "select missing from t", run_id="r", instance_id="s",
                candidate_id="c1", database_snapshot="snap",
            )
            second = executor.execute_sqlite(
                str(db), "select missing from t", run_id="r", instance_id="s",
                candidate_id="c2", database_snapshot="snap",
            )
            self.assertEqual(first.status, "execution_error")
            self.assertIsNone(second.reused_from_execution_id)
            self.assertIsNone(SQLExecutor.reuse_key(
                "select randomblob(4)", "snap", "sqlite", "local", "default"))
            self.assertIsNone(SQLExecutor.reuse_key(
                "select date('now')", "snap", "sqlite", "local", "default"))
            self.assertIsNone(SQLExecutor.reuse_key(
                "select strftime('%s', 'now')", "snap", "sqlite", "local", "default"))

    def test_checkpoint_hashes_database_without_reading_it_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "db.sqlite"
            with sqlite3.connect(db) as conn:
                conn.execute("create table t(x)")
            checkpoint = root / "checkpoint.json"
            checkpoint.write_text(json.dumps({
                "run_id": "r", "dataset": "d", "split": "s", "method": "m",
                "samples": [{"instance_id": "i", "db_path": str(db),
                             "gold_sql": "select x from t",
                             "candidates": [{"candidate_id": "c", "sql": "select x from t"}],
                             "selected_candidate_id": "c"}],
            }), encoding="utf-8")
            with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read")):
                bundle = evaluate_checkpoint(checkpoint, {})
            self.assertNotEqual(bundle.database_snapshot, "unknown")

    def test_database_snapshot_binds_database_content_to_sample_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            databases = []
            for value in (1, 2):
                db = root / f"db-{value}.sqlite"
                with sqlite3.connect(db) as conn:
                    conn.execute("create table t(x)")
                    conn.execute("insert into t values (?)", (value,))
                databases.append(db)

            def bundle_for(run_id, paths):
                checkpoint = root / f"{run_id}.json"
                checkpoint.write_text(json.dumps({
                    "run_id": run_id, "dataset": "d", "split": "s", "method": "m",
                    "database_snapshot": "declared",
                    "samples": [
                        {"instance_id": instance_id, "db_path": str(db),
                         "gold_sql": "select x from t",
                         "candidates": [{"candidate_id": f"c-{instance_id}",
                                         "sql": "select x from t"}],
                         "selected_candidate_id": f"c-{instance_id}"}
                        for instance_id, db in zip(("a", "b"), paths)
                    ],
                }), encoding="utf-8")
                return evaluate_checkpoint(checkpoint, {})

            original = bundle_for("original", databases)
            swapped = bundle_for("swapped", list(reversed(databases)))
            self.assertNotEqual(original.database_snapshot, swapped.database_snapshot)

    def test_cohort_comparison_rejects_snapshot_mismatch(self):
        a = CohortDescriptor(dataset="d", split="s", database_snapshot="one", sample_ids=["1"],
                             candidate_protocol="first", evaluator_protocol="p@1", model_config="m")
        b = a.model_copy(update={"database_snapshot": "two"})
        result = compare_bundles(a, b)
        self.assertFalse(result.compatible)
        self.assertIn("database_snapshot_mismatch", result.reasons)
        with self.assertRaises(ValueError):
            compare_bundles(a, b, allowed_differences={"database_snapshopt"})

    def test_bundle_rejects_candidate_identity_mismatch(self):
        candidate = Candidate(candidate_id="c1", occurrence_id="o1", sql="select 1", ordinal=0)
        metric = MetricResult(
            metric_id="selected_ex", protocol_id="project.ex", protocol_version="2",
            status="available", value=1, numerator=1, denominator=1, coverage=1,
            candidate_ids=["unknown"], value_domain="unit_interval",
        )
        with self.assertRaises(ValidationError):
            EvaluationBundleV2.minimal("r", "d", "s", "m", "i", [candidate], {"selected_ex": metric})

    def test_minimal_bundle_builds_consistent_aggregate_and_rejects_duplicate_samples(self):
        candidate = Candidate(candidate_id="c1", occurrence_id="o1", sql="select 1", ordinal=0)
        metric = MetricResult(
            metric_id="selected_ex", protocol_id="project.ex", protocol_version="2",
            status="available", value=1, numerator=1, denominator=1, coverage=1,
            candidate_ids=["c1"], candidate_scope="selected", value_domain="unit_interval",
        )
        bundle = EvaluationBundleV2.minimal(
            "r", "d", "s", "m", "i", [candidate], {"selected_ex": metric},
            selected_candidate_id="c1",
        )
        self.assertEqual(bundle.aggregate["selected_ex"].value, 1.0)
        duplicate = bundle.samples[0].model_copy()
        with self.assertRaises(ValidationError):
            EvaluationBundleV2(
                run_id="r", dataset="d", split="s", method="m",
                samples=[bundle.samples[0], duplicate], aggregate=bundle.aggregate,
            )
        with self.assertRaises(ValidationError):
            EvaluationBundleV2(
                run_id="r", dataset="d", split="s", method="m", samples=[],
                aggregate={"wrong-key": metric},
            )

    def test_minimal_bundle_supports_categorical_metrics(self):
        candidate = Candidate(candidate_id="c1", occurrence_id="o1", sql="select 1", ordinal=0)
        metric = MetricResult(
            metric_id="labels", protocol_id="project.labels", protocol_version="2",
            status="available", categorical_counts={"success": 1}, coverage=1,
        )
        bundle = EvaluationBundleV2.minimal(
            "r", "d", "s", "m", "i", [candidate], {"labels": metric},
        )
        self.assertEqual(bundle.aggregate["labels"].categorical_counts, {"success": 1})

    def test_l4_distribution_is_unavailable_without_a_selected_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "db.sqlite"
            with sqlite3.connect(db) as conn:
                conn.execute("create table t(x)")
            checkpoint = root / "checkpoint.json"
            checkpoint.write_text(json.dumps({
                "run_id": "r", "dataset": "d", "split": "s", "method": "m",
                "samples": [{"instance_id": "i", "db_path": str(db),
                             "gold_sql": "select x from t", "candidates": []}],
            }), encoding="utf-8")
            bundle = evaluate_checkpoint(checkpoint, {})
            self.assertEqual(bundle.aggregate["stage_attribution"].status, "unavailable")
            self.assertEqual(bundle.aggregate["stage_attribution"].coverage, 0)


if __name__ == "__main__":
    unittest.main()
