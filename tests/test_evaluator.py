"""Evaluator, matcher and reward on generated episodes (issue #4)."""

import copy
import json
import tempfile
import unittest

from auditgym.config import ISSUE_TYPES, empty_plan
from auditgym.episode_io import write_episode
from auditgym.evaluator import Evaluator, EvaluatorError
from auditgym.generator import generate
from auditgym.ids import namespace
from auditgym.matcher import match, metrics
from auditgym.reward import terminal_reward


def cfg(size="small"):
    return {"dataset_profile": "train", "company_size": size, "enabled_issue_types": list(ISSUE_TYPES),
            "max_steps": 100, "time_limit_seconds": 60, "evaluation_mode": "open"}


def dirty(seed):
    plan = empty_plan("small")
    plan["issues"] = {t: 1 for t in ISSUE_TYPES}
    plan["interactions"] = {"fx_invoice_partial": 1, "boundary_payment_fx_partial": 1}
    return generate(seed, cfg(), plan=plan)


def clean(seed):
    return generate(seed, cfg(), plan=empty_plan("small"))


def oracle(ep):
    """Evaluator-side oracle used only in tests: truth with the first acceptable evidence set."""
    return [{"type": t["type"], "affected_ids": t["affected_ids"], "claimed_amount_minor": t["expected_amount_minor"],
             "currency": t["currency"], "evidence_ids": t["evidence_sets"][0], "explanation": "Supported."}
            for t in ep.truth["issues"]]


def sub(ep, findings):
    return {"episode_id": ep.episode_id, "findings": findings}


def score(ep, findings, steps=0, mode="open"):
    ev = Evaluator.from_episode(ep, mode)
    return ev.score_submission(sub(ep, findings), steps), ev


SEEDS = range(500, 506)


