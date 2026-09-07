"""Behavioral acceptance at existing Actor entry points and the evidence reader."""

import tempfile
import unittest

from core.actor.base import Actor
from core.evidence import EvidenceRun, read_evidence
from concurrent.futures import ThreadPoolExecutor

from core.evidence import submit_with_evidence


class EchoActor(Actor):
    NAME = "echo"
    OUTPUT_NAME = "answer"
    STRATEGY = "overwrite"

    def act(self, item, **kwargs):
        return {"answer": item + 1}


class RuntimeEvidenceTests(unittest.TestCase):
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
            with EvidenceRun(directory, run_id="truncated", max_bytes=512, capture_outputs=True) as run:
                self.assertIsNone(run.artifact({"large": "x" * 1024}))
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
