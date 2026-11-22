"""Trusted offline diagnostics for issue #18; never an agent/public API."""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from auditgym.config import SIZE_PROFILES, derive_plan
from auditgym.generator import generate
from auditgym.verify import verify_episode
from space_team_genesis import lab
from space_team_genesis.integration import make_environment


def _case(run: dict, slot: str, repeat: int) -> dict:
    matches = [row for row in run["cases"] if row["slot"] == slot and row["repeat"] == repeat]
    if len(matches) != 1:
        raise ValueError("case not found")
    return matches[0]


def branch(run_path: Path, slot: str, action_number: int, replacement: dict | None = None,
           repeat: int = 0) -> dict:
    """Replay a saved script agent to the fork, then continue in a new isolated episode."""
    run, source = lab.load_run(run_path)
    if run["environment_sha256"] != lab.files_digest(lab.ENV_FILES) or \
            run["evaluator_sha256"] != lab.files_digest(lab.EVAL_FILES) or \
            run["runner_sha256"] != lab.runner_digest():
        raise ValueError("saved environment/evaluator/runner unavailable")
    row = _case(run, slot, repeat)
    original = lab._trajectory(run_path, row)
    timings = row.get("action_elapsed_seconds")
    if not isinstance(timings, (list, tuple)) or len(timings) != len(original):
        raise ValueError("per-action timing unavailable; exact remaining time cannot be restored")
    if not 1 <= action_number <= len(original):
        raise ValueError("action_number out of range")
    if replacement is not None and not isinstance(replacement, dict):
        raise ValueError("replacement action must be an object")
    if row["truncated"] and (not original or not original[-1]["truncated"]):
        raise ValueError("timeout before a step cannot be branched from this trajectory")
    environment = make_environment(row["seed"], dict(row["config"], evaluation_mode="closed"),
                                   trusted_metrics=True)
    clock_state = {"elapsed": 0.0, "live_started": None}
    environment.clock = lambda: clock_state["elapsed"] if clock_state["live_started"] is None else clock_state["elapsed"] + time.monotonic() - clock_state["live_started"]
    try:
        policy = lab._policy_from_source(run["agent"], source, repeat)
        reset = environment.reset()
        if reset["info"]["episode_id"] != row["episode_id"]:
            raise ValueError("episode identity changed")
        observation = reset["observation"]
        for index, saved in enumerate(original):
            proposed = policy.next_action(copy.deepcopy(observation))
            if proposed != saved["action"]:
                raise ValueError("agent state cannot be restored by deterministic replay at step " +
                                 str(index + 1))
            clock_state["elapsed"] = timings[index]
            action = replacement if replacement is not None and index + 1 == action_number else proposed
            if replacement is not None and index + 1 == action_number:
                clock_state["live_started"] = time.monotonic()
            result = environment.step(action)
            observation = result["observation"]
            if index + 1 < action_number or replacement is None:
                if any(result[key] != saved[key] for key in ("observation", "reward", "terminated", "truncated")):
                    raise ValueError("saved environment replay differs at step " + str(index + 1))
            if index + 1 == action_number:
                break
        if replacement is None:
            for index in range(action_number, len(original)):
                proposed = policy.next_action(copy.deepcopy(observation))
                if proposed != original[index]["action"]:
                    raise ValueError("agent state cannot be restored by deterministic replay")
                clock_state["elapsed"] = timings[index]
                result = environment.step(proposed)
                observation = result["observation"]
                if any(result[key] != original[index][key] for key in ("observation", "reward", "terminated", "truncated")):
                    raise ValueError("saved environment replay differs")
        else:
            while not environment.finalized:
                action = policy.next_action(copy.deepcopy(observation))
                result = environment.step(action)
                observation = result["observation"]
        changed = environment.trajectory()
        final_state = environment.public_state()
        metrics = environment.evaluator.metrics_for_completed_episode(environment)
    finally:
        environment.evaluator.close()
    first_difference = next((i + 1 for i, (a, b) in enumerate(zip(original, changed)) if a != b),
                            None)
    if first_difference is None and len(original) != len(changed):
        first_difference = min(len(original), len(changed)) + 1
    return {"mode": "diagnostic", "slot": slot, "repeat": repeat,
            "fork_action": action_number, "first_difference": first_difference,
            "original": original, "branch": changed,
            "terminated": final_state["terminated"], "truncated": final_state["truncated"],
            "branch_metrics": {"precision": metrics.precision, "recall": metrics.recall,
                               "f1": metrics.f1, "tp": metrics.tp, "fp": metrics.fp,
                               "fn": metrics.fn, "return": metrics.return_},
            "time_budget": {"limit_seconds": row["config"]["time_limit_seconds"],
                            "elapsed_at_fork": timings[action_number - 1],
                            "remaining_at_fork": max(0, row["config"]["time_limit_seconds"] -
                                                     timings[action_number - 1])}}


