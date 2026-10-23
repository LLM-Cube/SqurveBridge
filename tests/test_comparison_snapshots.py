"""Comparison snapshots: a benchmark comparison kept as it was, and checked against the runs it names."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from demo import comparison_snapshots as snapshots

MEMBERS = [
    {"method": "c3sql", "variant": "c3sql", "label": "C3SQL", "run_id": "spider-c3sql-a", "stages": {}},
    {"method": "c3sql", "variant": "c3sql.var-1a2b3c", "label": "C3SQL · generate→FINSQLGenerator",
     "run_id": "spider-c3sql-b", "stages": {"c3sql_generate": "FINSQLGenerator"}},
]
PAYLOAD = {
    "name": "spider comparison",
    "benchmark": "spider",
    "source": "archive",
    "sampling": {"limit": 20, "mode": "slice", "seed": 42},
    "members": MEMBERS,
}


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_a_saved_snapshot_is_read_back_as_it_was(self):
        saved = snapshots.save(self.root, PAYLOAD)
        loaded = snapshots.load(self.root, saved["id"], run_exists=lambda run_id: True)
        self.assertEqual(loaded["name"], "spider comparison")
        self.assertEqual(loaded["benchmark"], "spider")
        self.assertEqual(loaded["sampling"], PAYLOAD["sampling"])
        self.assertEqual([m["run_id"] for m in loaded["members"]], ["spider-c3sql-a", "spider-c3sql-b"])
        self.assertEqual(loaded["members"][1]["stages"], {"c3sql_generate": "FINSQLGenerator"})

    def test_a_member_whose_run_is_gone_is_flagged_not_dropped(self):
        saved = snapshots.save(self.root, PAYLOAD)
        loaded = snapshots.load(self.root, saved["id"], run_exists=lambda run_id: run_id != "spider-c3sql-b")
        self.assertEqual([m["status"] for m in loaded["members"]], ["ok", "run_missing"])
        self.assertEqual(loaded["members"][1]["run_id"], "spider-c3sql-b", "the number's source is still named")

    def test_a_snapshot_must_name_a_benchmark_and_at_least_one_member(self):
        with self.assertRaises(ValueError):
            snapshots.save(self.root, {**PAYLOAD, "benchmark": ""})
        with self.assertRaises(ValueError):
            snapshots.save(self.root, {**PAYLOAD, "members": []})

    def test_members_must_all_belong_to_the_snapshot_benchmark(self):
        with self.assertRaises(ValueError):
            snapshots.save(self.root, {**PAYLOAD, "members": [{**MEMBERS[0], "dataset": "bird"}]})

    def test_listing_shows_summaries_and_unknown_ids_are_absent(self):
        first = snapshots.save(self.root, PAYLOAD)
        second = snapshots.save(self.root, {**PAYLOAD, "name": "second"})
        listed = snapshots.list_snapshots(self.root)
        self.assertEqual({item["id"] for item in listed}, {first["id"], second["id"]})
        self.assertEqual({item["name"] for item in listed}, {"spider comparison", "second"})
        self.assertIsNone(snapshots.load(self.root, "missing", run_exists=lambda run_id: True))

    def test_ids_cannot_escape_the_snapshot_folder(self):
        self.assertIsNone(snapshots.load(self.root, "../secret", run_exists=lambda run_id: True))
        self.assertIsNone(snapshots.load(self.root, "a/b", run_exists=lambda run_id: True))


if __name__ == "__main__":
    unittest.main()


class SnapshotApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from demo import api_server

        api_server.app.config.update(TESTING=True)
        cls.api_server = api_server

    def setUp(self):
        import os
        from unittest.mock import patch

        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        env = patch.dict(os.environ, {"SQURVE_WORKSPACE_DIR": self.workspace.name, "SQURVE_DEPLOYMENT_TARGET": "local"}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.client = self.api_server.app.test_client()
        self.existing = {"run-a"}
        run_patch = patch.object(self.api_server, "_resolve_archive_run", side_effect=lambda run_id: ("artifacts", Path(run_id)) if run_id in self.existing else None)
        run_patch.start()
        self.addCleanup(run_patch.stop)

    def test_a_local_demo_saves_and_reopens_a_snapshot(self):
        response = self.client.post("/api/comparison-snapshots", json=PAYLOAD)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        snapshot_id = response.json["id"]
        listed = self.client.get("/api/comparison-snapshots").json["snapshots"]
        self.assertEqual([item["id"] for item in listed], [snapshot_id])
        opened = self.client.get(f"/api/comparison-snapshots/{snapshot_id}").json
        self.assertEqual(opened["benchmark"], "spider")
        self.assertEqual({member["status"] for member in opened["members"]}, {"run_missing"}, "no archived run matches these ids")

    def test_a_missing_snapshot_is_a_404(self):
        self.assertEqual(self.client.get("/api/comparison-snapshots/000000000000").status_code, 404)

    def test_the_hosted_space_reads_snapshots_but_cannot_save_one(self):
        from unittest.mock import patch

        with patch.dict(os.environ, {"SQURVE_DEPLOYMENT_TARGET": "hf-space"}, clear=False):
            response = self.client.post("/api/comparison-snapshots", json=PAYLOAD)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json["reason"], "local_only")
            self.assertEqual(self.client.get("/api/comparison-snapshots").status_code, 200)

    def test_snapshot_values_come_from_the_run_and_changed_scores_are_flagged(self):
        data = {'dataset': 'spider', 'split': 'dev', 'sampling': PAYLOAD['sampling'],
                'values': {'ex': .5}, 'cost': {'tokens_total': 12}, 'sample_count': 2,
                'sample_hash': 'same', 'scores_hash': 'original'}
        self.existing = {'run-a'}
        body = {**PAYLOAD, 'split': 'dev', 'members': [{**MEMBERS[0], 'run_id': 'run-a', 'values': {'ex': 1}}]}
        with patch.object(self.api_server, '_snapshot_run_data', return_value=data):
            response = self.client.post('/api/comparison-snapshots', json=body)
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json['members'][0]['values']['ex'], .5)
        with patch.object(self.api_server, '_snapshot_run_data', return_value={**data, 'scores_hash': 'changed'}):
            loaded = self.client.get('/api/comparison-snapshots/' + response.json['id'])
            self.assertEqual(loaded.json['members'][0]['status'], 'run_changed')

    def test_snapshot_rejects_mismatched_sampling_and_invalid_shapes(self):
        data = {'dataset': 'spider', 'split': 'dev', 'sampling': {'limit': 50, 'mode': 'slice'}}
        with patch.object(self.api_server, '_snapshot_run_data', return_value=data):
            self.assertEqual(self.client.post('/api/comparison-snapshots', json=PAYLOAD).status_code, 400)
        for body in ([1], {**PAYLOAD, 'sampling': []}, {**PAYLOAD, 'source': 'unknown'}):
            self.assertEqual(self.client.post('/api/comparison-snapshots', json=body).status_code, 400)
