"""Trusted offline agent-version experiments. Never expose run.json or cases to an agent."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from auditgym.dataset import load_manifest
from auditgym.version import ENVIRONMENT_VERSION
from auditgym.policies import EvaluationMetrics, PolicyRun, run_policy
from space_team_genesis.integration import make_environment, make_diagnostic_environment

ROOT = Path(__file__).resolve().parents[2]
POLICIES = ROOT / "auditgym" / "policies.py"
ENV_FILES = ("auditgym/version.py", "auditgym/config.py", "auditgym/generator.py",
             "auditgym/books.py", "auditgym/money.py", "auditgym/timeutil.py",
             "auditgym/ids.py", "auditgym/environment_adapter.py", "auditgym/episode_io.py",
             "auditgym/dataset.py", "src/space_team_genesis/environment.py")
EVAL_FILES = ("auditgym/evaluator.py", "auditgym/matcher.py", "auditgym/reward.py",
              "auditgym/submission.py", "auditgym/evaluator_oracle.py",
              "src/space_team_genesis/integration.py")
CLASS_NAMES = {"random": "RandomExplorer", "reconcile": "SimpleReconciliationPolicy",
               "mark-all": "MarkEverythingPolicy"}
FORMAT_VERSION = 3


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def files_digest(paths: tuple[str, ...]) -> str:
    return digest(canonical({name: digest((ROOT / name).read_bytes()) for name in paths}))


def runner_digest() -> str:
    return digest(inspect.getsource(run_policy) + inspect.getsource(EvaluationMetrics) +
                  inspect.getsource(PolicyRun) +
                  digest((ROOT / "src/space_team_genesis/lab.py").read_bytes()))


def git_commit(source: str) -> str | None:
    completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                               capture_output=True, check=False)
    if completed.returncode != 0:
        return None
    committed = subprocess.run(["git", "show", "HEAD:auditgym/policies.py"], cwd=ROOT,
                               text=True, capture_output=True, check=False)
    if committed.returncode != 0 or committed.stdout != source:
        return None
    return completed.stdout.strip()


def _agent(policy: str, prompt: str, settings: dict, model_id: str | None) -> tuple[dict, str]:
    if policy not in CLASS_NAMES:
        raise ValueError("unknown script policy")
    allowed = {"seed", "exploration_steps"} if policy == "random" else set()
    if not isinstance(settings, dict) or set(settings) - allowed:
        raise ValueError("unsupported script policy settings")
    if any(not isinstance(v, int) or isinstance(v, bool) for v in settings.values()):
        raise ValueError("script policy settings must be integers")
    if settings.get("exploration_steps", 1) < 1 or settings.get("seed", 0) < 0:
        raise ValueError("invalid script policy settings")
    source = POLICIES.read_text(encoding="utf-8")
    metadata = {"policy": policy, "code_commit": git_commit(source), "code_sha256": digest(source),
                "prompt": prompt, "settings": settings, "model_id": model_id}
    metadata["agent_version"] = digest(canonical(metadata))
    return metadata, source


def _policy_from_source(agent: dict, source: str, repeat: int):
    if digest(source) != agent["code_sha256"]:
        raise ValueError("agent source digest mismatch")
    policy = agent["policy"]
    if policy not in CLASS_NAMES:
        raise ValueError("unknown script policy")
    name = "auditgym._snapshot_" + digest(source)[:24]
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_loader(name, loader=None)
        module = importlib.util.module_from_spec(spec)
        module.__package__ = "auditgym"
        sys.modules[name] = module
        try:
            exec(compile(source, name, "exec"), module.__dict__)
        except Exception:
            del sys.modules[name]
            raise
    cls = getattr(module, CLASS_NAMES[policy])
    settings = dict(agent["settings"])
    if policy == "random":
        return cls(seed=settings.get("seed", 7) + repeat,
                   exploration_steps=settings.get("exploration_steps", 8))
    return cls()


class _BudgetPolicy:
    """Experimental action cap; official environment limits and reward stay unchanged."""

    def __init__(self, policy: Any, budget: int):
        self.policy, self.budget, self.used = policy, budget, 0
        self.name = policy.name + "_budget_" + str(budget)

    def next_action(self, observation: dict) -> dict:
        self.used += 1
        proposed = self.policy.next_action(observation)
        if self.used == self.budget:
            episode_id = observation["manifest"]["episode_id"] if observation["kind"] == "episode_start" else self.policy.episode_id
            findings = []
            if getattr(self.policy, "manifest", None) is not None and hasattr(self.policy, "findings"):
                findings = self.policy.findings()
            return {"kind": "submit", "findings": {"episode_id": episode_id, "findings": findings}}
        return proposed


def public_volume(trajectory: tuple | list) -> dict:
    """Count returned public payload, including repeated reads, excluding requests/metadata."""
    records = 0
    bytes_viewed = 0
    for item in trajectory:
        observation = item["observation"]
        kind = observation["kind"]
        if kind == "records":
            payloads = observation["records"]
        elif kind in ("record", "document"):
            payloads = [observation[kind]]
        else:
            payloads = []
        records += len(payloads)
        bytes_viewed += sum(len(canonical(payload).encode("utf-8")) for payload in payloads)
    return {"viewed_items": records, "viewed_bytes": bytes_viewed}


def _run_case(agent: dict, source: str, seed: int, config: dict, repeat: int, action_budget: int | None = None, diagnostic_plan: dict | None = None) -> tuple[dict, tuple]:
    closed_config = dict(config, evaluation_mode="closed")
    environment = (make_environment(seed, closed_config, trusted_metrics=True) if diagnostic_plan is None
                   else make_diagnostic_environment(seed, closed_config, diagnostic_plan))
    try:
        policy = _policy_from_source(agent, source, repeat)
        if action_budget is not None:
            policy = _BudgetPolicy(policy, action_budget)
        result = run_policy(environment, policy, metrics_provider=environment.evaluator)
    finally:
        environment.evaluator.close()
    if result.metrics is None:
        raise RuntimeError("trusted metrics missing")
    metrics = result.metrics
    row = {"episode_id": result.episode_id, "repeat": repeat,
           "precision": metrics.precision, "recall": metrics.recall, "f1": metrics.f1,
           "tp": metrics.tp, "fp": metrics.fp, "fn": metrics.fn,
           "return": result.return_, "inspection_steps": result.inspection_steps,
           "elapsed_seconds": result.elapsed_seconds,
           "terminated": result.terminated, "truncated": result.truncated,
           "action_elapsed_seconds": result.action_elapsed_seconds, **public_volume(result.trajectory)}
    return row, result.trajectory


def _write_json(path: Path, value: dict) -> None:
    resolved = path.resolve()
    if resolved.is_relative_to(ROOT) and not resolved.is_relative_to((ROOT / "outputs").resolve()):
        raise ValueError("trusted artifacts inside the repository must be under outputs/")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(value) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def create_run(out_dir: Path, policy: str, split: str, prompt: str = "",
               settings: dict | None = None, model_id: str | None = None,
               repeats: int = 1, action_budget: int | None = None) -> Path:
    """Save trusted metadata separately from strictly public trajectories."""
    if split not in ("train", "dev") or not 1 <= repeats <= 20:
        raise ValueError("invalid split or repeat count")
    if action_budget is not None and not 1 <= action_budget <= 100:
        raise ValueError("action_budget must be in 1..100")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError("run output directory must be empty")
    agent, source = _agent(policy, prompt, settings or {}, model_id)
    dataset = load_manifest(split)
    run = {"format_version": FORMAT_VERSION, "agent": agent, "mode": "experimental" if action_budget is not None else "official", "action_budget": action_budget,
           "environment_version": ENVIRONMENT_VERSION,
           "environment_sha256": files_digest(ENV_FILES),
           "evaluator_sha256": files_digest(EVAL_FILES),
           "runner_sha256": runner_digest(),
           "dataset_sha256": digest(canonical(dataset)), "split": split,
           "repeats": repeats, "cases": []}
    run["run_id"] = digest(canonical({key: value for key, value in run.items() if key != "cases"}))
    _write_json(out_dir / "agent_source.json", {"source": source, "sha256": digest(source)})
    for entry in dataset["episodes"]:
        for repeat in range(repeats):
            row, trajectory = _run_case(agent, source, entry["seed"], entry["config"], repeat, action_budget)
            relative = Path("public") / (entry["slot"] + "-" + str(repeat) + ".jsonl")
            target = out_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            data = "".join(canonical(item) + "\n" for item in trajectory)
            target.write_text(data, encoding="utf-8")
            row.update({"slot": entry["slot"], "seed": entry["seed"], "config": entry["config"],
                        "trajectory": str(relative), "trajectory_sha256": digest(data)})
            run["cases"].append(row)
    path = out_dir / "run.json"
    _write_json(path, run)
    return path


def load_run(path: Path) -> tuple[dict, str]:
    run = _read_json(path)
    if run.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported run format")
    snapshot = _read_json(path.parent / "agent_source.json")
    source = snapshot["source"]
    if digest(source) != snapshot["sha256"] or digest(source) != run["agent"]["code_sha256"]:
        raise ValueError("agent snapshot digest mismatch")
    return run, source


def _pairs(left: dict, right: dict) -> list[tuple[dict, dict]]:
    for field in ("environment_version", "environment_sha256", "evaluator_sha256",
                  "runner_sha256", "dataset_sha256", "split", "repeats"):
        if left[field] != right[field]:
            raise ValueError("incompatible runs: " + field)
    lmap = {(c["slot"], c["repeat"]): c for c in left["cases"]}
    if left.get("mode", "official") != right.get("mode", "official") or \
            left.get("action_budget") != right.get("action_budget"):
        raise ValueError("incompatible runs: experimental budget")
    rmap = {(c["slot"], c["repeat"]): c for c in right["cases"]}
    if lmap.keys() != rmap.keys():
        raise ValueError("runs cover different cases")
    pairs = []
    for key in sorted(lmap):
        a, b = lmap[key], rmap[key]
        if (a["seed"], a["config"], a["episode_id"]) != (b["seed"], b["config"], b["episode_id"]):
            raise ValueError("case identity mismatch: " + str(key))
        pairs.append((a, b))
    return pairs


def compare(left_path: Path, right_path: Path) -> dict:
    left, _ = load_run(left_path)
    right, _ = load_run(right_path)
    rows = []
    for a, b in _pairs(left, right):
        measures = ("precision", "recall", "f1", "tp", "fp", "fn", "return",
                    "inspection_steps", "elapsed_seconds", "viewed_items", "viewed_bytes")
        row = {"slot": a["slot"], "repeat": a["repeat"], "episode_id": a["episode_id"]}
        for measure in measures:
            row[measure + "_before"] = a[measure]
            row[measure + "_after"] = b[measure]
            row["delta_" + measure] = b[measure] - a[measure]
        row["regressed"] = b["f1"] < a["f1"] or (b["f1"] == a["f1"] and b["fp"] > a["fp"])
        rows.append(row)
    summary = {measure: {side: sum(row[measure + "_" + side] for row in rows) / len(rows)
                         for side in ("before", "after")}
               for measure in measures} if rows else {}
    return {"before": left["agent"]["agent_version"], "after": right["agent"]["agent_version"],
            "mode": left.get("mode", "official"), "action_budget": left.get("action_budget"),
            "units": {"viewed_items": "returned records/documents including rereads",
                      "viewed_bytes": "UTF-8 canonical JSON bytes of returned records/documents including rereads"},
            "episodes": rows, "summary_mean": summary,
            "regressions": [r for r in rows if r["regressed"]]}


def _trajectory(path: Path, row: dict) -> list:
    relative = Path(row["trajectory"])
    target = (path.parent / relative).resolve()
    if not target.is_relative_to(path.parent.resolve()):
        raise ValueError("trajectory path escapes run directory")
    data = target.read_bytes()
    if digest(data) != row["trajectory_sha256"]:
        raise ValueError("trajectory digest mismatch")
    return [json.loads(line) for line in data.decode("utf-8").splitlines()]


def _independent_replay(path: Path, row: dict) -> None:
    """Verify expected metrics from saved actions through a fresh trusted evaluator."""
    actions = _trajectory(path, row)
    environment = make_environment(row["seed"], dict(row["config"], evaluation_mode="closed"),
                                   trusted_metrics=True)
    try:
        reset = environment.reset()
        if reset["info"]["episode_id"] != row["episode_id"]:
            raise ValueError("episode changed")
        for saved in actions:
            actual = environment.step(saved["action"])
            if actual["observation"] != saved["observation"] or actual["reward"] != saved["reward"]:
                raise ValueError("saved trajectory does not replay")
        if not environment.finalized:
            raise ValueError("saved trajectory is incomplete")
        metrics = environment.evaluator.metrics_for_completed_episode(environment)
    finally:
        environment.evaluator.close()
    for field in ("precision", "recall", "f1", "tp", "fp", "fn"):
        if getattr(metrics, field) != row[field]:
            raise ValueError("saved expected result failed independent replay: " + field)
    if metrics.return_ != row["return"]:
        raise ValueError("saved return failed independent replay")


def save_failure(reference_path: Path, failed_path: Path, slot: str, out: Path,
                 repeat: int = 0) -> Path:
    reference, _ = load_run(reference_path)
    failed, _ = load_run(failed_path)
    if reference["split"] != "train":
        raise ValueError("only train episodes may enter known-failure collection")
    if reference.get("mode", "official") != "official" or failed.get("mode", "official") != "official":
        raise ValueError("known failures require official runs")
    pairs = _pairs(reference, failed)
    found = [(a, b) for a, b in pairs if a["slot"] == slot and a["repeat"] == repeat]
    if len(found) != 1:
        raise ValueError("case not found")
    a, b = found[0]
    if not (b["f1"] < a["f1"] or (b["f1"] == a["f1"] and b["fp"] > a["fp"])):
        raise ValueError("selected case is not a regression")
    if reference["environment_sha256"] != files_digest(ENV_FILES) or \
            reference["evaluator_sha256"] != files_digest(EVAL_FILES) or \
            reference["runner_sha256"] != runner_digest():
        raise ValueError("current environment/evaluator differs; cannot verify expected result")
    _independent_replay(reference_path, a)
    _independent_replay(failed_path, b)
    case = {"format_version": FORMAT_VERSION, "kind": "known_failure",
            "slot": slot, "seed": a["seed"], "config": a["config"],
            "episode_id": a["episode_id"], "environment_version": reference["environment_version"],
            "environment_sha256": reference["environment_sha256"],
            "evaluator_sha256": reference["evaluator_sha256"],
            "runner_sha256": reference["runner_sha256"],
            "dataset_sha256": reference["dataset_sha256"],
            "expected": {key: a[key] for key in ("precision", "recall", "f1", "tp", "fp", "fn")},
            "reference_agent_version": reference["agent"]["agent_version"],
            "failed_agent_version": failed["agent"]["agent_version"],
            "failed_trajectory": _trajectory(failed_path, b)}
    if out.exists():
        raise FileExistsError(out)
    _write_json(out, case)
    return out


def _check_case_compat(case: dict) -> None:
    if case["config"]["dataset_profile"] != "train":
        raise ValueError("known failures must come from train")
    if case["environment_version"] != ENVIRONMENT_VERSION or \
            case["environment_sha256"] != files_digest(ENV_FILES) or \
            case["evaluator_sha256"] != files_digest(EVAL_FILES) or \
            case["runner_sha256"] != runner_digest():
        raise ValueError("known failure is incompatible with current environment/evaluator")


def test_failure(case_path: Path, version_path: Path, repeats: int = 1) -> dict:
    case = _read_json(case_path)
    _check_case_compat(case)
    run, source = load_run(version_path)
    if run.get("mode", "official") != "official":
        raise ValueError("known-failure checks require an official run")
    if not 1 <= repeats <= 20:
        raise ValueError("repeats must be in 1..20")
    if run["environment_sha256"] != case["environment_sha256"] or \
            run["evaluator_sha256"] != case["evaluator_sha256"] or \
            run["runner_sha256"] != case["runner_sha256"] or \
            run["dataset_sha256"] != case["dataset_sha256"]:
        raise ValueError("agent run is incompatible with known failure")
    results = []
    for repeat in range(repeats):
        row, _ = _run_case(run["agent"], source, case["seed"], case["config"], repeat)
        expected = case["expected"]
        passed = row["f1"] >= expected["f1"] and row["fp"] <= expected["fp"]
        results.append({"repeat": repeat, "passed": passed, "f1": row["f1"], "fp": row["fp"],
                        "elapsed_seconds": row["elapsed_seconds"]})
    verdict = "pass" if all(r["passed"] for r in results) else \
              "fail" if not any(r["passed"] for r in results) else "uncertain"
    return {"agent_version": run["agent"]["agent_version"], "verdict": verdict,
            "results": results}


def test_collection(directory: Path, version_path: Path, repeats: int = 1) -> dict:
    if not directory.is_dir():
        raise ValueError("known-failure collection directory does not exist")
    cases = sorted(directory.glob("*.json"))
    if not cases:
        raise ValueError("known-failure collection is empty")
    results = [{"case": path.name, **test_failure(path, version_path, repeats)} for path in cases]
    return {"cases": results, "summary": {
        verdict: sum(item["verdict"] == verdict for item in results)
        for verdict in ("pass", "fail", "uncertain")}}



def scan_history(case_path: Path, version_paths: list[Path], repeats: int = 1) -> dict:
    if len(version_paths) < 2:
        raise ValueError("at least two versions required")
    candidates = []
    for path in version_paths:
        result = test_failure(case_path, path, repeats)
        run, source = load_run(path)
        candidates.append({"run": str(path), "verdict": result["verdict"],
                           "results": result["results"], "agent_version": result["agent_version"],
                           "source": source, "agent": run["agent"]})
    transitions = []
    for before, after in zip(candidates, candidates[1:]):
        if before["verdict"] != after["verdict"] and "uncertain" not in (before["verdict"], after["verdict"]):
            left = before["source"].splitlines(keepends=True) + \
                   [canonical({k: before["agent"][k] for k in ("policy", "code_commit", "prompt", "settings", "model_id")}) + "\n"]
            right = after["source"].splitlines(keepends=True) + \
                    [canonical({k: after["agent"][k] for k in ("policy", "code_commit", "prompt", "settings", "model_id")}) + "\n"]
            transitions.append({"before": before["agent_version"], "after": after["agent_version"],
                                "change": before["verdict"] + "->" + after["verdict"],
                                "diff": "".join(difflib.unified_diff(left, right, fromfile=before["run"],
                                                                   tofile=after["run"]))})
    return {"versions": [{k: v for k, v in candidate.items() if k not in ("source", "agent")}
                         for candidate in candidates], "transitions": transitions,
            "pass_to_fail": [t for t in transitions if t["change"] == "pass->fail"],
            "note": "All available versions tested in order; uncertain results are not proven transitions."}


def restore_run(version_path: Path, out_dir: Path) -> Path:
    run, source = load_run(version_path)
    if run["environment_sha256"] != files_digest(ENV_FILES) or \
            run["evaluator_sha256"] != files_digest(EVAL_FILES) or \
            run["runner_sha256"] != runner_digest() or \
            run["dataset_sha256"] != digest(canonical(load_manifest(run["split"]))):
        raise ValueError("saved environment/evaluator/dataset unavailable")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError("restore output directory must be empty")
    restored = dict(run)
    restored["cases"] = []
    restored["restored_from"] = str(version_path)
    _write_json(out_dir / "agent_source.json", {"source": source, "sha256": digest(source)})
    for old in run["cases"]:
        row, trajectory = _run_case(run["agent"], source, old["seed"], old["config"], old["repeat"], run.get("action_budget"))
        relative = Path("public") / (old["slot"] + "-" + str(old["repeat"]) + ".jsonl")
        target = out_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = "".join(canonical(item) + "\n" for item in trajectory)
        target.write_text(data, encoding="utf-8")
        row.update({"slot": old["slot"], "seed": old["seed"], "config": old["config"],
                    "trajectory": str(relative), "trajectory_sha256": digest(data)})
        restored["cases"].append(row)
    path = out_dir / "run.json"
    _write_json(path, restored)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trusted offline agent-version lab")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--policy", choices=tuple(CLASS_NAMES), required=True)
    run.add_argument("--split", choices=("train", "dev"), default="train")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--prompt", default="")
    run.add_argument("--model-id")
    run.add_argument("--settings", default="{}")
    run.add_argument("--repeats", type=int, default=1)
    compare_cmd = commands.add_parser("compare")
    compare_cmd.add_argument("before", type=Path)
    compare_cmd.add_argument("after", type=Path)
    save = commands.add_parser("save-failure")
    run.add_argument("--action-budget", type=int)
    save.add_argument("reference", type=Path)
    save.add_argument("failed", type=Path)
    save.add_argument("slot")
    save.add_argument("--repeat", type=int, default=0)
    save.add_argument("--out", type=Path, required=True)
    test = commands.add_parser("test-failure")
    test.add_argument("case", type=Path)
    test.add_argument("version", type=Path)
    test.add_argument("--repeats", type=int, default=1)
    collection = commands.add_parser("test-collection")
    collection.add_argument("directory", type=Path)
    collection.add_argument("version", type=Path)
    collection.add_argument("--repeats", type=int, default=1)
    scan = commands.add_parser("scan")
    scan.add_argument("case", type=Path)
    scan.add_argument("versions", type=Path, nargs="+")
    scan.add_argument("--repeats", type=int, default=1)
    restore = commands.add_parser("restore")
    restore.add_argument("version", type=Path)
    restore.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        result = {"run": str(create_run(args.out, args.policy, args.split, args.prompt,
                                        json.loads(args.settings), args.model_id, args.repeats, args.action_budget))}
    elif args.command == "compare":
        result = compare(args.before, args.after)
    elif args.command == "save-failure":
        result = {"case": str(save_failure(args.reference, args.failed, args.slot,
                                           args.out, args.repeat))}
    elif args.command == "test-failure":
        result = test_failure(args.case, args.version, args.repeats)
    elif args.command == "test-collection":
        result = test_collection(args.directory, args.version, args.repeats)
    elif args.command == "scan":
        result = scan_history(args.case, args.versions, args.repeats)
    else:
        result = {"run": str(restore_run(args.version, args.out))}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
