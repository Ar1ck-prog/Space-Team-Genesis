"""Evaluator-only oracle with no access to public environment trajectories."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


class OracleFindingsProvider(Protocol):
    """Private evaluator interface backed by truth and allowed evidence sets."""
    def findings_for_episode(self, episode_id: str) -> Mapping[str, object]: ...


class TrustedOracleSession(Protocol):
    """Closed evaluator boundary: no reset/step, observation or trajectory methods."""
    def submit_private_oracle(self, episode_id: str, findings: Mapping[str, object]) -> Mapping[str, object]: ...


@dataclass
class EvaluatorOracle:
    """Runs only against TrustedOracleSession supplied by issue #4's evaluator."""
    provider: OracleFindingsProvider
    name: str = "evaluator_oracle"
    def run(self, session: TrustedOracleSession, episode_id: str) -> Mapping[str, object]:
        return session.submit_private_oracle(episode_id, self.provider.findings_for_episode(episode_id))
