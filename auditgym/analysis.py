"""Measured failure analysis and public trajectories for the open episode sets.

  python -m auditgym.analysis                         # markdown tables: recall per issue type, FP sources
  python -m auditgym.analysis --trajectories trajectories   # also write public trajectories

Every policy runs through the public reset/step of AuditEnvironment. Scoring uses the official
evaluator code; to keep the run fast and deterministic it is called in-process here (the
isolated-process runtime gives the same numbers, see `make report`). Trajectories contain only
the contract fields (episode_id, step, action, observation, reward, terminated, truncated), so
no truth, seed or evaluator diagnostics can appear in them.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from .books import Books, audit
from .config import ISSUE_TYPES
from .dataset import SPLITS, load_manifest
from .environment_adapter import environment_from_public_episode
from .evaluator import Evaluator
from .generator import generate
from .policies import MarkEverythingPolicy, RandomExplorer, SimpleReconciliationPolicy, _Collector, run_policy

TRAJECTORY_FIELDS = ("episode_id", "step", "action", "observation", "reward", "terminated", "truncated")


class CarefulAuditorPolicy(_Collector):
    """Reads every table page by page, then applies policy_summary to all four issue types.
    Public data only: the same logic as frontend/agent.js (careful) and auditgym/books.py."""

    name = "careful_auditor"

    def findings(self):
        tables = dict(self.tables)
        tables["ledger"] = [{**r, "row_id": r["id"]} for r in self.tables["ledger"]]
        return audit(Books(tables, dict(self.manifest)))[:30]


POLICIES = {
    "random": ("Случайный исследователь", lambda: RandomExplorer(7)),
    "mark_all": ("«Пометить всё»", MarkEverythingPolicy),
    "reconcile": ("Простая сверка", SimpleReconciliationPolicy),
    "careful": ("Внимательный аудитор", CarefulAuditorPolicy),
}


class EvaluatorPort:
    """The environment's evaluator protocol on top of auditgym.Evaluator, in-process."""

    def __init__(self, evaluator: Evaluator):
        self.evaluator = evaluator

    def evaluate(self, findings, inspection_steps):
        from space_team_genesis.environment import EvaluationResult
        return EvaluationResult(reward=self.evaluator.score_submission(findings, inspection_steps).reward)

    def score_invalid_submission(self, findings, inspection_steps):
        from space_team_genesis.environment import EvaluationResult
        return EvaluationResult(reward=self.evaluator.score_submission(findings, inspection_steps).reward)

    def score_truncation(self, inspection_steps, reason):
        from space_team_genesis.environment import EvaluationResult
        return EvaluationResult(reward=self.evaluator.score_truncation(inspection_steps, reason).reward)


def run_case(entry: dict, policy_key: str):
    """Run one policy on one open episode. Returns (episode, run, private result)."""
    config = dict(entry["config"], evaluation_mode="open")
    ep = generate(entry["seed"], config)
    port = EvaluatorPort(Evaluator.from_episode(ep, "open"))
    env = environment_from_public_episode(ep, port, evaluation_mode="open")
    run = run_policy(env, POLICIES[policy_key][1]())
    return ep, run, port.evaluator.result()


def _submitted(run) -> list:
    last = run.trajectory[-1]["action"] if run.trajectory else {}
    if isinstance(last, dict) and last.get("kind") == "submit" and isinstance(last.get("findings"), dict):
        return last["findings"].get("findings") or []
    return []


