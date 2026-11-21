"""Independent data checks for one episode (case section 10, 'Данные и эталон').

Computed from public records + truth/provenance, without the generator's internal state:
  schema      - fields, types, canonical id namespaces, ISO-8601 offsets, explicit nulls, counts,
                known timezone and a period of exactly one calendar month in its offset
  policy      - policy.json has exactly the contract fields and values; accounts cover the ledger
  balance     - every posting group balances in the published ledger and in the clean ledger
  clean_books - accounting equalities hold before errors: the auditor finds nothing on the clean ledger
  evidence    - every evidence id is public; each declared amount is recomputed from its evidence set alone
  solvable    - the auditor working from public data alone reproduces exactly the truth set
  no_leak     - public artifacts contain no truth/provenance fields, seed or file paths
"""

from __future__ import annotations

import json
import re
from collections import defaultdict

import calendar

from .books import Books, audit
from .config import ISSUE_TYPES
from .money import EXPONENTS, JS_SAFE_INT, ROUNDING_RULE, convert_minor
from .timeutil import ZONES, offset_tz, parse

# Contract 1.1.0, section 10.2: every policy_summary field is required and has a fixed value.
POLICY_EXPECTED = {
    "fx_rate_date": "document_timestamp_local_date",
    "period_boundary": "inclusive_start_and_end",
    "rounding": ROUNDING_RULE,
    "fx_rate_direction": "target_per_source",
    "ledger_currency": "base_currency",
    "invoice_posting": "debit_1200_credit_4000_at_issued_at_local_date_rate",
    "payment_posting": "debit_1000_at_payment_rate_credit_1200_per_allocation_at_invoice_rate_"
                       "excess_to_2400_difference_to_7900",
    "allocation_order": "invoice_refs_in_order_up_to_outstanding",
    "refunds": "negative_amount_debit_2400_credit_1000",
}
POLICY_KEYS = ["fx_rate_date", "period_boundary", "rounding", "fx_rate_direction", "ledger_currency", "accounts",
               "invoice_posting", "payment_posting", "allocation_order", "refunds"]
REQUIRED_ACCOUNTS = {"1000", "1200", "2400", "4000", "7900"}
ZONE_OFFSETS = dict(ZONES)

INV_KEYS = ["doc_id", "invoice_id", "entity_id", "issued_at", "due_at", "currency", "total_minor", "line_items"]
PAY_KEYS = ["doc_id", "payment_id", "timestamp", "currency", "amount_minor", "invoice_refs"]
LEDGER_KEYS = ["row_id", "timestamp", "account", "debit_minor", "credit_minor", "currency", "entity_id",
               "invoice_id", "payment_id"]
FX_KEYS = ["date", "source_currency", "target_currency", "rate"]
MANIFEST_KEYS = ["schema_version", "episode_id", "base_currency", "currency_decimals", "period_start", "period_end",
                 "timezone", "record_counts", "document_count", "rounding_rule"]
LINE_KEYS = ["description", "quantity", "unit_price_minor", "amount_minor"]