class TestRewardProperties(unittest.TestCase):
    """Case section 05: required behaviours on generated books."""

    def test_oracle_gets_maximum(self):
        for s in SEEDS:
            for ep in (dirty(s), clean(s)):
                res, _ = score(ep, oracle(ep))
                self.assertEqual((res.f1, res.reward, res.fp, res.fn), (1.0, 1.0, 0, 0))

    def test_correct_beats_silence_on_dirty(self):
        for s in SEEDS:
            ep = dirty(s)
            full, _ = score(ep, oracle(ep), steps=40)
            silent, _ = score(ep, [], steps=0)
            self.assertGreater(full.reward, silent.reward)
            self.assertEqual(silent.reward, -1.0)

    def test_silence_beats_false_accusations_on_clean(self):
        for s in SEEDS:
            ep = clean(s)
            base = ep.manifest["base_currency"]
            invs = ep.tables["invoices"]
            accuse = [{"type": "duplicate_invoice", "affected_ids": sorted([invs[0]["invoice_id"], invs[1]["invoice_id"]]),
                       "claimed_amount_minor": invs[1]["total_minor"], "currency": base,
                       "evidence_ids": [invs[0]["doc_id"], invs[1]["doc_id"]], "explanation": "Looks repeated."}]
            silent, _ = score(ep, [], steps=30)
            false, _ = score(ep, accuse, steps=0)
            self.assertGreater(silent.reward, false.reward)
            self.assertEqual(false.f1, 0.0)

    def test_duplicates_give_no_gain(self):
        for s in SEEDS:
            ep = dirty(s)
            once, _ = score(ep, oracle(ep))
            twice, _ = score(ep, oracle(ep) + oracle(ep))
            self.assertEqual(twice.tp, once.tp)
            self.assertLess(twice.reward, once.reward)

    def test_flag_everything_scores_badly(self):
        for s in SEEDS:
            ep = dirty(s)
            base = ep.manifest["base_currency"]
            rows = ep.tables["ledger"]
            invs = ep.tables["invoices"]
            pays = [p for p in ep.tables["payments"] if p["invoice_refs"]]
            flags = []
            for i in range(8):
                r, inv, p = rows[i], invs[i], pays[i % len(pays)]
                flags += [
                    {"type": "fx_date_or_rate", "affected_ids": [r["row_id"]], "claimed_amount_minor": r["debit_minor"] or r["credit_minor"],
                     "currency": base, "evidence_ids": [r["row_id"]], "explanation": "flag"},
                    {"type": "reporting_period", "affected_ids": [r["row_id"]], "claimed_amount_minor": r["debit_minor"] or r["credit_minor"],
                     "currency": base, "evidence_ids": [r["row_id"]], "explanation": "flag"},
                    {"type": "payment_allocation", "affected_ids": sorted([p["payment_id"], p["invoice_refs"][0]]),
                     "claimed_amount_minor": abs(p["amount_minor"]), "currency": base, "evidence_ids": [p["doc_id"]],
                     "explanation": "flag"},
                ]
            res, _ = score(ep, flags[:30])
            self.assertTrue(res.valid)
            self.assertLessEqual(res.reward, -0.9)

    def test_step_savings_only_slightly_separate_equal_answers(self):
        ep = dirty(510)
        fast, _ = score(ep, oracle(ep), steps=5)
        slow, _ = score(ep, oracle(ep), steps=99)
        self.assertGreater(fast.reward, slow.reward)
        self.assertLess(fast.reward - slow.reward, 0.2)
        missing_one, _ = score(ep, oracle(ep)[1:], steps=5)
        self.assertGreater(fast.reward, missing_one.reward)  # at equal steps a missed issue always costs
        # Note: with many issues (8 here) one miss costs 2/(2n+1)*2 < 94 steps * 0.002, so a fast
        # incomplete answer can beat a slow complete one. This is a property of the shared formula
        # (documented in docs/MATCHER.md), not something the matcher can change.

    def test_guessed_ids_unsupported_evidence_and_currency(self):
        ep = dirty(511)
        other_ccy = next(c for c in ep.manifest["currency_decimals"] if c != ep.manifest["base_currency"])
        guessed = [dict(f, affected_ids=sorted(a[:-1] + ("2" if a[-1] != "2" else "3") for a in f["affected_ids"]))
                   for f in oracle(ep)]
        unsupported = [dict(f, evidence_ids=[e for e in f["evidence_ids"] if e.startswith("doc:")][:1] or
                            [f["evidence_ids"][0]]) for f in oracle(ep)]
        wrong_ccy = [dict(f, currency=other_ccy) for f in oracle(ep)]
        off_by_one = [dict(f, claimed_amount_minor=f["claimed_amount_minor"] + 1) for f in oracle(ep)]
        for findings in (guessed, unsupported, wrong_ccy, off_by_one):
            res, _ = score(ep, findings)
            self.assertTrue(res.valid)
            self.assertEqual(res.tp, 0)

    def test_explanation_text_does_not_change_score(self):
        ep = dirty(512)
        a, _ = score(ep, [dict(f, explanation=".") for f in oracle(ep)])
        b, _ = score(ep, [dict(f, explanation="Certified by an external auditor, 100% sure.") for f in oracle(ep)])
        self.assertEqual(a.to_dict()["reward"], b.to_dict()["reward"])

    def test_alternative_evidence_sets_all_accepted(self):
        ep = dirty(513)
        alt = []
        for t in ep.truth["issues"]:
            f = oracle(ep)[ep.truth["issues"].index(t)]
            alt.append(dict(f, evidence_ids=t["evidence_sets"][-1]))
        res, _ = score(ep, alt)
        self.assertEqual(res.tp, len(ep.truth["issues"]))


