"""Local WSGI transport for the isolated Hide the error from AI jury demo."""
from __future__ import annotations

import json
import random
import secrets
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from auditgym.hide_error_demo import build_case, organizer_evaluate, public_case_view, run_agent

from .hide_error_ui import render_demo_page

_MAX_BYTES = 16 * 1024


@dataclass
class _Run:
    case: Any
    agent_run: dict[str, Any]


@dataclass
class HideErrorDemoApp:
    """Offline-only demo server. Trusted state never enters agent observations."""

    seed_factory: Callable[[], int] = lambda: secrets.randbelow(9_007_199_254_740_991)
    _runs: dict[str, _Run] = field(default_factory=dict, init=False)
    _batch_number: int = field(default=0, init=False)

    def __call__(self, environ: Mapping[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        try:
            status, headers, body = self._dispatch(environ)
        except Exception:
            status, headers, body = self._json("500 Internal Server Error", {"error": {"message": "Локальная демонстрация не запустилась."}})
        start_response(status, headers)
        return [body]

    def _dispatch(self, environ: Mapping[str, Any]) -> tuple[str, list[tuple[str, str]], bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", ""))
        if method == "GET" and path in {"/", "/hide-error-demo"}:
            return "200 OK", [("Content-Type", "text/html; charset=utf-8")], render_demo_page(self._case_view()).encode("utf-8")
        if method != "POST" or not path.startswith("/api/v1/hide-error-demo/"):
            return self._json("404 Not Found", {"error": {"message": "Маршрут не найден."}})
        payload, error = self._body(environ)
        if error:
            return error
        suffix = path.rsplit("/", 1)[-1]
        if suffix == "start":
            return self._start(payload)
        if suffix == "run":
            return self._run(payload)
        if suffix == "batch":
            return self._batch(payload)
        return self._json("404 Not Found", {"error": {"message": "Маршрут не найден."}})

    def _case_view(self) -> dict[str, Any]:
        # This visible preview does not select a hidden variant.
        preview = build_case(20261101, False)
        public = public_case_view(preview)
        return self._browser_case_view(public)

    @staticmethod
    def _browser_case_view(public: Mapping[str, Any]) -> dict[str, Any]:
        months = (("2026-10", "Октябрь 2026"), ("2026-11", "Ноябрь 2026"))
        return {
            "case_id": "jury-demo",
            "company_name": "Genesis Demo LLP",
            "currency": "KZT",
            "operations": [
                {"id": operation["ledger_id"], "document_number": operation["document_number"],
                 "amount_minor": operation["amount_minor"], "ledger_date": operation["ledger_date"]}
                for operation in public["operations"]
            ],
            "monthly_totals": [
                {"month": label, "amount_minor": public["monthly_totals"][key]} for key, label in months
            ],
        }

    def _start(self, payload: Any) -> tuple[str, list[tuple[str, str]], bytes]:
        if not isinstance(payload, dict) or payload.get("case_id") != "jury-demo" or payload.get("choice") not in {"swap_months", "keep_as_is"}:
            return self._json("400 Bad Request", {"error": {"message": "Неверный выбор сценария."}})
        # The choice and seed stay server-side; only the resulting public transcript is sent later.
        case = build_case(self.seed_factory(), payload["choice"] == "swap_months")
        run_id = secrets.token_urlsafe(18)
        self._runs[run_id] = _Run(case, run_agent(case))
        return self._json("200 OK", {"run_id": run_id})

    def _run(self, payload: Any) -> tuple[str, list[tuple[str, str]], bytes]:
        if not isinstance(payload, dict) or not isinstance(payload.get("run_id"), str) or payload["run_id"] not in self._runs:
            return self._json("404 Not Found", {"error": {"message": "Запуск расследования не найден."}})
        run = self._runs[payload["run_id"]]
        if payload.get("action") is None:
            labels = {document["id"]: document["document_number"] for document in run.case.documents}
            return self._json("200 OK", {"opened_documents": [
                {"id": doc_id, "label": labels[doc_id]} for doc_id in run.agent_run["opened_document_ids"]
            ]})
        if payload.get("action") != "submit":
            return self._json("400 Bad Request", {"error": {"message": "Неизвестное действие."}})
        score = organizer_evaluate(run.case, run.agent_run)
        finding_ledgers = {finding["ledger_id"] for finding in score["findings"]}
        comparisons = []
        for ledger, document in zip(run.case.ledger, run.case.documents):
            found = ledger["id"] in finding_ledgers
            discrepant = ledger["accounting_month"] != document["document_date"][:7]
            outcome = "proved_error" if discrepant and found else "missed_error" if discrepant else "false_alarm" if found else "clear"
            comparisons.append({
                "ledger_date": ledger["accounting_month"], "source_date": document["document_date"],
                "agent_finding": "Несоответствие периода доказано" if found else "Не заявлено",
                "outcome": outcome,
            })
        return self._json("200 OK", {"result": {
            "agent_conclusion": f"Агент доказал ошибок: {score['errors_proven']}; ложных тревог: {int(score['false_alarm'])}.",
            "comparisons": comparisons,
        }})

    def _batch(self, payload: Any) -> tuple[str, list[tuple[str, str]], bytes]:
        if not isinstance(payload, dict) or payload.get("case_id") != "jury-demo":
            return self._json("400 Bad Request", {"error": {"message": "Неверный запрос серии."}})
        self._batch_number += 1
        rng = random.Random(730_000 + self._batch_number)
        variants = [False] * 5 + [True] * 5
        rng.shuffle(variants)
        proved = false_alarms = inserted = 0
        for index, swapped in enumerate(variants):
            # Each pair keeps equal amounts, while every company differs in both
            # amount and generated document numbers.
            amount = (180_000 + rng.randrange(20_000, 720_000)) * 100
            case = build_case(1_000_000 * self._batch_number + index, swapped, amount_minor=amount)
            score = organizer_evaluate(case, run_agent(case))
            proved += score["errors_proven"]
            false_alarms += int(score["false_alarm"])
            inserted += 2 if swapped else 0
        return self._json("200 OK", {"summary": {
            "total_cases": 10, "inserted_errors": inserted, "proved_errors": proved,
            "clean_cases": 5, "false_alarms": false_alarms,
        }})

    def _body(self, environ: Mapping[str, Any]) -> tuple[Any, tuple[str, list[tuple[str, str]], bytes] | None]:
        if not str(environ.get("CONTENT_TYPE", "")).lower().startswith("application/json"):
            return None, self._json("415 Unsupported Media Type", {"error": {"message": "Нужен JSON."}})
        try:
            size = int(environ.get("CONTENT_LENGTH", "0"))
            if size < 0 or size > _MAX_BYTES:
                raise ValueError
            return json.loads(environ["wsgi.input"].read(size).decode("utf-8")), None
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None, self._json("400 Bad Request", {"error": {"message": "Некорректный JSON."}})

    @staticmethod
    def _json(status: str, value: Mapping[str, Any]) -> tuple[str, list[tuple[str, str]], bytes]:
        return status, [("Content-Type", "application/json; charset=utf-8")], json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
