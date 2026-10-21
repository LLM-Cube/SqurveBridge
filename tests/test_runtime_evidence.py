"""Behavioral acceptance at existing Squrve entry points and the evidence reader."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.actor.base import Actor
from core.evidence import (
    AcceptanceProfile,
    Availability,
    ConditionValue,
    CapabilityProfile,
    CollectionConfig,
    EvidenceRun,
    evaluate_metric_dependencies,
    evaluate_evidence,
    acceptance_gate,
    aggregate_usage,
    read_evidence,
)
from concurrent.futures import ThreadPoolExecutor

from core.evidence import submit_with_evidence


class EchoActor(Actor):
    NAME = "echo"
    OUTPUT_NAME = "answer"
    STRATEGY = "overwrite"

    def act(self, item, **kwargs):
        return {"answer": item + 1}


class RuntimeEvidenceTests(unittest.TestCase):
    def test_profiles_and_metric_dependencies_are_versioned(self):
        profile = CapabilityProfile("parser-v1", "Parser", {"linking_output": "conditional"})
        config = CollectionConfig(required_fields={"actor_class"})
        acceptance = AcceptanceProfile("strict-v1", {"EX": {"pred_sql", "gold_sql"}})
        result = evaluate_metric_dependencies({"pred_sql": "x"}, acceptance)
        self.assertEqual(profile.to_dict()["version"], "parser-v1")
        self.assertEqual(config.to_dict()["required_fields"], ["actor_class"])
        self.assertEqual(acceptance.to_dict()["version"], "strict-v1")
        self.assertEqual(result["EX"]["status"], "unavailable")
        self.assertFalse(acceptance_gate(result, {"EX"})["accepted"])
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="eval", acceptance_profile=acceptance) as run:
                self.assertFalse(run.evaluate({"pred_sql": "x"})["acceptance"]["accepted"])
            self.assertEqual(read_evidence(directory)["evaluation_status"], "rejected")

        with tempfile.TemporaryDirectory() as directory:
            missing = CollectionConfig(required_fields={"selected_candidate_id"})
            with EvidenceRun(directory, run_id="missing-required", collection_config=missing):
                pass
            evidence = read_evidence(directory)
            self.assertIn("required_evidence_missing", evidence["issues"])
            self.assertEqual(evidence["evidence_status"], "partial")

    def test_candidate_calls_usage_db_and_lineage_are_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="facts", capture_outputs=True) as run:
                candidate = run.candidate("SELECT 1", producer_span_id="p")
                logical = run.logical_call("llm", provider="test")
                attempt = run.attempt(logical, "success", physical=True)
                run.usage(logical, scope="logical_call", source="provider", input_tokens=2)
                run.db_operation("SELECT 1", execution_status="success", fetch_completeness="complete",
                                 preservation_completeness="complete", observed_row_count=1)
                run.producer_change("schema", old_ref="a", new_ref="b", producer_basis_refs=["p"])
                run.merge("schema", ["a", "b"], "c")
            evidence = read_evidence(directory)
            kinds = {event["type"] for event in evidence["events"]}
            self.assertIn("candidate.created", kinds)
            self.assertIn("llm.attempt", kinds)
            self.assertIn("db.finished", kinds)
            self.assertEqual(attempt["logical_call_id"], logical["logical_call_id"])
            self.assertEqual(candidate["sql_text"], "SELECT 1")

    def test_condition_unknown_is_not_not_applicable(self):
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="availability") as run:
                run.condition("parser_scored", ConditionValue.UNKNOWN)
            evidence = read_evidence(directory)
            condition = next(e for e in evidence["events"] if e["type"] == "condition.evaluated")
            self.assertEqual(condition["value"], "unknown")
            self.assertIn("condition_unknown", evidence["issues"])

    def test_optimizer_noop_reuses_candidate_but_change_creates_occurrence(self):
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="optimizer") as run:
                candidate = run.candidate("SELECT 1")
                noop = run.optimization_attempt(candidate, "SELECT 1")
                changed = run.optimization_attempt(candidate, "SELECT  1")
                task_id = run.task_created(plan_ref="plan-1", dependency_refs=[])
                run.workflow_event("finished", task_id=task_id)
            self.assertEqual(noop["output_candidate_id"], candidate["candidate_occurrence_id"])
            self.assertNotEqual(changed["output_candidate_id"], candidate["candidate_occurrence_id"])

    def test_collection_enabled_disabled_preserves_result_and_exception(self):
        actor = EchoActor()
        disabled_result = actor.act(3)
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="enabled"):
                enabled_result = actor.act(3)
        self.assertEqual(enabled_result, disabled_result)

        class FailingActor(EchoActor):
            def act(self, item, **kwargs):
                raise RuntimeError("business failure")

        failing = FailingActor()
        with self.assertRaises(RuntimeError) as disabled:
            failing.act(1)
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="exception"):
                with self.assertRaises(RuntimeError) as enabled:
                    failing.act(1)
        self.assertEqual(str(enabled.exception), str(disabled.exception))

    def test_database_entry_point_records_unknown_preservation_without_payload_capture(self):
        import pandas as pd
        from core.db_connect import execute_sql

        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="db"):
                with patch("core.db_connect.get_sql_exec_result", return_value=(pd.DataFrame({"x": [1]}), None)):
                    self.assertIn("x", execute_sql("sqlite", "/tmp/db.sqlite", "SELECT 1", None))
            evidence = read_evidence(directory)
            finished = next(e for e in evidence["events"] if e["type"] == "db.finished")
            self.assertEqual(finished["preservation_completeness"], "disabled")

        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="db-retained", capture_outputs=True):
                with patch("core.db_connect.get_sql_exec_result", return_value=(pd.DataFrame({"x": [1]}), None)):
                    execute_sql("sqlite", "/tmp/db.sqlite", "SELECT 1", None)
            evidence = read_evidence(directory)
            finished = next(e for e in evidence["events"] if e["type"] == "db.finished")
            self.assertEqual(finished["preservation_completeness"], "complete")
            self.assertIn(finished["result_ref"], evidence["artifacts"])

        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="db-empty"):
                with patch("core.db_connect.get_sql_exec_result", return_value=(pd.DataFrame(columns=["x"]), None)):
                    execute_sql("sqlite", "/tmp/db.sqlite", "SELECT 1", None)
            evidence = read_evidence(directory)
            finished = next(e for e in evidence["events"] if e["type"] == "db.finished")
            self.assertEqual(finished["preservation_completeness"], "disabled")
            self.assertEqual(finished["known_total_row_count"], 0)

        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="db-empty-retained", capture_outputs=True):
                with patch("core.db_connect.get_sql_exec_result", return_value=(pd.DataFrame(columns=["x"]), None)):
                    execute_sql("sqlite", "/tmp/db.sqlite", "SELECT 1", None)
            evidence = read_evidence(directory)
            finished = next(e for e in evidence["events"] if e["type"] == "db.finished")
            self.assertEqual(finished["preservation_completeness"], "complete")
            self.assertEqual(evidence["artifacts"][finished["result_ref"]]["columns"], ["x"])

    def test_llm_entry_point_links_usage_to_observable_attempt(self):
        from core.llm.token_logger import TokenLogger, observe_llm_call, observe_llm_stream_call

        class FakeModel:
            model_name = "fake"
            token_logger = TokenLogger()

            @observe_llm_call
            def complete(self, prompt):
                self.token_logger.record({"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                                         model=self.model_name)
                return "ok"

        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="llm"):
                self.assertEqual(FakeModel().complete("hello"), "ok")
            evidence = read_evidence(directory)
            usage = next(e for e in evidence["events"] if e["type"] == "usage.reported")
            attempt = next(e for e in evidence["events"] if e["type"] == "llm.attempt.finished")
            self.assertEqual(usage["attempt_id"], attempt["attempt_id"])
            self.assertEqual(usage["logical_call_id"], attempt["logical_call_id"])
            self.assertEqual(usage["scope"], "attempt")
            self.assertEqual(usage["usage"], {
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "total_tokens": 3,
            })
            self.assertEqual(aggregate_usage(evidence["events"])["totals"]["total_tokens"], 3)

        class FakeStreamModel(FakeModel):
            @observe_llm_stream_call
            def stream_complete(self, prompt):
                self.token_logger.record({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                                         model=self.model_name)
                yield "a"
                yield "b"

        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="llm-stream"):
                self.assertEqual(list(FakeStreamModel().stream_complete("hello")), ["a", "b"])
            evidence = read_evidence(directory)
            usage = next(e for e in evidence["events"] if e["type"] == "usage.reported")
            self.assertIsNotNone(usage["attempt_id"])

    def test_opaque_retry_and_partial_usage_are_not_inferred(self):
        from core.llm.token_logger import TokenLogger, observe_llm_call

        class RetryingProvider:
            model_name = "opaque-provider"
            token_logger = TokenLogger()

            @observe_llm_call
            def complete(self, prompt):
                for _ in range(2):
                    pass  # Provider/SDK internals are intentionally opaque to the collector.
                self.token_logger.record({"total_tokens": 5}, model=self.model_name)
                return "ok"

        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="opaque-retry"):
                self.assertEqual(RetryingProvider().complete("hello"), "ok")
                RetryingProvider.token_logger.record({"total_tokens": 0}, model="opaque-provider")
            evidence = read_evidence(directory)
        attempts = [e for e in evidence["events"] if e["type"] == "llm.attempt.started"]
        usage = next(e for e in evidence["events"]
                     if e["type"] == "usage.reported" and e["usage"] == {"total_tokens": 5})
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["retry_observability"], "opaque")
        self.assertEqual(usage["usage"], {"total_tokens": 5})
        zero_usage = next(e for e in evidence["events"]
                          if e["type"] == "usage.reported" and e["usage"] == {"total_tokens": 0})
        self.assertEqual(zero_usage["scope"], "provider_observation")

        with tempfile.TemporaryDirectory() as directory:
            logger = TokenLogger()
            with EvidenceRun(directory, run_id="partial-usage"):
                logger.record({"prompt_tokens": 5}, model="partial-provider")
            evidence = read_evidence(directory)
        partial = next(e for e in evidence["events"] if e["type"] == "usage.reported")
        self.assertEqual(partial["usage"], {"prompt_tokens": 5})

    def test_selector_uses_sql_loaded_by_actor_instead_of_persisted_path(self):
        from core.actor.selector.BaseSelect import BaseSelector
        from core.data_manage import Dataset

        class PersistedSelector(BaseSelector):
            NAME = "persisted-selector"

            def act(self, item, pred_sql=None, **kwargs):
                return self.load_pred_sql(pred_sql, item)[0]

        PersistedSelector.__module__ = "core.actor.selector.test"

        with tempfile.TemporaryDirectory() as directory:
            sql_path = Path(directory) / "candidate.sql"
            sql_path.write_text("SELECT 1")
            dataset = Dataset([{"pred_sql": str(sql_path)}], "/tmp/schema.json")
            selector = PersistedSelector(dataset=dataset, is_save=False)
            with EvidenceRun(Path(directory) / "evidence", run_id="persisted-sql"):
                self.assertEqual(selector.act(0), "SELECT 1")
            evidence = read_evidence(Path(directory) / "evidence")
        candidates = [e for e in evidence["events"] if e["type"] == "candidate.created"]
        self.assertEqual([e["sql_text"] for e in candidates], ["SELECT 1"])
        decision = next(e for e in evidence["events"] if e["type"] == "selector.decision")
        self.assertEqual(decision["selection_identity"], "explicit")

    def test_dataset_mutation_is_attributed_to_active_actor_span(self):
        from core.data_manage import Dataset

        class MutatingActor(Actor):
            def __init__(self, dataset):
                self.dataset = dataset

            def act(self, item, **kwargs):
                self.dataset.setitem(item, "value", 2)
                return 2

        dataset = Dataset([{"db_id": "db", "value": 1}], "/tmp/schema.json")
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="mutation"):
                self.assertEqual(MutatingActor(dataset).act(0), 2)
            evidence = read_evidence(directory)
            change = next(e for e in evidence["events"] if e["type"] == "producer.change")
            actor = next(e for e in evidence["events"] if e["type"] == "actor.started")
            self.assertEqual(change["producer_basis_refs"], [actor["span_id"]])
            self.assertEqual(change["producer_attribution"], "explicit")

    def test_runtime_merge_records_input_and_output_lineage(self):
        from core.actor.base import MergeFunction

        values = {"pred_sql": ["SELECT 1"]}
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="merge"):
                MergeFunction.append(values, "pred_sql", "SELECT 2")
            evidence = read_evidence(directory)
            merge = next(e for e in evidence["events"] if e["type"] == "merge.observed")
            self.assertEqual(merge["lineage_status"], "explicit")
            self.assertEqual(len(merge["input_refs"]), 2)
            self.assertEqual(evidence["evidence_status"], "complete")

    def test_actor_adapters_preserve_candidate_pool_and_selection(self):
        class GeneratorActor(Actor):
            def act(self, item, **kwargs):
                return ["SELECT 1", "SELECT 2"]

        class SelectorActor(Actor):
            def act(self, item, pred_sql=None, **kwargs):
                return pred_sql[1]

        GeneratorActor.__module__ = "core.actor.generator.test"
        SelectorActor.__module__ = "core.actor.selector.test"
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="adapters"):
                pool = GeneratorActor().act(0)
                self.assertEqual(SelectorActor().act(0, pred_sql=pool), "SELECT 2")
            evidence = read_evidence(directory)
            generated = next(e for e in evidence["events"] if e["type"] == "generator.output")
            selected = next(e for e in evidence["events"] if e["type"] == "selector.decision")
            self.assertEqual(len(generated["candidate_ids"]), 2)
            self.assertEqual(selected["selected_candidate_id"], generated["candidate_ids"][1])

    def test_equal_sql_candidates_remain_distinct_and_selection_is_ambiguous(self):
        class GeneratorActor(Actor):
            def act(self, item, **kwargs):
                return ["SELECT 1", "SELECT 1"]

        class SelectorActor(Actor):
            def act(self, item, pred_sql=None, **kwargs):
                return pred_sql[0]

        GeneratorActor.__module__ = "core.actor.generator.test"
        SelectorActor.__module__ = "core.actor.selector.test"
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="duplicate-candidates"):
                pool = GeneratorActor().act(0)
                SelectorActor().act(0, pred_sql=pool)
            evidence = read_evidence(directory)
        generated = next(e for e in evidence["events"] if e["type"] == "generator.output")
        selected = next(e for e in evidence["events"] if e["type"] == "selector.decision")
        self.assertEqual(len(set(generated["candidate_ids"])), 2)
        self.assertEqual(selected["ordered_candidate_pool"], generated["candidate_ids"])
        self.assertIsNone(selected["selected_candidate_id"])
        self.assertEqual(selected["selection_identity"], "ambiguous")

    def test_pipeline_entry_point_preserves_output_with_collection_enabled(self):
        from core.actor.nest.pipeline import PipelineActor
        from core.data_manage import Dataset

        class AddOneActor(Actor):
            NAME = "add-one"
            OUTPUT_NAME = "value"
            STRATEGY = "overwrite"

            def __init__(self, dataset):
                self.dataset = dataset

            def act(self, item, value=0, **kwargs):
                return value + 1

        def execute(directory=None):
            dataset = Dataset([{"db_id": "db", "instance_id": "sample-1"}], "/tmp/schema.json")
            pipeline = PipelineActor(dataset=dataset, actors=[AddOneActor(dataset), AddOneActor(dataset)])
            if directory is None:
                return pipeline.act(0, value=1)
            with EvidenceRun(directory, run_id="pipeline"):
                return pipeline.act(0, value=1)

        disabled = execute()
        with tempfile.TemporaryDirectory() as directory:
            enabled = execute(directory)
            evidence = read_evidence(directory)
        self.assertEqual(enabled, disabled)
        self.assertEqual(enabled, 3)
        self.assertEqual(sum(e["type"] == "actor.started" for e in evidence["events"]), 3)

    def test_engine_entry_point_can_persist_run_local_evidence(self):
        from core.engine import Engine

        class FakeTask:
            name = "fake"
            task_id = "task-1"

            def __init__(self):
                self.calls = 0

            def run(self):
                self.calls += 1

        task = FakeTask()
        engine = Engine.__new__(Engine)
        engine.exec_process = []
        engine.parse_exec_process = lambda _process: task
        with tempfile.TemporaryDirectory() as directory:
            engine.execute(evidence_directory=directory, evidence_run_id="engine-run")
            evidence = read_evidence(directory)
        self.assertEqual(task.calls, 1)
        self.assertEqual(evidence["execution_status"], "success")

    def test_resume_legacy_and_reexport_preserve_observed_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="resumed", prior_run_id="original",
                             prior_segment="segment-1", checkpoint_ref="checkpoint-1") as run:
                adapted = run.adapt_legacy_record(
                    {"type": "actor.finished", "execution_status": "success", "unobserved": "ignored"},
                    source_schema_version=0, adapter_version="legacy-v1",
                )
            first = read_evidence(directory)
            resume = next(e for e in first["events"] if e["type"] == "execution.resumed")
            self.assertEqual(resume["prior_run_id"], "original")
            self.assertEqual(adapted["observed"], {"type": "actor.finished", "execution_status": "success"})

            event = next(e for e in first["events"] if e["type"] == "legacy.adapted")
            with EvidenceRun(directory, run_id="resumed") as run:
                self.assertTrue(run.reexport_event(event))
            second = read_evidence(directory)
            self.assertEqual(sum(e["event_id"] == event["event_id"] for e in second["events"]), 1)

    def test_usage_aggregation_does_not_double_count_logical_and_attempt_scope(self):
        records = [
            {"type": "usage.reported", "scope": "logical_call", "logical_call_id": "call-1",
             "usage": {"total_tokens": 10}},
            {"type": "usage.reported", "scope": "attempt", "logical_call_id": "call-1",
             "attempt_id": "attempt-1", "usage": {"total_tokens": 6}},
            {"type": "usage.reported", "scope": "attempt", "logical_call_id": "call-1",
             "attempt_id": "attempt-2", "usage": {"total_tokens": 4}},
        ]
        usage = aggregate_usage(records)
        self.assertEqual(usage["totals"]["total_tokens"], 10)
        self.assertEqual(usage["selected_records"], 2)

    def test_offline_evaluation_keeps_valid_metrics_when_cost_is_missing(self):
        profile = AcceptanceProfile("release-v1", {
            "EX": {"pred_result", "gold_result"},
            "cost": {"usage"},
        })
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="offline"):
                pass
            result = evaluate_evidence(
                directory, {"pred_result": [1], "gold_result": [1]}, profile, required_metrics={"EX"},
            )
        self.assertEqual(result["metrics"]["EX"]["status"], "computable")
        self.assertEqual(result["metrics"]["cost"]["status"], "unavailable")
        self.assertEqual(result["evaluation_status"], "accepted")

    def test_partial_database_results_block_result_metrics(self):
        profile = AcceptanceProfile("release-v1", {"EX": {"pred_result", "gold_result"}})
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="partial-db") as run:
                run.db_operation(
                    "SELECT 1",
                    execution_status="success",
                    fetch_completeness="complete",
                    preservation_completeness="disabled",
                )
            result = evaluate_evidence(
                directory,
                {"pred_result": [1], "gold_result": [1]},
                profile,
                required_metrics={"EX"},
            )
        self.assertEqual(result["metrics"]["EX"]["status"], "unavailable")
        self.assertIn("complete_result_evidence", result["metrics"]["EX"]["missing"])
        self.assertEqual(result["evaluation_status"], "rejected")

    def test_sink_failure_is_partial_but_does_not_change_actor_result(self):
        class FailingSink:
            def write(self, name, payload):
                raise OSError("disk full")

        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="failed-sink", sink=FailingSink(), write_wait_s=0.02) as run:
                with run.operation("actor"):
                    result = {"answer": 5}
            self.assertEqual(result, {"answer": 5})
            evidence = read_evidence(directory)
            self.assertEqual(evidence["evidence_status"], "unavailable")

    def test_unobserved_operation_is_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="unfinished") as run:
                run.event("operation.started", span_id="orphan")
                run.event("llm.attempt.started", attempt_id="attempt-orphan", logical_call_id="call")
            evidence = read_evidence(directory)
        self.assertIn("unobserved_completion", evidence["issues"])

    def test_truncated_artifact_is_reported_as_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="truncated", max_bytes=8192, capture_outputs=True) as run:
                self.assertIsNone(run.artifact({"large": "x" * 16384}))
            evidence = read_evidence(directory)
            self.assertIn("artifact_limit", evidence["issues"])
            self.assertEqual(evidence["evidence_status"], "partial")

    def test_run_storage_budget_is_bounded_and_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="budget", max_bytes=8192, max_total_bytes=12000) as run:
                for index in range(20):
                    run.event("payload", index=index, value="x" * 900)
            evidence = read_evidence(directory)
            self.assertIn("run_size_limit", evidence["issues"])

    def test_concurrent_operations_keep_parent_relationships_without_inventing_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="tree-run"):
                run = __import__("core.evidence", fromlist=["current_run"]).current_run()
                with run.operation("actor", actor_class="TreeActor"):
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        futures = [submit_with_evidence(executor, self._child_operation, run) for _ in range(2)]
                        [future.result() for future in futures]
            evidence = read_evidence(directory)
            starts = [e for e in evidence["events"] if e["type"] == "actor.started"]
            self.assertEqual(len(starts), 1)
            parent = starts[0]
            children = [e for e in evidence["events"] if e["type"] == "child.started"]
            self.assertEqual([e["parent_span_id"] for e in children], [parent["span_id"]] * 2)
            self.assertFalse(any(e["type"] == "task.created" for e in evidence["events"]))

    def test_explicit_runtime_tasks_receive_task_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="tasks") as run:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    submit_with_evidence(
                        executor, self._child_operation, run, evidence_task=True,
                    ).result()
            evidence = read_evidence(directory)
        task_ids = [e["task_id"] for e in evidence["events"] if e["type"] == "task.created"]
        self.assertEqual(len(task_ids), 1)
        scheduled = next(e for e in evidence["events"] if e["type"] == "workflow.scheduled")
        self.assertEqual(scheduled["task_id"], task_ids[0])

    def test_plain_thread_pool_work_is_not_claimed_as_squrve_task(self):
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="executor"):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    self.assertTrue(executor.submit(lambda: True).result())
            evidence = read_evidence(directory)
            kinds = {event["type"] for event in evidence["events"]}
            self.assertNotIn("task.created", kinds)
            self.assertNotIn("workflow.started", kinds)
            self.assertNotIn("workflow.finished", kinds)

    def test_corrupt_event_identity_invalidates_offline_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="corrupt") as run:
                run.event("observed.fact", value=1)
            event_path = next((Path(directory) / "segments").glob("*/events/*.json"))
            event = json.loads(event_path.read_text())
            event["event_id"] = "tampered"
            event_path.write_text(json.dumps(event))
            evidence = read_evidence(directory)
        self.assertEqual(evidence["evidence_status"], "invalid")
        self.assertIn("invalid_segment", evidence["issues"])

    def test_reexport_rejects_path_traversal_identity(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "run"
            with EvidenceRun(directory, run_id="reexport") as run:
                accepted = run.reexport_event({
                    "event_id": "foreign",
                    "execution_segment_id": "../../escaped",
                })
            self.assertFalse(accepted)
            self.assertFalse((Path(root) / "escaped").exists())

    def test_missing_database_result_artifact_makes_evidence_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="missing-result") as run:
                result_ref = run.artifact({"columns": ["x"], "data": [[1]]})
                run.event(
                    "db.finished",
                    operation_id="op",
                    execution_status="success",
                    fetch_completeness="complete",
                    preservation_completeness="complete",
                    result_ref=result_ref,
                )
            (Path(directory) / "artifacts" / f"{result_ref}.json").unlink()
            evidence = read_evidence(directory)
        self.assertEqual(evidence["evidence_status"], "partial")
        self.assertIn("result_ref_artifact_unavailable", evidence["issues"])

    def test_capability_requirements_are_checked_per_actor_occurrence(self):
        class IncompleteOptimizer(Actor):
            def act(self, item, **kwargs):
                return None

        IncompleteOptimizer.__module__ = "core.actor.optimizer.test"
        profile = CapabilityProfile(
            "optimizer-required-v1", "optimizer", {"missing_fact": "required"},
        )
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="capability", capability_profiles=[profile]):
                IncompleteOptimizer().act(0)
            evidence = read_evidence(directory)
        self.assertEqual(evidence["evidence_status"], "partial")
        incomplete = next(e for e in evidence["events"] if e["type"] == "actor.evidence_incomplete")
        self.assertEqual(incomplete["missing_required_fields"], ["missing_fact"])

    def test_nested_actor_requirements_are_attributed_to_the_child_span(self):
        class ChildGenerator(Actor):
            def act(self, item, **kwargs):
                return ["SELECT 1"]

        class ParentAgent(Actor):
            def act(self, item, **kwargs):
                return ChildGenerator().act(item)

        ChildGenerator.__module__ = "core.actor.generator.test"
        ParentAgent.__module__ = "core.actor.agent.test"
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="nested-requirements"):
                ParentAgent().act(0)
            evidence = read_evidence(directory)
        self.assertEqual(evidence["evidence_status"], "complete")
        self.assertFalse(any(e["type"] == "actor.evidence_incomplete" for e in evidence["events"]))

    def test_unresolved_conditional_requirement_is_not_complete(self):
        class ConditionalParser(Actor):
            def act(self, item, **kwargs):
                return None

        ConditionalParser.__module__ = "core.actor.parser.test"
        profile = CapabilityProfile(
            "parser-conditional-v1", "parser", {"score": "conditional"},
        )
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="conditional", capability_profiles=[profile]):
                ConditionalParser().act(0)
            evidence = read_evidence(directory)
        self.assertEqual(evidence["evidence_status"], "partial")
        self.assertIn("conditional_condition_unobserved", evidence["issues"])

    def test_content_equality_does_not_guess_candidate_occurrence(self):
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="candidate-input") as run:
                first = run.candidate("SELECT 1")
                second = run.candidate("SELECT 1")
                observed = run.candidates_for_pool(["SELECT 1"])[0]
            evidence = read_evidence(directory)
        self.assertNotIn(observed["candidate_occurrence_id"], {
            first["candidate_occurrence_id"], second["candidate_occurrence_id"],
        })
        provenance = [e for e in evidence["events"] if e["type"] == "candidate.provenance"]
        self.assertEqual(provenance[-1]["producer_attribution"], "unattributed")

    @staticmethod
    def _child_operation(run):
        with run.operation("child"):
            return True

    def test_actor_result_survives_local_evidence_round_trip(self):
        actor = EchoActor()
        expected = actor.act(4)
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="test-run", capture_outputs=True):
                self.assertEqual(actor.act(4), expected)
            evidence = read_evidence(directory)
            self.assertEqual(evidence["evidence_status"], "complete")
            starts = [e for e in evidence["events"] if e["type"] == "actor.started"]
            ends = [e for e in evidence["events"] if e["type"] == "actor.finished"]
            self.assertEqual(len(starts), 1)
            self.assertEqual(len(ends), 1)
            self.assertEqual(starts[0]["span_id"], ends[0]["span_id"])
            self.assertEqual(ends[0]["execution_status"], "success")
            self.assertEqual(evidence["artifacts"][ends[0]["output_ref"]], expected)


if __name__ == "__main__":
    unittest.main()
