"""Local offline commands for the integrated FinTech environment."""
from __future__ import annotations

import argparse
import json
import sys
import time
import unittest
from pathlib import Path
from statistics import mean
from wsgiref.simple_server import make_server

from auditgym.config import ISSUE_TYPES
from auditgym.dataset import SPLITS, check as check_dataset, load_manifest
from auditgym.policies import MarkEverythingPolicy, RandomExplorer, SimpleReconciliationPolicy, run_policy

from .http_api import EnvironmentHttpApp
from .hide_error_http import HideErrorDemoApp
from .integration import OfflineEpisodeFactory, make_environment, score_oracle
from .web import SiteWithApi


def _config(profile: str, size: str, mode: str) -> dict:
    return {
        "dataset_profile": profile,
        "company_size": size,
        "enabled_issue_types": list(ISSUE_TYPES),
        "max_steps": 100,
        "time_limit_seconds": 60,
        "evaluation_mode": mode,
    }


def _policy(name: str):
    if name == "random":
        return RandomExplorer(7)
    if name == "reconcile":
        return SimpleReconciliationPolicy()
    if name == "mark-all":
        return MarkEverythingPolicy()
    raise ValueError("unknown policy")


def _summary(run) -> dict:
    return {
        "policy": run.policy,
        "episode_id": run.episode_id,
        "precision": run.metrics.precision,
        "recall": run.metrics.recall,
        "f1": run.metrics.f1,
        "return": run.return_,
        "inspection_steps": run.inspection_steps,
        "elapsed_seconds": round(run.elapsed_seconds, 4),
    }


def run_one(args) -> int:
    environment = make_environment(args.seed, _config(args.profile, args.size, args.mode), trusted_metrics=True)
    try:
        result = run_policy(environment, _policy(args.policy), metrics_provider=environment.evaluator)
    finally:
        environment.evaluator.close()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in result.trajectory),
        encoding="utf-8",
    )
    print(json.dumps(_summary(result), ensure_ascii=False))
    print(f"public trajectory: {out}")
    return 0


def report(args) -> int:
    splits = SPLITS if args.split == "all" else (args.split,)
    grouped: dict[str, list[dict]] = {name: [] for name in ("random", "reconcile", "mark-all", "oracle")}
    for split in splits:
        for entry in load_manifest(split)["episodes"]:
            config = dict(entry["config"], evaluation_mode="closed")
            for name in ("random", "reconcile", "mark-all"):
                environment = make_environment(entry["seed"], config, trusted_metrics=True)
                try:
                    result = run_policy(environment, _policy(name), metrics_provider=environment.evaluator)
                finally:
                    environment.evaluator.close()
                grouped[name].append(_summary(result))
            started = time.perf_counter()
            oracle = score_oracle(entry["seed"], config)
            grouped["oracle"].append({
                "precision": oracle.precision, "recall": oracle.recall, "f1": oracle.f1,
                "return": oracle.return_, "inspection_steps": 0, "elapsed_seconds": time.perf_counter() - started,
            })
    print("policy,n,precision,recall,f1,return,inspection_steps,elapsed_seconds")
    for name, rows in grouped.items():
        values = [
            name, str(len(rows)),
            *[f"{mean(row[key] for row in rows):.4f}" for key in
              ("precision", "recall", "f1", "return", "inspection_steps", "elapsed_seconds")],
        ]
        print(",".join(values))
    return 0


def check_all() -> int:
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        return 1
    problems = check_dataset()
    for problem in problems:
        print("FAIL", problem)
    if problems:
        return 1
    print("dataset check: ok")
    return run_one(argparse.Namespace(
        seed=2013, profile="dev", size="small", mode="closed",
        policy="reconcile", out="outputs/trajectories.jsonl",
    ))


def serve(args) -> int:
    # The server, never the client, chooses the evaluation mode (contract section 7).
    app = EnvironmentHttpApp(OfflineEpisodeFactory(evaluation_mode=args.mode))
    site = Path(args.site) if args.site else None
    if site is not None and (site / "index.html").is_file():
        app = SiteWithApi(app, site)
    elif args.site:
        print(f"site directory {args.site!r} not found; serving the API only")
        site = None
    with make_server(args.host, args.port, app) as server:
        if site is not None:
            print(f"Site:     http://{args.host}:{args.port}/")
        print(f"HTTP API: http://{args.host}:{args.port}/api/v1/episodes  (evaluation mode: {args.mode})")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def jury_demo(args) -> int:
    """Serve the one-screen, internet-free jury scenario."""
    with make_server(args.host, args.port, HideErrorDemoApp()) as server:
        print(f"Jury demo: http://{args.host}:{args.port}/hide-error-demo")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m space_team_genesis.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="run all tests, dataset checks and an integrated smoke test")
    run = commands.add_parser("run", help="run one public baseline and export its trajectory")
    run.add_argument("--seed", type=int, default=2013)
    run.add_argument("--profile", choices=("train", "dev"), default="dev")
    run.add_argument("--size", choices=("small", "medium", "large"), default="small")
    run.add_argument("--mode", choices=("open", "closed"), default="closed")
    run.add_argument("--policy", choices=("random", "reconcile", "mark-all"), default="reconcile")
    run.add_argument("--out", default="outputs/trajectories.jsonl")
    summary = commands.add_parser("report", help="trusted diagnostics on open episode sets")
    summary.add_argument("--split", choices=("train", "dev", "all"), default="all")
    http = commands.add_parser("serve", help="serve the public HTTP API (and the site, when present)")
    http.add_argument("--host", default="127.0.0.1")
    http.add_argument("--port", type=int, default=8000)
    http.add_argument("--mode", choices=("closed", "open"), default="closed",
                      help="evaluation mode chosen by the server; open returns the terminal reward to the client")
    http.add_argument("--site", default="frontend" if Path("frontend/index.html").is_file() else "",
                      help="static site directory served at / (empty string disables it)")
    jury = commands.add_parser("jury-demo", help="serve the offline Hide the error from AI jury screen")
    jury.add_argument("--host", default="127.0.0.1")
    jury.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.command == "check":
        return check_all()
    if args.command == "run":
        return run_one(args)
    if args.command == "report":
        return report(args)
    if args.command == "jury-demo":
        return jury_demo(args)
    return serve(args)


if __name__ == "__main__":
    raise SystemExit(main())
