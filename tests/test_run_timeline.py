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


def trace(instance_id, stage, output, result, elapsed, error=None, actor_class=None):
    record = {
        "instance_id": instance_id, "actor_name": stage, "actor_class": actor_class, "stage_name": stage,
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

    def test_a_live_stage_reads_like_the_replays_one_line_summary(self):
        text = log(
            trace("dev_0", "reduce", "instance_schemas", ["singer.name", "singer.age"], 0.4, actor_class="C3SQLReducer"),
            trace("dev_1", "reduce", "instance_schemas", None, 0.2, error="RateLimitError: slow down\nretry later", actor_class="C3SQLReducer"),
        )
        first, second = self.build(text)["questions"]
        self.assertEqual(first["run"]["stages"][0]["text"], "C3SQLReducer · observed · 2 items: singer.name, singer.age")
        self.assertEqual(second["run"]["stages"][0]["text"], "C3SQLReducer · failed · error: RateLimitError: slow down")

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

    def test_a_finished_prediction_is_judged_once_however_often_it_is_polled(self):
        judge, calls = self.judge({
            "SELECT name FROM singer": {"ok": True, "rows": [("a",)], "ms": 1},
            "SELECT 1": {"ok": True, "rows": [("a",)], "ms": 1},
        })
        first = judge(self.QUESTION, "SELECT 1")
        second = judge(self.QUESTION, "SELECT 1")
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 2)

    def test_the_gold_query_runs_once_per_question(self):
        judge, calls = self.judge({
            "SELECT name FROM singer": {"ok": True, "rows": [], "ms": 1},
            "SELECT 1": {"ok": True, "rows": [], "ms": 1},
        })
        judge(self.QUESTION, "SELECT 1")
        judge(self.QUESTION, "SELECT 1")
        self.assertEqual([sql for _db, sql in calls].count("SELECT name FROM singer"), 1)


class WithholdTests(unittest.TestCase):
    def timeline(self):
        stage = {"name": "generate", "kind": "sql", "text": "SELECT secret FROM t", "seconds": 1.0, "state": "done", "error": None}
        run = {"state": "done", "stages": [stage], "pred_sql": "SELECT secret FROM t", "ex": True,
               "result": {"ok": True, "n": 3, "head": [["private"]], "ms": 2.0}}
        return {"questions": [{"index": 0, "id": "q", "db": "d", "question": "Q?", "gold_sql": "SELECT 1", "run": run}]}

    def test_withholding_keeps_the_outcome_and_drops_every_piece_of_benchmark_content(self):
        from demo.run_timeline import withhold_content

        item = withhold_content(self.timeline())["questions"][0]
        self.assertIsNone(item["question"])
        self.assertIsNone(item["gold_sql"])
        run = item["run"]
        self.assertTrue(run["withheld"])
        self.assertEqual(run["pred_sql"], "")
        self.assertEqual(run["stages"][0]["text"], "")
        self.assertEqual(run["stages"][0]["name"], "generate")
        self.assertIs(run["ex"], True)
        self.assertEqual(run["result"], {"ok": True, "n": 3, "ms": 2.0})
        self.assertNotIn("private", json.dumps(item))
        self.assertNotIn("secret", json.dumps(item))

    def test_withholding_keeps_an_error_class_but_not_the_database_message(self):
        from demo.run_timeline import withhold_content

        timeline = self.timeline()
        timeline["questions"][0]["run"]["result"] = {"ok": False, "error": "no such column: salary", "ms": 1}
        run = withhold_content(timeline)["questions"][0]["run"]
        self.assertEqual(run["result"], {"ok": False, "ms": 1})


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
        for registry in (self.api._jobs, self.api._job_owners, self.api._job_credentials, self.api._job_judges):
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
            run = body["questions"][0]["run"]
            self.assertTrue(run["withheld"])
            self.assertEqual(run["pred_sql"], "")
            self.assertEqual(run["stages"][0]["text"], "")
            self.assertIs(run["ex"], True)
            self.assertNotIn("count(*)", json.dumps(body))
            self.api._jobs["job1"]["dataset"] = "spider"
            self.assertEqual(client.get("/api/evaluations/job1/timeline").json["questions"][0]["question"], "How many singers?")
            self.assertEqual(self.api.app.test_client().get("/api/evaluations/job1/timeline").status_code, 404)

    def bundle(self, content):
        from pathlib import Path
        from unittest.mock import patch

        bundle = Path(self.tmp.name) / "evaluations" / "job1" / "score-bundle"
        bundle.mkdir(parents=True)
        (bundle / "scores.json").write_text(content, encoding="utf-8")
        patcher = patch.object(self.api, "_run_dir", Path(self.tmp.name) / "evaluations")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_finished_job_is_corrected_from_its_score_bundle(self):
        self.bundle(json.dumps({"per_sample": [evaluated_sample(ex=0)]}))
        self.api._jobs["job1"]["status"] = "completed"
        body = self.client.get("/api/evaluations/job1/timeline?since=1").json
        self.assertTrue(body["corrected"])
        first = body["questions"][0]
        self.assertEqual(first["run"]["stages"][0]["text"], "ReduceTask · observed · 3 call(s) · pred_schema_count=21")
        self.assertIs(first["run"]["ex"], False)

    def test_a_running_job_ignores_a_score_bundle_from_an_earlier_attempt(self):
        self.bundle(json.dumps({"per_sample": [evaluated_sample(ex=0)]}))
        self.assertFalse(self.client.get("/api/evaluations/job1/timeline").json["corrected"])

    def test_an_unreadable_score_bundle_keeps_the_live_timeline(self):
        self.bundle("{not json")
        self.api._jobs["job1"]["status"] = "completed"
        body = self.client.get("/api/evaluations/job1/timeline").json
        self.assertFalse(body["corrected"])
        self.assertEqual(body["evaluation"], "collection_failed")
        self.assertIs(body["questions"][0]["run"]["ex"], True)

    def test_hosted_correction_still_withholds_unpublished_benchmark_content(self):
        import os
        from unittest.mock import patch

        self.bundle(json.dumps({"per_sample": [evaluated_sample()]}))
        self.api._jobs["job1"].update(status="completed", dataset="bird")
        self.api._job_owners["job1"] = "visitor"
        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}):
            client = self.api.app.test_client()
            client.set_cookie("squrve_session", "visitor")
            body = client.get("/api/evaluations/job1/timeline").json
        self.assertTrue(body["corrected"])
        first = body["questions"][0]
        self.assertIsNone(first["question"])
        self.assertEqual(first["run"]["pred_sql"], "")
        self.assertEqual({stage["text"] for stage in first["run"]["stages"]}, {""})
        self.assertNotIn("FROM singer", json.dumps(body))

    def test_polling_does_not_run_the_same_sql_again(self):
        self.client.get("/api/evaluations/job1/timeline")
        self.client.get("/api/evaluations/job1/timeline")
        self.client.get("/api/evaluations/job1/timeline?since=0")
        self.assertEqual(self.api._timeline_execute.call_count, 2)  # gold and prediction of dev_0, once

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


