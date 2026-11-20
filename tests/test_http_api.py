from __future__ import annotations

import io
import json
import unittest

from space_team_genesis.environment import AuditEnvironment
from space_team_genesis.http_api import EnvironmentHttpApp
from tests.test_environment import FixedEvaluator, episode_data


class Factory:
    def create(self, dataset_profile, company_size):
        self.arguments = (dataset_profile, company_size)
        return AuditEnvironment(episode_data(), evaluator=FixedEvaluator(), evaluation_mode="closed")


def request(app, method, path, body=None, token=None, content_type="application/json"):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8") if body is not None else b""
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
    }
    if token:
        environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    payload = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], payload


class HttpApiTests(unittest.TestCase):
    def setUp(self):
        self.factory = Factory()
        self.app = EnvironmentHttpApp(self.factory, token_factory=lambda: "fixed-token")

    def create(self):
        status, _, body = request(self.app, "POST", "/api/v1/episodes", {"dataset_profile": "train", "company_size": "small"})
        self.assertEqual(status, "201 Created")
        return json.loads(body)

    def test_create_state_step_and_public_trajectory(self):
        created = self.create()
        self.assertNotIn("seed", created)
        self.assertNotIn("evaluation_mode", created)
        episode_id = created["info"]["episode_id"]

        status, _, body = request(self.app, "GET", f"/api/v1/episodes/{episode_id}", token="fixed-token")
        self.assertEqual(status, "200 OK")
        self.assertEqual(json.loads(body)["remaining_steps"], 100)

        status, _, body = request(self.app, "POST", f"/api/v1/episodes/{episode_id}/steps", {"kind": "get_record", "table": "ledger", "id": "ledger:nope"}, token="fixed-token")
        self.assertEqual(status, "200 OK")
        self.assertEqual(json.loads(body)["observation"]["error"]["code"], "unknown_id")

        status, headers, body = request(self.app, "GET", f"/api/v1/episodes/{episode_id}/trajectory", token="fixed-token")
        self.assertEqual(status, "200 OK")
        self.assertTrue(headers["Content-Type"].startswith("application/x-ndjson"))
        row = json.loads(body.decode("utf-8"))
        self.assertEqual(set(row), {"episode_id", "step", "action", "observation", "reward", "terminated", "truncated"})

    def test_transport_errors_do_not_become_environment_steps(self):
        created = self.create()
        episode_id = created["info"]["episode_id"]
        status, _, body = request(self.app, "POST", f"/api/v1/episodes/{episode_id}/steps", b"{", token="fixed-token")
        self.assertEqual(status, "400 Bad Request")
        self.assertEqual(json.loads(body)["error"]["code"], "malformed_json")
        status, _, body = request(self.app, "GET", f"/api/v1/episodes/{episode_id}", token="fixed-token")
        self.assertEqual(json.loads(body)["remaining_steps"], 100)

        status, _, body = request(self.app, "GET", f"/api/v1/episodes/{episode_id}")
        self.assertEqual(status, "401 Unauthorized")
        status, _, body = request(self.app, "GET", f"/api/v1/episodes/{episode_id}", token="wrong")
        self.assertEqual(status, "403 Forbidden")

    def test_finalized_episode_returns_409_without_a_step(self):
        created = self.create()
        episode_id = created["info"]["episode_id"]
        submit = {"kind": "submit", "findings": {"episode_id": episode_id, "findings": []}}
        status, _, _ = request(self.app, "POST", f"/api/v1/episodes/{episode_id}/steps", submit, token="fixed-token")
        self.assertEqual(status, "200 OK")
        status, _, body = request(self.app, "POST", f"/api/v1/episodes/{episode_id}/steps", submit, token="fixed-token")
        self.assertEqual(status, "409 Conflict")
        self.assertEqual(json.loads(body)["error"]["code"], "episode_finalized")

    def test_malformed_submit_is_a_terminal_environment_error_not_http_500(self):
        created = self.create()
        episode_id = created["info"]["episode_id"]
        invalid = {"kind": "submit", "findings": {"episode_id": episode_id, "findings": [{
            "type": "fx_date_or_rate", "affected_ids": [{"id": "ledger:L-1"}],
            "claimed_amount_minor": 1, "currency": "KZT", "evidence_ids": ["ledger:L-1"],
            "explanation": "Invalid ID type",
        }]}}
        status, _, body = request(self.app, "POST", f"/api/v1/episodes/{episode_id}/steps", invalid, token="fixed-token")
        self.assertEqual(status, "200 OK")
        result = json.loads(body)
        self.assertTrue(result["terminated"])
        self.assertEqual(result["observation"]["error"]["code"], "invalid_submission")
        self.assertEqual(result["reward"], 0)
