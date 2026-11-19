"""Synthetic company generator (issue #2, docs/GENERATOR.md).

generate(seed, config, environment_version) -> Episode

Pipeline:
  1. Build a coherent hidden history: entities, FX rates, invoices, payments, expenses.
  2. Add legitimate distractors (recurring amounts, split payments, refunds, boundary docs).
  3. Inject planned errors as explicit mutations of that history ("dirty" posting options).
  4. Post the clean and the dirty ledgers with the same posting code. Every truth amount is
     the difference between them, so each declared discrepancy is a real, recorded change.
  5. Render public tables (canonical IDs, contract formats) and private truth/provenance.
     IDs come from a separate RNG stream, so plan.transform.id_salt renames records without
     changing content; plan.transform.order_seed permutes record order.
"""

from __future__ import annotations

import calendar
import copy
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from . import config as cfgmod
from .money import EXPONENTS, JS_SAFE_INT, ROUNDING_RULE, convert_minor, format_rate, scale_minor
from .timeutil import ZONES, iso, offset_tz
from .version import ENVIRONMENT_VERSION, SCHEMA_VERSION

USD_VALUE = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "KZT": 0.00205, "CNY": 0.138, "TRY": 0.029, "JPY": 0.0067, "KWD": 3.25}
BASE_CHOICES = ["USD", "EUR", "KZT"]

ACCOUNTS = {
    "1000": "Cash",
    "1200": "Accounts receivable",
    "2400": "Unapplied receipts",
    "4000": "Revenue",
    "6100": "Rent",
    "6200": "Salaries",
    "6300": "Utilities",
    "6400": "Software subscriptions",
    "6500": "Bank fees",
    "7900": "FX gain and loss",
}
EXPENSE_ACCOUNTS = ["6100", "6200", "6300", "6400", "6500"]

CATALOG = [
    "Consulting hours", "Freight services", "Software license", "Maintenance visit",
    "Equipment rental", "Training session", "Cloud hosting", "Spare parts",
    "Design services", "Data labeling", "Warehouse storage", "Field survey",
]
ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# --------------------------------------------------------------------------- data classes

@dataclass
class Entity:
    key: str
    currency: str
    entity_id: str = ""


@dataclass
class Invoice:
    key: str
    entity: Entity
    issued: datetime  # true instant (aware)
    repr_offset: str  # UTC offset used to print issued_at/due_at
    due: datetime
    line_items: list
    total_minor: int
    tiebreak: float
    exists_clean: bool = True
    post_clean: bool = True
    post_dirty: bool = True
    rate_date_override: date | None = None  # dirty world only
    backdate: datetime | None = None  # dirty world only: ledger timestamp
    tags: set = field(default_factory=set)
    invoice_id: str = ""
    doc_id: str = ""

    @property
    def currency(self) -> str:
        return self.entity.currency


@dataclass
class Payment:
    key: str
    entity: Entity
    ts: datetime
    repr_offset: str
    amount_minor: int
    refs: list  # list[Invoice]
    tiebreak: float
    is_refund: bool = False
    exists_clean: bool = True
    post_clean: bool = True
    post_dirty: bool = True
    rate_date_override: date | None = None
    backdate: datetime | None = None
    misalloc: tuple | None = None  # ("missing", 0.0) | ("partial", f)
    tags: set = field(default_factory=set)
    payment_id: str = ""
    doc_id: str = ""

    @property
    def currency(self) -> str:
        return self.entity.currency


@dataclass
class Expense:
    key: str
    ts: datetime
    account: str
    amount_minor: int
    tiebreak: float


@dataclass
class Episode:
    episode_id: str
    manifest: dict
    policy: dict
    tables: dict  # ledger, invoices, payments, fx -> list[dict]
    truth: dict
    provenance: dict


class GenerationError(RuntimeError):
    pass


def fx_id(source: str, target: str, d: str) -> str:
    return f"fx:{d}:{source}:{target}"


# --------------------------------------------------------------------------- builder

