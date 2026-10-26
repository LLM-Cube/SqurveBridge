"""Run timeline: live reproduce runs expressed as the same per-question, per-stage shape as the recorded replay."""

from __future__ import annotations

import json
import unittest


class ActorTraceIdentityTests(unittest.TestCase):
    def test_logged_trace_names_the_sample_it_belongs_to(self):
        from core.trace import record_actor_trace

        class Actor:
            name = "Gen"
            output_name = "pred_sql"
            strategy = "append"

        class Logger:
            lines: list[str] = []

            def info(self, message):
                self.lines.append(message)

        dataset = [{"instance_id": "dev_7", "question": "How many singers?"}]
        logger = Logger()
        record_actor_trace(
            dataset=dataset, item=0, actor=Actor(), result="SELECT 1",
            elapsed_s=0.5, data_logger=logger, stage_name="gen1",
        )
        logged = json.loads(logger.lines[0].split("actor_trace=", 1)[1])
        self.assertEqual(logged["instance_id"], "dev_7")


def trace(instance_id, stage, output, result, elapsed, error=None):
    record = {
        "instance_id": instance_id, "actor_name": stage, "stage_name": stage,
        "output_name": output, "elapsed_s": elapsed, "result": result, "error": error,
    }
    return "[2026-10-26 10:00:00] [INFO]  actor_trace=" + json.dumps(record) + ' {"trailing": "noise"}'


def log(*lines):
    return "\n".join(lines) + "\n"


QUESTIONS = [
    {"instance_id": "dev_0", "db_id": "concert_singer", "question": "How many singers?", "query": "SELECT count(*) FROM singer"},
    {"instance_id": "dev_1", "db_id": "pets_1", "question": "List pet ages", "query": "SELECT age FROM pets"},
]


class RunTimelineTests(unittest.TestCase):
    def build(self, text, **kwargs):
        from demo.run_timeline import build_run_timeline

        return build_run_timeline(QUESTIONS, text, method="c3sql", **kwargs)

    def test_nothing_has_run_yet(self):
        timeline = self.build("")
        self.assertEqual(timeline["total"], 2)
        self.assertEqual([item["run"]["state"] for item in timeline["questions"]], ["idle", "idle"])
        self.assertEqual(timeline["cursor"], 0)

    def test_interleaved_samples_keep_their_own_stages(self):
        text = log(
            "[t] [INFO]  开始处理样本 dev_0",
            "[t] [INFO]  样本 dev_0 @ reduce",
            "[t] [INFO]  开始处理样本 dev_1",
            "[t] [INFO]  样本 dev_1 @ reduce",
            trace("dev_1", "reduce", "instance_schemas", ["pets.age"], 0.2),
            trace("dev_0", "reduce", "instance_schemas", ["singer.name", "singer.age"], 0.4),
            "[t] [INFO]  样本 dev_0 @ generate",
        )
        first, second = self.build(text)["questions"]
        self.assertEqual(first["run"]["state"], "running")
        self.assertEqual(
            [(stage["name"], stage["state"]) for stage in first["run"]["stages"]],
            [("reduce", "done"), ("generate", "running")],
        )
        self.assertEqual(first["run"]["stages"][0]["seconds"], 0.4)
        self.assertIn("singer.name", first["run"]["stages"][0]["text"])
        self.assertEqual(
            [(stage["name"], stage["state"]) for stage in second["run"]["stages"]],
            [("reduce", "done")],
        )

    def test_finished_sample_carries_its_final_sql(self):
        text = log(
            "[t] [INFO]  样本 dev_0 @ generate",
            trace("dev_0", "generate", "pred_sql", "SELECT COUNT(*) FROM singer;", 1.6),
            "[t] [INFO]  样本 dev_0 处理完成 (3.2s)",
        )
        run = self.build(text)["questions"][0]["run"]
        self.assertEqual(run["state"], "done")
        self.assertEqual(run["pred_sql"], "SELECT COUNT(*) FROM singer;")
        self.assertEqual(run["stages"][0]["kind"], "sql")

    def test_a_failed_stage_is_reported_not_hidden(self):
        text = log(
            trace("dev_0", "generate", "pred_sql", None, 9.0, error="connection reset"),
            "[t] [INFO]  样本 dev_0 处理完成 (9.1s)",
        )
        run = self.build(text)["questions"][0]["run"]
        self.assertEqual(run["stages"][0]["state"], "failed")
        self.assertEqual(run["pred_sql"], "")

    def finished(self, instance_id, sql):
        return [
            trace(instance_id, "generate", "pred_sql", sql, 1.0),
            f"[t] [INFO]  样本 {instance_id} 处理完成 (2.0s)",
        ]

    def test_a_judge_decides_only_finished_predictions(self):
        text = log(*self.finished("dev_0", "SELECT 1"), "[t] [INFO]  样本 dev_1 @ generate")
        seen = []

        def judge(question, sql):
            seen.append((question["instance_id"], sql))
            return {"ex": True, "result": {"ok": True, "n": 1, "head": [["6"]]}}

        first, second = self.build(text, judge=judge)["questions"]
        self.assertEqual(seen, [("dev_0", "SELECT 1")])
        self.assertIs(first["run"]["ex"], True)
        self.assertEqual(first["run"]["result"]["head"], [["6"]])
        self.assertIsNone(second["run"]["ex"])

    def test_without_a_judge_the_verdict_is_unknown_not_wrong(self):
        run = self.build(log(*self.finished("dev_0", "SELECT 1")))["questions"][0]["run"]
        self.assertEqual(run["state"], "done")
        self.assertIsNone(run["ex"])

    def test_cursor_stops_at_the_first_unfinished_question_and_resumes_from_it(self):
        text = log(*self.finished("dev_1", "SELECT 2"))
        # dev_1 finished first but dev_0 has not: the cursor must not skip past dev_0.
        full = self.build(text)
        self.assertEqual(full["cursor"], 0)
        text = log(*self.finished("dev_0", "SELECT 1"), "[t] [INFO]  样本 dev_1 @ generate")
        partial = self.build(text)
        self.assertEqual(partial["cursor"], 1)
        resumed = self.build(text, since=partial["cursor"])
        self.assertEqual([item["index"] for item in resumed["questions"]], [1])
        done = self.build(log(*self.finished("dev_0", "a"), *self.finished("dev_1", "b")))
        self.assertEqual(done["cursor"], 2)
        self.assertEqual(self.build("", since=99)["questions"], [])


