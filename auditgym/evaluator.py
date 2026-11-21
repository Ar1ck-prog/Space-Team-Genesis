"""Evaluator core: scores one episode's terminal outcome against its closed truth.

Responsibilities (issue #4):
  * validate the submission structure (auditgym.submission), invalid -> internal reward -1;
  * one-to-one matching, P/R/F1 and the shared terminal reward;
  * keep diagnostics private: `public_reward()` is the only value meant for the policy, and in
    closed mode it is always 0.

Process isolation and wiring to reset/step live in space_team_genesis.integration.
The environment calls exactly one of `score_submission()` / `score_truncation()` per episode
and forwards only `public_reward()`.

Truncation (step or time limit): the case says the final answer is then empty. The evaluator
therefore scores an empty findings list with the inspection steps actually used (all accepted
steps, 0..100). No submitted findings are ever evaluated after truncation.
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .matcher import match, metrics
from .reward import INVALID_SUBMISSION_REWARD, terminal_reward
from .submission import SubmissionError, validate_submission

MODES = ("open", "closed")
MAX_INSPECTION_STEPS = 100


@dataclass
class Result:
    episode_id: str
    outcome: str  # submitted | invalid_submission | truncated_steps | truncated_time
    valid: bool
    inspection_steps: int
    n_truth: int
    n_findings: int
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    reward: float  # internal terminal reward
    error: dict | None = None  # internal detail for an invalid submission
    matched: list = field(default_factory=list)  # [(finding_index, issue_id)]

    def to_dict(self) -> dict:
        return asdict(self)


class EvaluatorError(RuntimeError):
    pass


class Evaluator:
    def __init__(self, episode_id: str, truth: dict, currencies, mode: str = "closed"):
        if mode not in MODES:
            raise EvaluatorError("mode must be open or closed")
        if truth.get("episode_id") != episode_id:
            raise EvaluatorError("truth belongs to another episode")
        self.episode_id = episode_id
        self.mode = mode
        self._truth = copy.deepcopy(truth["issues"])
        self._currencies = frozenset(currencies)
        self._result: Result | None = None
        self.log: list = []  # private evaluator journal

    # ------------------------------------------------------------------ constructors
    @classmethod
    def from_episode(cls, episode, mode: str = "closed") -> "Evaluator":
        return cls(episode.episode_id, episode.truth, episode.manifest["currency_decimals"], mode)

    @classmethod
    def from_dir(cls, episode_dir, mode: str = "closed") -> "Evaluator":
        d = Path(episode_dir)
        manifest = json.loads((d / "public" / "manifest.json").read_bytes().decode("utf-8"))
        truth = json.loads((d / "private" / "truth.json").read_bytes().decode("utf-8"))
        return cls(manifest["episode_id"], truth, manifest["currency_decimals"], mode)

    # ------------------------------------------------------------------ scoring
    def _check_once(self, inspection_steps: int, hi: int) -> None:
        if self._result is not None:
            raise EvaluatorError("episode already scored")
        if not isinstance(inspection_steps, int) or isinstance(inspection_steps, bool) or \
                not 0 <= inspection_steps <= hi:
            raise EvaluatorError(f"inspection_steps must be an integer in 0..{hi}")

    def _finish(self, res: Result) -> Result:
        self._result = res
        self.log.append(res.to_dict())
        return res

    def _scored(self, findings: list, inspection_steps: int, outcome: str) -> Result:
        m = match(findings, self._truth)
        mm = metrics(m["tp"], m["fp"], m["fn"])
        return Result(self.episode_id, outcome, True, inspection_steps, len(self._truth), len(findings),
                      m["tp"], m["fp"], m["fn"], mm["precision"], mm["recall"], mm["f1"],
                      terminal_reward(mm["f1"], inspection_steps), None, m["pairs"])

    def score_submission(self, submission, inspection_steps: int) -> Result:
        """Score a submit action. inspection_steps = accepted steps before submit (0..99)."""
        self._check_once(inspection_steps, MAX_INSPECTION_STEPS - 1)
        try:
            findings = validate_submission(submission, self.episode_id, self._currencies)
        except SubmissionError as e:
            n = len(self._truth)
            return self._finish(Result(self.episode_id, "invalid_submission", False, inspection_steps, n, 0,
                                       0, 0, n, 0.0, 0.0, 0.0, INVALID_SUBMISSION_REWARD,
                                       {"code": e.code, "detail": e.detail}, []))
        return self._finish(self._scored(findings, inspection_steps, "submitted"))

    def score_truncation(self, inspection_steps: int, reason: str = "steps") -> Result:
        """Score a truncated episode as an empty final answer."""
        if reason not in ("steps", "time"):
            raise EvaluatorError("reason must be steps or time")
        self._check_once(inspection_steps, MAX_INSPECTION_STEPS)
        return self._finish(self._scored([], inspection_steps, f"truncated_{reason}"))

    # ------------------------------------------------------------------ views
    def public_reward(self) -> float:
        """The only value the policy may see: the terminal reward in open mode, always 0 in closed."""
        if self._result is None:
            raise EvaluatorError("episode not scored yet")
        return float(self._result.reward) if self.mode == "open" else 0.0

    def result(self) -> Result:
        """Full private result (evaluator side only)."""
        if self._result is None:
            raise EvaluatorError("episode not scored yet")
        return self._result
