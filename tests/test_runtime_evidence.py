"""Behavioral acceptance at existing Actor entry points and the evidence reader."""

import tempfile
import unittest
from unittest.mock import patch

from core.actor.base import Actor
from core.evidence import (
    AcceptanceProfile,
    Availability,
    CapabilityProfile,
    CollectionConfig,
    EvidenceRun,
    evaluate_metric_dependencies,
    acceptance_gate,
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

    def test_availability_unknown_is_not_not_applicable(self):
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="availability") as run:
                run.field("confidence", Availability.UNKNOWN, condition="parser_scored")
            evidence = read_evidence(directory)
            field = next(e for e in evidence["events"] if e["type"] == "field.availability")
            self.assertEqual(field["availability"], "unknown")

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
            self.assertEqual(finished["preservation_completeness"], "unknown")
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
            evidence = read_evidence(directory)
            self.assertIn("unobserved_completion", evidence["issues"])

    def test_truncated_artifact_is_reported_as_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            with EvidenceRun(directory, run_id="truncated", max_bytes=2048, capture_outputs=True) as run:
                self.assertIsNone(run.artifact({"large": "x" * 4096}))
            evidence = read_evidence(directory)
            self.assertIn("artifact_limit", evidence["issues"])
            self.assertEqual(evidence["evidence_status"], "partial")

    def test_concurrent_operations_keep_parent_relationships(self):
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
