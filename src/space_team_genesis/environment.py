"""Contract-first, in-memory implementation of the public reset/step interface.

The generator and matcher are injected deliberately. Integrated offline wiring lives
in space_team_genesis.integration; this module keeps public transition semantics.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext
from typing import Any, Callable, Mapping, Protocol, Sequence


TABLES = ("ledger", "invoices", "payments", "fx")
ISSUE_TYPES = (
    "duplicate_invoice",
    "payment_allocation",
    "fx_date_or_rate",
    "reporting_period",
)
SAFE_INTEGER = 9_007_199_254_740_991
CALCULATOR_RE = re.compile(r"^[0-9+*/(). \t\-]+$")


class EpisodeFinalizedError(RuntimeError):
    """A caller attempted a step after terminated or truncated."""


@dataclass(frozen=True)
class EvaluationResult:
    """The evaluator's non-public terminal result.

    Diagnostics stay at the evaluator boundary. The environment only needs reward.
    """

    reward: float


class SubmissionEvaluator(Protocol):
    def evaluate(self, findings: Mapping[str, Any], inspection_steps: int) -> EvaluationResult:
        """Evaluate a structurally valid submission without exposing diagnostics."""


class EpisodeGenerator(Protocol):
    """Trusted integration point implemented by the synthetic-company generator."""

    def generate(self, seed: int, config: Mapping[str, Any]) -> "EpisodeData":
        """Create one episode's public data; private artifacts stay outside this API."""


@dataclass(frozen=True)
class EpisodeData:
    """Public generator output consumed by the environment.

    No truth, provenance, seed, filesystem path or closed evaluator setting belongs
    here. The generator owns construction and validation of these records.
    """

    episode_id: str
    manifest: Mapping[str, Any]
    policy_summary: Mapping[str, Any]
    records: Mapping[str, Sequence[Mapping[str, Any]]]
    documents: Mapping[str, Mapping[str, Any]]


@dataclass
class EnvironmentRuntime:
    """Trusted `reset(seed, config)` facade around isolated episode instances.

    Agents never receive this object directly. It is for local runners, tests and
    server-side factories that own the seed and evaluation mode.
    """

    generator: EpisodeGenerator
    evaluator_factory: Callable[[EpisodeData, Mapping[str, Any]], SubmissionEvaluator]
    clock: Callable[[], float] = time.monotonic
    _environment: "AuditEnvironment | None" = field(default=None, init=False)

    def reset(self, seed: int, config: Mapping[str, Any]) -> Mapping[str, Any]:
        _validate_reset_request(seed, config)
        # The generator receives configuration only at this trusted boundary.
        # Public reset output below intentionally omits seed and evaluation_mode.
        data = self.generator.generate(seed, copy.deepcopy(dict(config)))
        self._environment = AuditEnvironment(
            data=data,
            evaluation_mode=config["evaluation_mode"],
            evaluator=self.evaluator_factory(data, config),
            max_steps=config["max_steps"],
            time_limit_seconds=config["time_limit_seconds"],
            clock=self.clock,
        )
        return self._environment.reset()

    def step(self, action: Any) -> Mapping[str, Any]:
        return self._require_environment().step(action)

    def public_state(self) -> Mapping[str, Any]:
        return self._require_environment().public_state()

    def trajectory(self) -> list[Mapping[str, Any]]:
        return self._require_environment().trajectory()

    @property
    def episode_id(self) -> str:
        return self._require_environment().episode_id

    @property
    def finalized(self) -> bool:
        return self._require_environment().finalized

    def _require_environment(self) -> "AuditEnvironment":
        if self._environment is None:
            raise RuntimeError("reset(seed, config) must be called before accessing an episode")
        return self._environment


