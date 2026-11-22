"""Offline jury scenario for the Genesis demo, "Hide an error from AI".

This deliberately small case is separate from the normal synthetic generator.  It is
intended for a short, explainable offline demo: two equal KZT operations straddle a
month boundary.  A jury choice can swap *only* their accounting months, while the
source documents retain their factual dates.

``public_case_view`` is the only input used by :func:`run_agent`; it excludes the
trusted seed, the jury choice, and the evaluator truth.  The latter stay on the
organiser side of :func:`organizer_evaluate`.
"""

from __future__ import annotations

import copy
import random
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping

from .evaluator import Evaluator
from .ids import canonical_array
from space_team_genesis.environment import AuditEnvironment, EpisodeData, EvaluationResult


KZT = "KZT"
# 400,000 KZT expressed in two-decimal minor units.
DEFAULT_AMOUNT_MINOR = 40_000_000


@dataclass(frozen=True)
class DemoCase:
    """Trusted complete case.  Do not pass an instance to a browser or an agent."""

    episode_id: str
    manifest: dict[str, Any]
    ledger: tuple[dict[str, Any], ...]
    documents: tuple[dict[str, Any], ...]
    truth: dict[str, Any]
    # Kept only so an organiser can audit reproducibility; never expose publicly.
    seed: int
    swapped: bool

    def to_dict(self, *, include_private: bool = False) -> dict[str, Any]:
        """Return JSON-compatible data; private fields require explicit opt-in."""
        if not include_private:
            return public_case_view(self)
        return asdict(self)


@dataclass(frozen=True)
class AgentRun:
    """JSON-compatible transcript of the public-document-only demo agent."""

    submission: dict[str, Any]
    opened_document_ids: tuple[str, ...]
    observations: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission": copy.deepcopy(self.submission),
            "opened_document_ids": list(self.opened_document_ids),
            "observations": copy.deepcopy(list(self.observations)),
        }


def _safe_seed(seed: int) -> int:
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 9_007_199_254_740_991:
        raise ValueError("seed must be a non-negative JavaScript-safe integer")
    return seed


