"""Issue #18: trusted diagnostic extensions must stay off the public API."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from auditgym.generator import generate
from auditgym.verify import verify_episode
from space_team_genesis import diagnostics, lab


class DiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        manifest = lab.load_manifest("train")
        one = dict(manifest, episodes=[
            next(item for item in manifest["episodes"] if item["slot"] == "clean_small")])
        cls.patch = mock.patch.object(lab, "load_manifest", return_value=one)
        cls.patch.start()
        cls.reference = lab.create_run(cls.root / "reference", "reconcile", "train")
        cls.failed = lab.create_run(cls.root / "failed", "mark-all", "train")
        cls.random = lab.create_run(cls.root / "random", "random", "train",
                                    settings={"seed": 5, "exploration_steps": 7})
        cls.case = lab.save_failure(cls.reference, cls.failed, "clean_small",
                                    cls.root / "failure.json")

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        cls.tmp.cleanup()

    def test_branch_replays_rng_and_changes_only_after_fork(self):
        replay = diagnostics.branch(self.random, "clean_small", 3)
        self.assertIsNone(replay["first_difference"])
        self.assertEqual(replay["original"], replay["branch"])
        changed = diagnostics.branch(self.random, "clean_small", 3,
                                     {"kind": "calculator", "expression": "1+1"})
        self.assertEqual(changed["original"][:2], changed["branch"][:2])
        self.assertEqual(changed["first_difference"], 3)
        self.assertEqual(changed["branch"][2]["action"]["kind"], "calculator")
        original, _ = lab.load_run(self.random)
        self.assertEqual(lab._trajectory(self.random, original["cases"][0]),
                         replay["original"])

    def test_branch_reports_unrestorable_agent_and_time(self):
        run, source = lab.load_run(self.random)
        bad = copy.deepcopy(run)
        bad["cases"][0]["action_elapsed_seconds"] = []
        with mock.patch.object(lab, "load_run", return_value=(bad, source)):
            with self.assertRaisesRegex(ValueError, "per-action timing unavailable"):
                diagnostics.branch(self.random, "clean_small", 2)
        exhausted = copy.deepcopy(run)
        exhausted["cases"][0]["action_elapsed_seconds"][1] = 60.0
        with mock.patch.object(lab, "load_run", return_value=(exhausted, source)):
            timed = diagnostics.branch(self.random, "clean_small", 2,
                                       {"kind": "calculator", "expression": "1+1"})
        self.assertTrue(timed["truncated"])
        self.assertEqual(len(timed["branch"]), 1)
        timeout_run = copy.deepcopy(run)
        timeout_run["cases"][0]["truncated"] = True
        with mock.patch.object(lab, "load_run", return_value=(timeout_run, source)):
            with self.assertRaisesRegex(ValueError, "timeout before a step"):
                diagnostics.branch(self.random, "clean_small", 2)

        with mock.patch.object(lab, "_policy_from_source") as factory:
            factory.return_value.next_action.return_value = {"kind": "submit"}
            with self.assertRaisesRegex(ValueError, "agent state cannot be restored"):
                diagnostics.branch(self.random, "clean_small", 2)

    def test_branch_at_hundredth_action_truncates_on_replacement(self):
        run_path = lab.create_run(self.root / "hundred", "random", "train",
                                  settings={"seed": 1, "exploration_steps": 99})
        run, _ = lab.load_run(run_path)
        original = lab._trajectory(run_path, run["cases"][0])
        self.assertEqual(len(original), 100)
        self.assertEqual(original[-1]["action"]["kind"], "submit")
        result = diagnostics.branch(run_path, "clean_small", 100,
                                    {"kind": "calculator", "expression": "1+1"})
        self.assertTrue(result["branch"][-1]["truncated"])
        self.assertEqual(len(result["branch"]), 100)

    def test_minimize_real_company_preserves_failure_and_verification(self):
        result = diagnostics.minimize(self.case, self.failed, self.root / "minimized.json",
                                      max_attempts=8, time_limit_seconds=30)
        self.assertGreater(result["original_size"]["records"] +
                           result["original_size"]["documents"],
                           result["final_size"]["records"] +
                           result["final_size"]["documents"])
        self.assertTrue(result["accepted"])
        self.assertLessEqual(result["attempts"], 8)
        self.assertEqual(result["soft_time_limit_seconds"], 30)
        regenerated = generate(result["seed"], result["config"], plan=result["plan"])
        self.assertTrue(verify_episode(regenerated)["ok"])
        self.assertTrue((self.root / "minimized.json").is_file())
        with self.assertRaisesRegex(ValueError, "positive search limits"):
            diagnostics.minimize(self.case, self.failed, self.root / "unused.json",
                                 max_attempts=0)

    def test_minimize_rejects_invalid_candidate(self):
        with mock.patch.object(diagnostics, "verify_episode",
                               side_effect=[{"ok": True}, {"ok": False}]), \
             mock.patch.object(lab, "_run_case") as runner:
            result = diagnostics.minimize(self.case, self.failed,
                                          self.root / "rejected.json", max_attempts=1)
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["accepted"], [])
        self.assertEqual(result["original_size"], result["final_size"])
        runner.assert_not_called()


    def test_mutations_are_isolated_and_detected(self):
        original = (lab.ROOT / "auditgym" / "matcher.py").read_bytes()
        report = diagnostics.mutation_report()
        self.assertTrue(report["baseline_passed"])
        self.assertTrue({"currency", "evidence", "duplicates", "amount", "reward_steps", "closed_reward"} <= set(report["detected"]))
        self.assertIn("equivalent_control", report["survived"])
        self.assertEqual((lab.ROOT / "auditgym" / "matcher.py").read_bytes(), original)

    def test_resource_counts_and_experimental_budget(self):
        run, _ = lab.load_run(self.reference)
        row = run["cases"][0]
        trajectory = lab._trajectory(self.reference, row)
        self.assertEqual(row["viewed_items"], sum(
            len(t["observation"]["records"]) for t in trajectory
            if t["observation"]["kind"] == "records"))
        self.assertGreater(row["viewed_bytes"], row["viewed_items"])
        short = lab.create_run(self.root / "short", "reconcile", "train", action_budget=2)
        short_run, _ = lab.load_run(short)
        self.assertEqual(short_run["mode"], "experimental")
        self.assertEqual(len(lab._trajectory(short, short_run["cases"][0])), 2)
        with self.assertRaisesRegex(ValueError, "official run"):
            lab.test_failure(self.case, short)
        with self.assertRaisesRegex(ValueError, "experimental budget"):
            lab.compare(self.reference, short)
        short_b = lab.create_run(self.root / "short-b", "mark-all", "train",
                                 action_budget=2)
        comparison = lab.compare(short, short_b)
        self.assertEqual(comparison["mode"], "experimental")
        self.assertIn("viewed_items", comparison["summary_mean"])
        with self.assertRaisesRegex(ValueError, "official runs"):
            lab.save_failure(short, short_b, "clean_small", self.root / "bad.json")


if __name__ == "__main__":
    unittest.main()
