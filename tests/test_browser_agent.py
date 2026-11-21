"""The sandbox's browser agents (frontend/agent.js) against the real evaluator.

The careful agent must reproduce the truth exactly on all 20 open episodes (it uses only public
records, so this also re-proves solvability); the simple agent must stay a weak baseline.
Needs Node.js; skipped otherwise, so the official offline check stays pure Python.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from auditgym.dataset import load_manifest
from auditgym.evaluator import Evaluator
from auditgym.generator import generate

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


@unittest.skipIf(NODE is None, "Node.js not installed")
class TestBrowserAgents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / "frontend" / "data" / "index.js").is_file():
            raise unittest.SkipTest("frontend data not built (python frontend/build_data.py)")
        out = subprocess.run([NODE, str(ROOT / "tests" / "js" / "agent_on_episodes.js"), str(ROOT)],
                             capture_output=True, text=True, encoding="utf-8", check=True, timeout=120).stdout
        cls.rows = [json.loads(line) for line in out.splitlines() if line.strip()]
        cls.specs = {e["episode_id"]: e for s in ("train", "dev") for e in load_manifest(s)["episodes"]}

    def score(self, row):
        e = self.specs[row["episode_id"]]
        ep = generate(e["seed"], e["config"])
        return Evaluator.from_episode(ep, "open").score_submission(
            {"episode_id": ep.episode_id, "findings": row["findings"]}, 0)

    def test_careful_agent_matches_truth_everywhere(self):
        careful = [r for r in self.rows if r["agent"] == "careful"]
        self.assertEqual(len(careful), 20)
        for r in careful:
            res = self.score(r)
            self.assertTrue(res.valid, r["episode_id"])
            self.assertEqual((res.fp, res.fn), (0, 0), r["episode_id"])

    def test_simple_agent_is_a_weak_baseline(self):
        simple = [self.score(r) for r in self.rows if r["agent"] == "simple"]
        self.assertTrue(all(r.valid for r in simple))
        mean_f1 = sum(r.f1 for r in simple) / len(simple)
        self.assertLess(mean_f1, 0.6)


if __name__ == "__main__":
    unittest.main()
