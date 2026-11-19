"""Space Team Genesis: reproducible RL environment for training financial-audit agents.

This package currently contains the synthetic company generator (issue #2). The environment,
evaluator and policies are built on top of it in later issues.
"""

from .generator import generate
from .version import ENVIRONMENT_VERSION, SCHEMA_VERSION

__all__ = ["generate", "ENVIRONMENT_VERSION", "SCHEMA_VERSION"]
