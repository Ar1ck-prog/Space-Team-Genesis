"""Shared matcher (docs/API_CONTRACT.md section 5, docs/MATCHER.md). Public on purpose.

Normalization: none beyond the contract's canonical arrays (sorted, no repeats). IDs are
compared exactly and case-sensitively; no aliases (an invoice:* id does not stand in for its
doc:* id).

Tolerance: AMOUNT_TOLERANCE_MINOR = 0 (contract v1: the generator rounds before comparison).

A finding f is *eligible* for truth issue t when all hold:
  1. f.type == t.type
  2. set(f.affected_ids) == set(t.affected_ids)
  3. f.currency == t.currency
  4. |f.claimed_amount_minor - t.expected_amount_minor| <= AMOUNT_TOLERANCE_MINOR
  5. set(f.evidence_ids) contains at least one acceptable evidence set of t in full

Ambiguity: the matching is a maximum-cardinality one-to-one assignment over eligible pairs,
found with augmenting paths. Findings are visited in submission order and truth issues in
issue_id order, so the result is deterministic. Different maximum matchings have the same
size, so the tie-break never changes TP/FP/FN or the reward. Unmatched and repeated findings
are FP; unmatched truth issues are FN.
"""

from __future__ import annotations

AMOUNT_TOLERANCE_MINOR = 0


def eligible(f: dict, t: dict) -> bool:
    if f["type"] != t["type"] or f["currency"] != t["currency"]:
        return False
    if set(f["affected_ids"]) != set(t["affected_ids"]):
        return False
    if abs(f["claimed_amount_minor"] - t["expected_amount_minor"]) > AMOUNT_TOLERANCE_MINOR:
        return False
    ev = set(f["evidence_ids"])
    return any(set(s) <= ev for s in t["evidence_sets"])


def match(findings: list, truth: list) -> dict:
    """Return {"tp", "fp", "fn", "pairs": [(finding_index, issue_id), ...]}."""
    issues = sorted(truth, key=lambda t: t["issue_id"])
    adj = [[j for j, t in enumerate(issues) if eligible(f, t)] for f in findings]
    owner = [-1] * len(issues)  # truth index -> finding index

    def augment(i: int, seen: set) -> bool:
        for j in adj[i]:
            if j not in seen:
                seen.add(j)
                if owner[j] == -1 or augment(owner[j], seen):
                    owner[j] = i
                    return True
        return False

    for i in range(len(findings)):
        augment(i, set())
    pairs = sorted((i, issues[j]["issue_id"]) for j, i in enumerate(owner) if i != -1)
    tp = len(pairs)
    return {"tp": tp, "fp": len(findings) - tp, "fn": len(issues) - tp, "pairs": pairs}


def metrics(tp: int, fp: int, fn: int) -> dict:
    """P, R, F1 with the contract's zero rules."""
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    if tp + fp + fn == 0:
        f1 = 1.0  # empty truth and empty findings
    else:
        f1 = 2 * tp / (2 * tp + fp + fn)  # empty truth with findings -> 0
    return {"precision": precision, "recall": recall, "f1": f1}
