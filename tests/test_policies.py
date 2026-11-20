from __future__ import annotations

import unittest

from auditgym.environment_adapter import environment_from_public_episode
from auditgym.evaluator_oracle import EvaluatorOracle
from auditgym.config import empty_plan
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

    def test_mark_everything_uses_full_budget_and_multiple_issue_types(self):
        run = run_policy(self.environment(), MarkEverythingPolicy())
        submission = run.trajectory[-1]["action"]["findings"]["findings"]
        self.assertEqual(len(submission), 30)
        self.assertEqual(len({item["type"] for item in submission}), 4)
        self.assertEqual(run.trajectory[-1]["observation"]["kind"], "submission")

    def test_simple_reconciliation_finds_planned_duplicate_from_public_data(self):
        plan = empty_plan("small")
        plan["issues"]["duplicate_invoice"] = 1
        episode = generate(51, CONFIG, plan=plan)
        run = run_policy(environment_from_public_episode(episode, Evaluator()), SimpleReconciliationPolicy())
        findings = run.trajectory[-1]["action"]["findings"]["findings"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["type"], "duplicate_invoice")
        self.assertEqual(run.trajectory[-1]["observation"]["kind"], "submission")

    def test_simple_reconciliation_does_not_accuse_clean_distractors(self):
        episode = generate(52, CONFIG, plan=empty_plan("small"))
        run = run_policy(environment_from_public_episode(episode, Evaluator()), SimpleReconciliationPolicy())
        self.assertEqual(run.trajectory[-1]["action"]["findings"]["findings"], [])

    def test_public_adapter_renames_ledger_and_document_ids(self):
        environment = self.environment()
        environment.reset()
        ledger = environment.step({"kind": "list_records", "table": "ledger", "cursor": None, "limit": 1})
        self.assertIn("id", ledger["observation"]["records"][0])
        self.assertNotIn("row_id", ledger["observation"]["records"][0])
        document_id = next(iter(environment.data.documents))
        document = environment.step({"kind": "get_document", "doc_id": document_id})["observation"]["document"]
        self.assertEqual(document["id"], document_id)
        self.assertNotIn("doc_id", document)

    def test_closed_runner_needs_private_provider_to_report_internal_return(self):
        episode = generate(1001, CONFIG)
        environment = environment_from_public_episode(episode, Evaluator(), evaluation_mode="closed")
        public_only = run_policy(environment, RandomExplorer(2))
        self.assertIsNone(public_only.return_)
        self.assertEqual(public_only.trajectory[-1]["reward"], 0)
    def test_oracle_uses_only_the_closed_evaluator_session(self):
        result = EvaluatorOracle(Provider()).run(Session(), "ep_private")
        self.assertEqual(result, {"episode_id": "ep_private", "accepted": True})