ID_RE = {
    "ledger": re.compile(r"^ledger:[!-9;-~]{1,120}$"),
    "invoice": re.compile(r"^invoice:[!-9;-~]{1,119}$"),
    "payment": re.compile(r"^payment:[!-9;-~]{1,119}$"),
    "doc": re.compile(r"^doc:[!-9;-~]{1,124}$"),
    "entity": re.compile(r"^entity:[!-9;-~]{1,121}$"),
}
FX_ID_RE = re.compile(r"^fx:\d{4}-\d{2}-\d{2}:[A-Z]{3}:[A-Z]{3}$")
RATE_RE = re.compile(r"^\d+(\.\d+)?$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


def fx_id(row: dict) -> str:
    return f"fx:{row['date']}:{row['source_currency']}:{row['target_currency']}"


def _id(v, ns: str, nullable: bool = False) -> bool:
    if v is None:
        return nullable
    return isinstance(v, str) and bool(ID_RE[ns].match(v))


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and abs(v) <= JS_SAFE_INT


def _period_problems(m: dict) -> list:
    """Timezone is a known fixed-offset zone and the period is exactly one calendar month in it."""
    errs = []
    offset = ZONE_OFFSETS.get(m["timezone"])
    if offset is None:
        return [f"manifest.timezone {m['timezone']!r} is not a supported zone"]
    start, end = parse(m["period_start"]), parse(m["period_end"])
    if m["period_start"][-6:] != offset or m["period_end"][-6:] != offset:
        errs.append("period bounds must use the offset of manifest.timezone")
    local_start, local_end = start.astimezone(offset_tz(offset)), end.astimezone(offset_tz(offset))
    last_day = calendar.monthrange(local_start.year, local_start.month)[1]
    if (local_start.day, local_start.hour, local_start.minute, local_start.second) != (1, 0, 0, 0):
        errs.append("period_start must be 00:00:00 on the first day of the month")
    if (local_end.year, local_end.month, local_end.day, local_end.hour, local_end.minute, local_end.second) != \
            (local_start.year, local_start.month, last_day, 23, 59, 59):
        errs.append("period_end must be 23:59:59 on the last day of the same month (inclusive)")
    return errs


def check_policy(ep) -> list:
    """policy.json == policy_summary: exact field set and values (contract 1.1.0, 10.2)."""
    p = ep.policy
    errs = []
    if not isinstance(p, dict) or sorted(p) != sorted(POLICY_KEYS):
        return ["policy fields differ from the contract"]
    for key, value in POLICY_EXPECTED.items():
        if p[key] != value:
            errs.append(f"policy.{key} has an unsupported value")
    accounts = p["accounts"]
    if not isinstance(accounts, dict) or not all(isinstance(k, str) and k.isdigit() and isinstance(v, str) and v
                                                  for k, v in accounts.items()):
        errs.append("policy.accounts must map account codes to names")
    elif not REQUIRED_ACCOUNTS <= set(accounts):
        errs.append("policy.accounts lacks a required account")
    else:
        used = {r["account"] for r in ep.tables["ledger"]}
        if not used <= set(accounts):
            errs.append("ledger uses an account that policy.accounts does not define")
    return errs


def check_schema(ep) -> list:
    errs = []
    m = ep.manifest
    if list(m) != MANIFEST_KEYS:
        errs.append("manifest fields differ from the contract")
    if set(m["record_counts"]) != {"ledger", "invoices", "payments", "fx"}:
        errs.append("record_counts must list the four tables")
    for t, n in m["record_counts"].items():
        if len(ep.tables[t]) != n:
            errs.append(f"record_counts mismatch for {t}")
    if m["document_count"] != len(ep.tables["invoices"]) + len(ep.tables["payments"]):
        errs.append("document_count mismatch")
    if not (TS_RE.match(m["period_start"]) and TS_RE.match(m["period_end"])):
        errs.append("period bounds must be ISO-8601 with offset")
    else:
        errs += _period_problems(m)
    if m.get("rounding_rule") != ROUNDING_RULE:
        errs.append("manifest rounding_rule differs from the contract")
    ccys = set(m["currency_decimals"])
    for r in ep.tables["ledger"]:
        if list(r) != LEDGER_KEYS:
            errs.append(f"ledger fields {r.get('row_id')}")
            continue
        ok = (_id(r["row_id"], "ledger") and TS_RE.match(r["timestamp"]) and _int(r["debit_minor"]) and
              _int(r["credit_minor"]) and r["currency"] == m["base_currency"] and _id(r["entity_id"], "entity", True)
              and _id(r["invoice_id"], "invoice", True) and _id(r["payment_id"], "payment", True))
        if not ok:
            errs.append(f"ledger values {r['row_id']}")
        if (r["debit_minor"] > 0) == (r["credit_minor"] > 0) or min(r["debit_minor"], r["credit_minor"]) < 0:
            errs.append(f"ledger row needs exactly one positive side {r['row_id']}")
    for d in ep.tables["invoices"]:
        if list(d) != INV_KEYS:
            errs.append(f"invoice fields {d.get('doc_id')}")
            continue
        ok = (_id(d["doc_id"], "doc") and _id(d["invoice_id"], "invoice") and _id(d["entity_id"], "entity") and
              TS_RE.match(d["issued_at"]) and TS_RE.match(d["due_at"]) and d["currency"] in ccys and
              _int(d["total_minor"]) and isinstance(d["line_items"], list) and d["line_items"] and
              all(list(li) == LINE_KEYS for li in d["line_items"]))
        if not ok:
            errs.append(f"invoice values {d['doc_id']}")
        elif d["total_minor"] != sum(li["amount_minor"] for li in d["line_items"]) or any(
                li["amount_minor"] != li["quantity"] * li["unit_price_minor"] for li in d["line_items"]):
            errs.append(f"invoice arithmetic {d['doc_id']}")
    for d in ep.tables["payments"]:
        if list(d) != PAY_KEYS:
            errs.append(f"payment fields {d.get('doc_id')}")
            continue
        refs = d["invoice_refs"]
        ok = (_id(d["doc_id"], "doc") and _id(d["payment_id"], "payment") and TS_RE.match(d["timestamp"]) and
              d["currency"] in ccys and _int(d["amount_minor"]) and
              (refs is None or (isinstance(refs, list) and refs and all(_id(x, "invoice") for x in refs))))
        if not ok:
            errs.append(f"payment values {d['doc_id']}")
    for r in ep.tables["fx"]:
        if list(r) != FX_KEYS or not RATE_RE.match(r["rate"]) or r["source_currency"] not in ccys or \
                r["target_currency"] != m["base_currency"]:
            errs.append(f"fx row {r}")
    for c, e in m["currency_decimals"].items():
        if EXPONENTS.get(c) != e:
            errs.append(f"currency_decimals {c}")
    ids = [r["row_id"] for r in ep.tables["ledger"]] + [d["doc_id"] for t in ("invoices", "payments")
                                                         for d in ep.tables[t]] + \
          [d["invoice_id"] for d in ep.tables["invoices"]] + [d["payment_id"] for d in ep.tables["payments"]]
    if len(ids) != len(set(ids)):
        errs.append("duplicate ids")
    known_inv = {d["invoice_id"] for d in ep.tables["invoices"]}
    known_pay = {d["payment_id"] for d in ep.tables["payments"]}
    for r in ep.tables["ledger"]:
        if (r["invoice_id"] and r["invoice_id"] not in known_inv) or (r["payment_id"] and r["payment_id"] not in known_pay):
            errs.append(f"ledger references an unknown document {r['row_id']}")
    for d in ep.tables["payments"]:
        if any(x not in known_inv for x in d["invoice_refs"] or []):
            errs.append(f"payment references an unknown invoice {d['doc_id']}")
    return errs


def _group_balance(rows: list, label: str) -> list:
    groups = defaultdict(int)
    total = 0
    for r in rows:
        key = r["payment_id"] or (("invoice", r["invoice_id"]) if r["invoice_id"] else None)
        delta = r["debit_minor"] - r["credit_minor"]
        total += delta
        if key is not None:
            groups[key] += delta
    errs = [f"{label}: unbalanced posting group {k}" for k, v in groups.items() if v != 0]
    if total != 0:
        errs.append(f"{label}: total debits != total credits")
    return errs


def check_balance(ep) -> list:
    return _group_balance(ep.tables["ledger"], "published") + _group_balance(ep.provenance["clean_ledger"], "clean")


def clean_tables(ep) -> dict:
    hidden = set(ep.provenance["history"]["documents_not_in_clean_world"])
    return {
        "ledger": ep.provenance["clean_ledger"],
        "invoices": [d for d in ep.tables["invoices"] if d["invoice_id"] not in hidden],
        "payments": ep.tables["payments"],
        "fx": ep.tables["fx"],
    }


def check_clean_books(ep) -> list:
    return [f"clean ledger flagged: {f['type']} {f['affected_ids']}" for f in audit(Books(clean_tables(ep), ep.manifest))]


def check_evidence(ep) -> list:
    errs = []
    m = ep.manifest
    base = m["base_currency"]
    start, end = parse(m["period_start"]), parse(m["period_end"])
    tz = start.tzinfo
    rows = {r["row_id"]: r for r in ep.tables["ledger"]}
    docs = {d["doc_id"]: d for t in ("invoices", "payments") for d in ep.tables[t]}
    fx = {fx_id(r): r for r in ep.tables["fx"]}
    public = set(rows) | set(docs) | set(fx) | {d["invoice_id"] for d in ep.tables["invoices"]} | \
        {d["payment_id"] for d in ep.tables["payments"]}

    def ldate(ts):
        return parse(ts).astimezone(tz).date().isoformat()

    for iss in ep.truth["issues"]:
        iid, t, exp, aff = iss["issue_id"], iss["type"], iss["expected_amount_minor"], iss["affected_ids"]
        if t not in ISSUE_TYPES or iss["currency"] != base or not _int(exp) or exp <= 0:
            errs.append(f"{iid}: bad type, currency or amount")
        if aff != sorted(set(aff)) or any(a not in public for a in aff):
            errs.append(f"{iid}: affected_ids not canonical or not public")
        ns = [a.split(":", 1)[0] for a in aff]
        shape_ok = {
            "duplicate_invoice": ns.count("invoice") >= 2 and set(ns) == {"invoice"},
            "payment_allocation": ns.count("payment") == 1 and ns.count("invoice") >= 1 and set(ns) <= {"invoice", "payment"},
            "fx_date_or_rate": len(ns) >= 1 and set(ns) == {"ledger"},
            "reporting_period": len(ns) >= 1 and set(ns) == {"ledger"},
        }.get(t, False)
        if not shape_ok:
            errs.append(f"{iid}: affected_ids shape violates the contract")
        if not iss["evidence_sets"] or any(not 1 <= len(s) <= 10 for s in iss["evidence_sets"]):
            errs.append(f"{iid}: evidence sets must have 1..10 ids")
        ok_any = False
        for ev in iss["evidence_sets"]:
            missing = [e for e in ev if e not in rows and e not in docs and e not in fx]
            if missing:
                errs.append(f"{iid}: evidence not in public records {missing}")
                continue
            R = [rows[e] for e in ev if e in rows]
            D = [docs[e] for e in ev if e in docs]
            F = [fx[e] for e in ev if e in fx]
            try:
                if t == "duplicate_invoice":
                    a, b = D
                    later = max(D, key=lambda d: parse(d["issued_at"]))
                    ok = (all(a[k] == b[k] for k in ("entity_id", "currency", "total_minor", "line_items")) and
                          ldate(a["issued_at"]) == ldate(b["issued_at"]) and R[0]["invoice_id"] == later["invoice_id"]
                          and max(R[0]["debit_minor"], R[0]["credit_minor"]) == exp and
                          set(aff) == {a["invoice_id"], b["invoice_id"]})
                elif t == "payment_allocation":
                    pay = next(d for d in D if "payment_id" in d)
                    inv = next(d for d in D if "invoice_id" in d and "payment_id" not in d)
                    row = R[0]
                    ok = (inv["invoice_id"] in (pay["invoice_refs"] or []) and row["account"] == "2400" and
                          row["payment_id"] == pay["payment_id"] and row["credit_minor"] == exp and
                          0 < pay["amount_minor"] <= inv["total_minor"] and
                          set(aff) == {pay["payment_id"], inv["invoice_id"]})
                elif t == "fx_date_or_rate":
                    doc, row, rate = D[0], R[0], F[0]
                    ts = doc.get("issued_at") or doc.get("timestamp")
                    amt = doc["total_minor"] if "total_minor" in doc else doc["amount_minor"]
                    good = convert_minor(amt, doc["currency"], base, rate["rate"])
                    posted = max(row["debit_minor"], row["credit_minor"])
                    ok = (rate["date"] == ldate(ts) and rate["source_currency"] == doc["currency"] and
                          abs(posted - good) == exp and row["row_id"] in aff and start <= parse(ts) <= end)
                else:
                    doc, row = D[0], R[0]
                    ts = doc.get("issued_at") or doc.get("timestamp")
                    ok = (not (start <= parse(ts) <= end) and start <= parse(row["timestamp"]) <= end and
                          max(row["debit_minor"], row["credit_minor"]) == exp and row["row_id"] in aff)
            except (StopIteration, ValueError, IndexError, KeyError, TypeError):
                ok = False
            ok_any = ok_any or ok
            if not ok:
                errs.append(f"{iid}: evidence set {ev} does not establish the declared discrepancy")
        if not ok_any:
            errs.append(f"{iid}: no sufficient evidence set")
    return errs


def _key(f: dict) -> tuple:
    return (f["type"], tuple(sorted(f["affected_ids"])), f.get("claimed_amount_minor", f.get("expected_amount_minor")),
            f["currency"])


def check_solvable(ep) -> list:
    found = audit(Books(ep.tables, ep.manifest))
    errs = []
    truth = {_key(i): i for i in ep.truth["issues"]}
    got = {_key(f): f for f in found}
    for k in truth.keys() - got.keys():
        errs.append(f"public-data auditor missed {k[0]} {list(k[1])}")
    for k in got.keys() - truth.keys():
        errs.append(f"public-data auditor raised {k[0]} {list(k[1])} not in truth")
    for k in truth.keys() & got.keys():
        ev = set(got[k]["evidence_ids"])
        if not any(set(s) <= ev for s in truth[k]["evidence_sets"]):
            errs.append(f"public-data auditor's evidence for {k[0]} matches no acceptable set")
    if len(found) != len(got):
        errs.append("public-data auditor produced duplicate findings")
    return errs


PATH_RE = re.compile(r"([A-Za-z]:\\|\\\\|/(home|tmp|private|Users|var|etc)/|\.json|\.csv|\.jsonl)")


def check_no_leak(ep) -> list:
    text = json.dumps({"manifest": ep.manifest, "policy": ep.policy, "tables": ep.tables}, ensure_ascii=False)
    errs = []
    for token in ("issue_id", "issue_", "expected_amount_minor", "evidence_sets", "provenance", "truth", "scenario",
                  "clean_ledger", "mutation", '"seed"', "clean-only"):
        if token in text:
            errs.append(f"public data contains private token {token!r}")
    if PATH_RE.search(text):
        errs.append("public data contains something that looks like a file path")
    return errs


CHECKS = {
    "schema": check_schema,
    "policy": check_policy,
    "balance": check_balance,
    "clean_books": check_clean_books,
    "evidence": check_evidence,
    "solvable": check_solvable,
    "no_leak": check_no_leak,
}


def verify_episode(ep) -> dict:
    out = {}
    for name, fn in CHECKS.items():
        try:
            out[name] = fn(ep)
        except Exception as e:  # a crashing check is a failed check
            out[name] = [f"check crashed: {type(e).__name__}: {e}"]
    return {"episode_id": ep.episode_id, "ok": all(not v for v in out.values()), "errors": out}
