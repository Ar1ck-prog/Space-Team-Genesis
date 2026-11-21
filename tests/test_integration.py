"""End-to-end wiring: private evaluator process, public environment and HTTP."""
from __future__ import annotations

import json
import os
import unittest

from auditgym.config import ISSUE_TYPES
from space_team_genesis.http_api import EnvironmentHttpApp
from space_team_genesis.integration import OfflineEpisodeFactory, make_environment, score_oracle
from tests.test_http_api import request


def config(mode: str = "closed") -> dict:
    return {
        "dataset_profile": "dev",
        "company_size": "small",
        "enabled_issue_types": list(ISSUE_TYPES),
        "max_steps": 100,
        "time_limit_seconds": 60,
        "evaluation_mode": mode,
    }


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class IntegrationTests(unittest.TestCase):
    def test_closed_submit_keeps_truth_in_worker_and_returns_trusted_metrics(self):
        environment = make_environment(2013, config(), trusted_metrics=True)
        self.addCleanup(environment.evaluator.close)
        self.assertNotEqual(environment.evaluator._process.pid, os.getpid())
        start = environment.reset()
        public = json.dumps(start)
        for marker in ("truth", "provenance", '"seed"', "private"):
            self.assertNotIn(marker, public.lower())
        final = environment.step({"kind": "submit", "findings": {
            "episode_id": environment.episode_id, "findings": [],
        }})
        self.assertTrue(final["terminated"])
        self.assertEqual(final["reward"], 0)
        self.assertEqual(environment.trajectory()[-1]["reward"], 0)
        metrics = environment.evaluator.metrics_for_completed_episode(environment)
        self.assertEqual(metrics.f1, 0)
        self.assertEqual(metrics.return_, -1)

    def test_invalid_submit_is_scored_once_and_hidden_in_closed_mode(self):
        environment = make_environment(2013, config(), trusted_metrics=True)
        self.addCleanup(environment.evaluator.close)
        environment.reset()
        final = environment.step({"kind": "submit"})
        self.assertTrue(final["terminated"])
        self.assertEqual(final["observation"]["error"]["code"], "invalid_submission")
        self.assertEqual(final["reward"], 0)
        self.assertEqual(environment.evaluator.metrics_for_completed_episode(environment).return_, -1)

    def test_time_truncation_scores_empty_answer_in_open_mode(self):
        environment = make_environment(2013, config("open"), trusted_metrics=True)
        self.addCleanup(environment.evaluator.close)
        clock = Clock()
        environment.clock = clock
        environment.reset()
        clock.now = 60.0
        final = environment.step({"kind": "list_records", "table": "ledger", "cursor": None, "limit": 1})
        self.assertTrue(final["truncated"])
        self.assertEqual(final["info"]["step"], 0)
        self.assertEqual(final["reward"], -1)
        self.assertEqual(environment.trajectory(), [])
        self.assertEqual(environment.evaluator.metrics_for_completed_episode(environment).return_, -1)

    def test_oracle_is_evaluator_only_and_scores_full_answer(self):
        self.assertEqual(score_oracle(2013, config()).f1, 1)
        self.assertEqual(score_oracle(2013, config()).return_, 1)
        environment = make_environment(2013, config())
        self.addCleanup(environment.evaluator.close)
        with self.assertRaises(PermissionError):
            environment.evaluator.oracle_metrics()

    def test_http_factory_uses_closed_mode_without_private_output(self):
        app = EnvironmentHttpApp(OfflineEpisodeFactory(), token_factory=lambda: "local-test-token")
        status, _, body = request(app, "POST", "/api/v1/episodes",
                                  {"dataset_profile": "dev", "company_size": "small"})
        self.assertEqual(status, "201 Created")
        created = json.loads(body)
        episode_id = created["info"]["episode_id"]
        status, _, body = request(app, "POST", f"/api/v1/episodes/{episode_id}/steps",
                                  {"kind": "submit", "findings": {
                                      "episode_id": episode_id, "findings": [],
                                  }}, token="local-test-token")
        self.assertEqual(status, "200 OK")
        self.assertEqual(json.loads(body)["reward"], 0)
        status, _, body = request(app, "GET", f"/api/v1/episodes/{episode_id}/trajectory",
                                  token="local-test-token")
        self.assertEqual(status, "200 OK")
        for marker in ("truth", "provenance", '"seed"', "private"):
            self.assertNotIn(marker, body.decode().lower())