class JudgeTests(unittest.TestCase):
    QUESTION = {"instance_id": "dev_0", "db_id": "concert_singer", "query": "SELECT name FROM singer"}

    def judge(self, outcomes):
        from demo.run_timeline import make_judge

        calls = []

        def execute(db_id, sql):
            calls.append((db_id, sql))
            return outcomes[sql]

        return make_judge(execute), calls

    def test_same_rows_in_any_order_is_a_hit_and_previews_the_prediction(self):
        judge, _calls = self.judge({
            "SELECT name FROM singer": {"ok": True, "rows": [("a",), ("b",)], "ms": 1.0},
            "SELECT Name FROM singer;": {"ok": True, "rows": [("b",), ("a",)], "ms": 2.0},
        })
        verdict = judge(self.QUESTION, "SELECT Name FROM singer;")
        self.assertIs(verdict["ex"], True)
        self.assertEqual(verdict["result"], {"ok": True, "n": 2, "head": [["b"], ["a"]], "ms": 2.0})

    def test_order_matters_when_the_gold_query_orders(self):
        question = {**self.QUESTION, "query": "SELECT name FROM singer ORDER BY age"}
        judge, _ = self.judge({
            question["query"]: {"ok": True, "rows": [("a",), ("b",)], "ms": 1},
            "SELECT name FROM singer": {"ok": True, "rows": [("b",), ("a",)], "ms": 1},
        })
        self.assertIs(judge(question, "SELECT name FROM singer")["ex"], False)

    def test_a_prediction_that_does_not_run_is_a_miss_with_its_error(self):
        judge, _ = self.judge({
            "SELECT name FROM singer": {"ok": True, "rows": [("a",)], "ms": 1},
            "SELEC": {"ok": False, "error": "syntax error", "ms": 0.1},
        })
        verdict = judge(self.QUESTION, "SELEC")
        self.assertIs(verdict["ex"], False)
        self.assertEqual(verdict["result"], {"ok": False, "error": "syntax error", "ms": 0.1})

    def test_an_unrunnable_gold_query_leaves_the_verdict_unknown(self):
        judge, _ = self.judge({
            "SELECT name FROM singer": {"ok": False, "error": "no database", "ms": 0},
            "SELECT 1": {"ok": True, "rows": [(1,)], "ms": 1},
        })
        self.assertIsNone(judge(self.QUESTION, "SELECT 1")["ex"])

    def test_the_gold_query_runs_once_per_question(self):
        judge, calls = self.judge({
            "SELECT name FROM singer": {"ok": True, "rows": [], "ms": 1},
            "SELECT 1": {"ok": True, "rows": [], "ms": 1},
        })
        judge(self.QUESTION, "SELECT 1")
        judge(self.QUESTION, "SELECT 1")
        self.assertEqual([sql for _db, sql in calls].count("SELECT name FROM singer"), 1)


class TimelineApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from demo import api_server

        api_server.app.config.update(TESTING=True)
        cls.api = api_server

    def setUp(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        run = root / "runs" / "spider-c3sql-abc"
        (run / "data_source").mkdir(parents=True)
        (run / "data_source" / "spider_dev_2.json").write_text(json.dumps(QUESTIONS), encoding="utf-8")
        self.log = root / "run.log"
        self.log.write_text(log(
            f"[t] [INFO]  workspace {run}/pred_sql",
            *[
                trace("dev_0", "generate", "pred_sql", "SELECT count(*) FROM singer", 1.0),
                "[t] [INFO]  样本 dev_0 处理完成 (2.0s)",
                "[t] [INFO]  样本 dev_1 @ generate",
            ],
        ), encoding="utf-8")
        for registry in (self.api._jobs, self.api._job_owners, self.api._job_credentials):
            registry.clear()
        self.api._jobs["job1"] = {
            "job_id": "job1", "dataset": "spider", "method": "c3sql", "status": "running",
            "log_path": str(self.log),
        }
        patcher = patch.object(self.api, "workspace_run_dir", side_effect=lambda name: root / "runs" / name)
        patcher.start()
        self.addCleanup(patcher.stop)
        judging = patch.object(self.api, "_timeline_execute", return_value={"ok": True, "rows": [(6,)], "ms": 1.0})
        judging.start()
        self.addCleanup(judging.stop)
        self.client = self.api.app.test_client()

    def test_a_job_timeline_follows_the_live_log(self):
        body = self.client.get("/api/evaluations/job1/timeline").json
        self.assertEqual(body["method"], "c3sql")
        self.assertEqual(body["status"], "running")
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["cursor"], 1)
        first, second = body["questions"]
        self.assertEqual(first["question"], "How many singers?")
        self.assertIs(first["run"]["ex"], True)
        self.assertEqual(second["run"]["state"], "running")

    def test_since_returns_only_what_may_still_change(self):
        body = self.client.get("/api/evaluations/job1/timeline?since=1").json
        self.assertEqual([item["index"] for item in body["questions"]], [1])

    def test_an_unknown_job_is_not_found(self):
        self.assertEqual(self.client.get("/api/evaluations/nope/timeline").status_code, 404)

    def test_the_log_path_never_reaches_the_client(self):
        self.assertNotIn(self.tmp.name, self.client.get("/api/evaluations/job1/timeline").get_data(as_text=True))

    def test_hosted_runs_withhold_questions_of_benchmarks_that_are_not_published(self):
        import os
        from unittest.mock import patch

        self.api._jobs["job1"]["dataset"] = "bird"
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}):
            self.api._job_owners["job1"] = "visitor"
            client = self.api.app.test_client()
            client.set_cookie("squrve_session", "visitor")
            body = client.get("/api/evaluations/job1/timeline").json
            self.assertIsNone(body["questions"][0]["question"])
            self.assertIsNone(body["questions"][0]["gold_sql"])
            self.assertEqual(body["questions"][0]["run"]["pred_sql"], "SELECT count(*) FROM singer")
            self.api._jobs["job1"]["dataset"] = "spider"
            self.assertEqual(client.get("/api/evaluations/job1/timeline").json["questions"][0]["question"], "How many singers?")
            self.assertEqual(self.api.app.test_client().get("/api/evaluations/job1/timeline").status_code, 404)

    def test_an_experiment_timeline_gathers_its_cells(self):
        from unittest.mock import patch

        snapshot = {
            "experiment_id": "exp1", "status": "running",
            "cells": [{"cell_id": "c1", "method": "c3sql", "dataset": "spider", "job_id": "job1"},
                      {"cell_id": "c2", "method": "dinsql", "dataset": "spider", "job_id": None}],
        }
        with patch.object(self.api._experiments, "get", return_value=snapshot):
            body = self.client.get("/api/experiments/exp1/timeline").json
        self.assertEqual([cell["cell_id"] for cell in body["cells"]], ["c1", "c2"])
        self.assertEqual(body["cells"][0]["questions"][0]["id"], "dev_0")
        self.assertEqual(body["cells"][1]["questions"], [])
        self.assertEqual(body["cursor"], 0)


if __name__ == "__main__":
    unittest.main()