class ContractTests(unittest.TestCase):
    """The recorded replay and a live run must keep the same shape (see tests/fixtures/run_timeline_contract.json)."""

    def test_live_timeline_has_every_field_the_replay_renderer_reads(self):
        from pathlib import Path

        from demo.run_timeline import build_run_timeline

        contract = json.loads((Path(__file__).parent / "fixtures" / "run_timeline_contract.json").read_text())
        text = log(trace("dev_0", "gen", "pred_sql", "SELECT 1", 0.5), "样本 dev_0 处理完成 (0.5s)")
        item = build_run_timeline(QUESTIONS, text, method="c3sql")["questions"][0]
        for key in contract["question"]:
            self.assertIn(key, item)
        for key in contract["run"]:
            self.assertIn(key, item["run"])
        for stage in item["run"]["stages"]:
            for key in contract["stage"]:
                self.assertIn(key, stage)
            self.assertIn(stage["kind"], contract["stage_kinds"])


def evaluated_sample(**overrides):
    """One per-sample record of a score bundle, trimmed to what the timeline reads."""
    def stage(task_type, status, signals):
        return {"task_type": task_type, "status": status, "signals": signals,
                "runtime": {"call_count": 3, "elapsed_s": 76.292, "errors": []}}

    sample = {
        "instance_id": "dev_0", "db_id": "concert_singer", "hardness": "easy", "ex": 1,
        "pred_sql": "SELECT COUNT(*) FROM singer;", "tokens": {"unknown": 4744}, "act_elapsed_s": 76.295,
        "workflow": {"stages": {
            "c3sql_reduce": stage("ReduceTask", "observed", {"gold_schema_count": None, "pred_schema_count": 21, "fatal_schema_miss": False}),
            "c3sql_parse": stage("ParseTask", "observed", {"linked_schema_count": 15, "missing_gold_schemas": []}),
            "c3sql_generate": stage("GenerateTask", "pass", {"candidate_count": 1}),
        }},
    }
    sample.update(overrides)
    return sample


