from __future__ import annotations

import unittest

from auditgym.environment_adapter import environment_from_public_episode
from auditgym.evaluator_oracle import EvaluatorOracle
from auditgym.generator import generate
from auditgym.policies import EvaluationMetrics, MarkEverythingPolicy, RandomExplorer, SimpleReconciliationPolicy, run_policy
from space_team_genesis.environment import EvaluationResult

CONFIG = {"dataset_profile": "train", "company_size": "small", "enabled_issue_types": ["duplicate_invoice", "payment_allocation", "fx_date_or_rate", "reporting_period"], "max_steps": 100, "time_limit_seconds": 60, "evaluation_mode": "open"}
class Evaluator:
    def evaluate(self, findings, inspection_steps): return EvaluationResult(0.5)
class Metrics:
    def metrics_for_completed_episode(self, environment): return EvaluationMetrics(1.0, 1.0, 1.0)
class Provider:
    def findings_for_episode(self, episode_id): return {"episode_id": episode_id, "findings": []}
class Session:
    def submit_private_oracle(self, episode_id, findings): return {"episode_id": episode_id, "accepted": True}

class PolicyTests(unittest.TestCase):
    def environment(self): return environment_from_public_episode(generate(1001, CONFIG), Evaluator())
    def test_public_policies_finish_and_collect_runner_metrics(self):
        for policy in (RandomExplorer(7), SimpleReconciliationPolicy(), MarkEverythingPolicy()):
            with self.subTest(policy=policy.name):
                result = run_policy(self.environment(), policy, metrics_provider=Metrics())
                self.assertTrue(result.terminated)
                self.assertEqual(result.metrics.f1, 1.0)
                self.assertLess(result.inspection_steps, 100)
    def test_random_policy_is_deterministic_for_fixed_seed(self):
        one, two = run_policy(self.environment(), RandomExplorer(9)), run_policy(self.environment(), RandomExplorer(9))
        self.assertEqual([x["action"] for x in one.trajectory], [x["action"] for x in two.trajectory])
    def test_oracle_uses_only_the_closed_evaluator_session(self):
        result = EvaluatorOracle(Provider()).run(Session(), "ep_private")
        self.assertEqual(result, {"episode_id": "ep_private", "accepted": True})
