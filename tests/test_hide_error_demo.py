"""Black-box checks for the offline ``Hide the error from AI`` jury demo.

These tests intentionally use only the five functions exported by
``auditgym.hide_error_demo``.  In particular, they do not inspect a case
object, truth/provenance files, or a generator plan: that mirrors what the
jury-facing agent is allowed to learn.
"""
from __future__ import annotations

import json
import unittest

from auditgym.hide_error_demo import (
    build_case,
    organizer_evaluate,
    public_case_view,
    run_agent,
)


OCTOBER = "2026-10"
NOVEMBER = "2026-11"


def _as_json(value):
    """Ensure an API boundary remains serialisable, then return a deep copy."""
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _operations(view: dict) -> list[dict]:
    operations = view.get("operations")
    if not isinstance(operations, list) or len(operations) != 2:
        raise AssertionError("public case must expose exactly two operations")
    return operations


def _amount(operation: dict) -> int:
    amount = operation.get("amount_minor")
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise AssertionError("each public operation needs integer amount_minor")
    return amount


def _observation_stream(agent_run):
    """Return only data visible before the agent submits its answer."""
    if not isinstance(agent_run, dict):
        raise AssertionError("run_agent must return a JSON object")
    observations = agent_run.get("observations")
    if not isinstance(observations, list):
        raise AssertionError("agent run must expose its public observations")
    return observations


def _assert_no_secret(value, seed: int) -> None:
    """Guard the public boundary without knowing the shape of observations."""
    encoded = json.dumps(_as_json(value), ensure_ascii=False, sort_keys=True).lower()
    for forbidden in ("truth", "provenance", "reference", "expected", "oracle", "swapped", "seed"):
        if forbidden in encoded:
            raise AssertionError(f"public agent data leaked private marker: {forbidden}")
    if str(seed) in encoded:
        raise AssertionError("public agent data leaked the organizer seed")


class HideErrorDemoTests(unittest.TestCase):
    """The demo's invariant and confidentiality contract, from public data only."""

    def test_clean_case_is_balanced_and_agent_raises_no_false_alarm(self):
        case = build_case(seed=481516, swapped=False)
        view = _as_json(public_case_view(case))

        self.assertEqual(view["monthly_totals"], {OCTOBER: 400_000_00, NOVEMBER: 400_000_00})
        operations = _operations(view)
        self.assertEqual([op["ledger_month"] for op in operations], [OCTOBER, NOVEMBER])
        self.assertEqual([op["document_month"] for op in operations], [OCTOBER, NOVEMBER])
        self.assertEqual([_amount(op) for op in operations], [400_000_00, 400_000_00])

        agent_run = _as_json(run_agent(case))
        evaluation = _as_json(organizer_evaluate(case, agent_run))
        self.assertEqual(evaluation["errors_proven"], 0)
        self.assertFalse(evaluation["false_alarm"])
        self.assertEqual(evaluation["findings"], [])

    def test_swapped_case_has_two_document_provable_errors(self):
        case = build_case(seed=271828, swapped=True)
        view = _as_json(public_case_view(case))

        self.assertEqual(view["monthly_totals"], {OCTOBER: 400_000_00, NOVEMBER: 400_000_00})
        operations = _operations(view)
        self.assertEqual([_amount(op) for op in operations], [400_000_00, 400_000_00])
        self.assertEqual([op["ledger_month"] for op in operations], [NOVEMBER, OCTOBER])
        self.assertEqual([op["document_month"] for op in operations], [OCTOBER, NOVEMBER])
        self.assertTrue(all(op.get("document_id") for op in operations))

        agent_run = _as_json(run_agent(case))
        evaluation = _as_json(organizer_evaluate(case, agent_run))
        self.assertEqual(evaluation["errors_proven"], 2)
        self.assertFalse(evaluation["false_alarm"])
        findings = evaluation["findings"]
        self.assertEqual(len(findings), 2)
        self.assertEqual({finding["document_id"] for finding in findings},
                         {operation["document_id"] for operation in operations})

    def test_equal_monthly_totals_do_not_hide_the_swapped_dates(self):
        clean = _as_json(public_case_view(build_case(seed=42, swapped=False)))
        swapped = _as_json(public_case_view(build_case(seed=42, swapped=True)))

        self.assertEqual(clean["monthly_totals"], swapped["monthly_totals"])
        self.assertEqual(sum(_amount(operation) for operation in _operations(clean)), 800_000_00)
        self.assertEqual(sum(_amount(operation) for operation in _operations(swapped)), 800_000_00)
        self.assertNotEqual(
            [(op["ledger_month"], op["document_month"]) for op in _operations(clean)],
            [(op["ledger_month"], op["document_month"]) for op in _operations(swapped)],
        )

    def test_same_seed_reproduces_the_public_case_and_agent_result(self):
        first = build_case(seed=987654, swapped=True)
        second = build_case(seed=987654, swapped=True)

        self.assertEqual(_as_json(public_case_view(first)), _as_json(public_case_view(second)))
        self.assertEqual(_as_json(run_agent(first)), _as_json(run_agent(second)))

    def test_public_view_and_pre_submit_observations_do_not_leak_organizer_choice(self):
        seed = 314159
        for swapped in (False, True):
            with self.subTest(swapped=swapped):
                case = build_case(seed=seed, swapped=swapped)
                _assert_no_secret(public_case_view(case), seed)
                _assert_no_secret(_observation_stream(run_agent(case)), seed)


if __name__ == "__main__":
    unittest.main()