def _size(episode: Any) -> dict:
    return {"records": sum(len(episode.tables[name]) for name in ("ledger", "invoices", "payments", "fx")),
            "documents": episode.manifest["document_count"]}


def _candidates(plan: dict, size: str):
    profile = SIZE_PROFILES[size]
    for key in ("n_expenses", "n_invoices", "n_entities"):
        candidate = copy.deepcopy(plan)
        minimum = profile[key][0]
        if candidate[key][1] > minimum:
            candidate[key][1] = minimum
            yield key, candidate
    for key, (minimum, _) in profile["distractors"].items():
        if plan["distractors"][key] > minimum:
            candidate = copy.deepcopy(plan)
            candidate["distractors"][key] = minimum
            yield "distractors." + key, candidate


def minimize(case_path: Path, failed_run_path: Path, out: Path, *,
             max_attempts: int = 12, time_limit_seconds: float = 60.0) -> dict:
    """Regenerate a smaller valid company, verify it, and rerun the failed agent."""
    if max_attempts < 1 or time_limit_seconds <= 0:
        raise ValueError("positive search limits required")
    case = lab._read_json(case_path)
    lab._check_case_compat(case)
    run, source = lab.load_run(failed_run_path)
    if run["agent"]["agent_version"] != case["failed_agent_version"]:
        raise ValueError("wrong failed agent version")
    if any(run[key] != case[key] for key in ("environment_version", "environment_sha256",
                                           "evaluator_sha256", "runner_sha256", "dataset_sha256")) or \
            run["split"] != "train":
        raise ValueError("failed run is incompatible with saved case")
    if run.get("mode", "official") != "official":
        raise ValueError("minimization requires an official baseline")
    seed, config = case["seed"], case["config"]
    plan = derive_plan(seed, config)
    original = generate(seed, config, plan=plan)
    if not verify_episode(original)["ok"]:
        raise ValueError("source episode failed verification")
    initial = _size(original)
    old_truth_types = sorted(issue["type"] for issue in original.truth["issues"])
    attempts, accepted = 0, []
    search_started = time.monotonic()
    deadline = search_started + time_limit_seconds
    while attempts < max_attempts and time.monotonic() < deadline:
        found = False
        for change, candidate in _candidates(plan, config["company_size"]):
            if attempts >= max_attempts or time.monotonic() >= deadline:
                break
            attempts += 1
            try:
                episode = generate(seed, config, plan=candidate)
                if not verify_episode(episode)["ok"]:
                    continue
                if sorted(issue["type"] for issue in episode.truth["issues"]) != old_truth_types:
                    continue
                size = _size(episode)
                if (size["records"] + size["documents"] >=
                        initial["records"] + initial["documents"]):
                    continue
                row, _ = lab._run_case(run["agent"], source, seed, config, 0,
                                       diagnostic_plan=candidate)
                if row["f1"] < case["expected"]["f1"] or \
                        (row["f1"] == case["expected"]["f1"] and row["fp"] > case["expected"]["fp"]):
                    plan, initial = candidate, size
                    accepted.append({"change": change, "size": size, "f1": row["f1"], "fp": row["fp"]})
                    found = True
                    break
            except (ValueError, RuntimeError):
                continue
        if not found:
            break
    result = {"mode": "diagnostic", "source_case": str(case_path),
              "agent_version": run["agent"]["agent_version"],
              "original_size": _size(original), "final_size": initial,
              "attempts": attempts, "accepted": accepted,
              "search_elapsed_seconds": time.monotonic() - search_started,
              "soft_time_limit_seconds": time_limit_seconds,
              "stop_reason": "time" if time.monotonic() >= deadline else
                             "attempts" if attempts >= max_attempts else "no_smaller_failing_candidate",
              "not_guaranteed_minimal": True, "plan": plan,
              "seed": seed, "config": config}
    if out.exists():
        raise FileExistsError(out)
    lab._write_json(out, result)
    return result


MUTATIONS = {
    "currency": ('matcher.py', 'if f["type"] != t["type"] or f["currency"] != t["currency"]:',
                 'if f["type"] != t["type"]:'),
    "amount": ('matcher.py', 'if abs(f["claimed_amount_minor"] - t["expected_amount_minor"]) > AMOUNT_TOLERANCE_MINOR:',
               'if False:'),
    "evidence": ('matcher.py', 'return any(set(s) <= ev for s in t["evidence_sets"])',
                 'return True'),
    "duplicates": ('matcher.py', 'tp = len(pairs)', 'tp = sum(bool(edges) for edges in adj)'),
    "reward_steps": ('reward.py', 'STEP_COST = 0.002', 'STEP_COST = 0.0'),
    "closed_reward": ('evaluator.py', 'return float(self._result.reward) if self.mode == "open" else 0.0',
                      'return float(self._result.reward)'),
    "equivalent_control": ('matcher.py', 'ev = set(f["evidence_ids"])',
                           'ev = set(list(f["evidence_ids"]))'),
}


