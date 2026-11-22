"""Trusted offline wiring for generator, public environment and isolated evaluator.

The child process alone holds the generated private truth. The parent receives only
public EpisodeData and a reward-only scoring interface. Private metrics and oracle
are available only when explicitly requested by a trusted local runner.
"""
from __future__ import annotations

import multiprocessing as mp
import secrets
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any, Mapping

from auditgym.config import ISSUE_TYPES, validate_config, validate_seed
from auditgym.environment_adapter import public_episode_data
from auditgym.evaluator import Evaluator
from auditgym.evaluator_oracle import EvaluatorOracle
from auditgym.generator import generate
from auditgym.policies import EvaluationMetrics

from .environment import AuditEnvironment, EpisodeData, EvaluationResult

_RESPONSE_TIMEOUT = 60


def _metrics(result: Any) -> tuple[float, float, float, float, int, int, int]:
    return result.precision, result.recall, result.f1, result.reward, result.tp, result.fp, result.fn


class _PrivateOracleProvider:
    """Construct oracle findings inside the evaluator process only."""

    def __init__(self, episode: Any):
        self._episode = episode

    def findings_for_episode(self, episode_id: str) -> Mapping[str, object]:
        if episode_id != self._episode.episode_id:
            raise ValueError("episode mismatch")
        return {
            "episode_id": episode_id,
            "findings": [
                {
                    "type": issue["type"],
                    "affected_ids": issue["affected_ids"],
                    "claimed_amount_minor": issue["expected_amount_minor"],
                    "currency": issue["currency"],
                    "evidence_ids": issue["evidence_sets"][0],
                    "explanation": "Supported by public evidence.",
                }
                for issue in self._episode.truth["issues"]
            ],
        }


class _PrivateOracleSession:
    def __init__(self, evaluator: Evaluator):
        self._evaluator = evaluator

    def submit_private_oracle(self, episode_id: str, findings: Mapping[str, object]) -> Any:
        if episode_id != self._evaluator.episode_id:
            raise ValueError("episode mismatch")
        return self._evaluator.score_submission(findings, 0)


def _evaluator_worker(connection: Connection, seed: int, config: dict[str, Any], trusted: bool, plan: dict | None = None) -> None:
    try:
        episode = generate(seed, config, plan=plan)
        evaluator = Evaluator.from_episode(episode, mode=config["evaluation_mode"])
        connection.send(("ready", public_episode_data(episode)))
        while True:
            command, payload = connection.recv()
            if command == "close":
                break
            if command == "submit":
                evaluator.score_submission(payload["findings"], payload["steps"])
                connection.send(("reward", evaluator.public_reward()))
            elif command == "invalid_submission":
                evaluator.score_submission(payload["findings"], payload["steps"])
                connection.send(("reward", evaluator.public_reward()))
            elif command == "truncate":
                evaluator.score_truncation(payload["steps"], payload["reason"])
                connection.send(("reward", evaluator.public_reward()))
            elif command == "metrics" and trusted:
                connection.send(("metrics", _metrics(evaluator.result())))
            elif command == "oracle" and trusted:
                result = EvaluatorOracle(_PrivateOracleProvider(episode)).run(
                    _PrivateOracleSession(evaluator), episode.episode_id
                )
                connection.send(("metrics", _metrics(result)))
            else:
                raise ValueError("unsupported evaluator command")
            if command in {"submit", "invalid_submission", "truncate"} and not trusted:
                break
    except (EOFError, BrokenPipeError):
        pass
    except Exception:
        # Do not transmit exception text: it may include closed paths or values.
        try:
            connection.send(("error", None))
        except (EOFError, BrokenPipeError):
            pass
    finally:
        connection.close()


