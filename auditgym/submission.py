"""Structural validation of `submit.findings` (docs/API_CONTRACT.md sections 2 and 5).

A structurally invalid submission ends the episode with internal reward -1. Validation never
looks at truth: it only checks shape, limits, id syntax and the manifest currencies, so the
error codes below carry no information about the correct answer.
"""

from __future__ import annotations

from .config import ISSUE_TYPES
from .ids import canonical_array, namespace
from .money import JS_SAFE_INT

MAX_FINDINGS = 30
MAX_EVIDENCE = 10
MAX_EXPLANATION = 1000
FINDING_KEYS = ("type", "affected_ids", "claimed_amount_minor", "currency", "evidence_ids", "explanation")
AFFECTED_NAMESPACES = ("ledger", "invoice", "payment", "fx")  # doc:* is forbidden in affected_ids


class SubmissionError(ValueError):
    """Internal detail of why a submission is invalid (for the evaluator log only)."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _ids(value, field: str, allowed: tuple, lo: int, hi: int | None) -> list:
    if not isinstance(value, list):
        raise SubmissionError("bad_field", f"{field} must be an array")
    if len(value) < lo or (hi is not None and len(value) > hi):
        raise SubmissionError("limit", f"{field} must have {lo}..{hi if hi is not None else 'n'} ids")
    for x in value:
        ns = namespace(x)
        if ns is None:
            raise SubmissionError("bad_id", f"{field} contains an id that is not canonical")
        if ns not in allowed:
            raise SubmissionError("bad_namespace", f"{field} contains a {ns}: id, which is not allowed here")
    if len(set(value)) != len(value):
        raise SubmissionError("repeated_id", f"{field} contains a repeated id")
    return canonical_array(value)


def _affected_shape(type_: str, ids: list) -> None:
    ns = [i.split(":", 1)[0] for i in ids]
    ok = {
        "duplicate_invoice": ns.count("invoice") >= 2,
        "payment_allocation": ns.count("payment") == 1 and ns.count("invoice") >= 1,
        "fx_date_or_rate": ns.count("ledger") >= 1,
        "reporting_period": ns.count("ledger") >= 1,
    }[type_]
    if not ok:
        raise SubmissionError("affected_shape", f"affected_ids do not have the shape required for {type_}")


def validate_submission(sub, episode_id: str, currencies) -> list:
    """Return canonical findings or raise SubmissionError.

    Canonical finding: same fields, affected_ids and evidence_ids sorted (repeats are invalid,
    so canonicalization never merges anything)."""
    if not isinstance(sub, dict):
        raise SubmissionError("bad_structure", "findings must be an object")
    if sorted(sub) != ["episode_id", "findings"]:
        raise SubmissionError("bad_structure", "findings object must have exactly episode_id and findings")
    if sub["episode_id"] != episode_id:
        raise SubmissionError("wrong_episode", "episode_id does not match the current episode")
    items = sub["findings"]
    if not isinstance(items, list):
        raise SubmissionError("bad_structure", "findings.findings must be an array")
    if len(items) > MAX_FINDINGS:
        raise SubmissionError("limit", f"at most {MAX_FINDINGS} findings")
    currencies = set(currencies)
    out = []
    for f in items:
        if not isinstance(f, dict) or sorted(f) != sorted(FINDING_KEYS):
            raise SubmissionError("bad_structure", f"each finding must have exactly {list(FINDING_KEYS)}")
        if f["type"] not in ISSUE_TYPES:
            raise SubmissionError("bad_type", "unknown finding type")
        affected = _ids(f["affected_ids"], "affected_ids", AFFECTED_NAMESPACES, 1, None)
        _affected_shape(f["type"], affected)
        evidence = _ids(f["evidence_ids"], "evidence_ids", ("ledger", "invoice", "payment", "doc", "fx"), 1,
                        MAX_EVIDENCE)
        amt = f["claimed_amount_minor"]
        if not isinstance(amt, int) or isinstance(amt, bool) or abs(amt) > JS_SAFE_INT:
            raise SubmissionError("bad_amount", "claimed_amount_minor must be a safe integer")
        if not isinstance(f["currency"], str) or f["currency"] not in currencies:
            raise SubmissionError("bad_currency", "currency must be one of the manifest currencies")
        expl = f["explanation"]
        if not isinstance(expl, str) or not 1 <= len(expl) <= MAX_EXPLANATION:
            raise SubmissionError("bad_explanation", f"explanation must have 1..{MAX_EXPLANATION} code points")
        out.append({"type": f["type"], "affected_ids": affected, "claimed_amount_minor": amt,
                    "currency": f["currency"], "evidence_ids": evidence, "explanation": expl})
    return out