class _Builder:
    def __init__(self, seed: int, config: dict, plan: dict, version: str):
        self.seed = seed
        self.config = config
        self.plan = plan
        self.version = version
        content = cfgmod.canonical_json({"v": version, "seed": seed, "c": cfgmod.content_key(config)})
        self.rng = random.Random("content:" + content)
        self.idr = random.Random("ids:" + content + ":" + plan["transform"]["id_salt"])
        self.used_ids: set = set()
        self.entities: list[Entity] = []
        self.invoices: list[Invoice] = []
        self.payments: list[Payment] = []
        self.expenses: list[Expense] = []
        self.locked: set = set()  # keys reserved by distractors/errors
        self.issues: list[dict] = []
        self.mutations: list[dict] = []
        self._counter = 0

    # ---------------------------------------------------------------- utilities
    def key(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}{self._counter}"

    def new_id(self, namespace: str, local_prefix: str, n: int) -> str:
        while True:
            token = f"{namespace}:{local_prefix}" + "".join(self.idr.choice(ID_ALPHABET) for _ in range(n))
            if token not in self.used_ids:
                self.used_ids.add(token)
                return token

    def local(self, d: date, seconds: float) -> datetime:
        return datetime.combine(d, time(0, 0), tzinfo=self.tz) + timedelta(seconds=int(seconds))

    def local_date(self, dt: datetime) -> date:
        return dt.astimezone(self.tz).date()

    def rate(self, ccy: str, d: date) -> str:
        if ccy == self.base:
            return "1"
        return self.fx[(ccy, d.isoformat())]

    def to_base(self, amount_minor: int, ccy: str, d: date) -> int:
        return convert_minor(amount_minor, ccy, self.base, self.rate(ccy, d))

    def pick_repr(self) -> str:
        r = self.rng.random()
        if r < 0.55:
            return self.offset
        if r < 0.9:
            return "+00:00"
        return self.rng.choice([z[1] for z in ZONES])

    def display_date(self, dt: datetime, off: str) -> date:
        return dt.astimezone(offset_tz(off)).date()

    # ---------------------------------------------------------------- world
    def build_world(self):
        rng = self.rng
        self.year = rng.choice([2025, 2026])
        self.month = rng.randint(1, 12)
        self.zone, self.offset = rng.choice(ZONES)
        self.tz = offset_tz(self.offset)
        self.base = rng.choice(BASE_CHOICES)
        pool = sorted(c for c in EXPONENTS if c != self.base)
        self.foreign = sorted(rng.sample(pool, rng.randint(1, 3)))
        days = calendar.monthrange(self.year, self.month)[1]
        self.first_day = date(self.year, self.month, 1)
        self.last_day = date(self.year, self.month, days)
        self.period_start = self.local(self.first_day, 0)
        self.period_end = self.local(self.last_day, 86399)
        self.build_fx()
        self.build_entities()

    def build_fx(self):
        rng = self.rng
        start = self.first_day - timedelta(days=50)
        end = self.last_day + timedelta(days=10)
        self.fx_dates = []
        d = start
        while d <= end:
            self.fx_dates.append(d)
            d += timedelta(days=1)
        self.fx = {}
        base_value = USD_VALUE[self.base]
        for ccy in self.foreign:
            v = USD_VALUE[ccy] * math.exp(rng.gauss(0, 0.04))
            for d in self.fx_dates:
                # Every daily move is at least 0.2%, so a wrong rate date is always detectable.
                v *= math.exp(rng.uniform(0.002, 0.008) * rng.choice([-1, 1]))
                self.fx[(ccy, d.isoformat())] = format_rate(v / base_value)

    def build_entities(self):
        rng = self.rng
        lo, hi = self.plan["n_entities"]
        n = rng.randint(max(lo, 4), max(hi, 4))
        share = self.plan["foreign_share"]
        for i in range(n):
            if i < 2:
                ccy = self.base
            elif i < 4:
                ccy = rng.choice(self.foreign)
            else:
                ccy = rng.choice(self.foreign) if rng.random() < share else self.base
            self.entities.append(Entity(self.key("e"), ccy))

    def base_entities(self):
        return [e for e in self.entities if e.currency == self.base]

    def foreign_entities(self):
        return [e for e in self.entities if e.currency != self.base]

    # ---------------------------------------------------------------- record factories
    def unit_price(self, ccy: str) -> int:
        usd = math.exp(self.rng.gauss(math.log(140), 0.9))
        return max(1, int(round(usd / USD_VALUE[ccy] * 10 ** EXPONENTS[ccy])))

    def make_lines(self, ccy: str) -> list:
        rng = self.rng
        lines = []
        for desc in rng.sample(CATALOG, rng.randint(1, 4)):
            q = rng.randint(1, 20)
            up = self.unit_price(ccy)
            lines.append({"description": desc, "quantity": q, "unit_price_minor": up, "amount_minor": q * up})
        return lines

    def new_invoice(self, entity: Entity, issued: datetime, lines: list | None = None,
                    repr_offset: str | None = None) -> Invoice:
        lines = lines if lines is not None else self.make_lines(entity.currency)
        inv = Invoice(
            key=self.key("i"), entity=entity, issued=issued, repr_offset=repr_offset or self.pick_repr(),
            due=issued + timedelta(days=self.rng.choice([14, 30, 45])), line_items=lines,
            total_minor=sum(li["amount_minor"] for li in lines), tiebreak=self.rng.random(),
        )
        self.invoices.append(inv)
        return inv

    def new_payment(self, entity: Entity, ts: datetime, amount: int, refs: list, repr_offset: str | None = None,
                    is_refund: bool = False) -> Payment:
        p = Payment(key=self.key("p"), entity=entity, ts=ts, repr_offset=repr_offset or self.pick_repr(),
                    amount_minor=amount, refs=refs, tiebreak=self.rng.random(), is_refund=is_refund)
        self.payments.append(p)
        return p

    def random_period_instant(self, lo_frac: float = 0.0, hi_frac: float = 1.0, business: bool = True) -> datetime:
        rng = self.rng
        days = (self.last_day - self.first_day).days
        d = self.first_day + timedelta(days=int(round(rng.uniform(lo_frac, hi_frac) * days)))
        secs = rng.uniform(8 * 3600, 20 * 3600) if (business and rng.random() < 0.9) else rng.uniform(0, 86399)
        return self.local(d, secs)

    # ---------------------------------------------------------------- base history
    def build_history(self):
        rng = self.rng
        lo, hi = self.plan["n_invoices"]
        for _ in range(rng.randint(lo, hi)):
            ent = rng.choice(self.entities)
            inv = self.new_invoice(ent, self.random_period_instant())
            if rng.random() < self.plan["pay_prob"]:
                ts = inv.issued + timedelta(seconds=rng.uniform(3600, 20 * 86400))
                if ts <= self.period_end - timedelta(minutes=30):
                    self.new_payment(ent, ts, inv.total_minor, [inv])
        lo, hi = self.plan["n_expenses"]
        for _ in range(rng.randint(lo, hi)):
            acct = rng.choice(EXPENSE_ACCOUNTS)
            usd = math.exp(rng.gauss(math.log(300 if acct != "6500" else 8), 0.8))
            amount = max(1, int(round(usd / USD_VALUE[self.base] * 10 ** EXPONENTS[self.base])))
            self.expenses.append(Expense(self.key("x"), self.random_period_instant(business=False), acct, amount,
                                         rng.random()))

    def payments_for(self, inv: Invoice) -> list:
        return [p for p in self.payments if inv in p.refs]

    def unpaid_period_invoices(self, max_frac: float = 1.0, ccy: str | None = None) -> list:
        cutoff = self.period_start + (self.period_end - self.period_start) * max_frac
        out = []
        for inv in self.invoices:
            if inv.key in self.locked or inv.tags or not inv.post_clean or not inv.exists_clean:
                continue
            if inv.issued > cutoff or self.payments_for(inv):
                continue
            if ccy == "base" and inv.currency != self.base:
                continue
            if ccy == "foreign" and inv.currency == self.base:
                continue
            out.append(inv)
        return out

    def ensure_unpaid(self, max_frac: float, ccy: str | None = None) -> Invoice:
        cands = self.unpaid_period_invoices(max_frac, ccy)
        if cands:
            return self.rng.choice(cands)
        ents = {"foreign": self.foreign_entities(), "base": self.base_entities()}.get(ccy, self.entities)
        return self.new_invoice(self.rng.choice(ents), self.random_period_instant(0.0, max_frac))

    def pay_after(self, inv: Invoice, min_s: float, max_ts: datetime) -> datetime:
        lo = inv.issued + timedelta(seconds=min_s)
        hi = max_ts if max_ts > lo else lo + timedelta(minutes=30)
        return lo + (hi - lo) * self.rng.random()

    # ---------------------------------------------------------------- distractors
    def build_distractors(self):
        rng = self.rng
        d = self.plan["distractors"]
        end_margin = self.period_end - timedelta(hours=2)

        for _ in range(d["recurring_invoices"]):
            # Legitimate repeated amounts: weekly invoices, same entity and price, different weeks.
            ent = rng.choice(self.entities)
            up = self.unit_price(ent.currency) * rng.randint(3, 8)
            day0 = self.first_day + timedelta(days=rng.randint(0, 5))
            k = 0
            while day0 + timedelta(days=7 * k) <= self.last_day:
                dd = day0 + timedelta(days=7 * k)
                lines = [{"description": f"Weekly service subscription, week {dd.isocalendar()[1]}", "quantity": 1,
                          "unit_price_minor": up, "amount_minor": up}]
                inv = self.new_invoice(ent, self.local(dd, rng.uniform(9 * 3600, 11 * 3600)), lines)
                inv.tags.add("recurring")
                self.locked.add(inv.key)
                if rng.random() < 0.6:
                    ts = inv.issued + timedelta(days=rng.uniform(0.5, 3))
                    if ts < end_margin:
                        self.locked.add(self.new_payment(ent, ts, inv.total_minor, [inv]).key)
                k += 1

        for _ in range(d["split_payments"]):
            inv = self.ensure_unpaid(0.6)
            a1 = max(1, scale_minor(inv.total_minor, rng.uniform(0.3, 0.7)))
            t1 = self.pay_after(inv, 3600, end_margin - timedelta(days=3))
            t2 = t1 + (end_margin - t1) * rng.uniform(0.2, 0.9)
            p1 = self.new_payment(inv.entity, t1, a1, [inv])
            p2 = self.new_payment(inv.entity, t2, inv.total_minor - a1, [inv])
            inv.tags.add("split")
            self.locked.update({inv.key, p1.key, p2.key})

        for _ in range(d["multi_invoice_payments"]):
            ent = rng.choice(self.entities)
            a = self.new_invoice(ent, self.random_period_instant(0.0, 0.5))
            b = self.new_invoice(ent, self.random_period_instant(0.0, 0.5))
            first, second = sorted([a, b], key=lambda x: (x.issued, x.tiebreak))
            p = self.new_payment(ent, self.pay_after(second, 3600, end_margin), a.total_minor + b.total_minor,
                                 [first, second])
            a.tags.add("multi")
            b.tags.add("multi")
            self.locked.update({a.key, b.key, p.key})

        for _ in range(d["overpayment_refunds"]):
            inv = self.ensure_unpaid(0.5, "base")
            extra = max(1, scale_minor(inv.total_minor, rng.uniform(0.01, 0.1)))
            t1 = self.pay_after(inv, 3600, end_margin - timedelta(days=5))
            t2 = t1 + (end_margin - t1) * rng.uniform(0.2, 0.9)
            p1 = self.new_payment(inv.entity, t1, inv.total_minor + extra, [inv])
            p2 = self.new_payment(inv.entity, t2, -extra, [inv], is_refund=True)
            inv.tags.add("overpaid")
            self.locked.update({inv.key, p1.key, p2.key})

        for _ in range(d["opening_invoices"]):
            ent = rng.choice(self.entities)
            prev_day = self.first_day - timedelta(days=rng.randint(3, 28))
            inv = self.new_invoice(ent, self.local(prev_day, rng.uniform(9 * 3600, 18 * 3600)))
            inv.post_clean = inv.post_dirty = False
            inv.tags.add("opening")
            p = self.new_payment(ent, self.random_period_instant(0.0, 0.8), inv.total_minor, [inv])
            self.locked.update({inv.key, p.key})

        for i in range(d["boundary_documents"]):
            kind = i % 3
            ent = rng.choice(self.entities)
            if kind == 0:
                # Issued shortly after period end, printed in an offset whose date is still in period.
                ts = self.local(self.last_day + timedelta(days=1), rng.uniform(600, 5 * 3600))
                inv = self.new_invoice(ent, ts, repr_offset=self.offset_showing_in_period(ts))
                inv.post_clean = inv.post_dirty = False
                inv.tags.add("after_period")
                self.locked.add(inv.key)
            elif kind == 1:
                # Issued late on the last day, printed in an offset whose date is already next month.
                ts = self.local(self.last_day, rng.uniform(21 * 3600, 23.8 * 3600))
                inv = self.new_invoice(ent, ts, repr_offset=self.offset_showing_after_period(ts))
                inv.tags.add("late_in_period")
                self.locked.add(inv.key)
            else:
                # Payment received just after period end: correctly not posted this period.
                inv = self.ensure_unpaid(0.8)
                ts = self.period_end + timedelta(seconds=rng.uniform(300, 6 * 3600))
                p = self.new_payment(inv.entity, ts, inv.total_minor, [inv],
                                     repr_offset=self.offset_showing_in_period(ts))
                p.post_clean = p.post_dirty = False
                p.tags.add("after_period")
                self.locked.update({inv.key, p.key})

    def offset_showing_in_period(self, ts: datetime) -> str:
        for off in ["+00:00", "-05:00", "-10:00"]:
            if self.display_date(ts, off) <= self.last_day:
                return off
        return self.offset

    def offset_showing_after_period(self, ts: datetime) -> str:
        for off in ["+00:00", "+09:00", "+12:00"]:
            if self.display_date(ts, off) > self.last_day:
                return off
        return self.offset

    # ---------------------------------------------------------------- error helpers
    def base_amount(self, inv: Invoice) -> int:
        return self.to_base(inv.total_minor, inv.currency, self.local_date(inv.issued))

    def pick(self, cands: list, what: str):
        if not cands:
            raise GenerationError(f"no candidate for {what}")
        return self.rng.choice(cands)

    def clean_posted_invoices(self, foreign: bool | None = None) -> list:
        out = []
        for inv in self.invoices:
            if inv.key in self.locked or not (inv.post_clean and inv.exists_clean) or inv.tags:
                continue
            if foreign is True and inv.currency == self.base:
                continue
            out.append(inv)
        return out

    def simple_payments(self, foreign: bool | None = None) -> list:
        """Posted, single-invoice, full payments not used elsewhere."""
        out = []
        for p in self.payments:
            if p.key in self.locked or p.is_refund or not (p.post_clean and p.exists_clean) or len(p.refs) != 1:
                continue
            inv = p.refs[0]
            if inv.key in self.locked or p.amount_minor != inv.total_minor or len(self.payments_for(inv)) != 1:
                continue
            if foreign is True and p.currency == self.base:
                continue
            out.append(p)
        return out

    def min_detectable(self, base_minor: int) -> int:
        return max(50, int(base_minor * 0.0015))

    def wrong_rate_date(self, ccy: str, amount_minor: int, correct: date, extra: list) -> date | None:
        good = convert_minor(amount_minor, ccy, self.base, self.rate(ccy, correct))
        options = [correct + timedelta(days=k) for k in (-1, -2, -3, 1, 2, -7)] + extra
        self.rng.shuffle(options)
        for d in options:
            if d == correct or (ccy, d.isoformat()) not in self.fx:
                continue
            bad = convert_minor(amount_minor, ccy, self.base, self.rate(ccy, d))
            if abs(bad - good) >= self.min_detectable(good):
                return d
        return None

    def add_issue(self, type_: str, scenario: str | None, target: str, **refs):
        self.issues.append({"type": type_, "scenario": scenario, "target": target, **refs})

    # ---------------------------------------------------------------- error injection
    def inject_duplicate(self, scenario=None):
        cands = [inv for inv in self.clean_posted_invoices() if inv.issued.astimezone(self.tz).time() <= time(21, 0)]
        x = self.pick(cands, "duplicate_invoice")
        day_end = self.local(self.local_date(x.issued), 86399)
        room = min((day_end - x.issued).total_seconds() - 60, 6 * 3600)
        issued = x.issued + timedelta(seconds=self.rng.uniform(600, max(601, room)))
        dup = self.new_invoice(x.entity, issued, copy.deepcopy(x.line_items))
        dup.due = issued + (x.due - x.issued)
        dup.exists_clean = False
        dup.post_clean = False
        self.locked.update({x.key, dup.key})
        self.mutations.append({"type": "duplicate_invoice", "change": "duplicate invoice document created and posted",
                               "original": x.key, "duplicate": dup.key})
        self.add_issue("duplicate_invoice", scenario, "invoice", original=x, duplicate=dup)

    def inject_allocation(self, pay: Payment | None = None, mode: str | None = None, scenario=None):
        p = pay or self.pick(self.simple_payments(), "payment_allocation")
        mode = mode or self.rng.choice(["missing", "partial"])
        frac = 0.0 if mode == "missing" else round(self.rng.uniform(0.2, 0.8), 2)
        p.misalloc = (mode, frac)
        self.locked.update({p.key, p.refs[0].key})
        self.mutations.append({"type": "payment_allocation", "change": f"{mode} allocation, applied fraction {frac}",
                               "payment": p.key, "invoice": p.refs[0].key})
        self.add_issue("payment_allocation", scenario, "payment", payment=p, invoice=p.refs[0])

    def inject_fx_invoice(self, inv: Invoice | None = None, scenario=None) -> bool:
        cands = [inv] if inv else self.clean_posted_invoices(foreign=True)
        self.rng.shuffle(cands)
        for c in cands:
            correct = self.local_date(c.issued)
            extra = [self.local_date(c.due), c.issued.astimezone(offset_tz("+00:00")).date()]
            wrong = self.wrong_rate_date(c.currency, c.total_minor, correct, extra)
            if wrong is None:
                continue
            c.rate_date_override = wrong
            self.locked.add(c.key)
            self.mutations.append({"type": "fx_date_or_rate",
                                   "change": f"invoice converted at the rate of {wrong} instead of {correct}",
                                   "invoice": c.key})
            self.add_issue("fx_date_or_rate", scenario, "invoice", invoice=c, correct_date=correct)
            return True
        return False

    def inject_fx_payment(self, pay: Payment | None = None, wrong: date | None = None, scenario=None) -> bool:
        cands = [pay] if pay else self.simple_payments(foreign=True)
        self.rng.shuffle(cands)
        for p in cands:
            correct = self.local_date(p.ts)
            w = wrong if wrong is not None else self.wrong_rate_date(p.currency, p.amount_minor, correct, [])
            if w is None:
                continue
            p.rate_date_override = w
            self.locked.update({p.key, p.refs[0].key})
            self.mutations.append({"type": "fx_date_or_rate",
                                   "change": f"payment converted at the rate of {w} instead of {correct}",
                                   "payment": p.key})
            self.add_issue("fx_date_or_rate", scenario, "payment", payment=p, correct_date=correct)
            return True
        return False

    def inject_period(self, target: str, scenario=None):
        rng = self.rng
        backdate = self.local(self.last_day, rng.uniform(17 * 3600, 23.9 * 3600))
        if target == "invoice":
            ts = self.local(self.last_day + timedelta(days=rng.randint(1, 2)), rng.uniform(300, 9 * 3600))
            inv = self.new_invoice(rng.choice(self.entities), ts, repr_offset=self.offset_showing_in_period(ts))
            inv.post_clean = False
            inv.backdate = backdate
            inv.rate_date_override = self.last_day
            self.locked.add(inv.key)
            self.mutations.append({"type": "reporting_period",
                                   "change": "next-period invoice posted in this period (backdated)", "invoice": inv.key})
            self.add_issue("reporting_period", scenario, "invoice", invoice=inv)
        else:
            inv = self.ensure_unpaid(0.9)
            ts = self.period_end + timedelta(seconds=rng.uniform(300, 2 * 86400))
            p = self.new_payment(inv.entity, ts, inv.total_minor, [inv], repr_offset=self.offset_showing_in_period(ts))
            p.post_clean = False
            p.backdate = backdate
            p.rate_date_override = self.last_day
            self.locked.update({inv.key, p.key})
            self.mutations.append({"type": "reporting_period",
                                   "change": "next-period payment posted in this period (backdated)", "payment": p.key})
            self.add_issue("reporting_period", scenario, "payment", payment=p)

    def inject_fx_invoice_partial(self):
        """Interaction 1: a foreign invoice converted at a wrong-date rate AND its payment partially
        applied. A naive receivable-balance check sees one mixed residual of both errors."""
        for _ in range(30):
            inv = self.ensure_unpaid(0.6, "foreign")
            p = self.new_payment(inv.entity, self.pay_after(inv, 3600, self.period_end - timedelta(hours=3)),
                                 inv.total_minor, [inv])
            if self.inject_fx_invoice(inv, scenario="fx_invoice_partial"):
                self.inject_allocation(p, "partial", scenario="fx_invoice_partial")
                return
            self.payments.remove(p)
            self.locked.add(inv.key)
        raise GenerationError("could not build fx_invoice_partial")

    def inject_boundary_payment(self):
        """Interaction 2: a foreign partial payment at the period boundary, printed in UTC and
        converted at the rate of its UTC calendar date instead of the local date."""
        rng = self.rng
        ent = rng.choice(self.foreign_entities())
        off_minutes = int(self.tz.utcoffset(None).total_seconds() // 60)
        for _ in range(30):
            if off_minutes > 0:
                # First local day, before the local offset has elapsed: the UTC date is the previous month.
                ts = self.local(self.first_day, rng.uniform(300, off_minutes * 60 - 300))
                prev_day = self.first_day - timedelta(days=rng.randint(5, 25))
                inv = self.new_invoice(ent, self.local(prev_day, rng.uniform(9 * 3600, 18 * 3600)))
                inv.post_clean = inv.post_dirty = False
                inv.tags.add("opening")
            else:
                # Last local day, late evening: the UTC date is already the next month.
                ts = self.local(self.last_day, rng.uniform(86400 + off_minutes * 60 + 300, 86400 - 300))
                inv = self.new_invoice(ent, self.random_period_instant(0.0, 0.7))
            utc_date = ts.astimezone(offset_tz("+00:00")).date()
            correct = self.local_date(ts)
            good = self.to_base(inv.total_minor, ent.currency, correct)
            bad = self.to_base(inv.total_minor, ent.currency, utc_date)
            if utc_date != correct and abs(bad - good) >= self.min_detectable(good):
                p = self.new_payment(ent, ts, inv.total_minor, [inv], repr_offset="+00:00")
                self.locked.update({inv.key, p.key})
                self.inject_fx_payment(p, wrong=utc_date, scenario="boundary_payment_fx_partial")
                self.inject_allocation(p, "partial", scenario="boundary_payment_fx_partial")
                return
            self.invoices.remove(inv)
        raise GenerationError("could not build boundary_payment_fx_partial")

    def inject_errors(self):
        plan, rng = self.plan, self.rng
        for _ in range(plan["interactions"]["fx_invoice_partial"]):
            self.inject_fx_invoice_partial()
        for _ in range(plan["interactions"]["boundary_payment_fx_partial"]):
            self.inject_boundary_payment()
        for _ in range(plan["issues"]["duplicate_invoice"]):
            self.inject_duplicate()
        for _ in range(plan["issues"]["payment_allocation"]):
            self.inject_allocation()
        for _ in range(plan["issues"]["fx_date_or_rate"]):
            target = plan["targets"]["fx_date_or_rate"] or rng.choice(["invoice", "payment"])
            ok = self.inject_fx_invoice() if target == "invoice" else self.inject_fx_payment()
            if not ok and plan["targets"]["fx_date_or_rate"] is None:
                ok = self.inject_fx_payment() if target == "invoice" else self.inject_fx_invoice()
            if not ok and target == "invoice":
                inv = self.new_invoice(rng.choice(self.foreign_entities()), self.random_period_instant())
                ok = self.inject_fx_invoice(inv)
            if not ok and target == "payment":
                inv = self.new_invoice(rng.choice(self.foreign_entities()), self.random_period_instant(0, 0.7))
                p = self.new_payment(inv.entity, self.pay_after(inv, 3600, self.period_end - timedelta(hours=3)),
                                     inv.total_minor, [inv])
                ok = self.inject_fx_payment(p)
            if not ok:
                raise GenerationError("could not inject fx_date_or_rate")
        for _ in range(plan["issues"]["reporting_period"]):
            self.inject_period(plan["targets"]["reporting_period"] or rng.choice(["invoice", "payment"]))

    # ---------------------------------------------------------------- posting
    def post(self, dirty: bool) -> list:
        """Post the ledger of the clean or the dirty world. Rows carry internal keys `_k`."""
        rows = []

        def row(k, ts, account, debit, credit, entity, inv, pay, tb, order):
            rows.append({"_k": k, "_ts": ts, "_tb": tb, "_order": order, "account": account,
                         "debit_minor": int(debit), "credit_minor": int(credit),
                         "entity": entity, "inv": inv, "pay": pay})

        def posted(doc) -> bool:
            return doc.post_dirty if dirty else (doc.post_clean and doc.exists_clean)

        for inv in self.invoices:
            if not posted(inv):
                continue
            rd = inv.rate_date_override if (dirty and inv.rate_date_override) else self.local_date(inv.issued)
            ts = inv.backdate if (dirty and inv.backdate) else inv.issued
            b = self.to_base(inv.total_minor, inv.currency, rd)
            row(f"{inv.key}:ar", ts, "1200", b, 0, inv.entity, inv, None, inv.tiebreak, 0)
            row(f"{inv.key}:rev", ts, "4000", 0, b, inv.entity, inv, None, inv.tiebreak, 1)

        outstanding = {inv.key: inv.total_minor for inv in self.invoices}

        def post_ts(p):
            return p.backdate if (dirty and p.backdate) else p.ts

        for p in sorted(self.payments, key=lambda q: (post_ts(q), q.tiebreak)):
            if not posted(p):
                continue
            ts = post_ts(p)
            if p.is_refund:
                x = -p.amount_minor
                row(f"{p.key}:unapplied", ts, "2400", x, 0, p.entity, p.refs[0], p, p.tiebreak, 0)
                row(f"{p.key}:cash", ts, "1000", 0, x, p.entity, None, p, p.tiebreak, 1)
                continue
            rd = p.rate_date_override if (dirty and p.rate_date_override) else self.local_date(p.ts)
            cash = self.to_base(p.amount_minor, p.currency, rd)
            remaining = p.amount_minor
            allocs = []
            for inv in p.refs:
                a = min(remaining, outstanding[inv.key])
                if a <= 0:
                    continue
                outstanding[inv.key] -= a
                remaining -= a
                allocs.append((inv, self.to_base(a, inv.currency, self.local_date(inv.issued))))
            excess = self.to_base(remaining, p.currency, rd) if remaining > 0 else 0
            unapplied = excess
            row(f"{p.key}:cash", ts, "1000", cash, 0, p.entity, None, p, p.tiebreak, 0)
            order = 1
            for inv, r in allocs:
                applied = r
                if dirty and p.misalloc:
                    applied = 0 if p.misalloc[0] == "missing" else scale_minor(r, p.misalloc[1])
                    unapplied += r - applied
                if applied:
                    row(f"{p.key}:ar:{inv.key}", ts, "1200", 0, applied, p.entity, inv, p, p.tiebreak, order)
                order += 1
            if unapplied:
                row(f"{p.key}:unapplied", ts, "2400", 0, unapplied, p.entity, None, p, p.tiebreak, order)
            fxd = cash - sum(r for _, r in allocs) - excess
            if fxd:
                row(f"{p.key}:fx", ts, "7900", max(0, -fxd), max(0, fxd), p.entity, None, p, p.tiebreak, order + 1)

        for x in self.expenses:
            row(f"{x.key}:exp", x.ts, x.account, x.amount_minor, 0, None, None, None, x.tiebreak, 0)
            row(f"{x.key}:cash", x.ts, "1000", 0, x.amount_minor, None, None, None, x.tiebreak, 1)

        rows.sort(key=lambda r: (r["_ts"], r["_tb"], r["_order"]))
        return rows

    # ---------------------------------------------------------------- render
    def assign_ids(self):
        for e in self.entities:
            e.entity_id = self.new_id("entity", "E-", 5)
        for inv in self.invoices:
            inv.invoice_id = self.new_id("invoice", "INV-", 6)
            inv.doc_id = self.new_id("doc", "DOC-", 8)
        for p in self.payments:
            p.payment_id = self.new_id("payment", "PAY-", 6)
            p.doc_id = self.new_id("doc", "DOC-", 8)

    def render_rows(self, rows: list, row_ids: dict) -> list:
        return [{
            "row_id": row_ids[r["_k"]],
            "timestamp": iso(r["_ts"].astimezone(self.tz)),
            "account": r["account"],
            "debit_minor": r["debit_minor"],
            "credit_minor": r["credit_minor"],
            "currency": self.base,
            "entity_id": r["entity"].entity_id if r["entity"] else None,
            "invoice_id": r["inv"].invoice_id if r["inv"] else None,
            "payment_id": r["pay"].payment_id if r["pay"] else None,
        } for r in rows]

    def invoice_doc(self, inv: Invoice) -> dict:
        off = offset_tz(inv.repr_offset)
        return {
            "doc_id": inv.doc_id,
            "invoice_id": inv.invoice_id,
            "entity_id": inv.entity.entity_id,
            "issued_at": iso(inv.issued.astimezone(off)),
            "due_at": iso(inv.due.astimezone(off)),
            "currency": inv.currency,
            "total_minor": inv.total_minor,
            "line_items": copy.deepcopy(inv.line_items),
        }

    def payment_doc(self, p: Payment) -> dict:
        return {
            "doc_id": p.doc_id,
            "payment_id": p.payment_id,
            "timestamp": iso(p.ts.astimezone(offset_tz(p.repr_offset))),
            "currency": p.currency,
            "amount_minor": p.amount_minor,
            "invoice_refs": [inv.invoice_id for inv in p.refs] if p.refs else None,
        }

    def build(self) -> Episode:
        self.build_world()
        self.build_history()
        self.build_distractors()
        self.inject_errors()
        self.assign_ids()

        dirty_rows = self.post(dirty=True)
        clean_rows = self.post(dirty=False)
        row_ids = {r["_k"]: self.new_id("ledger", "L-", 7) for r in dirty_rows}
        dirty_by_k = {r["_k"]: r for r in dirty_rows}
        clean_by_k = {r["_k"]: r for r in clean_rows}

        ledger = self.render_rows(dirty_rows, row_ids)
        inv_docs = [self.invoice_doc(i) for i in sorted(self.invoices, key=lambda i: (i.issued, i.tiebreak))]
        pay_docs = [self.payment_doc(p) for p in sorted(self.payments, key=lambda p: (p.ts, p.tiebreak))]
        fx_rows = [{"date": d.isoformat(), "source_currency": ccy, "target_currency": self.base,
                    "rate": self.fx[(ccy, d.isoformat())]} for d in self.fx_dates for ccy in self.foreign]

        truth = self.resolve_truth(dirty_by_k, clean_by_k, row_ids, dirty_rows)

        order_seed = self.plan["transform"]["order_seed"]
        if order_seed is not None:
            orng = random.Random(f"order:{order_seed}")
            for t in (ledger, inv_docs, pay_docs, fx_rows):
                orng.shuffle(t)

        for t in (ledger, inv_docs, pay_docs):
            for r in t:
                for k in ("debit_minor", "credit_minor", "total_minor", "amount_minor"):
                    if k in r and abs(r[k]) > JS_SAFE_INT:
                        raise GenerationError("amount outside the JavaScript safe integer range")

        eid = cfgmod.episode_id(self.seed, self.config, self.plan, self.version)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "episode_id": eid,
            "base_currency": self.base,
            "currency_decimals": {c: EXPONENTS[c] for c in sorted([self.base] + self.foreign)},
            "period_start": iso(self.period_start),
            "period_end": iso(self.period_end),
            "timezone": self.zone,
            "record_counts": {"ledger": len(ledger), "invoices": len(inv_docs), "payments": len(pay_docs),
                              "fx": len(fx_rows)},
            "document_count": len(inv_docs) + len(pay_docs),
            "rounding_rule": ROUNDING_RULE,
        }
        provenance = {
            "episode_id": eid,
            "generator": {"environment_version": self.version, "seed": self.seed, "config": self.config,
                          "plan": self.plan},
            "company": {"timezone": self.zone, "utc_offset": self.offset, "base_currency": self.base,
                        "foreign_currencies": self.foreign},
            "history": {
                "entities": [{"entity_id": e.entity_id, "currency": e.currency} for e in self.entities],
                "invoice_tags": {i.invoice_id: sorted(i.tags) for i in self.invoices if i.tags},
                "payment_tags": {p.payment_id: sorted(p.tags) for p in self.payments if p.tags},
                "documents_not_in_clean_world": [i.invoice_id for i in self.invoices if not i.exists_clean],
                "payments_not_posted_in_clean_world": [p.payment_id for p in self.payments
                                                       if not (p.post_clean and p.exists_clean)],
            },
            "mutations": [self.render_mutation(m) for m in self.mutations],
            "clean_ledger": self.render_rows(
                clean_rows, {r["_k"]: row_ids.get(r["_k"], f"clean-only:{r['_k']}") for r in clean_rows}),
        }
        return Episode(eid, manifest, make_policy(manifest),
                       {"ledger": ledger, "invoices": inv_docs, "payments": pay_docs, "fx": fx_rows},
                       {"episode_id": eid, "issues": truth}, provenance)

    def render_mutation(self, m: dict) -> dict:
        lookup = {i.key: i.invoice_id for i in self.invoices}
        lookup.update({p.key: p.payment_id for p in self.payments})
        return {k: (lookup.get(v, v) if k not in ("type", "change") else v) for k, v in m.items()}

    def resolve_truth(self, dirty, clean, row_ids, dirty_rows) -> list:
        """Canonical affected sets and evidence sets (docs/GENERATOR.md, 'Эталон')."""
        issues = []

        def rid(k):
            return row_ids[k]

        def group(prefix):
            return sorted(row_ids[r["_k"]] for r in dirty_rows if r["_k"].startswith(prefix + ":"))

        for spec in self.issues:
            t = spec["type"]
            if t == "duplicate_invoice":
                x, dup = spec["original"], spec["duplicate"]
                amount = dirty[f"{dup.key}:ar"]["debit_minor"]
                affected = [x.invoice_id, dup.invoice_id]
                ev = [[x.doc_id, dup.doc_id, rid(f"{dup.key}:ar")], [x.doc_id, dup.doc_id, rid(f"{dup.key}:rev")]]
            elif t == "payment_allocation":
                p, inv = spec["payment"], spec["invoice"]
                amount = dirty[f"{p.key}:unapplied"]["credit_minor"]
                affected = [p.payment_id, inv.invoice_id]
                ev = [[p.doc_id, inv.doc_id, rid(f"{p.key}:unapplied")]]
            elif t == "fx_date_or_rate" and spec["target"] == "invoice":
                inv = spec["invoice"]
                k = f"{inv.key}:ar"
                amount = abs(dirty[k]["debit_minor"] - clean[k]["debit_minor"])
                affected = [rid(k), rid(f"{inv.key}:rev")]
                f = fx_id(inv.currency, self.base, spec["correct_date"].isoformat())
                ev = [[inv.doc_id, rid(k), f], [inv.doc_id, rid(f"{inv.key}:rev"), f]]
            elif t == "fx_date_or_rate":
                p = spec["payment"]
                k = f"{p.key}:cash"
                amount = abs(dirty[k]["debit_minor"] - clean[k]["debit_minor"])
                affected = [rid(k)]
                ev = [[p.doc_id, rid(k), fx_id(p.currency, self.base, spec["correct_date"].isoformat())]]
            elif t == "reporting_period" and spec["target"] == "invoice":
                inv = spec["invoice"]
                amount = dirty[f"{inv.key}:ar"]["debit_minor"]
                affected = group(inv.key)
                ev = [[inv.doc_id, rid(f"{inv.key}:ar")], [inv.doc_id, rid(f"{inv.key}:rev")]]
            else:
                p = spec["payment"]
                amount = dirty[f"{p.key}:cash"]["debit_minor"]
                affected = group(p.key)
                ev = [[p.doc_id, rid(f"{p.key}:cash")]]
            if amount <= 0:
                raise GenerationError(f"non-positive misstatement for {t}")
            issues.append({"type": t, "target": spec["target"], "scenario": spec["scenario"],
                           "affected_ids": sorted(set(affected)), "expected_amount_minor": int(amount),
                           "currency": self.base, "evidence_sets": [sorted(set(e)) for e in ev]})
        issues.sort(key=lambda i: (i["type"], i["affected_ids"]))
        return [{"issue_id": f"issue_{n:02d}", **i} for n, i in enumerate(issues, 1)]


