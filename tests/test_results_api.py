"""Cross-benchmark comparison: the results matrix and per-question diff, through the HTTP API."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from demo.experiments import ExperimentScheduler
from tests.test_experiments_api import READY_ENV, FakeBackend


def make_scores(method, dataset, *, correct, run_id=None, limit=None, mode="slice", seed=42,
                timestamp="2026-10-21T10:00:00", ids=None, tokens=None, latency=2.5, sc_note=None):
    ids = ids or [f"q{index}" for index in range(len(correct))]
    token = {"total_calls": 3, "total_tokens": tokens} if tokens is not None else {"total_calls": 0, "total_tokens": 0}
    ex_avg = sum(correct) / len(correct)
    return {
        "run_id": run_id or f"{dataset}-{method}-{''.join(ch for ch in timestamp if ch.isdigit())}",
        "method": method,
        "dataset": dataset,
        "split": "dev",
        "scope": "smoke",
        "timestamp": timestamp,
        "sample_count": len(correct),
        "config_snapshot": {"generate_num": 1, "sampling": {"mode": mode, "limit": limit or len(correct), "seed": seed}},
        "aggregate": {
            "ex": {"avg": ex_avg, "valid": len(correct), "total": len(correct)},
            "em": {"avg": ex_avg / 2, "valid": len(correct), "total": len(correct)},
            "sc": {"avg": None, "valid": 0, "total": len(correct), "note": sc_note or "require generate_num>=2"},
            "token": token,
            "latency": {"avg_s": latency},
        },
        "per_sample": [
            {
                "instance_id": instance_id,
                "db_id": "concert_singer",
                "question": f"Question {instance_id}?",
                "pred_sql": f"SELECT {index} -- {method}",
                "ex": value,
            }
            for index, (instance_id, value) in enumerate(zip(ids, correct))
        ],
    }


class ResultsApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from demo import api_server

        api_server.app.config.update(TESTING=True)
        cls.api_server = api_server

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        env = patch.dict(os.environ, {
            "SQURVE_DEPLOYMENT_TARGET": "local",
            "SQURVE_WORKSPACE_DIR": self.workspace.name,
            **READY_ENV,
        }, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.backend = FakeBackend()
        self.original = self.api_server._experiments
        self.api_server._experiments = ExperimentScheduler(self.backend, concurrency=4)
        self.addCleanup(setattr, self.api_server, "_experiments", self.original)
        self.client = self.api_server.app.test_client()

    def archive(self, scores):
        run_dir = Path(self.workspace.name) / "artifacts" / scores["run_id"]
        run_dir.mkdir(parents=True)
        (run_dir / "scores.json").write_text(json.dumps(scores), encoding="utf-8")

    def matrix(self, **params):
        query = {"methods": "alpha,beta", "datasets": "minibench,tinybench",
                 "sample_limit": 3, "sample_mode": "slice", **params}
        return self.client.get("/api/results/matrix", query_string=query)

    @staticmethod
    def cell(payload, method, dataset):
        return next(item for item in payload["cells"] if item["method"] == method and item["dataset"] == dataset)


class MatrixTests(ResultsApiTestCase):
    def test_every_cell_of_a_valid_large_experiment_can_be_compared_and_exported(self):
        experiment = self.api_server._experiments.create(
            [("alpha", "minibench", {"generate": f"Actor{index}"}) for index in range(13)],
            {"limit": 3, "mode": "slice", "seed": 42},
        )
        query = {"experiment_id": experiment["experiment_id"],
                 "methods": ",".join(cell["variant"] for cell in experiment["cells"]),
                 "datasets": "minibench"}
        response = self.client.get("/api/results/matrix", query_string=query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json["cells"]), 13)
        self.assertEqual(self.client.get("/api/results/matrix.csv", query_string=query).status_code, 200)

    def test_archived_runs_fill_the_method_by_benchmark_grid(self):
        self.archive(make_scores("alpha", "minibench", correct=[1, 1, 0], tokens=900, latency=4.0))
        self.archive(make_scores("beta", "minibench", correct=[1, 0, 0]))
        response = self.matrix()
        self.assertEqual(response.status_code, 200)
        payload = response.json
        self.assertEqual(payload["methods"], ["alpha", "beta"])
        self.assertEqual(payload["datasets"], ["minibench", "tinybench"])
        alpha = self.cell(payload, "alpha", "minibench")
        self.assertEqual(alpha["status"], "ok")
        self.assertAlmostEqual(alpha["values"]["ex"], 2 / 3)
        self.assertEqual(alpha["cost"], {"tokens_total": 900, "latency_s": 4.0})
        self.assertEqual(alpha["source"], "archive")
        self.assertEqual(self.cell(payload, "alpha", "tinybench")["status"], "missing")
        ex = next(item for item in payload["metrics"] if item["id"] == "ex")
        self.assertTrue(ex["higher_is_better"])

    def test_untracked_tokens_are_not_reported_as_zero(self):
        self.archive(make_scores("alpha", "minibench", correct=[1, 0, 1]))
        alpha = self.cell(self.matrix().json, "alpha", "minibench")
        self.assertIsNone(alpha["cost"]["tokens_total"])

    def test_a_metric_that_cannot_be_computed_carries_its_reason(self):
        self.archive(make_scores("alpha", "minibench", correct=[1, 0, 1], sc_note="needs several candidates"))
        alpha = self.cell(self.matrix().json, "alpha", "minibench")
        self.assertIsNone(alpha["values"]["sc"])
        self.assertEqual(alpha["not_computable"]["sc"], "needs several candidates")

    def test_only_runs_with_the_requested_sampling_are_used(self):
        self.archive(make_scores("alpha", "minibench", correct=[1] * 10, limit=10))
        self.assertEqual(self.cell(self.matrix().json, "alpha", "minibench")["status"], "missing")

    def test_the_latest_matching_run_wins(self):
        self.archive(make_scores("alpha", "minibench", correct=[0, 0, 0], timestamp="2026-10-01T10:00:00"))
        self.archive(make_scores("alpha", "minibench", correct=[1, 1, 1], timestamp="2026-10-20T10:00:00"))
        self.assertEqual(self.cell(self.matrix().json, "alpha", "minibench")["values"]["ex"], 1.0)

    def test_runs_over_different_questions_are_marked_unaligned(self):
        self.archive(make_scores("alpha", "minibench", correct=[1, 1, 0], ids=["a", "b", "c"]))
        self.archive(make_scores("beta", "minibench", correct=[1, 0, 0], ids=["x", "y", "z"],
                                 timestamp="2026-10-01T09:00:00"))
        payload = self.matrix().json
        self.assertFalse(payload["alignment"]["minibench"]["aligned"])
        statuses = {self.cell(payload, method, "minibench")["status"] for method in ("alpha", "beta")}
        self.assertEqual(statuses, {"ok", "unaligned"})

    def test_experiment_results_come_from_that_experiment_only(self):
        self.archive(make_scores("c3sql", "spider", correct=[0, 0, 0]))
        experiment = self.client.post("/api/experiments", json={
            "cells": [{"method": "c3sql", "dataset": "spider"}, {"method": "dinsql", "dataset": "spider"}],
            "sampling": {"limit": 3, "mode": "slice", "seed": 42},
        }).json
        self.backend.finish("job1", scores=make_scores("c3sql", "spider", correct=[1, 1, 1]))
        response = self.client.get("/api/results/matrix", query_string={
            "methods": "c3sql,dinsql,resdsql", "datasets": "spider", "experiment_id": experiment["experiment_id"],
        })
        payload = response.json
        self.assertEqual(self.cell(payload, "c3sql", "spider")["values"]["ex"], 1.0)
        self.assertEqual(self.cell(payload, "c3sql", "spider")["source"], "session")
        self.assertEqual(self.cell(payload, "dinsql", "spider")["status"], "running")
        self.assertEqual(self.cell(payload, "resdsql", "spider")["status"], "missing")

    def archive_evidence_bundle(self, method, dataset, *, ex_by_id, latency_ms=2000.0):
        run_dir = Path(self.workspace.name) / "artifacts" / f"{dataset}-{method}-evidence"
        run_dir.mkdir(parents=True)
        scores = {
            "run_id": run_dir.name, "method": method, "dataset": dataset, "split": "dev",
            "scope": "random-3", "timestamp": "2026-07-10T00:00:00", "sample_count": len(ex_by_id),
            "config_snapshot": {"generate_num": 1, "sampling": {"mode": "slice", "limit": 3, "seed": 42}},
            "aggregate": {"ex": sum(ex_by_id.values()) / len(ex_by_id), "em": 0.25},
        }
        (run_dir / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
        rows = [
            json.dumps({"instance_id": instance_id, "latency_ms": latency_ms, "metrics": {"ex": value}})
            for instance_id, value in ex_by_id.items()
        ]
        (run_dir / "sample-diagnostics.jsonl").write_text("\n".join(rows), encoding="utf-8")

    def test_recorded_evidence_bundles_are_read_too(self):
        self.archive_evidence_bundle("alpha", "minibench", ex_by_id={"q2": 1, "q0": 1, "q1": 0})
        self.archive_evidence_bundle("beta", "minibench", ex_by_id={"q0": 0, "q1": 0, "q2": 1})
        payload = self.matrix().json
        alpha = self.cell(payload, "alpha", "minibench")
        self.assertAlmostEqual(alpha["values"]["ex"], 2 / 3)
        self.assertEqual(alpha["values"]["em"], 0.25)
        self.assertEqual(alpha["cost"]["latency_s"], 2.0)
        self.assertTrue(payload["alignment"]["minibench"]["aligned"], "same questions in another order still align")

    def test_a_cancelled_experiment_cell_stays_cancelled(self):
        experiment = self.client.post("/api/experiments", json={
            "cells": [{"method": "c3sql", "dataset": "spider"}],
            "sampling": {"limit": 3, "mode": "slice", "seed": 42},
        }).json
        self.client.post(f"/api/experiments/{experiment['experiment_id']}/cancel")
        payload = self.client.get("/api/results/matrix", query_string={
            "methods": "c3sql", "datasets": "spider", "experiment_id": experiment["experiment_id"],
        }).json
        self.assertEqual(self.cell(payload, "c3sql", "spider")["status"], "cancelled")

    def test_runs_without_per_question_rows_are_never_called_aligned(self):
        for method in ("alpha", "beta"):
            scores = make_scores(method, "minibench", correct=[1, 0, 1])
            scores["per_sample"] = []
            self.archive(scores)
        payload = self.matrix().json
        self.assertFalse(payload["alignment"]["minibench"]["aligned"])
        self.assertIsNone(self.cell(payload, "alpha", "minibench")["sample_hash"])

    def test_demo_runs_from_this_workspace_are_labelled_session(self):
        bundle = Path(self.workspace.name) / "sessions" / "evaluations" / "job-x" / "score-bundle"
        bundle.mkdir(parents=True)
        (bundle / "scores.json").write_text(json.dumps(make_scores("alpha", "minibench", correct=[1, 0, 1])), encoding="utf-8")
        (bundle.parent / "job.json").write_text(json.dumps({"sample_limit": 3, "sample_mode": "slice", "sample_seed": 42}), encoding="utf-8")
        self.assertEqual(self.cell(self.matrix().json, "alpha", "minibench")["source"], "session")

    def test_bad_parameters_are_rejected(self):
        self.assertEqual(self.matrix(methods="").status_code, 400)
        self.assertEqual(self.matrix(datasets="../etc").status_code, 400)
        self.assertEqual(self.matrix(sample_limit=7).status_code, 400)
        self.assertEqual(self.matrix(experiment_id="missing").status_code, 404)

    def test_the_public_space_can_read_recorded_results(self):
        self.archive(make_scores("alpha", "minibench", correct=[1, 0, 1]))
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False):
            response = self.matrix()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.cell(response.json, "alpha", "minibench")["status"], "ok")

    def test_the_matrix_exports_as_csv(self):
        self.archive(make_scores("alpha", "minibench", correct=[1, 1, 0], tokens=900))
        response = self.client.get("/api/results/matrix.csv", query_string={
            "methods": "alpha", "datasets": "minibench", "sample_limit": 3, "sample_mode": "slice",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.content_type)
        lines = response.get_data(as_text=True).strip().splitlines()
        self.assertTrue(lines[0].startswith("method,dataset,status"))
        self.assertIn("alpha,minibench,ok", lines[1])


class DiffTests(ResultsApiTestCase):
    def setUp(self):
        super().setUp()
        self.archive(make_scores("alpha", "minibench", correct=[1, 1, 0, 0], run_id="run-a"))
        self.archive(make_scores("beta", "minibench", correct=[1, 0, 1, 0], run_id="run-b"))

    def diff(self, **params):
        return self.client.get("/api/results/diff", query_string={"a": "run-a", "b": "run-b", **params})

    def test_questions_are_split_by_which_method_got_them_right(self):
        response = self.diff()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["summary"], {"both_right": 1, "a_only": 1, "b_only": 1, "both_wrong": 1, "unknown": 0})
        row = next(item for item in response.json["rows"] if item["instance_id"] == "q1")
        self.assertEqual(row["category"], "a_only")
        self.assertEqual(row["question"], "Question q1?")
        self.assertEqual(row["db_id"], "concert_singer")
        self.assertEqual(row["a"], {"correct": True, "sql": "SELECT 1 -- alpha"})
        self.assertEqual(row["b"], {"correct": False, "sql": "SELECT 1 -- beta"})
        self.assertEqual(response.json["a"]["method"], "alpha")
        self.assertEqual(response.json["b"]["method"], "beta")

    def test_rows_can_be_filtered_and_paged(self):
        only_b = self.diff(category="b_only").json
        self.assertEqual([row["instance_id"] for row in only_b["rows"]], ["q2"])
        self.assertEqual(only_b["total"], 1)
        paged = self.diff(limit=2, offset=2).json
        self.assertEqual(len(paged["rows"]), 2)
        self.assertEqual(paged["total"], 4)

    def test_runs_over_different_questions_cannot_be_diffed(self):
        self.archive(make_scores("gamma", "minibench", correct=[1, 0, 1, 0], ids=["x", "y", "z", "w"], run_id="run-c"))
        response = self.diff(b="run-c")
        self.assertEqual(response.status_code, 409)

    def test_unaligned_runs_report_the_unaligned_code(self):
        self.archive(make_scores("gamma", "minibench", correct=[1, 0, 1, 0], ids=["x", "y", "z", "w"], run_id="run-d"))
        self.assertEqual(self.diff(b="run-d").json["code"], "unaligned")

    def test_runs_from_another_benchmark_cannot_be_diffed(self):
        self.archive(make_scores("beta", "otherbench", correct=[1, 0, 1, 0], run_id="run-o"))
        response = self.diff(b="run-o")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.diff(dataset="otherbench").status_code, 409)

    def test_runs_without_per_question_rows_cannot_be_diffed(self):
        for run_id in ("run-e1", "run-e2"):
            scores = make_scores("alpha", "minibench", correct=[1, 0], run_id=run_id)
            scores["per_sample"] = []
            self.archive(scores)
        self.assertEqual(self.diff(a="run-e1", b="run-e2").status_code, 409)

    def test_the_public_space_does_not_publish_generated_sql(self):
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False):
            rows = self.diff().json["rows"]
        self.assertTrue(all(row["a"]["sql"] is None and row["b"]["sql"] is None for row in rows))
        self.assertTrue(all(row["question"] for row in rows))

    def test_unknown_runs_and_bad_parameters_are_rejected(self):
        self.assertEqual(self.diff(b="no-such-run").status_code, 404)
        self.assertEqual(self.diff(a="../x").status_code, 400)
        self.assertEqual(self.diff(category="sideways").status_code, 400)


SPIDER_DEV = Path(__file__).resolve().parents[1] / "benchmarks" / "spider" / "dev" / "dataset.json"


@unittest.skipUnless(SPIDER_DEV.is_file(), "Spider dev is not installed")
class DiffEnrichmentTests(ResultsApiTestCase):
    def test_recorded_runs_without_question_text_borrow_it_from_the_installed_benchmark(self):
        for method, run_id, correct in (("alpha", "run-ea", [1, 0]), ("beta", "run-eb", [0, 0])):
            scores = make_scores(method, "spider", correct=correct, run_id=run_id, ids=["dev_0", "dev_1"])
            for row in scores["per_sample"]:
                row.pop("question"), row.pop("db_id"), row.pop("pred_sql")
            self.archive(scores)
        rows = self.client.get("/api/results/diff", query_string={"a": "run-ea", "b": "run-eb"}).json["rows"]
        first = next(row for row in rows if row["instance_id"] == "dev_0")
        self.assertEqual(first["db_id"], "concert_singer")
        self.assertEqual(first["question"], "How many singers do we have?")
        self.assertIsNone(first["a"]["sql"])


if __name__ == "__main__":
    unittest.main()