@dataclass
class AuditEnvironment:
    """A single isolated episode following docs/API_CONTRACT.md."""

    data: EpisodeData
    evaluator: SubmissionEvaluator
    evaluation_mode: str = "open"
    max_steps: int = 100
    time_limit_seconds: int = 60
    clock: Callable[[], float] = time.monotonic
    _step: int = field(default=0, init=False)
    _terminated: bool = field(default=False, init=False)
    _truncated: bool = field(default=False, init=False)
    _started_at: float | None = field(default=None, init=False)
    _last_observation: Mapping[str, Any] | None = field(default=None, init=False)
    _trajectories: list[Mapping[str, Any]] = field(default_factory=list, init=False)
    _cursor_index: dict[str, tuple[str, int]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if self.evaluation_mode not in {"open", "closed"}:
            raise ValueError("evaluation_mode must be open or closed")
        if self.max_steps != 100:
            raise ValueError("v1 requires max_steps to be exactly 100")
        if not 1 <= self.time_limit_seconds <= 60:
            raise ValueError("time_limit_seconds must be in 1..60")
        if not self.data.episode_id:
            raise ValueError("episode_id is required")
        if set(self.data.records) != set(TABLES):
            raise ValueError("records must contain exactly ledger, invoices, payments and fx")

    def reset(self) -> Mapping[str, Any]:
        """Start a new isolated in-memory state from the supplied public data."""
        self._step = 0
        self._terminated = False
        self._truncated = False
        self._started_at = self.clock()
        self._trajectories.clear()
        self._cursor_index.clear()
        observation = {
            "kind": "episode_start",
            "task": "Investigate synthetic accounting records for the stated reporting period and submit supported findings.",
            "manifest": copy.deepcopy(dict(self.data.manifest)),
            "policy_summary": copy.deepcopy(dict(self.data.policy_summary)),
            "remaining_steps": self.max_steps,
        }
        self._last_observation = observation
        return {
            "observation": copy.deepcopy(observation),
            "info": {
                "episode_id": self.data.episode_id,
                "environment_version": str(self.data.manifest.get("schema_version", "1.0.0")),
                "step": 0,
            },
        }

    @property
    def episode_id(self) -> str:
        return self.data.episode_id

    @property
    def finalized(self) -> bool:
        return self._terminated or self._truncated

    def public_state(self) -> Mapping[str, Any]:
        if self._last_observation is None:
            raise RuntimeError("reset must be called before reading public state")
        return {
            "episode_id": self.episode_id,
            "observation": copy.deepcopy(self._last_observation),
            "remaining_steps": max(0, self.max_steps - self._step),
            "terminated": self._terminated,
            "truncated": self._truncated,
        }

    def trajectory(self) -> list[Mapping[str, Any]]:
        return copy.deepcopy(self._trajectories)

    def step(self, action: Any) -> Mapping[str, Any]:
        if self._last_observation is None:
            raise RuntimeError("reset must be called before step")
        if self.finalized:
            raise EpisodeFinalizedError("episode is already finalized")
        if self._timed_out():
            return self._truncate()

        self._step += 1
        valid_action, validation_error = self._validate_action(action)
        if validation_error:
            if validation_error == "invalid_submission":
                # Structural submit errors are terminal. Matcher details stay outside
                # this process; open mode exposes only the contractually fixed -1.
                self._terminated = True
                score_invalid = getattr(self.evaluator, "score_invalid_submission", None)
                internal_reward = score_invalid(action.get("findings"), self._step - 1).reward if score_invalid else -1
                reward: float | int = internal_reward if self.evaluation_mode == "open" else 0
                result = self._result(self._error_observation(validation_error), reward=reward, terminated=True)
            else:
                result = self._result(self._error_observation(validation_error), reward=0)
        elif valid_action["kind"] == "submit":
            result = self._submit(valid_action)
        else:
            result = self._execute(valid_action)

        # A valid submit takes priority over the budget. Every other 100th step
        # is truncated, including an error action.
        if not result["terminated"] and (self._step >= self.max_steps or self._timed_out()):
            result = self._truncate("steps" if self._step >= self.max_steps else "time")
        self._last_observation = result["observation"]
        self._record_trajectory(action, result)
        return copy.deepcopy(result)

    def _timed_out(self) -> bool:
        return self._started_at is not None and self.clock() - self._started_at >= self.time_limit_seconds

    def _truncate(self, reason: str = "time") -> Mapping[str, Any]:
        self._truncated = True
        score_truncation = getattr(self.evaluator, "score_truncation", None)
        internal_reward = score_truncation(self._step, reason).reward if score_truncation else 0
        reward: float | int = internal_reward if self.evaluation_mode == "open" else 0
        observation = {"kind": "truncated", "result": None, "remaining_steps": 0}
        self._last_observation = observation
        return self._result(observation, reward=reward, truncated=True)

    def _result(
        self,
        observation: Mapping[str, Any],
        *,
        reward: float | int,
        terminated: bool = False,
        truncated: bool = False,
    ) -> Mapping[str, Any]:
        return {
            "observation": copy.deepcopy(dict(observation)),
            "reward": reward,
            "terminated": terminated,
            "truncated": truncated,
            "info": {"episode_id": self.episode_id, "step": self._step},
        }

    def _record_trajectory(self, action: Any, result: Mapping[str, Any]) -> None:
        # Public action/response only. This method never sees evaluator details.
        self._trajectories.append(
            {
                "episode_id": self.episode_id,
                "step": self._step,
                "action": copy.deepcopy(action),
                "observation": copy.deepcopy(result["observation"]),
                "reward": result["reward"],
                "terminated": result["terminated"],
                "truncated": result["truncated"],
            }
        )

    def _validate_action(self, action: Any) -> tuple[Mapping[str, Any] | None, str | None]:
        if not isinstance(action, Mapping) or not isinstance(action.get("kind"), str):
            return None, "invalid_action"
        kind = action["kind"]
        required: dict[str, set[str]] = {
            "list_records": {"kind", "table", "cursor", "limit"},
            "get_record": {"kind", "table", "id"},
            "get_document": {"kind", "doc_id"},
            "calculator": {"kind", "expression"},
            "submit": {"kind", "findings"},
        }
        if kind == "submit" and set(action) != required[kind]:
            return None, "invalid_submission"
        if kind not in required or set(action) != required[kind]:
            return None, "invalid_action"
        if kind == "list_records":
            if action["table"] not in TABLES:
                return None, "unsupported_table"
            if action["cursor"] is not None and not isinstance(action["cursor"], str):
                return None, "invalid_cursor"
            if not _is_plain_int(action["limit"]) or not 1 <= action["limit"] <= 50:
                return None, "invalid_argument"
        elif kind == "get_record":
            if action["table"] not in TABLES:
                return None, "unsupported_table"
            if not isinstance(action["id"], str) or not _id_matches_table(action["table"], action["id"]):
                return None, "invalid_argument"
        elif kind == "get_document":
            if not isinstance(action["doc_id"], str) or not _is_identifier(action["doc_id"], "doc:"):
                return None, "invalid_argument"
        elif kind == "calculator":
            if not isinstance(action["expression"], str) or not 1 <= len(action["expression"]) <= 256:
                return None, "invalid_argument"
        elif kind == "submit" and not _valid_findings_shape(
            action["findings"], self.episode_id, set(self.data.manifest.get("currency_decimals", {}))
        ):
            return None, "invalid_submission"
        return dict(action), None

    def _execute(self, action: Mapping[str, Any]) -> Mapping[str, Any]:
        kind = action["kind"]
        if kind == "list_records":
            return self._list_records(action)
        if kind == "get_record":
            return self._get_record(action)
        if kind == "get_document":
            document = self.data.documents.get(action["doc_id"])
            if document is None:
                return self._result(self._error_observation("unknown_id"), reward=0)
            return self._result(
                {
                    "kind": "document",
                    "request": copy.deepcopy(dict(action)),
                    "document": copy.deepcopy(dict(document)),
                    "remaining_steps": self._remaining_steps(),
                },
                reward=0,
            )
        if kind == "calculator":
            try:
                value = _calculate(action["expression"])
            except UnsafeExpression:
                return self._result(self._error_observation("unsafe_expression"), reward=0)
            except ZeroDivisionError:
                return self._result(self._error_observation("division_by_zero"), reward=0)
            return self._result(
                {
                    "kind": "calculation",
                    "request": copy.deepcopy(dict(action)),
                    "expression": action["expression"],
                    "result": value,
                    "remaining_steps": self._remaining_steps(),
                },
                reward=0,
            )
        raise AssertionError(f"unhandled action: {kind}")

    def _list_records(self, action: Mapping[str, Any]) -> Mapping[str, Any]:
        table = action["table"]
        start = 0
        cursor = action["cursor"]
        if cursor is not None:
            cursor_target = self._cursor_index.get(cursor)
            if cursor_target is None or cursor_target[0] != table:
                return self._result(self._error_observation("invalid_cursor"), reward=0)
            start = cursor_target[1]
        rows = self.data.records[table]
        page = rows[start : start + action["limit"]]
        next_start = start + len(page)
        next_cursor = self._cursor_for(table, next_start) if next_start < len(rows) else None
        return self._result(
            {
                "kind": "records",
                "request": copy.deepcopy(dict(action)),
                "records": copy.deepcopy([dict(row) for row in page]),
                "next_cursor": next_cursor,
                "remaining_steps": self._remaining_steps(),
            },
            reward=0,
        )

    def _cursor_for(self, table: str, start: int) -> str:
        digest = hashlib.sha256(f"{self.episode_id}:{table}:{start}".encode()).hexdigest()[:24]
        cursor = f"c_{digest}"
        self._cursor_index[cursor] = (table, start)
        return cursor

    def _get_record(self, action: Mapping[str, Any]) -> Mapping[str, Any]:
        row = next((row for row in self.data.records[action["table"]] if row.get("id") == action["id"]), None)
        if row is None:
            return self._result(self._error_observation("unknown_id"), reward=0)
        return self._result(
            {
                "kind": "record",
                "request": copy.deepcopy(dict(action)),
                "record": copy.deepcopy(dict(row)),
                "remaining_steps": self._remaining_steps(),
            },
            reward=0,
        )

    def _submit(self, action: Mapping[str, Any]) -> Mapping[str, Any]:
        self._terminated = True
        findings = action["findings"]
        normalized = {
            "episode_id": findings["episode_id"],
            "findings": [
                {**finding, "affected_ids": sorted(finding["affected_ids"]), "evidence_ids": sorted(finding["evidence_ids"])}
                for finding in findings["findings"]
            ],
        }
        evaluation = self.evaluator.evaluate(normalized, inspection_steps=self._step - 1)
        reward: float | int = evaluation.reward if self.evaluation_mode == "open" else 0
        observation = {
            "kind": "submission",
            "request": copy.deepcopy(dict(action)),
            "accepted_findings": len(findings["findings"]),
            "remaining_steps": self._remaining_steps(),
        }
        return self._result(observation, reward=reward, terminated=True)

    def _error_observation(self, code: str) -> Mapping[str, Any]:
        messages = {
            "invalid_action": "Action does not match the public schema.",
            "invalid_argument": "Action argument is invalid.",
            "invalid_cursor": "Cursor is invalid for this episode or table.",
            "unknown_id": "Requested public identifier was not found.",
            "unsupported_table": "Requested table is not supported.",
            "unsafe_expression": "Expression contains unsupported syntax.",
            "division_by_zero": "Expression divides by zero.",
            "invalid_submission": "Submission does not match the public schema.",
        }
        return {
            "kind": "error",
            "error": {"code": code, "message": messages[code]},
            "remaining_steps": self._remaining_steps(),
        }

    def _remaining_steps(self) -> int:
        return max(0, self.max_steps - self._step)


class UnsafeExpression(ValueError):
    pass


def _calculate(expression: str) -> str:
    if not CALCULATOR_RE.fullmatch(expression):
        raise UnsafeExpression
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise UnsafeExpression from error

    def visit(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            source = ast.get_source_segment(expression, node)
            if source is None or not re.fullmatch(r"\d+(?:\.\d+)?", source):
                raise UnsafeExpression
            return Decimal(source)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                raise ZeroDivisionError
            return left / right
        raise UnsafeExpression

    try:
        with localcontext() as context:
            context.prec = 28
            result = visit(tree)
    except (InvalidOperation, DivisionByZero) as error:
        raise UnsafeExpression from error
    return format(result, "f")


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_reset_request(seed: int, config: Mapping[str, Any]) -> None:
    expected = {
        "dataset_profile",
        "company_size",
        "enabled_issue_types",
        "max_steps",
        "time_limit_seconds",
        "evaluation_mode",
    }
    if not _is_plain_int(seed) or not 0 <= seed <= SAFE_INTEGER:
        raise ValueError("seed must be a safe non-negative integer")
    if not isinstance(config, Mapping) or set(config) != expected:
        raise ValueError("config does not match the v1 reset schema")
    if not isinstance(config["dataset_profile"], str) or config["dataset_profile"] not in {"train", "dev"}:
        raise ValueError("dataset_profile must be train or dev")
    if not isinstance(config["company_size"], str) or config["company_size"] not in {"small", "medium", "large"}:
        raise ValueError("company_size must be small, medium or large")
    enabled = config["enabled_issue_types"]
    if not isinstance(enabled, list) or not enabled or any(not isinstance(item, str) or item not in ISSUE_TYPES for item in enabled) or len(enabled) != len(set(enabled)):
        raise ValueError("enabled_issue_types must be a unique non-empty issue-type list")
    if config["max_steps"] != 100:
        raise ValueError("max_steps must be 100")
    if not _is_plain_int(config["time_limit_seconds"]) or not 1 <= config["time_limit_seconds"] <= 60:
        raise ValueError("time_limit_seconds must be in 1..60")
    if not isinstance(config["evaluation_mode"], str) or config["evaluation_mode"] not in {"open", "closed"}:
        raise ValueError("evaluation_mode must be open or closed")


def _is_identifier(value: str, prefix: str) -> bool:
    if not (value.startswith(prefix) and 1 <= len(value) <= 128 and all(ord(char) < 128 and not char.isspace() for char in value)):
        return False
    if prefix == "fx:":
        return re.fullmatch(r"fx:\d{4}-\d{2}-\d{2}:[A-Z]{3}:[A-Z]{3}", value) is not None
    return value.count(":") == 1 and len(value) > len(prefix)


def _id_matches_table(table: str, identifier: str) -> bool:
    prefix = {"ledger": "ledger:", "invoices": "invoice:", "payments": "payment:", "fx": "fx:"}[table]
    return _is_identifier(identifier, prefix)


def _valid_findings_shape(findings: Any, episode_id: str, currencies: set[str]) -> bool:
    if not isinstance(findings, Mapping) or set(findings) != {"episode_id", "findings"}:
        return False
    if findings["episode_id"] != episode_id or not isinstance(findings["findings"], list) or len(findings["findings"]) > 30:
        return False
    for finding in findings["findings"]:
        required = {"type", "affected_ids", "claimed_amount_minor", "currency", "evidence_ids", "explanation"}
        if not isinstance(finding, Mapping) or set(finding) != required or finding["type"] not in ISSUE_TYPES:
            return False
        if not _is_plain_int(finding["claimed_amount_minor"]) or abs(finding["claimed_amount_minor"]) > SAFE_INTEGER:
            return False
        if not isinstance(finding["currency"], str) or finding["currency"] not in currencies:
            return False
        if not isinstance(finding["explanation"], str) or not 1 <= len(finding["explanation"]) <= 1000:
            return False
        affected, evidence = finding["affected_ids"], finding["evidence_ids"]
        if not isinstance(affected, list) or not isinstance(evidence, list) or not 1 <= len(evidence) <= 10:
            return False
        if not all(isinstance(item, str) and any(_is_identifier(item, prefix) for prefix in ("ledger:", "invoice:", "payment:", "fx:")) for item in affected):
            return False
        if not all(isinstance(item, str) and any(_is_identifier(item, prefix) for prefix in ("ledger:", "invoice:", "payment:", "fx:", "doc:")) for item in evidence):
            return False
        if len(affected) != len(set(affected)) or len(evidence) != len(set(evidence)):
            return False
        prefixes = {item.split(":", 1)[0] for item in affected}
        if finding["type"] == "duplicate_invoice" and (len(affected) < 2 or prefixes != {"invoice"}):
            return False
        if finding["type"] == "payment_allocation" and (
            sum(item.startswith("payment:") for item in affected) != 1 or not any(item.startswith("invoice:") for item in affected)
        ):
            return False
        if finding["type"] in {"fx_date_or_rate", "reporting_period"} and "ledger" not in prefixes:
            return False
    return True
