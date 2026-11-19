"""Tests for issue #2: deterministic, consistent synthetic companies.

Run: python -m unittest discover -s tests -t .
"""

import collections
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from auditgym import config as cfgmod
from auditgym.config import ISSUE_TYPES, ConfigError, empty_plan
from auditgym.episode_io import PUBLIC_FILES, load_episode, render_files, write_episode
from auditgym.generator import generate
from auditgym.timeutil import parse
from auditgym.verify import verify_episode

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = json.loads((ROOT / "tests" / "fixtures" / "generator_fixtures.json").read_text(encoding="utf-8"))["fixtures"]


def cfg(size="small", types=ISSUE_TYPES, profile="train", **over):
    c = {"dataset_profile": profile, "company_size": size, "enabled_issue_types": list(types), "max_steps": 100,
         "time_limit_seconds": 60, "evaluation_mode": "open"}
    c.update(over)
    return c


def fixture_plan(fx: dict) -> dict:
    plan = empty_plan(fx["config"]["company_size"])
    for k, v in fx["plan"].items():
        if isinstance(v, dict):
            plan[k] = {**plan[k], **v}
        else:
            plan[k] = v
    return plan


def gen_fixture(fx: dict):
    return generate(fx["seed"], fx["config"], plan=fixture_plan(fx))


def digest(ep) -> str:
    return hashlib.sha256(json.dumps(render_files(ep), sort_keys=True).encode()).hexdigest()


class TestFixtures(unittest.TestCase):
    """Every type, clean companies, distractors and both interacting scenarios."""

    @classmethod
    def setUpClass(cls):
        cls.eps = {fx["name"]: (fx, gen_fixture(fx)) for fx in FIXTURES}

    def test_fixture_catalog_covers_requirements(self):
        names = set(self.eps)
        covered = collections.Counter()
        for fx, _ in self.eps.values():
            covered.update(fx["expect"]["types"].keys())
        self.assertTrue(set(ISSUE_TYPES) <= set(covered))
        self.assertIn("clean_small", names)
        scen = {s for fx, _ in self.eps.values() for s in fx["expect"]["scenarios"]}
        self.assertEqual(scen, set(cfgmod.INTERACTIONS))

    def test_each_fixture_verifies(self):
        for name, (fx, ep) in self.eps.items():
            with self.subTest(name):
                res = verify_episode(ep)
                self.assertTrue(res["ok"], {k: v[:3] for k, v in res["errors"].items() if v})

    def test_each_fixture_has_expected_issues(self):
        for name, (fx, ep) in self.eps.items():
            with self.subTest(name):
                iss = ep.truth["issues"]
                self.assertEqual(dict(collections.Counter(i["type"] for i in iss)), fx["expect"]["types"])
                self.assertEqual(dict(collections.Counter(i["scenario"] for i in iss if i["scenario"])),
                                 fx["expect"]["scenarios"])
                if "targets" in fx["expect"]:
                    self.assertEqual({i["target"] for i in iss}, set(fx["expect"]["targets"]))
                if "utc_offset_sign" in fx["expect"]:
                    self.assertEqual(ep.provenance["company"]["utc_offset"][0], fx["expect"]["utc_offset_sign"])

    def test_interacting_errors_share_records(self):
        _, ep = self.eps["interaction_fx_invoice_partial"]
        fx = next(i for i in ep.truth["issues"] if i["type"] == "fx_date_or_rate")
        al = next(i for i in ep.truth["issues"] if i["type"] == "payment_allocation")
        inv_id = next(i for i in al["affected_ids"] if i.startswith("invoice:"))
        rows = {r["row_id"]: r for r in ep.tables["ledger"]}
        self.assertTrue(all(rows[r]["invoice_id"] == inv_id for r in fx["affected_ids"]))  # same invoice
        for name in ("interaction_boundary_positive_offset", "interaction_boundary_negative_offset"):
            _, ep = self.eps[name]
            fx = next(i for i in ep.truth["issues"] if i["type"] == "fx_date_or_rate")
            al = next(i for i in ep.truth["issues"] if i["type"] == "payment_allocation")
            rows = {r["row_id"]: r for r in ep.tables["ledger"]}
            pay_id = rows[fx["affected_ids"][0]]["payment_id"]
            self.assertIn(pay_id, al["affected_ids"])  # same payment
            pay = next(p for p in ep.tables["payments"] if p["payment_id"] == pay_id)
            local = parse(pay["timestamp"]).astimezone(parse(ep.manifest["period_start"]).tzinfo).date().isoformat()
            self.assertTrue(pay["timestamp"].endswith("+00:00"))
            self.assertNotEqual(pay["timestamp"][:10], local)  # printed UTC date differs from local date
            self.assertIn(local, (ep.manifest["period_start"][:10], ep.manifest["period_end"][:10]))

    def test_clean_company_has_every_distractor(self):
        for name in ("clean_small", "clean_heavy_distractors"):
            _, ep = self.eps[name]
            self.assertEqual(ep.truth["issues"], [])
            tags = {t for v in ep.provenance["history"]["invoice_tags"].values() for t in v}
            self.assertTrue({"recurring", "split", "multi", "overpaid", "opening", "after_period",
                             "late_in_period"} <= tags, tags)
            self.assertTrue(any(p["amount_minor"] < 0 for p in ep.tables["payments"]))  # refund
            self.assertTrue(any(r["account"] == "2400" for r in ep.tables["ledger"]))  # legit unapplied receipt
            self.assertTrue(any(len(p["invoice_refs"] or []) > 1 for p in ep.tables["payments"]))
            totals = collections.Counter((d["entity_id"], d["total_minor"]) for d in ep.tables["invoices"])
            self.assertGreater(max(totals.values()), 1)  # legitimate repeated amounts