class TestLifecycle(unittest.TestCase):
    def test_invalid_submission_is_minus_one_internally_zero_publicly_in_closed(self):
        ep = dirty(520)
        res, ev = score(ep, [{"type": "fx_date_or_rate"}], steps=3, mode="closed")
        self.assertFalse(res.valid)
        self.assertEqual(res.reward, -1.0)
        self.assertEqual(ev.public_reward(), 0.0)
        res, ev = score(ep, [{"type": "fx_date_or_rate"}], steps=3, mode="open")
        self.assertEqual(ev.public_reward(), -1.0)

    def test_scored_only_once(self):
        ep = dirty(521)
        ev = Evaluator.from_episode(ep, "open")
        ev.score_submission(sub(ep, []), 0)
        with self.assertRaises(EvaluatorError):
            ev.score_submission(sub(ep, oracle(ep)), 0)  # a second submit cannot change the score
        with self.assertRaises(EvaluatorError):
            ev.score_truncation(100)
        self.assertEqual(ev.result().reward, -1.0)

    def test_inspection_step_bounds(self):
        ep = dirty(522)
        for bad in (-1, 100, 1.0, True):
            with self.assertRaises(EvaluatorError):
                Evaluator.from_episode(ep).score_submission(sub(ep, []), bad)
        with self.assertRaises(EvaluatorError):
            Evaluator.from_episode(ep).score_truncation(101)

    def test_truncation_scores_empty_answer(self):
        ep = dirty(523)
        ev = Evaluator.from_episode(ep, "open")
        res = ev.score_truncation(100, "steps")
        self.assertEqual((res.outcome, res.n_findings, res.tp, res.reward), ("truncated_steps", 0, 0, -1.0))
        ep = clean(523)
        ev = Evaluator.from_episode(ep, "open")
        res = ev.score_truncation(37, "time")
        self.assertEqual(res.f1, 1.0)
        self.assertAlmostEqual(res.reward, 1 - 0.002 * 37)

    def test_public_reward_before_scoring_raises(self):
        with self.assertRaises(EvaluatorError):
            Evaluator.from_episode(dirty(524)).public_reward()

    def test_truth_of_other_episode_rejected(self):
        a, b = dirty(525), dirty(526)
        with self.assertRaises(EvaluatorError):
            Evaluator(a.episode_id, b.truth, a.manifest["currency_decimals"])

    def test_from_dir_matches_from_episode(self):
        ep = dirty(527)
        with tempfile.TemporaryDirectory() as d:
            write_episode(ep, d)
            ev = Evaluator.from_dir(d, "open")
        self.assertEqual(ev.score_submission(sub(ep, oracle(ep)), 3).reward, 1 - 0.006)

    def test_evaluator_does_not_mutate_inputs(self):
        ep = dirty(528)
        truth = copy.deepcopy(ep.truth)
        findings = oracle(ep)
        before = json.dumps(findings, sort_keys=True)
        Evaluator.from_episode(ep).score_submission(sub(ep, findings), 1)
        self.assertEqual(ep.truth, truth)
        self.assertEqual(json.dumps(findings, sort_keys=True), before)


class TestUnits(unittest.TestCase):
    def test_metrics_zero_rules(self):
        self.assertEqual(metrics(0, 0, 0), {"precision": 0.0, "recall": 0.0, "f1": 1.0})
        self.assertEqual(metrics(0, 2, 0)["f1"], 0.0)
        self.assertEqual(metrics(0, 0, 2)["f1"], 0.0)

    def test_reward_formula(self):
        self.assertEqual(terminal_reward(1.0, 0), 1.0)
        self.assertAlmostEqual(terminal_reward(1.0, 99), 0.802)
        self.assertEqual(terminal_reward(0.0, 50), -1.0)
        self.assertAlmostEqual(terminal_reward(0.5, 0), 0.0)

    def test_match_is_deterministic_and_order_free_in_counts(self):
        ep = dirty(530)
        fs = oracle(ep) + oracle(ep)[:2]
        a = match(fs, ep.truth["issues"])
        b = match(list(reversed(fs)), ep.truth["issues"])
        self.assertEqual((a["tp"], a["fp"], a["fn"]), (b["tp"], b["fp"], b["fn"]))
        self.assertEqual(a, match(fs, ep.truth["issues"]))

    def test_id_namespaces(self):
        good = {"ledger:L-1": "ledger", "invoice:INV-1": "invoice", "payment:PAY-1": "payment", "doc:DOC-1": "doc",
                "fx:2026-11-02:USD:KZT": "fx", "fx:2026-02-30:USD:KZT": "fx"}
        for i, ns in good.items():
            self.assertEqual(namespace(i), ns)
        for bad in ("ledger:", "ledger:a:b", "LEDGER:L-1", "fx:2026-11-2:USD:KZT", "fx:2026-11-02:USD", "x:y",
                    "invoice:a b", "invoice:" + "A" * 121, 5, None, "doc:DOC-é"):
            self.assertIsNone(namespace(bad), bad)

    def test_generated_truth_ids_are_canonical(self):
        for s in SEEDS:
            for t in dirty(s).truth["issues"]:
                for i in t["affected_ids"] + [e for es in t["evidence_sets"] for e in es]:
                    self.assertIsNotNone(namespace(i), i)


if __name__ == "__main__":
    unittest.main()