def analyse(specs: list) -> dict:
    out = {}
    for key, (name, _) in POLICIES.items():
        found = collections.Counter()
        total = collections.Counter()
        fp_types = collections.Counter()
        missed_scen = collections.Counter()
        rewards, f1s, steps, clean_ok, clean_n, truncated = [], [], [], 0, 0, 0
        for e in specs:
            ep, run, res = run_case(e, key)
            truth = {i["issue_id"]: i for i in ep.truth["issues"]}
            matched = {issue for _, issue in res.matched}
            for i in truth.values():
                total[i["type"]] += 1
                if i["issue_id"] in matched:
                    found[i["type"]] += 1
                elif i["scenario"]:
                    missed_scen[i["scenario"]] += 1
            matched_idx = {idx for idx, _ in res.matched}
            for idx, f in enumerate(_submitted(run)):
                if idx not in matched_idx:
                    fp_types[f.get("type", "?")] += 1
            if not truth:
                clean_n += 1
                clean_ok += res.f1 == 1.0
            rewards.append(res.reward)
            f1s.append(res.f1)
            steps.append(res.inspection_steps)
            truncated += res.outcome.startswith("truncated")
        n = len(specs)
        out[key] = {"name": name, "episodes": n, "mean_return": round(sum(rewards) / n, 4),
                    "mean_f1": round(sum(f1s) / n, 4), "mean_steps": round(sum(steps) / n, 1),
                    "recall_by_type": {t: [found[t], total[t]] for t in ISSUE_TYPES},
                    "false_positives_by_type": dict(fp_types), "missed_in_scenarios": dict(missed_scen),
                    "clean_episodes_correct": [clean_ok, clean_n], "truncated": truncated}
    return out


TYPE_RU = {"duplicate_invoice": "дубли", "payment_allocation": "распределение", "fx_date_or_rate": "курс",
           "reporting_period": "период"}


def markdown(result: dict) -> str:
    head = "| Политика | " + " | ".join(TYPE_RU[t] for t in ISSUE_TYPES) + \
           " | Чистые книги без обвинений | Ложных выводов | F1 | Награда |"
    lines = [head, "| --- |" + " ---: |" * (len(ISSUE_TYPES) + 4)]
    for r in result.values():
        cells = [f"{a}/{b}" for a, b in (r["recall_by_type"][t] for t in ISSUE_TYPES)]
        ok, cn = r["clean_episodes_correct"]
        fp = sum(r["false_positives_by_type"].values())
        lines.append(f"| {r['name']} | " + " | ".join(cells) +
                     f" | {ok}/{cn} | {fp} | {r['mean_f1']:.3f} | {r['mean_return']:+.3f} |")
    return "\n".join(lines)


def trajectory_text(specs: list, policy_key: str) -> str:
    rows = []
    for e in specs:
        _, run, _ = run_case(e, policy_key)
        rows += [{f: row[f] for f in TRAJECTORY_FIELDS} for row in run.trajectory]
    return "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)


def write_trajectories(out_dir: Path) -> list:
    """trajectories.jsonl: simple reconciliation (the organisers' baseline) on all 20 open episodes.
    by_policy/<key>.jsonl: the other public policies on the 10 train episodes. Strict contract fields."""
    out_dir = Path(out_dir)
    (out_dir / "by_policy").mkdir(parents=True, exist_ok=True)
    written = [out_dir / "trajectories.jsonl"]
    written[0].write_bytes(trajectory_text(open_specs(), "reconcile").encode("utf-8"))
    train = load_manifest("train")["episodes"]
    for key in ("random", "mark_all", "careful"):
        path = out_dir / "by_policy" / f"{key}.jsonl"
        path.write_bytes(trajectory_text(train, key).encode("utf-8"))
        written.append(path)
    return written


def open_specs() -> list:
    return [e for s in SPLITS for e in load_manifest(s)["episodes"]]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m auditgym.analysis")
    ap.add_argument("--trajectories", default="", help="directory for public trajectories")
    ap.add_argument("--json", action="store_true", help="print the raw analysis as JSON")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    specs = open_specs()
    result = analyse(specs)
    print(json.dumps(result, ensure_ascii=False, indent=2) if a.json else markdown(result))
    if a.trajectories:
        for p in write_trajectories(Path(a.trajectories)):
            print(f"written {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