class TestInvariants(unittest.TestCase):
    """Accounting equalities before errors and the declared discrepancy after them."""

    @classmethod
    def setUpClass(cls):
        cls.ep = gen_fixture(next(f for f in FIXTURES if f["name"] == "mixed_max_12_large"))

    def test_clean_and_published_ledgers_balance(self):
        for rows in (self.ep.provenance["clean_ledger"], self.ep.tables["ledger"]):
            self.assertEqual(sum(r["debit_minor"] for r in rows), sum(r["credit_minor"] for r in rows))

    def test_declared_amount_equals_clean_vs_published_difference(self):
        clean = {r["row_id"]: r for r in self.ep.provenance["clean_ledger"]}
        pub = {r["row_id"]: r for r in self.ep.tables["ledger"]}

        def amount(r):
            return r["debit_minor"] or r["credit_minor"]

        for iss in self.ep.truth["issues"]:
            with self.subTest(iss["issue_id"]):
                t, exp = iss["type"], iss["expected_amount_minor"]
                ev_rows = [e for s in iss["evidence_sets"] for e in s if e.startswith("ledger:")]
                if t == "fx_date_or_rate":
                    row = iss["affected_ids"][0]
                    self.assertEqual(abs(amount(pub[row]) - amount(clean[row])), exp)
                elif t == "reporting_period":
                    for row in iss["affected_ids"]:
                        self.assertNotIn(row, clean)  # these rows do not exist in the correct books
                    self.assertIn(exp, [amount(pub[r]) for r in ev_rows])
                elif t == "duplicate_invoice":
                    for row in ev_rows:
                        self.assertNotIn(row, clean)
                        self.assertEqual(amount(pub[row]), exp)
                else:
                    row = ev_rows[0]
                    self.assertEqual(pub[row]["account"], "2400")
                    self.assertEqual(pub[row]["credit_minor"], exp)
                    self.assertTrue(row not in clean or clean[row]["account"] != "2400" or
                                    clean[row]["credit_minor"] == 0)

    def test_only_planned_rows_differ(self):
        clean = {r["row_id"]: r for r in self.ep.provenance["clean_ledger"] if r["row_id"].startswith("ledger:")}
        pub = {r["row_id"]: r for r in self.ep.tables["ledger"]}
        touched_payments = {m.get("payment") for m in self.ep.provenance["mutations"]} - {None}
        touched_invoices = {m.get(k) for m in self.ep.provenance["mutations"] for k in ("invoice", "duplicate")} - {None}
        for rid, r in pub.items():
            if rid in clean and r != clean[rid]:
                self.assertTrue(r["payment_id"] in touched_payments or r["invoice_id"] in touched_invoices, r)


