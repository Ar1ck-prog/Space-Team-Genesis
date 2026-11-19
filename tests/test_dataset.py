"""Open train/dev episodes (issue #8): committed files match regeneration, diversity holds."""

import json
import unittest
from pathlib import Path

from auditgym import dataset

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_TOKENS = ("issue_id", "expected_amount_minor", "evidence_sets", "provenance", "clean_ledger", "mutations",
                  "scenario", '"seed"', "truth")


class TestOpenDatasets(unittest.TestCase):
    def test_check_passes(self):
        problems = dataset.check()
        self.assertEqual(problems, [], "\n".join(problems))

    def test_no_private_files_or_tokens_committed(self):
        for split in dataset.SPLITS:
            for path in (ROOT / "episodes" / split).rglob("*"):
                if path.is_file():
                    self.assertEqual(path.parent.name, "public", path)
                    text = path.read_text(encoding="utf-8")
                    for tok in PRIVATE_TOKENS:
                        self.assertNotIn(tok, text, (path, tok))

    def test_coverage_rules_reject_a_poor_set(self):
        manifests = {s: dataset.load_manifest(s) for s in dataset.SPLITS}
        poor = json.loads(json.dumps(manifests))
        poor["dev"]["episodes"] = [e for e in poor["dev"]["episodes"] if e["summary"]["n_issues"]][:9]
        self.assertTrue(dataset.coverage_problems(poor))
        self.assertEqual(dataset.coverage_problems(manifests), [])

    def test_manifest_configs_are_contract_configs(self):
        from auditgym.config import validate_config
        for split in dataset.SPLITS:
            for e in dataset.load_manifest(split)["episodes"]:
                self.assertEqual(validate_config(e["config"])["dataset_profile"], split)


if __name__ == "__main__":
    unittest.main()
