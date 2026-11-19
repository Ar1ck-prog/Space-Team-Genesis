"""CLI: generate one episode and write its artifacts.

  python -m auditgym.generate --seed 1001 --profile train --size small --out episodes/train
  python -m auditgym.generate --seed 7 --types duplicate_invoice fx_date_or_rate --out /tmp/ep --verify

The episode is written to <out>/<episode_id>/{public,private}/. Seeds and private files are for
trusted code only; never mount private/ for an agent.
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import ISSUE_TYPES, PROFILES, SIZES
from .episode_io import write_episode
from .generator import generate
from .verify import verify_episode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m auditgym.generate")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--profile", choices=PROFILES, default="train")
    ap.add_argument("--size", choices=SIZES, default="small")
    ap.add_argument("--types", nargs="+", choices=ISSUE_TYPES, default=list(ISSUE_TYPES))
    ap.add_argument("--out", required=True)
    ap.add_argument("--verify", action="store_true", help="run the independent data checks")
    a = ap.parse_args(argv)
    config = {"dataset_profile": a.profile, "company_size": a.size, "enabled_issue_types": a.types,
              "max_steps": 100, "time_limit_seconds": 60, "evaluation_mode": "open"}
    ep = generate(a.seed, config)
    path = write_episode(ep, f"{a.out}/{ep.episode_id}")
    print(f"{ep.episode_id}: {ep.manifest['record_counts']}, issues={len(ep.truth['issues'])} -> {path}")
    if a.verify:
        res = verify_episode(ep)
        print(json.dumps(res["errors"], indent=2) if not res["ok"] else "verify: ok")
        return 0 if res["ok"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