class TestDeterminism(unittest.TestCase):
    def test_same_inputs_same_bytes(self):
        for fx in FIXTURES[:4]:
            self.assertEqual(digest(gen_fixture(fx)), digest(gen_fixture(fx)))
        self.assertEqual(digest(generate(5, cfg("medium"))), digest(generate(5, cfg("medium"))))

    def test_bytes_identical_across_processes_and_hash_seeds(self):
        code = ("import hashlib,json,sys;from auditgym.generator import generate;from auditgym.episode_io import "
                "render_files;c={'dataset_profile':'dev','company_size':'small','enabled_issue_types':["
                "'duplicate_invoice','payment_allocation','fx_date_or_rate','reporting_period'],'max_steps':100,"
                "'time_limit_seconds':60,'evaluation_mode':'open'};"
                "print(hashlib.sha256(json.dumps(render_files(generate(77,c)),sort_keys=True).encode()).hexdigest())")
        outs = set()
        for hs in ("0", "1", "12345"):
            env = {**os.environ, "PYTHONHASHSEED": hs}
            outs.add(subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                                    check=True).stdout.strip())
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs.pop(), digest(generate(77, cfg(profile="dev"))))

    def test_written_files_are_byte_identical(self):
        ep = generate(9, cfg())
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            write_episode(ep, a)
            write_episode(generate(9, cfg()), b)
            for rel in render_files(ep):
                self.assertEqual((Path(a) / rel).read_bytes(), (Path(b) / rel).read_bytes(), rel)

    def test_disk_round_trip(self):
        ep = generate(9, cfg())
        with tempfile.TemporaryDirectory() as d:
            write_episode(ep, d)
            back = load_episode(d)
        self.assertEqual(back.tables, ep.tables)
        self.assertEqual(back.manifest, ep.manifest)
        self.assertEqual(back.truth, ep.truth)
        self.assertTrue(verify_episode(back)["ok"])

    def test_mode_and_limits_do_not_change_data(self):
        fa = render_files(generate(13, cfg()))
        fb = render_files(generate(13, cfg(evaluation_mode="closed", time_limit_seconds=30)))
        for rel in fa:
            if rel != "private/provenance.json":  # provenance records the full config that was used
                self.assertEqual(fa[rel], fb[rel], rel)

    def test_seed_profile_size_types_change_data(self):
        base = digest(generate(13, cfg()))
        for other in (generate(14, cfg()), generate(13, cfg(profile="dev")), generate(13, cfg("medium")),
                      generate(13, cfg(types=["duplicate_invoice"]))):
            self.assertNotEqual(digest(other), base)

    def test_id_salt_changes_only_ids(self):
        plan = cfgmod.derive_plan(5, cfgmod.validate_config(cfg()))
        p2 = copy.deepcopy(plan)
        p2["transform"]["id_salt"] = "pair"
        a, b = generate(5, cfg(), plan=plan), generate(5, cfg(), plan=p2)
        strip = lambda ep: [(r["timestamp"], r["account"], r["debit_minor"], r["credit_minor"]) for r in ep.tables["ledger"]]
        self.assertEqual(strip(a), strip(b))
        self.assertFalse({r["row_id"] for r in a.tables["ledger"]} & {r["row_id"] for r in b.tables["ledger"]})
        key = lambda ep: sorted((i["type"], i["target"], i["expected_amount_minor"]) for i in ep.truth["issues"])
        self.assertEqual(key(a), key(b))
        self.assertTrue(verify_episode(b)["ok"])

    def test_order_seed_permutes_records(self):
        plan = cfgmod.derive_plan(5, cfgmod.validate_config(cfg()))
        p2 = copy.deepcopy(plan)
        p2["transform"]["order_seed"] = 3
        a, b = generate(5, cfg(), plan=plan), generate(5, cfg(), plan=p2)
        self.assertNotEqual(a.tables["ledger"], b.tables["ledger"])
        self.assertEqual(sorted(map(json.dumps, a.tables["ledger"])), sorted(map(json.dumps, b.tables["ledger"])))
        self.assertEqual(a.truth["issues"], b.truth["issues"])
        self.assertTrue(verify_episode(b)["ok"])