@dataclass
class IsolatedEvaluator:
    """Reward-only proxy. A trusted runner may request final aggregate metrics."""

    _connection: Connection
    _process: mp.Process
    _trusted: bool

    def _request(self, command: str, payload: Any = None) -> Any:
        try:
            self._connection.send((command, payload))
            if not self._connection.poll(_RESPONSE_TIMEOUT):
                raise RuntimeError("Isolated evaluator did not respond")
            kind, value = self._connection.recv()
        except (EOFError, BrokenPipeError, OSError) as error:
            raise RuntimeError("Isolated evaluator is unavailable") from error
        if kind == "error":
            raise RuntimeError("Isolated evaluator failed")
        if kind not in {"reward", "metrics"}:
            raise RuntimeError("Isolated evaluator returned an invalid response")
        return value

    def evaluate(self, findings: Mapping[str, Any], inspection_steps: int) -> EvaluationResult:
        reward = self._request("submit", {"findings": findings, "steps": inspection_steps})
        if not self._trusted:
            self.close()
        return EvaluationResult(reward=reward)

    def score_invalid_submission(self, findings: Any, inspection_steps: int) -> EvaluationResult:
        reward = self._request("invalid_submission", {"findings": findings, "steps": inspection_steps})
        if not self._trusted:
            self.close()
        return EvaluationResult(reward=reward)

    def score_truncation(self, inspection_steps: int, reason: str) -> EvaluationResult:
        reward = self._request("truncate", {"steps": inspection_steps, "reason": reason})
        if not self._trusted:
            self.close()
        return EvaluationResult(reward=reward)

    def metrics_for_completed_episode(self, environment: AuditEnvironment) -> EvaluationMetrics:
        if not self._trusted or not environment.finalized or environment.evaluator is not self:
            raise PermissionError("Private metrics require a completed trusted episode")
        precision, recall, f1, reward, tp, fp, fn = self._request("metrics")
        self.close()
        return EvaluationMetrics(precision, recall, f1, reward, tp, fp, fn)

    def oracle_metrics(self) -> EvaluationMetrics:
        if not self._trusted:
            raise PermissionError("Oracle is evaluator-only")
        precision, recall, f1, reward, tp, fp, fn = self._request("oracle")
        self.close()
        return EvaluationMetrics(precision, recall, f1, reward, tp, fp, fn)

    def close(self) -> None:
        if self._process.is_alive():
            try:
                self._connection.send(("close", None))
            except (EOFError, BrokenPipeError, OSError):
                pass
            self._process.join(timeout=1)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=1)
        self._connection.close()


def _start(seed: int, config: Mapping[str, Any], *, trusted: bool, plan: dict | None = None) -> tuple[EpisodeData, IsolatedEvaluator]:
    checked_seed = validate_seed(seed)
    checked_config = validate_config(dict(config))
    context = mp.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(
        target=_evaluator_worker, args=(child, checked_seed, checked_config, trusted, plan), daemon=True
    )
    process.start()
    child.close()
    if not parent.poll(_RESPONSE_TIMEOUT):
        process.terminate()
        process.join(timeout=1)
        parent.close()
        raise RuntimeError("Isolated evaluator did not initialize")
    try:
        kind, data = parent.recv()
    except EOFError as error:
        process.join(timeout=1)
        parent.close()
        raise RuntimeError("Isolated evaluator did not initialize") from error
    if kind != "ready":
        process.join(timeout=1)
        parent.close()
        raise RuntimeError("Isolated evaluator failed to initialize")
    return data, IsolatedEvaluator(parent, process, trusted)


def make_environment(seed: int, config: Mapping[str, Any], *, trusted_metrics: bool = False) -> AuditEnvironment:
    """Create one episode; only public data crosses the evaluator process boundary."""
    data, evaluator = _start(seed, config, trusted=trusted_metrics)
    return AuditEnvironment(
        data=data,
        evaluator=evaluator,
        evaluation_mode=config["evaluation_mode"],
        max_steps=config["max_steps"],
        time_limit_seconds=config["time_limit_seconds"],
    )


def score_oracle(seed: int, config: Mapping[str, Any]) -> EvaluationMetrics:
    """Trusted evaluator-side oracle; findings never leave the worker."""
    _, evaluator = _start(seed, config, trusted=True)
    try:
        return evaluator.oracle_metrics()
    finally:
        evaluator.close()


@dataclass
class OfflineEpisodeFactory:
    """Server-owned seed/config selection for the contract HTTP application."""

    evaluation_mode: str = "closed"

    def create(self, dataset_profile: str, company_size: str) -> AuditEnvironment:
        config = {
            "dataset_profile": dataset_profile,
            "company_size": company_size,
            "enabled_issue_types": list(ISSUE_TYPES),
            "max_steps": 100,
            "time_limit_seconds": 60,
            "evaluation_mode": self.evaluation_mode,
        }
        return make_environment(secrets.randbits(53), config)


def make_diagnostic_environment(seed: int, config: Mapping[str, Any], plan: dict) -> AuditEnvironment:
    """Trusted-only plan override; never wired to HTTP or agent-facing reset/step."""
    if not isinstance(plan, dict):
        raise ValueError("diagnostic plan must be an object")
    data, evaluator = _start(seed, config, trusted=True, plan=plan)
    return AuditEnvironment(
        data=data,
        evaluator=evaluator,
        evaluation_mode=config["evaluation_mode"],
        max_steps=config["max_steps"],
        time_limit_seconds=config["time_limit_seconds"],
    )