def _month_start(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _next_month(d: date) -> date:
    return date(d.year + (d.month == 12), 1 if d.month == 12 else d.month + 1, 1)


def build_case(seed: int, swapped: bool, *, amount_minor: int = DEFAULT_AMOUNT_MINOR) -> DemoCase:
    """Build a reproducible two-operation case.

    The default is the promised 400,000 KZT pair.  ``amount_minor`` is an organiser
    option for the ten-company batch and must remain a positive safe integer.  Dates
    always remain an October 31 / November 1 boundary, with deterministic year and
    timestamps; document numbers are also deterministic per seed.
    """
    seed = _safe_seed(seed)
    if not isinstance(swapped, bool):
        raise ValueError("swapped must be a boolean")
    if not isinstance(amount_minor, int) or isinstance(amount_minor, bool) or not 0 < amount_minor <= 9_007_199_254_740_991:
        raise ValueError("amount_minor must be a positive JavaScript-safe integer")

    rng = random.Random(f"hide-error-demo:{seed}")
    year = 2026
    oct_31 = date(year, 10, 31)
    nov_1 = date(year, 11, 1)
    # Distinct times make the documents look like ordinary separate operations.
    times = [time(rng.randint(8, 11), rng.choice((0, 15, 30, 45))), time(rng.randint(13, 17), rng.choice((0, 15, 30, 45)))]
    suffixes = rng.sample(range(10_000, 99_999), 2)
    case_token = f"DEMO-{year}-{rng.randrange(10**8):08d}"

    true_dates = (oct_31, nov_1)
    document_ids = (f"doc:GEN-{suffixes[0]}", f"doc:GEN-{suffixes[1]}")
    ledger_ids = (f"ledger:GEN-{suffixes[0]}", f"ledger:GEN-{suffixes[1]}")
    accounting_months = (_month_start(nov_1), _month_start(oct_31)) if swapped else (_month_start(oct_31), _month_start(nov_1))

    documents: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    for index, (true_date, doc_id, ledger_id, accounting_month) in enumerate(
        zip(true_dates, document_ids, ledger_ids, accounting_months), start=1
    ):
        occurred = datetime.combine(true_date, times[index - 1], tzinfo=timezone(timedelta(hours=5))).isoformat()
        documents.append({
            "id": doc_id,
            "document_number": doc_id.split(":", 1)[1],
            "operation": f"Операция {index}",
            "occurred_at": occurred,
            "document_date": true_date.isoformat(),
            "amount_minor": amount_minor,
            "currency": KZT,
        })
        ledger.append({
            "id": ledger_id,
            "document_id": doc_id,
            "operation": f"Операция {index}",
            "accounting_month": accounting_month,
            "amount_minor": amount_minor,
            "currency": KZT,
        })

    issues: list[dict[str, Any]] = []
    if swapped:
        for index, (ledger_row, doc) in enumerate(zip(ledger, documents), start=1):
            issues.append({
                "issue_id": f"issue_{index:02d}",
                "type": "reporting_period",
                "target": "ledger_month",
                "scenario": "jury_month_swap",
                "affected_ids": [ledger_row["id"]],
                "expected_amount_minor": amount_minor,
                "currency": KZT,
                "evidence_sets": [[doc["id"], ledger_row["id"]]],
            })

    manifest = {
        "schema_version": "1.0.0",
        "episode_id": f"ep_hide_{case_token}",
        "base_currency": KZT,
        "currency_decimals": {KZT: 2},
        "document_count": 2,
        "record_counts": {"ledger": 2, "invoices": 0, "payments": 0, "fx": 0},
    }
    return DemoCase(manifest["episode_id"], manifest, tuple(ledger), tuple(documents),
                    {"episode_id": manifest["episode_id"], "issues": issues}, seed, swapped)


def public_case_view(case: DemoCase) -> dict[str, Any]:
    """Return the browser/agent-safe projection without seed, choice or truth."""
    operations = []
    for ledger, document in zip(case.ledger, case.documents):
        operations.append({
            "id": ledger["id"], "ledger_id": ledger["id"], "document_id": document["id"],
            "document_number": document["document_number"], "ledger_date": ledger["accounting_month"],
            "document_date": document["document_date"], "ledger_month": ledger["accounting_month"],
            "document_month": document["document_date"][:7], "amount_minor": ledger["amount_minor"],
            "currency": ledger["currency"],
        })
    totals = {"2026-10": 0, "2026-11": 0}
    for operation in operations:
        totals[operation["document_month"]] += operation["amount_minor"]
    return {
        "episode_id": case.episode_id,
        "manifest": copy.deepcopy(case.manifest),
        "ledger": [copy.deepcopy(row) for row in case.ledger],
        "operations": operations,
        "monthly_totals": totals,
        # Documents are listed by ID only until the agent opens each one.
        "documents": [{"id": row["id"], "document_number": row["document_number"]} for row in case.documents],
    }


def _open_document(public: dict[str, Any], doc_id: str, all_documents: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    """A tiny read-only document gateway used by the demo agent."""
    allowed = {item["id"] for item in public["documents"]}
    if doc_id not in allowed:
        raise KeyError("unknown public document")
    return copy.deepcopy(next(doc for doc in all_documents if doc["id"] == doc_id))


class _PublicOnlyEvaluator:
    """Terminal evaluator visible to the agent runner; it deliberately reveals no score."""

    def evaluate(self, findings: Mapping[str, Any], inspection_steps: int) -> EvaluationResult:
        return EvaluationResult(reward=0.0)


def _public_environment(case: DemoCase) -> AuditEnvironment:
    """Create the regular reset/step environment from public demo records only."""
    documents = {row["id"]: copy.deepcopy(row) for row in case.documents}
    data = EpisodeData(
        episode_id=case.episode_id, manifest=copy.deepcopy(case.manifest), policy_summary={},
        records={"ledger": [copy.deepcopy(row) for row in case.ledger], "invoices": [], "payments": [], "fx": []},
        documents=documents,
    )
    return AuditEnvironment(data=data, evaluator=_PublicOnlyEvaluator(), evaluation_mode="closed",
                            max_steps=100, time_limit_seconds=60)


def run_agent(case: DemoCase) -> dict[str, Any]:
    """Run the deterministic agent through the standard public reset/step interface.

    The agent receives only observations from AuditEnvironment: it lists ledger
    rows, opens referenced public documents, then submits supported period
    findings.  The standard matcher is invoked later by organizer_evaluate.
    """
    environment = _public_environment(case)
    reset = environment.reset()
    observations: list[dict[str, Any]] = [reset["observation"]]
    listed = environment.step({"kind": "list_records", "table": "ledger", "cursor": None, "limit": 50})
    observations.append(listed["observation"])
    opened: list[str] = []
    findings: list[dict[str, Any]] = []
    for row in listed["observation"]["records"]:
        opened_result = environment.step({"kind": "get_document", "doc_id": row["document_id"]})
        document = opened_result["observation"]["document"]
        observations.append(opened_result["observation"])
        opened.append(document["id"])
        if row["accounting_month"] != document["document_date"][:7]:
            findings.append({
                "type": "reporting_period", "affected_ids": [row["id"]],
                "claimed_amount_minor": row["amount_minor"], "currency": row["currency"],
                "evidence_ids": canonical_array([row["id"], document["id"]]),
                "explanation": "Месяц в учёте не совпадает с месяцем даты исходного документа.",
            })
    submission = {"episode_id": case.episode_id, "findings": findings}
    submitted = environment.step({"kind": "submit", "findings": submission})
    observations.append(submitted["observation"])
    return AgentRun(submission, tuple(opened), tuple(observations)).to_dict()


def organizer_evaluate(case: DemoCase, agent_run: AgentRun | Mapping[str, Any]) -> dict[str, Any]:
    """Score the submission with the repository's standard evaluator/matcher."""
    run = agent_run.to_dict() if isinstance(agent_run, AgentRun) else dict(agent_run)
    evaluator = Evaluator(case.episode_id, case.truth, case.manifest["currency_decimals"], mode="closed")
    result = evaluator.score_submission(run["submission"], inspection_steps=len(run["opened_document_ids"]) + 1)
    document_ids = {row["id"] for row in case.documents}
    findings = [
        {"document_id": next((item for item in finding["evidence_ids"] if item in document_ids), None),
         "ledger_id": finding["affected_ids"][0]}
        for finding in run["submission"]["findings"]
    ]
    return {"errors_proven": result.tp, "false_alarm": result.fp > 0, "findings": findings,
            "metrics": {"tp": result.tp, "fp": result.fp, "fn": result.fn, "f1": result.f1}}