class TestDerivedPlans(unittest.TestCase):
    def test_many_seeds_verify_and_stay_in_limits(self):
        for size, (ledger_lo, docs_lo) in (("small", (200, 20)), ("medium", (200, 20)), ("large", (200, 20))):
            for seed in range(12):
                ep = generate(seed, cfg(size, profile="dev" if seed % 2 else "train"))
                with self.subTest(size=size, seed=seed):
                    res = verify_episode(ep)
                    self.assertTrue(res["ok"], {k: v[:3] for k, v in res["errors"].items() if v})
                    self.assertTrue(ledger_lo <= ep.manifest["record_counts"]["ledger"] <= 2000)
                    self.assertTrue(docs_lo <= ep.manifest["document_count"] <= 200)
                    self.assertLessEqual(len(ep.truth["issues"]), 12)

    def test_plan_mix_clean_and_interacting(self):
        c = cfgmod.validate_config(cfg())
        plans = [cfgmod.derive_plan(s, c) for s in range(300)]
        clean = sum(1 for p in plans if not any(p["issues"].values()) and not any(p["interactions"].values()))
        inter = sum(1 for p in plans if any(p["interactions"].values()))
        self.assertTrue(30 <= clean <= 100, clean)
        self.assertTrue(40 <= inter, inter)

    def test_enabled_types_respected(self):
        for types in (["duplicate_invoice"], ["reporting_period", "duplicate_invoice"], ["fx_date_or_rate"]):
            for seed in range(15):
                ep = generate(seed, cfg(types=types))
                self.assertTrue({i["type"] for i in ep.truth["issues"]} <= set(types))
                self.assertFalse(any(i["scenario"] for i in ep.truth["issues"]))

    def test_variation_across_seeds(self):
        eps = [generate(s, cfg()) for s in range(30)]
        self.assertGreater(len({e.manifest["base_currency"] for e in eps}), 1)
        self.assertGreater(len({e.manifest["timezone"] for e in eps}), 3)
        self.assertGreater(len({tuple(e.manifest["currency_decimals"]) for e in eps}), 5)
        self.assertEqual(len({e.episode_id for e in eps}), 30)


class TestConfigValidation(unittest.TestCase):
    def test_rejects_bad_configs(self):
        bad = [
            {**cfg(), "extra": 1},
            {k: v for k, v in cfg().items() if k != "company_size"},
            cfg(size="huge"),
            cfg(profile="test"),
            cfg(types=[]),
            cfg(types=["duplicate_invoice", "duplicate_invoice"]),
            cfg(types=["fraud"]),
            cfg(max_steps=50),
            cfg(time_limit_seconds=0),
            cfg(time_limit_seconds=61),
            cfg(time_limit_seconds=True),
            cfg(evaluation_mode="secret"),
        ]
        for c in bad:
            with self.assertRaises(ConfigError, msg=c):
                generate(1, c)

    def test_rejects_bad_seed_version_and_plan(self):
        for s in (-1, 2 ** 53, 1.5, True, "1"):
            with self.assertRaises(ConfigError):
                generate(s, cfg())
        with self.assertRaises(ConfigError):
            generate(1, cfg(), environment_version="9.9.9")
        p = empty_plan("small")
        p["issues"]["fx_date_or_rate"] = 13
        with self.assertRaises(ConfigError):
            generate(1, cfg(), plan=p)
        p = empty_plan("small")
        p["issues"]["fx_date_or_rate"] = 1
        with self.assertRaises(ConfigError):
            generate(1, cfg(types=["duplicate_invoice"]), plan=p)  # disabled type in plan


