"""Offline validation for reproducibility and public-data isolation (issue #7)."""
from __future__ import annotations

import copy
import json
import unittest

from auditgym.books import Books, audit
from auditgym.config import empty_plan
from auditgym.environment_adapter import environment_from_public_episode, public_episode_data
from auditgym.generator import generate
from space_team_genesis.environment import AuditEnvironment, EnvironmentRuntime, EvaluationResult

CONFIG = {"dataset_profile": "train", "company_size": "small", "enabled_issue_types": ["duplicate_invoice", "payment_allocation", "fx_date_or_rate", "reporting_period"], "max_steps": 100, "time_limit_seconds": 60, "evaluation_mode": "open"}

class PublicGenerator:
    def generate(self, seed, config):
        return public_episode_data(generate(seed, config))

class Evaluator:
    def evaluate(self, findings, inspection_steps): return EvaluationResult(0.0)

class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now

class ValidationTests(unittest.TestCase):
    def actions(self, runtime):
        out = [runtime.reset(17, CONFIG)]
        out.append(runtime.step({"kind": "list_records", "table": "ledger", "cursor": None, "limit": 2}))
        out.append(runtime.step({"kind": "calculator", "expression": "100 / 4"}))
        out.append(runtime.step({"kind": "submit", "findings": {"episode_id": out[0]["info"]["episode_id"], "findings": []}}))
        return out, runtime.trajectory()

    def test_same_seed_config_and_actions_reproduce_public_transitions(self):
        first, first_trajectory = self.actions(EnvironmentRuntime(PublicGenerator(), evaluator_factory=lambda data, config: Evaluator()))
        second, second_trajectory = self.actions(EnvironmentRuntime(PublicGenerator(), evaluator_factory=lambda data, config: Evaluator()))
        self.assertEqual(first, second)
        self.assertEqual(first_trajectory, second_trajectory)

    def test_renumbered_and_reordered_public_books_preserve_reconciliation_semantics(self):
        plan = empty_plan("small")
        plan["issues"]["duplicate_invoice"] = 1
        original = generate(51, CONFIG, plan=plan)
        transformed_plan = copy.deepcopy(plan)
        transformed_plan["transform"] = {"id_salt": "paired", "order_seed": 19}
        transformed = generate(51, CONFIG, plan=transformed_plan)
        left = audit(Books(original.tables, original.manifest))
        right = audit(Books(transformed.tables, transformed.manifest))
        self.assertEqual([(f["type"], f["claimed_amount_minor"], f["currency"]) for f in left],
                         [(f["type"], f["claimed_amount_minor"], f["currency"]) for f in right])
        self.assertNotEqual(original.tables["ledger"][0]["row_id"], transformed.tables["ledger"][0]["row_id"])

    def test_public_responses_errors_and_trajectory_contain_no_private_markers(self):
        environment = AuditEnvironment(public_episode_data(generate(23, CONFIG)), evaluator=Evaluator())
        reset = environment.reset()
        unknown = environment.step({"kind": "get_record", "table": "ledger", "id": "ledger:GUESS"})
        unsafe = environment.step({"kind": "calculator", "expression": "__import__('os')"})
        environment.step({"kind": "submit", "findings": {"episode_id": reset["info"]["episode_id"], "findings": []}})
        public = json.dumps([reset, unknown, unsafe, environment.public_state(), environment.trajectory()])
        for marker in ("truth", "provenance", "seed", "private", "/home/"):
            self.assertNotIn(marker, public.lower())

    def test_timeout_truncates_without_evaluating_or_consuming_a_public_action(self):
        clock = Clock()
        environment = AuditEnvironment(public_episode_data(generate(29, CONFIG)), evaluator=Evaluator(), clock=clock)
        environment.reset()
        clock.now = 60
        result = environment.step({"kind": "list_records", "table": "ledger", "cursor": None, "limit": 1})
        self.assertTrue(result["truncated"])
        self.assertEqual(result["info"]["step"], 0)
        self.assertEqual(environment.trajectory(), [])
