"""Issue #16, stage 0: confirmed core gaps (policy/timezone validation, environment version)."""

import copy
import unittest

from auditgym.config import ISSUE_TYPES
from auditgym.generator import generate
from auditgym.verify import verify_episode
from auditgym.version import ENVIRONMENT_VERSION

try:
    from space_team_genesis.environment import AuditEnvironment, EpisodeData, EvaluationResult
except ImportError:  # src/ layout: run with PYTHONPATH=src
    AuditEnvironment = None

CFG = {"dataset_profile": "dev", "company_size": "small", "enabled_issue_types": list(ISSUE_TYPES), "max_steps": 100,
       "time_limit_seconds": 60, "evaluation_mode": "open"}


class TestVerifierRejectsBadPolicyAndTime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ep = generate(2013, CFG)

    def broken(self, mutate):
        ep = copy.deepcopy(self.ep)
        mutate(ep)
        return verify_episode(ep)

    def test_valid_episode_passes(self):
        self.assertTrue(verify_episode(self.ep)["ok"])

    def test_policy_value(self):
        res = self.broken(lambda e: e.policy.__setitem__("fx_rate_date", "invalid_policy"))
        self.assertFalse(res["ok"])
        self.assertTrue(res["errors"]["policy"])

    def test_policy_missing_and_extra_fields(self):
        self.assertFalse(self.broken(lambda e: e.policy.pop("refunds"))["ok"])
        self.assertFalse(self.broken(lambda e: e.policy.__setitem__("note", "x"))["ok"])

    def test_policy_accounts(self):
        self.assertFalse(self.broken(lambda e: e.policy["accounts"].pop("2400"))["ok"])
        self.assertFalse(self.broken(lambda e: e.policy["accounts"].pop("6100"))["ok"])  # used by the ledger

    def test_rounding_rule(self):
        self.assertFalse(self.broken(lambda e: e.manifest.__setitem__("rounding_rule", "bankers"))["ok"])

    def test_invalid_timezone(self):
        res = self.broken(lambda e: e.manifest.__setitem__("timezone", "Invalid/Zone"))
        self.assertFalse(res["ok"])
        self.assertTrue(res["errors"]["schema"])

    def test_timezone_inconsistent_with_offsets(self):
        self.assertFalse(self.broken(lambda e: e.manifest.__setitem__("timezone", "Asia/Tokyo"))["ok"])

    def test_period_is_one_calendar_month(self):
        start, end = self.ep.manifest["period_start"], self.ep.manifest["period_end"]
        self.assertFalse(self.broken(lambda e: e.manifest.__setitem__("period_end", end.replace("31T", "30T")))["ok"])
        self.assertFalse(self.broken(lambda e: e.manifest.__setitem__("period_start", start.replace("T00:", "T01:")))["ok"])


@unittest.skipIf(AuditEnvironment is None, "space_team_genesis not importable (run with PYTHONPATH=src)")
class TestEnvironmentVersion(unittest.TestCase):
    def test_info_reports_environment_not_schema_version(self):
        class _Eval:
            def evaluate(self, findings, inspection_steps):
                return EvaluationResult(reward=0.0)

        data = EpisodeData(episode_id="ep_TEST", manifest={"schema_version": "9.9.9", "episode_id": "ep_TEST",
                                                           "currency_decimals": {"KZT": 2}},
                           policy_summary={}, records={t: [] for t in ("ledger", "invoices", "payments", "fx")},
                           documents={})
        env = AuditEnvironment(data=data, evaluator=_Eval())
        info = env.reset()["info"]
        self.assertEqual(info["environment_version"], ENVIRONMENT_VERSION)
        self.assertNotEqual(info["environment_version"], "9.9.9")


if __name__ == "__main__":
    unittest.main()
