"""Strict Evo decisions are auditable independently of execution success."""
import copy
import tempfile
import unittest
from pathlib import Path
from reproduce.evolve.verification import assess, freeze_policy, verify_attempt, seal_attempt
from core.evidence import EvidenceRun


class VerifiedEvolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = {"version": "evo-acceptance-v1", "budget": {"rollouts": 2}, "sample_selection": {"mode": "fixed_ids", "sample_ids": ["0", "1"]}, "weights": {"ex": 1}, "required_metrics": ["ex"], "minimum_improvement": 0, "max_regression_rate": .5, "tolerance": 1e-9, "evidence_dependencies": {"ex": ["runtime"]}}
        self.before = self.scores('before', [0, 1])
        self.after = self.scores('after', [1, 1])

    def scores(self, run_id, values):
        return {"run_id": run_id, "database_snapshot": "synthetic-snapshot", "candidate_protocol": "synthetic-final@1", "evaluator_protocol": "synthetic-eval@1", "model_config_id": "controlled@1", "dataset": "synthetic", "split": "dev", "ex_variant": "custom", "sample_count": len(values), "aggregate": {"ex": {"avg": sum(values) / len(values)}}, "per_sample": [{"instance_id": str(i), "ex": value} for i, value in enumerate(values)]}

    def test_missing_active_metric_is_unavailable_not_neutral(self):
        policy = {**self.policy, "weights": {"ex": 1, "em": .1}}
        result = assess(self.before, self.after, policy)
        self.assertFalse(result['computable'])
        self.assertIsNone(result['fitness'])
        self.assertEqual(result['inputs']['ex']['candidate'], 1)

    def test_cohort_and_aggregate_conflicts_reject_without_erasing_valid_metrics(self):
        for change in ({"split": "test"}, {"ex_variant": "bird"}, {"aggregate": {"ex": {"avg": .7}}}):
            result = assess(self.before, {**self.after, **change}, self.policy)
            self.assertFalse(result['accepted'])
        result = assess(self.before, self.after, self.policy)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['fitness'], .5)

    def test_policy_cannot_be_lowered_after_results(self):
        freeze_policy(self.root, self.policy)
        with self.assertRaises(ValueError):
            freeze_policy(self.root, {**self.policy, "required_metrics": []})

    def test_sealed_attempt_recomputes_offline_and_detects_tampering(self):
        for name in ('baseline', 'candidate'):
            with EvidenceRun(self.root / name, run_id='before' if name == 'baseline' else 'after'):
                pass
        seal_attempt(self.root, baseline=self.before, candidate=self.after, policy=self.policy, actual_diff='diff --git a/test b/test\n', execution={"base_commit": "abc", "base_tree": "def", "command": ["synthetic"], "returncode": 0}, review={"approved": True, "human_required": False}, baseline_evidence='baseline', candidate_evidence='candidate')
        result = verify_attempt(self.root)
        self.assertFalse(result['accepted'], result)
        self.assertTrue(any('offline_evaluation' in item for item in result['issues']))
        (self.root / 'candidate.scores.json').write_text('{}')
        self.assertFalse(verify_attempt(self.root)['accepted'])