def mutation_report(names: list[str] | None = None) -> dict:
    """Run real matcher vectors against temporary package copies; production is untouched."""
    selected = names if names is not None else list(MUTATIONS)
    if any(name not in MUTATIONS for name in selected):
        raise ValueError("unknown mutation")
    results = []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        shutil.copytree(lab.ROOT / "auditgym", root / "auditgym",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(lab.ROOT / "tests", root / "tests",
                        ignore=shutil.ignore_patterns("__pycache__"))
        script = ("import sys,unittest;sys.path.insert(0,sys.argv[1]);"
                  "suite=unittest.defaultTestLoader.discover(sys.argv[1]+'/tests',"
                  "pattern='test_matcher_vectors.py');"
                  "r=unittest.TextTestRunner(verbosity=0).run(suite);"
                  "sys.exit(0 if r.wasSuccessful() else 1)")
        def run_tests() -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, "-c", script, str(root)], cwd=root,
                                  text=True, capture_output=True, timeout=30, check=False)
        baseline = run_tests()
        if baseline.returncode != 0:
            raise RuntimeError("unmutated evaluator vectors failed: " + baseline.stderr[-1000:])
        for name in selected:
            filename, old, new = MUTATIONS[name]
            target = root / "auditgym" / filename
            original = target.read_text(encoding="utf-8")
            if original.count(old) != 1:
                raise RuntimeError("mutation anchor changed: " + name)
            changed = original.replace(old, new)
            compile(changed, str(target), "exec")
            target.write_text(changed, encoding="utf-8")
            result = run_tests()
            results.append({"mutation": name, "detected": result.returncode != 0,
                            "test_output": result.stderr[-1500:]})
            target.write_text(original, encoding="utf-8")
    return {"mode": "diagnostic", "baseline_passed": True, "mutations": results,
            "detected": [r["mutation"] for r in results if r["detected"]],
            "survived": [r["mutation"] for r in results if not r["detected"]]}


def _brief_action(item: dict | None) -> dict | None:
    if item is None:
        return None
    action = item["action"]
    out = {key: action[key] for key in ("kind", "table", "id", "doc_id", "cursor", "limit", "expression")
           if key in action}
    if action["kind"] == "submit":
        out["findings_count"] = len(action.get("findings", {}).get("findings", []))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trusted offline investigation diagnostics")
    commands = parser.add_subparsers(dest="command", required=True)
    b = commands.add_parser("branch")
    b.add_argument("run", type=Path)
    b.add_argument("slot")
    b.add_argument("action_number", type=int)
    b.add_argument("--repeat", type=int, default=0)
    b.add_argument("--replacement", help="JSON action, omitted for exact replay")
    b.add_argument("--out", type=Path, help="save full trusted trajectories")
    m = commands.add_parser("minimize")
    m.add_argument("case", type=Path)
    m.add_argument("failed_run", type=Path)
    m.add_argument("--out", type=Path, required=True)
    m.add_argument("--max-attempts", type=int, default=12)
    m.add_argument("--time-limit", type=float, default=60.0)
    u = commands.add_parser("mutations")
    u.add_argument("--only", nargs="*", choices=tuple(MUTATIONS))
    args = parser.parse_args(argv)
    if args.command == "branch":
        full = branch(args.run, args.slot, args.action_number,
                      json.loads(args.replacement) if args.replacement else None, args.repeat)
        if args.out:
            if args.out.exists():
                raise FileExistsError(args.out)
            lab._write_json(args.out, full)
        original, changed = full["original"], full["branch"]
        differences = []
        start = (full["first_difference"] or 1) - 1
        for index in range(start, min(max(len(original), len(changed)), start + 10)):
            old = original[index] if index < len(original) else None
            new = changed[index] if index < len(changed) else None
            differences.append({"step": index + 1,
                                "original_action": _brief_action(old),
                                "branch_action": _brief_action(new),
                                "original_observation": old["observation"]["kind"] if old else None,
                                "branch_observation": new["observation"]["kind"] if new else None})
        result = {key: value for key, value in full.items() if key not in ("original", "branch")}
        result.update({"original_steps": len(original), "branch_steps": len(changed),
                       "changes_first_10": differences, "full_report": str(args.out) if args.out else None})
    elif args.command == "minimize":
        result = minimize(args.case, args.failed_run, args.out,
                          max_attempts=args.max_attempts, time_limit_seconds=args.time_limit)
    else:
        result = mutation_report(args.only)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
