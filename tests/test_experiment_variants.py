"""Experiment variants: a method with some stages swapped to another Actor, through the HTTP API."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from demo.variants import variant_key
from reproduce.lib.stage_overrides import STAGE_ACTORS_ENV, apply_stage_overrides, stage_overrides_from_env
from tests.test_experiments_api import ExperimentApiTestCase
from tests.test_results_api import ResultsApiTestCase, make_scores

SWAP = {"c3sql_generate": "FINSQLGenerator"}
VARIANT = variant_key("c3sql", SWAP)


class StageActorCatalogTests(ExperimentApiTestCase):
    def test_capabilities_list_the_actors_each_task_type_can_take(self):
        stage_actors = self.client.get("/api/capabilities").json["stage_actors"]
        self.assertIn("C3SQLGenerator", stage_actors["GenerateTask"])
        self.assertIn("FINSQLGenerator", stage_actors["GenerateTask"])
        self.assertNotIn("C3SQLGenerator", stage_actors["ReduceTask"])

    def test_each_configuration_says_whether_its_benchmark_is_installed(self):
        configs = self.client.get("/api/capabilities").json["reproduce_configs"]
        self.assertTrue(all(isinstance(config["installed"], bool) for config in configs))


class CreateVariantTests(ExperimentApiTestCase):
    def test_a_variant_runs_next_to_its_method(self):
        response = self.create([
            {"method": "c3sql", "dataset": "spider"},
            {"method": "c3sql", "dataset": "spider", "stages": SWAP},
        ])
        self.assertEqual(response.status_code, 202)
        original, variant = response.json["cells"]
        self.assertEqual((original["variant"], original["stages"], original["label"]), ("c3sql", {}, "c3sql"))
        self.assertEqual(variant["variant"], VARIANT)
        self.assertEqual(variant["stages"], SWAP)
        self.assertEqual(variant["label"], "c3sql · c3sql_generate→FINSQLGenerator")
        self.assertEqual(self.backend.launched_stages, [{}, SWAP])
        self.assertEqual(self.backend.launched[0][2], self.backend.launched[1][2], "both share the sampling")

    def test_identical_variants_are_merged_and_a_no_op_swap_is_the_method(self):
        response = self.create([
            {"method": "c3sql", "dataset": "spider", "stages": SWAP},
            {"method": "c3sql", "dataset": "spider", "stages": dict(SWAP)},
            {"method": "c3sql", "dataset": "spider", "stages": {"c3sql_generate": "C3SQLGenerator"}},
            {"method": "c3sql", "dataset": "spider"},
        ])
        self.assertEqual([cell["variant"] for cell in response.json["cells"]], [VARIANT, "c3sql"])

    def test_several_variants_and_several_swaps_per_variant(self):
        both = {"c3sql_generate": "FINSQLGenerator", "c3sql_reduce": "FINSQLReducer"}
        response = self.create([
            {"method": "c3sql", "dataset": "spider", "stages": SWAP},
            {"method": "c3sql", "dataset": "spider", "stages": both},
        ])
        self.assertEqual(response.status_code, 202)
        self.assertEqual([cell["stages"] for cell in response.json["cells"]], [SWAP, dict(sorted(both.items()))])
        self.assertNotEqual(response.json["cells"][0]["variant"], response.json["cells"][1]["variant"])

    def test_disallowed_swaps_are_rejected_with_a_reason_per_stage(self):
        response = self.create([
            {"method": "c3sql", "dataset": "spider", "stages": {
                "c3sql_reduce": "C3SQLGenerator",
                "no_such_stage": "FINSQLGenerator",
                "c3sql_generate": "NotAnActor",
            }},
        ])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["code"], "invalid_stages")
        problems = {item["stage"]: item["reason"] for item in response.json["cells"][0]["stages"]}
        self.assertEqual(problems, {
            "c3sql_reduce": "actor_not_allowed",
            "no_such_stage": "unknown_stage",
            "c3sql_generate": "actor_not_allowed",
        })
        self.assertEqual(self.backend.launched, [])

    def test_stages_must_be_a_mapping(self):
        response = self.create([{"method": "c3sql", "dataset": "spider", "stages": ["FINSQLGenerator"]}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.backend.launched, [])


class VariantResultsTests(ResultsApiTestCase):
    def variant_scores(self, correct, **kwargs):
        return {**make_scores("c3sql", "spider", correct=correct, **kwargs), "stage_overrides": SWAP}

    def test_a_variant_is_its_own_column_in_an_experiment(self):
        experiment = self.client.post("/api/experiments", json={
            "cells": [
                {"method": "c3sql", "dataset": "spider"},
                {"method": "c3sql", "dataset": "spider", "stages": SWAP},
            ],
            "sampling": {"limit": 3, "mode": "slice", "seed": 42},
        }).json
        self.backend.finish("job1", scores=make_scores("c3sql", "spider", correct=[1, 0, 0]))
        self.backend.finish("job2", scores=self.variant_scores([1, 1, 0]))
        payload = self.client.get("/api/results/matrix", query_string={
            "methods": f"c3sql,{VARIANT}", "datasets": "spider", "experiment_id": experiment["experiment_id"],
        }).json
        self.assertAlmostEqual(self.cell(payload, "c3sql", "spider")["values"]["ex"], 1 / 3)
        self.assertAlmostEqual(self.cell(payload, VARIANT, "spider")["values"]["ex"], 2 / 3)
        self.assertEqual(payload["labels"][VARIANT], "c3sql · c3sql_generate→FINSQLGenerator")
        self.assertEqual(payload["labels"]["c3sql"], "c3sql")
        self.assertTrue(payload["alignment"]["spider"]["aligned"])

    def test_an_archived_variant_never_stands_in_for_its_method(self):
        self.archive(self.variant_scores([1, 1, 1], timestamp="2026-10-22T10:00:00"))
        self.archive(make_scores("c3sql", "spider", correct=[0, 0, 1], timestamp="2026-10-21T10:00:00"))
        payload = self.matrix(methods=f"c3sql,{VARIANT}", datasets="spider").json
        self.assertAlmostEqual(self.cell(payload, "c3sql", "spider")["values"]["ex"], 1 / 3)
        self.assertAlmostEqual(self.cell(payload, VARIANT, "spider")["values"]["ex"], 1.0)

    def test_the_question_diff_names_the_variant(self):
        variant = self.variant_scores([1, 1, 0], run_id="spider-c3sql-variant")
        self.archive(variant)
        self.archive(make_scores("c3sql", "spider", correct=[1, 0, 0], run_id="spider-c3sql-plain"))
        payload = self.client.get("/api/results/diff", query_string={
            "a": "spider-c3sql-plain", "b": "spider-c3sql-variant",
        }).json
        self.assertEqual(payload["a"]["variant"], "c3sql")
        self.assertEqual(payload["b"]["variant"], VARIANT)
        self.assertEqual(payload["b"]["stages"], SWAP)
        self.assertEqual(payload["summary"]["b_only"], 1)

    def test_the_archive_marks_a_variant_run(self):
        self.archive(self.variant_scores([1, 0, 1], run_id="spider-c3sql-variant"))
        detail = self.client.get("/api/archive/spider-c3sql-variant").json
        self.assertEqual(detail["variant"], VARIANT)
        self.assertEqual(detail["stages"], SWAP)


class VariantRunConfigTests(unittest.TestCase):
    """The run of a variant swaps Actors in its own runtime config, never in the repository."""

    def setUp(self):
        from demo import api_server

        self.api_server = api_server
        self.config_path = Path(api_server._project_root) / "reproduce" / "configs" / "spider" / "c3sql.json"
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))

    def overrides(self, stages):
        configs = self.api_server._config_catalog()
        entry = next(item for item in configs if (item["method"], item["dataset"]) == ("c3sql", "spider"))
        from demo.variants import runner_overrides

        return runner_overrides(Path(self.api_server._project_root), configs, entry, stages)

    def test_the_swapped_actor_brings_its_settings_and_keeps_the_stage_output(self):
        overrides = self.overrides(SWAP)
        swapped = apply_stage_overrides(self.config, overrides)
        generate = next(task for task in swapped["task"]["task_meta"] if task["task_id"] == "c3sql_generate")
        self.assertEqual(generate["meta"]["task"]["generate_type"], "FINSQLGenerator")
        self.assertTrue(generate["meta"]["actor"]["use_cot"], "FinSQL's own generator settings")
        self.assertEqual(generate["meta"]["actor"]["save_dir"], "../files/pred_sql/spider_c3sql")
        untouched = [task for task in swapped["task"]["task_meta"] if task["task_id"] != "c3sql_generate"]
        self.assertEqual(untouched, [task for task in self.config["task"]["task_meta"] if task["task_id"] != "c3sql_generate"])
        self.assertEqual(json.loads(self.config_path.read_text(encoding="utf-8")), self.config, "file on disk unchanged")

    def test_an_unknown_stage_fails_the_run(self):
        with self.assertRaises(ValueError):
            apply_stage_overrides(self.config, {"no_such_stage": {"actor": "FINSQLGenerator"}})

    def test_the_job_hands_its_swaps_to_the_runner(self):
        job = {"job_id": "j1", "dataset": "spider", "method": "c3sql", "sample_limit": 3,
               "sample_mode": "slice", "sample_seed": 42, "stage_overrides": self.overrides(SWAP)}
        environment = self.api_server._job_environment(job)
        self.assertEqual(stage_overrides_from_env(environment), job["stage_overrides"])
        plain = self.api_server._job_environment({**job, "stage_overrides": {}})
        self.assertEqual(stage_overrides_from_env(plain), {})

    def test_a_plain_job_ignores_swaps_left_in_the_server_environment(self):
        job = {"job_id": "j1", "dataset": "spider", "method": "c3sql", "sample_limit": 3,
               "sample_mode": "slice", "sample_seed": 42}
        with patch.dict(os.environ, {STAGE_ACTORS_ENV: json.dumps({"x": {"actor": "y"}})}, clear=False):
            self.assertNotIn(STAGE_ACTORS_ENV, self.api_server._job_environment(job))


if __name__ == "__main__":
    unittest.main()