class RealEvolutionTests(unittest.TestCase):
    def test_real_orchestration_retains_worktree_evidence_and_verifies_without_model_calls(self):
        import json
        import os
        import subprocess
        import sys
        from reproduce.evolve.verified_search import VerifiedSimulator, action_hash, run_verified_search
        from reproduce.evolve.mcts.expand import Action
        from reproduce.evolve.journal import EvolutionJournal
        from reproduce.evolve.review import ReviewState, ReviewRound
        from reproduce.evolve.verification import write_json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'repo'
            repo.mkdir()
            script = r'''
import json, os, sqlite3
from pathlib import Path
from types import SimpleNamespace
from core.actor.base import Actor
from core.engine import Engine
from core.llm.token_logger import TokenLogger, observe_llm_call
from core.db_connect import execute_sql
from reproduce.runner.run import execute_with_evo_evidence, evo_evidence_directory, _write_runtime_config
from reproduce.evolve.evaluation import build_evaluation
class ControlledModel:
    model_name = 'controlled'
    token_logger = TokenLogger()
    @observe_llm_call
    def complete(self, prompt):
        self.token_logger.record({'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3}, model=self.model_name)
        return 'SELECT 0'
class Generator(Actor):
    __module__ = 'core.actor.generator.synthetic'
    def act(self, item, **kwargs):
        sql = ControlledModel().complete('synthetic')
        execute_sql('sqlite', 'db.sqlite', sql, None)
        outputs.append(sql)
        return {'pred_sql': sql}
outputs = []
connection = sqlite3.connect('db.sqlite')
connection.execute('CREATE TABLE IF NOT EXISTS synthetic (value INTEGER)')
connection.commit()
connection.close()
class Task:
    name, task_id = 'controlled', 'synthetic-task'
    def run(self):
        actor = Generator()
        for index in range(2): actor.act(index)
engine = Engine.__new__(Engine)
engine.exec_process = []
engine.parse_exec_process = lambda _: Task()
run_id = os.environ['SQURVE_EVO_RUN_ID']
_write_runtime_config({'llm': {'model_name': 'controlled'}}, run_id)
execute_with_evo_evidence(engine, run_id)
rows = [{'index': i, 'instance_id': str(i), 'db_id': '', 'db_type': 'sqlite', 'gold_sql': 'SELECT 1', 'pred_sql': sql, 'ex': int(sql == 'SELECT 1')} for i, sql in enumerate(outputs)]
scores = {'run_id': run_id, 'dataset': 'synthetic', 'split': 'dev', 'method': 'synthetic', 'ex_variant': 'custom', 'sample_count': 2, 'aggregate': {'ex': {'avg': sum(row['ex'] for row in rows)/2}}, 'per_sample': rows, 'config_snapshot': {'llm': {'model_name': 'controlled'}}}
Path('scores.json').write_text(json.dumps(scores))
build_evaluation(scores, SimpleNamespace(db_path='db.sqlite'), evo_evidence_directory(run_id), scores['config_snapshot'])
'''
            (repo / 'controlled.py').write_text(script)
            for command in (['git', 'init', '-q'], ['git', 'add', 'controlled.py'], ['git', '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.org', 'commit', '-qm', 'synthetic fixture']):
                subprocess.run(command, cwd=repo, check=True, capture_output=True)
            action = Action('improve', 'controlled replacement', 'ex', 'A', 'low', patches=[{'path': 'controlled.py', 'old_string': "return 'SELECT 0'", 'new_string': "return 'SELECT 1'"}])
            review = ReviewState(1, 'patch', 'controlled.py', rounds=[ReviewRound(1, 'controlled-reviewer')]).to_dict()
            review['action_sha256'] = action_hash(action)
            review_path = root / 'review.json'
            write_json(review_path, review)
            evolved = root / 'evolve'
            evolved.mkdir()
            journal_path = evolved / 'journal.json'
            EvolutionJournal('synthetic').write(journal_path)
            policy = {'version': 'evo-acceptance-v1', 'stage': 'smoke', 'budget': {'rollouts': 2}, 'sample_selection': {'mode': 'fixed_ids', 'sample_ids': ['0', '1']}, 'weights': {'ex': 1}, 'required_metrics': ['ex'], 'minimum_improvement': 0, 'max_regression_rate': 0, 'tolerance': 1e-9, 'evidence_dependencies': {'ex': ['runtime']}, 'collection': {'capture_sql': True, 'capture_outputs': True}}
            simulator = VerifiedSimulator(repo_root=repo, directory=evolved / 'verification', policy=policy,
                                          command=[sys.executable, 'controlled.py'], scores_path='scores.json', reviews={'improve': review_path},
                                          env={'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
            result = run_verified_search(actions=[action], simulator=simulator, journal_path=journal_path, evolve_dir=evolved, rollouts=1)
            self.assertEqual(result['verdict']['verdict'], 'CONTINUE', result)
            attempts = list((evolved / 'verification' / 'attempts').iterdir())
            from unittest.mock import patch
            with patch('subprocess.run', side_effect=AssertionError('offline verification must not execute commands')), patch('reproduce.eval.protocol.execution.SQLExecutor.execute_sqlite', side_effect=AssertionError('offline verification must not execute SQL')):
                verified = verify_attempt(attempts[0])
            self.assertTrue(verified['accepted'], verified)
            self.assertEqual(verified['measurement']['fitness'], 1)
            missing_cost = json.loads((attempts[0] / 'candidate.scores.json').read_text())
            missing_cost['aggregate'].pop('token_total')
            for sample in missing_cost['samples']:
                sample['metrics'].pop('token_total')
            before_cost = json.loads((attempts[0] / 'baseline.scores.json').read_text())
            cost_result = assess(before_cost, missing_cost, {**policy, 'weights': {'ex': 1, 'cost': .1}})
            self.assertIsNone(cost_result['fitness'])
            self.assertEqual(cost_result['inputs']['ex']['candidate'], 1)
            partial_before = json.loads((attempts[0] / 'baseline.scores.json').read_text())
            partial_after = json.loads((attempts[0] / 'candidate.scores.json').read_text())
            for bundle, removed in ((partial_before, 0), (partial_after, 1)):
                bundle['samples'][removed]['metrics'].pop('structural_match')
                observed = bundle['samples'][1 - removed]['metrics']['structural_match']['value']
                bundle['aggregate']['structural_match'].update(value=observed, numerator=observed, denominator=1, coverage=1)
            partial = assess(partial_before, partial_after, {**policy, 'weights': {'ex': 1, 'em': .1}})
            self.assertFalse(partial['computable'])
            self.assertIsNone(partial['fitness'])
            self.assertEqual(partial['inputs']['em']['reason'], 'incomplete_metric_coverage')

            worktrees = subprocess.run(['git', 'worktree', 'list', '--porcelain'], cwd=repo, text=True, capture_output=True, check=True).stdout
            self.assertEqual(worktrees.count('worktree '), 1)
            original_node = EvolutionJournal.read(journal_path).nodes[0].to_dict()
            another = Action.from_dict({**action.to_dict(), 'action_id': 'another-improvement', 'description': 'a separately reviewed attempt'})
            another_review = {**review, 'action_sha256': action_hash(another)}
            another_review_path = root / 'another-review.json'
            write_json(another_review_path, another_review)
            resumed = VerifiedSimulator(repo_root=repo, directory=evolved / 'verification', policy=policy,
                                        command=[sys.executable, 'controlled.py'], scores_path='scores.json', reviews={'another-improvement': another_review_path},
                                        env={'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
            run_verified_search(actions=[another], simulator=resumed, journal_path=journal_path, evolve_dir=evolved, rollouts=1)
            self.assertEqual(len(list((evolved / 'verification' / 'attempts').iterdir())), 2)
            old_node = EvolutionJournal.read(journal_path).get_node(original_node['node_id']).to_dict()
            for key in ('action', 'verification_ref'):
                self.assertEqual(old_node['metadata'][key], original_node['metadata'][key])
            self.assertEqual(len(EvolutionJournal.read(journal_path).nodes), 2)
            self.assertTrue(verify_attempt(attempts[0])['accepted'], 'resuming never overwrites old evidence')
            unreviewed = VerifiedSimulator(repo_root=repo, directory=evolved / 'unreviewed', policy=policy,
                                           command=[sys.executable, 'this-command-must-not-run.py'], scores_path='scores.json', reviews={})
            rejected = unreviewed(action, 0)
            self.assertFalse(rejected['verification']['accepted'])
            self.assertFalse((evolved / 'unreviewed' / 'baselines').exists())
            rejection = next((evolved / 'unreviewed' / 'attempts').iterdir())
            self.assertTrue(verify_attempt(rejection)['inspection_complete'])
            # Corruption is rejected without invoking the original command/model.
            sample_file = next((attempts[0] / 'runtime').glob('**/events/*.json'))
            sample_file.write_text('{}')
            self.assertFalse(verify_attempt(attempts[0])['accepted'])


class StageBoundaryTests(unittest.TestCase):
    def test_smoke_success_cannot_supply_a_rejected_bounded_or_full_decision(self):
        from reproduce.evolve.mcts.orchestrator import run_bounded_funnel
        from reproduce.evolve.mcts.expand import Action
        from reproduce.evolve.journal import EvolutionJournal
        class ControlledStage:
            strict_evidence = True
            def __init__(self, accepted): self.accepted = accepted
            def __call__(self, action, iteration):
                return {'score': 1, 'verification': {'accepted': self.accepted, 'verifiable': self.accepted,
                        'measurement': {'computable': self.accepted, 'fitness': .5 if self.accepted else None},
                        'issues': [] if self.accepted else ['incomplete_evidence']}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = root / 'journal.json'
            EvolutionJournal('controlled').write(journal)
            def full(action, iteration):
                raise AssertionError('full execution must not run after bounded rejection')
            result = run_bounded_funnel(actions=[Action('candidate', 'controlled', 'ex', 'A', 'low', run_command='controlled')],
                                        smoke_simulator=ControlledStage(True), bounded_simulator=ControlledStage(False),
                                        full_simulator=full, journal_path=journal, evolve_dir=root,
                                        smoke_rollouts=1, bounded_rollouts=1)
            self.assertIsNone(result['full'])
            self.assertIsNone(EvolutionJournal.read(journal).best_node)
            rejected = [node for node in EvolutionJournal.read(journal).nodes if node.stage == 'bounded'][0]
            self.assertIsNone(rejected.fitness)
            self.assertEqual(rejected.metadata['scheduling_control'], -2)