class TestPublicArtifacts(unittest.TestCase):
    def test_public_files_hide_truth_seed_and_paths(self):
        seed = 4242424242
        ep = generate(seed, cfg())
        files = render_files(ep)
        self.assertEqual({k.split("/", 1)[1] for k in files if k.startswith("public/")}, set(PUBLIC_FILES))
        public = "".join(v for k, v in files.items() if k.startswith("public/"))
        self.assertNotIn(str(seed), public)
        for tok in ("seed", "issue_id", "expected_amount", "evidence_sets", "provenance", "truth", "scenario",
                    "clean-only", "mutation", "\\", "private/"):
            self.assertNotIn(tok, public, tok)
        self.assertIn(str(seed), files["private/provenance.json"])

    def test_episode_id_is_keyed_and_opaque(self):
        c = cfgmod.validate_config(cfg())
        plan = cfgmod.derive_plan(1, c)
        a = cfgmod.episode_id(1, c, plan, "1.0.0")
        self.assertRegex(a, r"^ep_[A-Z2-7]{20}$")
        old = os.environ.get("GENESIS_EPISODE_KEY")
        os.environ["GENESIS_EPISODE_KEY"] = "jury-secret"
        try:
            self.assertNotEqual(a, cfgmod.episode_id(1, c, plan, "1.0.0"))
        finally:
            if old is None:
                os.environ.pop("GENESIS_EPISODE_KEY")
            else:
                os.environ["GENESIS_EPISODE_KEY"] = old

    def test_error_rows_not_marked_by_position(self):
        ep = gen_fixture(next(f for f in FIXTURES if f["name"] == "mixed_max_12_large"))
        ev = {e for i in ep.truth["issues"] for s in i["evidence_sets"] for e in s if e.startswith("ledger:")}
        pos = [n for n, r in enumerate(ep.tables["ledger"]) if r["row_id"] in ev]
        self.assertLess(min(pos), len(ep.tables["ledger"]) // 2)
        ts = [parse(r["timestamp"]) for r in ep.tables["ledger"]]
        self.assertEqual(ts, sorted(ts))  # chronological, error rows are not appended at the end


class TestVerifierCatchesCorruption(unittest.TestCase):
    """The independent checks must fail when data and truth disagree."""

    def setUp(self):
        self.ep = gen_fixture(next(f for f in FIXTURES if f["name"] == "mixed_max_12_large"))

    def test_wrong_amount(self):
        self.ep.truth["issues"][0]["expected_amount_minor"] += 500
        self.assertFalse(verify_episode(self.ep)["ok"])

    def test_unavailable_evidence(self):
        self.ep.truth["issues"][0]["evidence_sets"] = [["ledger:L-NOTREAL"]]
        self.assertFalse(verify_episode(self.ep)["ok"])

    def test_unbalanced_ledger(self):
        r = self.ep.tables["ledger"][0]
        r["debit_minor" if r["debit_minor"] else "credit_minor"] += 1
        self.assertFalse(verify_episode(self.ep)["ok"])

    def test_undeclared_error(self):
        self.ep.truth["issues"].pop()
        self.assertFalse(verify_episode(self.ep)["ok"])

    def test_leaked_truth(self):
        self.ep.tables["invoices"][0]["line_items"][0]["description"] = "see truth"
        self.assertFalse(verify_episode(self.ep)["ok"])


if __name__ == "__main__":
    unittest.main()
