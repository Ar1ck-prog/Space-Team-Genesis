"""Shared terminal reward (docs/API_CONTRACT.md section 5).

reward = clip(2 * F1 - 1 - 0.002 * inspection_steps, -1, 1)
An experimental reward may be added later, but it must be reported separately from this one.
"""

from __future__ import annotations

STEP_COST = 0.002
INVALID_SUBMISSION_REWARD = -1.0


def clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def terminal_reward(f1: float, inspection_steps: int) -> float:
    return clip(2.0 * f1 - 1.0 - STEP_COST * inspection_steps)