class EvaluatedStagesTests(unittest.TestCase):
    """The stages of an evaluated question read exactly as the recorded replay shows them."""

    def test_stages_match_the_recorded_replay_of_the_same_sample(self):
        from demo.run_timeline import stages_from_evaluation

        stages = stages_from_evaluation(evaluated_sample())
        # Literal values from the committed replay fixture, question q1, C3SQL.
        self.assertEqual(stages, [
            {"name": "c3sql_reduce", "kind": "text", "text": "ReduceTask · observed · 3 call(s) · pred_schema_count=21", "tokens": 1581, "seconds": 25.43},
            {"name": "c3sql_parse", "kind": "text", "text": "ParseTask · observed · 3 call(s) · linked_schema_count=15", "tokens": 1581, "seconds": 25.43},
            {"name": "c3sql_generate", "kind": "sql", "text": "SELECT COUNT(*) FROM singer;", "tokens": 1581, "seconds": 25.43},
        ])

    def test_a_stage_error_is_named_in_its_summary(self):
        from demo.run_timeline import stages_from_evaluation

        sample = evaluated_sample()
        sample["workflow"]["stages"]["c3sql_parse"]["runtime"]["errors"] = ["RateLimitError: slow down"]
        self.assertTrue(stages_from_evaluation(sample)[1]["text"].endswith("· error: RateLimitError: slow down"))

    def test_tokens_stay_not_provided_when_the_sample_has_no_token_record(self):
        from demo.run_timeline import stages_from_evaluation

        self.assertEqual({stage["tokens"] for stage in stages_from_evaluation(evaluated_sample(tokens={}))}, {None})


class CorrectionTests(unittest.TestCase):
    """Once a cell is evaluated, its timeline is the evaluation's, field for field."""

    def build(self, text="", **kwargs):
        from demo.run_timeline import build_run_timeline

        return build_run_timeline(QUESTIONS, text, method="c3sql", **kwargs)

    def test_an_evaluated_question_shows_the_evaluations_stages_verdict_and_hardness(self):
        live = log(trace("dev_0", "gen", "pred_sql", "SELECT 9", 0.5), "样本 dev_0 处理完成 (0.5s)")
        preview = {"ok": True, "n": 1, "head": [["6"]], "ms": 0.1}
        timeline = self.build(live, evaluation=[evaluated_sample(ex=0)], judge=lambda q, sql: {"ex": True, "result": preview})
        item = timeline["questions"][0]
        self.assertTrue(timeline["corrected"])
        self.assertEqual(item["hardness"], "easy")
        self.assertEqual([stage["name"] for stage in item["run"]["stages"]], ["c3sql_reduce", "c3sql_parse", "c3sql_generate"])
        self.assertEqual({stage["state"] for stage in item["run"]["stages"]}, {"done"})
        self.assertEqual(item["run"]["state"], "done")
        self.assertEqual(item["run"]["pred_sql"], "SELECT COUNT(*) FROM singer;")
        # The verdict is the evaluation's; the live execution only supplies the result preview.
        self.assertIs(item["run"]["ex"], False)
        self.assertEqual(item["run"]["verdict_source"], "evaluation")
        self.assertEqual(item["run"]["result"], preview)

    def test_an_evaluated_stage_that_recorded_an_error_is_failed_not_done(self):
        sample = evaluated_sample()
        sample["workflow"]["stages"]["c3sql_parse"]["runtime"]["errors"] = ["RateLimitError: slow down"]
        stages = self.build(evaluation=[sample])["questions"][0]["run"]["stages"]
        self.assertEqual([stage["state"] for stage in stages], ["done", "failed", "done"])
        self.assertEqual(stages[1]["error"], "RateLimitError: slow down")

    def test_a_corrected_timeline_repeats_every_question_so_a_client_replaces_what_it_saw(self):
        both = [evaluated_sample(), evaluated_sample(instance_id="dev_1", db_id="pets_1")]
        timeline = self.build(evaluation=both, since=2)
        self.assertEqual([item["index"] for item in timeline["questions"]], [0, 1])
        self.assertEqual(timeline["cursor"], 2)
        again = self.build(evaluation=both, since=2)
        self.assertEqual(again, timeline)

    def test_a_question_missing_from_the_evaluation_keeps_its_live_content(self):
        live = log(trace("dev_1", "gen", "pred_sql", "SELECT age FROM pets", 0.5), "样本 dev_1 处理完成 (0.5s)")
        item = self.build(live, evaluation=[evaluated_sample()])["questions"][1]
        self.assertEqual(item["run"]["pred_sql"], "SELECT age FROM pets")

    def test_a_live_question_carries_the_hardness_of_its_gold_sql(self):
        item = self.build()["questions"][0]
        self.assertEqual(item["hardness"], "easy")
        self.assertFalse(self.build()["corrected"])
