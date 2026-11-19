"""Offline public baseline policies and their reset/step runner."""
from __future__ import annotations

import copy
import random
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .books import Books, audit

TABLES = ("ledger", "invoices", "payments", "fx")


@dataclass(frozen=True)
class EvaluationMetrics:
    precision: float
    recall: float
    f1: float


@dataclass(frozen=True)
class PolicyRun:
    policy: str
    episode_id: str
    return_: float | int
    inspection_steps: int
    elapsed_seconds: float
    terminated: bool
    truncated: bool
    metrics: EvaluationMetrics | None
    trajectory: tuple[Mapping[str, Any], ...]


class MetricsProvider(Protocol):
    def metrics_for_completed_episode(self, environment: Any) -> EvaluationMetrics: ...


def run_policy(environment: Any, policy: Any, *, metrics_provider: MetricsProvider | None = None) -> PolicyRun:
    """Run a policy via public reset/step; optional metrics stay in trusted runner."""
    started, reset = time.perf_counter(), environment.reset()
    observation = reset["observation"]
    while True:
        final = environment.step(policy.next_action(copy.deepcopy(observation)))
        observation = final["observation"]
        if final["terminated"] or final["truncated"]:
            break
    metrics = metrics_provider.metrics_for_completed_episode(environment) if metrics_provider else None
    return PolicyRun(policy.name, reset["info"]["episode_id"], final["reward"],
                     max(0, final["info"]["step"] - 1 if final["terminated"] else final["info"]["step"]),
                     time.perf_counter() - started, final["terminated"], final["truncated"], metrics,
                     tuple(environment.trajectory()))


class RandomExplorer:
    name = "random_explorer"
    def __init__(self, seed: int, exploration_steps: int = 8):
        self.rng, self.limit, self.steps, self.episode_id = random.Random(seed), exploration_steps, 0, ""
        self.records = {table: [] for table in TABLES}
    def next_action(self, observation: Mapping[str, Any]) -> Mapping[str, Any]:
        if observation["kind"] == "episode_start": self.episode_id = observation["manifest"]["episode_id"]
        if observation["kind"] == "records":
            self.records[observation["request"]["table"]].extend(r["id"] for r in observation["records"] if "id" in r)
        if self.steps >= self.limit:
            return {"kind": "submit", "findings": {"episode_id": self.episode_id, "findings": []}}
        self.steps += 1
        found = [(table, item) for table, ids in self.records.items() for item in ids]
        if found and self.rng.random() < .45:
            table, item = self.rng.choice(found)
            return {"kind": "get_record", "table": table, "id": item}
        return {"kind": "list_records", "table": self.rng.choice(TABLES), "cursor": None, "limit": self.rng.randint(1, 50)}


class _Collector:
    name = "collector"
    def __init__(self):
        self.index, self.episode_id, self.manifest = 0, None, None
        self.tables = {table: [] for table in TABLES}
    def next_action(self, observation: Mapping[str, Any]) -> Mapping[str, Any]:
        if observation["kind"] == "episode_start":
            self.episode_id, self.manifest = observation["manifest"]["episode_id"], observation["manifest"]
            return self._list(None)
        if observation["kind"] == "records":
            self.tables[observation["request"]["table"]].extend(copy.deepcopy(observation["records"]))
            if observation["next_cursor"] is not None: return self._list(observation["next_cursor"])
            self.index += 1
            if self.index < len(TABLES): return self._list(None)
        return {"kind": "submit", "findings": {"episode_id": self.episode_id, "findings": self.findings()}}
    def _list(self, cursor): return {"kind": "list_records", "table": TABLES[self.index], "cursor": cursor, "limit": 50}
    def findings(self): raise NotImplementedError


class SimpleReconciliationPolicy(_Collector):
    name = "simple_reconciliation"
    def findings(self): return audit(Books(self.tables, dict(self.manifest)))


class MarkEverythingPolicy(_Collector):
    name = "mark_everything"
    def findings(self):
        c, rows = self.manifest["base_currency"], self.tables
        ledger, invoices, payments = [r["id"] for r in rows["ledger"]], [r["id"] for r in rows["invoices"]], [r["id"] for r in rows["payments"]]
        docs = [r.get("doc_id") for r in rows["invoices"] + rows["payments"]]
        def finding(kind, affected):
            return {"type": kind, "affected_ids": sorted(affected), "claimed_amount_minor": 1, "currency": c,
                    "evidence_ids": sorted(set([x for x in [*docs[:2], *ledger[:3]] if x]))[:10], "explanation": "Public baseline accusation."}
        out = []
        if len(invoices) >= 2: out.append(finding("duplicate_invoice", invoices[:2]))
        if payments and invoices: out.append(finding("payment_allocation", [payments[0], invoices[0]]))
        if ledger: out.extend([finding("fx_date_or_rate", [ledger[0]]), finding("reporting_period", [ledger[-1]])])
        return out
