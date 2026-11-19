"""Episode configuration (docs/API_CONTRACT.md section 3) and the internal issue plan.

Public config (trusted runner only):
  {"dataset_profile", "company_size", "enabled_issue_types", "max_steps", "time_limit_seconds",
   "evaluation_mode"}
Only dataset_profile, company_size and enabled_issue_types influence generated data; the step
budget, time limit and evaluation mode do not, so the same seed gives the same books in open
and closed mode.

The *plan* (how many issues of which type, interacting scenarios, distractors, size ranges) is
derived deterministically from (seed, config, environment_version). Tests and fixtures may
pass an explicit plan; that is an internal, trusted-code option and is not part of the
public API.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import random

from .version import ENVIRONMENT_VERSION, SUPPORTED_ENVIRONMENT_VERSIONS

ISSUE_TYPES = ("duplicate_invoice", "payment_allocation", "fx_date_or_rate", "reporting_period")
INTERACTIONS = ("fx_invoice_partial", "boundary_payment_fx_partial")
INTERACTION_TYPES = {"fx_date_or_rate", "payment_allocation"}  # both scenarios combine these two
MAX_ISSUES = 12

CONFIG_KEYS = ("dataset_profile", "company_size", "enabled_issue_types", "max_steps", "time_limit_seconds",
               "evaluation_mode")
PROFILES = ("train", "dev")
SIZES = ("small", "medium", "large")
MODES = ("open", "closed")

DISTRACTOR_KINDS = ("recurring_invoices", "split_payments", "multi_invoice_payments", "overpayment_refunds",
                    "opening_invoices", "boundary_documents")

SIZE_PROFILES = {
    "small": {"n_entities": (5, 9), "n_invoices": (22, 32), "n_expenses": (75, 130), "max_singles": 4,
              "distractors": {"recurring_invoices": (1, 1), "split_payments": (1, 2), "multi_invoice_payments": (1, 1),
                              "overpayment_refunds": (1, 1), "opening_invoices": (1, 2), "boundary_documents": (2, 3)}},
    "medium": {"n_entities": (8, 14), "n_invoices": (40, 55), "n_expenses": (190, 300), "max_singles": 6,
               "distractors": {"recurring_invoices": (1, 2), "split_payments": (2, 3), "multi_invoice_payments": (1, 2),
                               "overpayment_refunds": (1, 2), "opening_invoices": (2, 3), "boundary_documents": (2, 4)}},
    "large": {"n_entities": (12, 20), "n_invoices": (62, 75), "n_expenses": (480, 600), "max_singles": 8,
              "distractors": {"recurring_invoices": (2, 2), "split_payments": (2, 4), "multi_invoice_payments": (2, 3),
                              "overpayment_refunds": (1, 2), "opening_invoices": (2, 4), "boundary_documents": (3, 4)}},
}

CLEAN_PROBABILITY = 0.2
INTERACTION_PROBABILITY = 0.35

PUBLIC_DEFAULT_KEY = "space-team-genesis-public-dev-key"


class ConfigError(ValueError):
    pass


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_config(config) -> dict:
    """Validate a contract config and return a normalized copy. Raises ConfigError."""
    if not isinstance(config, dict):
        raise ConfigError("config must be an object")
    missing = [k for k in CONFIG_KEYS if k not in config]
    extra = [k for k in config if k not in CONFIG_KEYS]
    if missing or extra:
        raise ConfigError(f"config keys must be exactly {list(CONFIG_KEYS)}")
    if config["dataset_profile"] not in PROFILES:
        raise ConfigError(f"dataset_profile must be one of {list(PROFILES)}")
    if config["company_size"] not in SIZES:
        raise ConfigError(f"company_size must be one of {list(SIZES)}")
    types = config["enabled_issue_types"]
    if not isinstance(types, list) or not types or len(set(types)) != len(types) or \
            any(t not in ISSUE_TYPES for t in types):
        raise ConfigError("enabled_issue_types must be a non-empty list of unique issue types")
    if not _is_int(config["max_steps"]) or config["max_steps"] != 100:
        raise ConfigError("max_steps must be exactly 100 in v1")
    if not _is_int(config["time_limit_seconds"]) or not 1 <= config["time_limit_seconds"] <= 60:
        raise ConfigError("time_limit_seconds must be an integer in 1..60")
    if config["evaluation_mode"] not in MODES:
        raise ConfigError(f"evaluation_mode must be one of {list(MODES)}")
    out = copy.deepcopy(config)
    out["enabled_issue_types"] = [t for t in ISSUE_TYPES if t in types]  # canonical order
    return out


def validate_seed(seed) -> int:
    if not _is_int(seed) or not 0 <= seed <= 9007199254740991:
        raise ConfigError("seed must be an integer in 0..9007199254740991")
    return seed


def validate_version(environment_version: str) -> str:
    if environment_version not in SUPPORTED_ENVIRONMENT_VERSIONS:
        raise ConfigError(f"unsupported environment_version {environment_version!r}")
    return environment_version


def content_key(config: dict) -> dict:
    """The part of the config that determines generated data."""
    return {k: config[k] for k in ("dataset_profile", "company_size", "enabled_issue_types")}


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ----------------------------------------------------------------------------- plan

def empty_plan(company_size: str = "small") -> dict:
    sp = SIZE_PROFILES[company_size]
    return {
        "issues": {t: 0 for t in ISSUE_TYPES},
        "interactions": {k: 0 for k in INTERACTIONS},
        "targets": {"fx_date_or_rate": None, "reporting_period": None},
        "distractors": {k: v[0] for k, v in sp["distractors"].items()},
        "n_entities": list(sp["n_entities"]),
        "n_invoices": list(sp["n_invoices"]),
        "n_expenses": list(sp["n_expenses"]),
        "foreign_share": 0.45,
        "pay_prob": 0.7,
        "transform": {"id_salt": "", "order_seed": None},
    }


def derive_plan(seed: int, config: dict, environment_version: str = ENVIRONMENT_VERSION) -> dict:
    """Deterministic plan for (seed, config, version). Roughly 20% of episodes are clean and
    about a third of the rest contain an interacting scenario (when both fx_date_or_rate and
    payment_allocation are enabled)."""
    key = canonical_json({"v": environment_version, "seed": seed, "c": content_key(config)})
    rng = random.Random("plan:" + key)
    size = config["company_size"]
    sp = SIZE_PROFILES[size]
    plan = empty_plan(size)
    plan["distractors"] = {k: rng.randint(lo, hi) for k, (lo, hi) in sp["distractors"].items()}
    enabled = config["enabled_issue_types"]
    if rng.random() < CLEAN_PROBABILITY:
        return plan
    total = 0
    if INTERACTION_TYPES <= set(enabled) and rng.random() < INTERACTION_PROBABILITY:
        if rng.random() < 0.3:
            plan["interactions"] = {k: 1 for k in INTERACTIONS}
            total += 4
        else:
            plan["interactions"][rng.choice(INTERACTIONS)] = 1
            total += 2
    for _ in range(rng.randint(1, sp["max_singles"])):
        if total >= MAX_ISSUES:
            break
        plan["issues"][rng.choice(enabled)] += 1
        total += 1
    return plan


def validate_plan(plan: dict, config: dict) -> None:
    enabled = set(config["enabled_issue_types"])
    for t, n in plan["issues"].items():
        if t not in ISSUE_TYPES or not _is_int(n) or n < 0:
            raise ConfigError(f"bad plan issue count {t}")
        if n and t not in enabled:
            raise ConfigError(f"plan uses disabled issue type {t}")
    for k, n in plan["interactions"].items():
        if k not in INTERACTIONS or not _is_int(n) or n < 0:
            raise ConfigError(f"bad plan interaction {k}")
        if n and not INTERACTION_TYPES <= enabled:
            raise ConfigError(f"interaction {k} needs {sorted(INTERACTION_TYPES)} enabled")
    for k, v in plan["targets"].items():
        if v not in (None, "invoice", "payment"):
            raise ConfigError(f"plan target {k} must be invoice, payment or null")
    if set(plan["distractors"]) != set(DISTRACTOR_KINDS):
        raise ConfigError("plan distractors must list every distractor kind")
    total = sum(plan["issues"].values()) + 2 * sum(plan["interactions"].values())
    if total > MAX_ISSUES:
        raise ConfigError(f"at most {MAX_ISSUES} issues per episode, got {total}")
    if plan["n_invoices"][0] < 20:
        raise ConfigError("n_invoices lower bound must be >= 20")


# ----------------------------------------------------------------------------- episode id

def episode_key() -> bytes:
    return os.environ.get("GENESIS_EPISODE_KEY", PUBLIC_DEFAULT_KEY).encode()


def episode_id(seed: int, config: dict, plan: dict, environment_version: str) -> str:
    """Opaque, reproducible id: keyed HMAC, so the seed cannot be read back from it."""
    msg = canonical_json({"seed": seed, "c": content_key(config), "plan": plan, "v": environment_version}).encode()
    digest = hmac.new(episode_key(), msg, hashlib.sha256).digest()
    return "ep_" + base64.b32encode(digest).decode()[:20]
