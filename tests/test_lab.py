"""Regression-lab checks use one real clean train company, never closed fixtures."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from auditgym.config import ISSUE_TYPES
from auditgym.generator import generate
from auditgym.verify import verify_episode
from auditgym.environment_adapter import public_episode_data
from space_team_genesis import lab
from space_team_genesis.environment import AuditEnvironment, EvaluationResult

CONFIG = {"dataset_profile": "train", "company_size": "small",
          "enabled_issue_types": list(ISSUE_TYPES), "max_steps": 100,
          "time_limit_seconds": 60, "evaluation_mode": "closed"}


class _Evaluator:
    def evaluate(self, findings, inspection_steps):
        return EvaluationResult(0)


class CoreVersionTests(unittest.TestCase):
    def test_invalid_policy_and_timezone_are_rejected(self):
        original = generate(2013, dict(CONFIG, dataset_profile="dev"))
        self.assertTrue(verify_episode(original)["ok"])
        bad_policy = copy.deepcopy(original)
        bad_policy.policy["fx_rate_date"] = "invalid_policy"
        self.assertFalse(verify_episode(bad_policy)["ok"])
        bad_zone = copy.deepcopy(original)
        bad_zone.manifest["timezone"] = "Invalid/Zone"
        self.assertFalse(verify_episode(bad_zone)["ok"])

    def test_environment_version_is_not_schema_version(self):
        episode = generate(2013, dict(CONFIG, dataset_profile="dev"))
        public = public_episode_data(episode)
        with mock.patch("space_team_genesis.environment.ENVIRONMENT_VERSION", "9.9.9"):
            result = AuditEnvironment(public, _Evaluator()).reset()
        self.assertEqual(result["info"]["environment_version"], "9.9.9")
        self.assertNotEqual(result["info"]["environment_version"],
                            result["observation"]["manifest"]["schema_version"])


class LabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        manifest = lab.load_manifest("train")
        cls.one_train = dict(manifest, episodes=[
            next(item for item in manifest["episodes"] if item["slot"] == "clean_small")])
        cls.patch = mock.patch.object(lab, "load_manifest", return_value=cls.one_train)
        cls.patch.start()
        cls.reference = lab.create_run(cls.root / "reference", "reconcile", "train")
        cls.failed = lab.create_run(cls.root / "failed", "mark-all", "train")
        cls.case = lab.save_failure(cls.reference, cls.failed, "clean_small",
                                    cls.root / "known-failure.json")

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        cls.tmp.cleanup()

    def test_compare_identifies_concrete_case_and_versions(self):
        result = lab.compare(self.reference, self.failed)
        self.assertEqual(len(result["regressions"]), 1)
        self.assertIn("delta_inspection_steps", result["episodes"][0])
        self.assertIn("delta_elapsed_seconds", result["episodes"][0])
        self.assertEqual(result["regressions"][0]["slot"], "clean_small")
        self.assertEqual(result["regressions"][0]["fp_after"], 30)
        before, _ = lab.load_run(self.reference)
        self.assertEqual(result["before"], before["agent"]["agent_version"])
        changed = copy.deepcopy(before)
        changed["dataset_sha256"] = "different"
        with self.assertRaisesRegex(ValueError, "incompatible"):
            lab._pairs(before, changed)

    def test_save_replay_scan_and_restore(self):
        case = self.case
        saved = lab._read_json(case)
        self.assertEqual(saved["expected"]["f1"], 1.0)
        self.assertEqual(lab.test_failure(case, self.reference)["verdict"], "pass")
        self.assertEqual(lab.test_failure(case, self.failed)["verdict"], "fail")
        collection = lab.test_collection(self.root, self.failed)
        self.assertEqual(collection["summary"]["fail"], 1)
        self.assertEqual(collection["cases"][0]["case"], case.name)
        history = lab.scan_history(case, [self.reference, self.failed, self.reference])
        self.assertEqual([x["change"] for x in history["transitions"]],
                         ["pass->fail", "fail->pass"])
        self.assertIn('"policy":"mark-all"', history["transitions"][0]["diff"])
        restored = lab.restore_run(self.reference, self.root / "restored")
        self.assertFalse(lab.compare(self.reference, restored)["regressions"])
        original, _ = lab.load_run(self.reference)
        repeated, _ = lab.load_run(restored)
        self.assertEqual(lab._trajectory(self.reference, original["cases"][0]),
                         lab._trajectory(restored, repeated["cases"][0]))
        self.assertEqual(original["cases"][0]["f1"], repeated["cases"][0]["f1"])

    def test_repeats_can_be_uncertain(self):
        case = self.case
        rows = iter([({"f1": 1.0, "fp": 0, "elapsed_seconds": 0.01}, ()),
                     ({"f1": 0.0, "fp": 1, "elapsed_seconds": 0.01}, ())])
        with mock.patch.object(lab, "_run_case", side_effect=lambda *args: next(rows)):
            result = lab.test_failure(case, self.reference, repeats=2)
        self.assertEqual(result["verdict"], "uncertain")

    def test_dev_cannot_be_saved_as_known_failure(self):
        # A copied train fixture with a dev designation is enough to test the guard.
        reference, _ = lab.load_run(self.reference)
        failed, _ = lab.load_run(self.failed)
        reference["split"] = failed["split"] = "dev"
        with mock.patch.object(lab, "load_run", side_effect=[(reference, ""), (failed, "")]):
            with self.assertRaisesRegex(ValueError, "only train"):
                lab.save_failure(self.reference, self.failed, "clean_small",
                                 self.root / "must-not-exist.json")

    def test_mismatched_source_cannot_claim_head_commit(self):
        self.assertIsNone(lab.git_commit("source that differs from HEAD"))

    def test_trusted_artifact_cannot_be_written_to_tracked_tree(self):
        with self.assertRaisesRegex(ValueError, "under outputs"):
            lab._write_json(lab.ROOT / "docs" / "must-not-exist.json", {})

    def test_public_trajectory_and_snapshot_integrity(self):
        run, _ = lab.load_run(self.reference)
        row = run["cases"][0]
        public = (self.reference.parent / row["trajectory"]).read_text(encoding="utf-8")
        for marker in ('"seed"', '"truth"', '"provenance"', '"matched"', '"fp"'):
            self.assertNotIn(marker, public)
        source_path = self.reference.parent / "agent_source.json"
        snapshot = json.loads(source_path.read_text(encoding="utf-8"))
        with mock.patch.object(lab, "_read_json", side_effect=lambda path:
                               dict(snapshot, sha256="wrong") if path == source_path else run):
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                lab.load_run(self.reference)
