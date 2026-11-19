"""Analysis over PUBLIC records only.

`audit()` is a rule-based auditor that applies policy.json to the open records. The generator
tests use it as an independent solvability check: on every generated episode it must find
exactly the truth set using public data alone, and nothing on the clean ledger.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date

from .money import convert_minor
from .timeutil import parse


class Books:
    def __init__(self, tables: dict, manifest: dict):
        self.manifest = manifest
        self.base = manifest["base_currency"]
        self.start = parse(manifest["period_start"])
        self.end = parse(manifest["period_end"])
        self.tz = self.start.tzinfo  # company offset is carried by period_start
        self.rates = {(r["source_currency"], r["date"]): r["rate"] for r in tables["fx"]
                      if r["target_currency"] == self.base}
        self.invoices = {d["invoice_id"]: d for d in tables["invoices"]}
        self.payments = {d["payment_id"]: d for d in tables["payments"]}
        self.ledger = list(tables["ledger"])
        self.rows_by_invoice = defaultdict(list)  # invoice postings (payment_id is null)
        self.rows_by_payment = defaultdict(list)
        for r in self.ledger:
            if r["payment_id"] is not None:
                self.rows_by_payment[r["payment_id"]].append(r)
            elif r["invoice_id"] is not None:
                self.rows_by_invoice[r["invoice_id"]].append(r)

    def local_date(self, ts: str) -> date:
        return parse(ts).astimezone(self.tz).date()

    def in_period(self, ts: str) -> bool:
        return self.start <= parse(ts) <= self.end

    def rate(self, ccy: str, d: date) -> str | None:
        return "1" if ccy == self.base else self.rates.get((ccy, d.isoformat()))

    def to_base(self, amount: int, ccy: str, d: date) -> int | None:
        r = self.rate(ccy, d)
        return None if r is None else convert_minor(amount, ccy, self.base, r)

    def fx_id(self, ccy: str, d: date) -> str:
        return f"fx:{d.isoformat()}:{ccy}:{self.base}"

    def rows(self, by: dict, key: str, account: str) -> list:
        return [r for r in by.get(key, []) if r["account"] == account]

    def posted_invoices(self) -> list:
        return [inv for iid, inv in self.invoices.items() if self.rows(self.rows_by_invoice, iid, "1200")]

    def posted_payments(self) -> list:
        return [p for pid, p in self.payments.items() if self.rows_by_payment.get(pid)]


def _finding(type_, affected, amount, base, evidence, why):
    return {"type": type_, "affected_ids": sorted(set(affected)), "claimed_amount_minor": int(amount),
            "currency": base, "evidence_ids": sorted({e for e in evidence if e})[:10], "explanation": why[:1000]}


def audit(b: Books) -> list:
    base = b.base
    out = []

    # Invoices: period first, then conversion.
    for inv in b.posted_invoices():
        iid = inv["invoice_id"]
        ar = b.rows(b.rows_by_invoice, iid, "1200")[0]
        rev = b.rows(b.rows_by_invoice, iid, "4000")[0]
        if not b.in_period(inv["issued_at"]):
            out.append(_finding("reporting_period", [r["row_id"] for r in b.rows_by_invoice[iid]], ar["debit_minor"],
                                base, [inv["doc_id"], ar["row_id"]], "Invoice issued outside the period is posted in it."))
            continue
        if inv["currency"] == base:
            continue
        d = b.local_date(inv["issued_at"])
        expected = b.to_base(inv["total_minor"], inv["currency"], d)
        if expected is not None and expected != ar["debit_minor"]:
            out.append(_finding("fx_date_or_rate", [ar["row_id"], rev["row_id"]], abs(ar["debit_minor"] - expected),
                                base, [inv["doc_id"], ar["row_id"], b.fx_id(inv["currency"], d)],
                                f"Posted {ar['debit_minor']}, the policy rate gives {expected}."))

    # Payments: period first, then conversion.
    for p in b.posted_payments():
        if p["amount_minor"] < 0:
            continue
        pid = p["payment_id"]
        cash = b.rows(b.rows_by_payment, pid, "1000")[0]
        if not b.in_period(p["timestamp"]):
            out.append(_finding("reporting_period", [r["row_id"] for r in b.rows_by_payment[pid]], cash["debit_minor"],
                                base, [p["doc_id"], cash["row_id"]], "Payment received outside the period is posted in it."))
            continue
        if p["currency"] == base:
            continue
        d = b.local_date(p["timestamp"])
        expected = b.to_base(p["amount_minor"], p["currency"], d)
        if expected is not None and expected != cash["debit_minor"]:
            out.append(_finding("fx_date_or_rate", [cash["row_id"]], abs(cash["debit_minor"] - expected), base,
                                [p["doc_id"], cash["row_id"], b.fx_id(p["currency"], d)],
                                f"Cash posted {cash['debit_minor']}, the policy rate gives {expected}."))

    # Duplicates: same entity, currency, total, line items and local issue date.
    groups = defaultdict(list)
    for inv in b.posted_invoices():
        key = (inv["entity_id"], inv["currency"], inv["total_minor"], json.dumps(inv["line_items"], sort_keys=True),
               b.local_date(inv["issued_at"]).isoformat())
        groups[key].append(inv)
    for invs in groups.values():
        if len(invs) < 2:
            continue
        invs = sorted(invs, key=lambda i: parse(i["issued_at"]))
        for dup in invs[1:]:
            ar = b.rows(b.rows_by_invoice, dup["invoice_id"], "1200")[0]
            out.append(_finding("duplicate_invoice", [invs[0]["invoice_id"], dup["invoice_id"]], ar["debit_minor"], base,
                                [invs[0]["doc_id"], dup["doc_id"], ar["row_id"]], "Same invoice content posted twice."))

    # Allocation: replay the policy in posting order and compare AR credits.
    outstanding = {iid: inv["total_minor"] for iid, inv in b.invoices.items()}
    ordered = sorted(b.posted_payments(), key=lambda q: parse(b.rows_by_payment[q["payment_id"]][0]["timestamp"]))
    for p in ordered:
        if p["amount_minor"] < 0 or not p["invoice_refs"]:
            continue
        pid = p["payment_id"]
        remaining = p["amount_minor"]
        unapplied = b.rows(b.rows_by_payment, pid, "2400")
        for iid in p["invoice_refs"]:
            inv = b.invoices.get(iid)
            if inv is None:
                continue
            a = min(remaining, outstanding[iid])
            if a <= 0:
                continue
            outstanding[iid] -= a
            remaining -= a
            expected = b.to_base(a, inv["currency"], b.local_date(inv["issued_at"]))
            actual = sum(r["credit_minor"] for r in b.rows(b.rows_by_payment, pid, "1200") if r["invoice_id"] == iid)
            if expected is not None and expected > actual:
                out.append(_finding("payment_allocation", [pid, iid], expected - actual, base,
                                    [p["doc_id"], inv["doc_id"], unapplied[0]["row_id"] if unapplied else None],
                                    f"Expected AR credit {expected}, applied {actual}."))
    return out