def make_policy(manifest: dict) -> dict:
    """Public accounting policy (policy.json == observation.policy_summary, contract 1.1.0)."""
    return {
        "fx_rate_date": "document_timestamp_local_date",
        "period_boundary": "inclusive_start_and_end",
        "rounding": ROUNDING_RULE,
        "fx_rate_direction": "target_per_source",
        "ledger_currency": "base_currency",
        "accounts": dict(ACCOUNTS),
        "invoice_posting": "debit_1200_credit_4000_at_issued_at_local_date_rate",
        "payment_posting": "debit_1000_at_payment_rate_credit_1200_per_allocation_at_invoice_rate_"
                           "excess_to_2400_difference_to_7900",
        "allocation_order": "invoice_refs_in_order_up_to_outstanding",
        "refunds": "negative_amount_debit_2400_credit_1000",
    }


def generate(seed: int, config: dict, environment_version: str = ENVIRONMENT_VERSION, *,
             plan: dict | None = None) -> Episode:
    """Generate one episode. `plan` is a trusted-code override used by tests and fixtures."""
    seed = cfgmod.validate_seed(seed)
    config = cfgmod.validate_config(config)
    environment_version = cfgmod.validate_version(environment_version)
    plan = copy.deepcopy(plan) if plan is not None else cfgmod.derive_plan(seed, config, environment_version)
    cfgmod.validate_plan(plan, config)
    return _Builder(seed, config, plan, environment_version).build()
