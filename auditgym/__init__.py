"""Space Team Genesis: reproducible RL environment for training financial-audit agents.

It contains the synthetic generator, evaluator, matcher and baseline policies.
The public environment and isolated runtime are wired in space_team_genesis.
"""

from .generator import generate
from .version import ENVIRONMENT_VERSION, SCHEMA_VERSION

__all__ = ["generate", "ENVIRONMENT_VERSION", "SCHEMA_VERSION"]
