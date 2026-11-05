"""Six benchmark workers: no evolution, three frozen skills, five fresh test runs."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reproduction.prepare_official_data import digest  # noqa: E402
from reproduction.run_experiments import MODEL, REPRO, credentials, install_request_audit, now, write_json  # noqa: E402
from reproduction.stability_metrics import VARIANTS, aggregate_items, collect_trial  # noqa: E402
from reproduction.tool_reasoning_compat import install_tool_reasoning  # noqa: E402
from skillopt.config import flatten_config, load_config  # noqa: E402

SOURCES = {
    "searchqa": "terra_high_20261104",
    "livemathematicianbench": "terra_high_20261104_reflection_fix",
    "docvqa": "terra_high_four_20261105",
    "officeqa": "terra_high_four_20261105",
    "spreadsheetbench": "terra_high_four_20261105",
    "alfworld": "terra_high_four_20261105",
}
BENCHMARKS = tuple(SOURCES)
TERMINAL = {"completed", "completed_with_errors", "failed", "incomplete"}


def read(path: Path):
    return json.loads(path.read_text())


def command(benchmark: str, *arguments: str) -> list[str]:
    return ["bash", str(REPRO / "benchmark_python.sh"), benchmark,
            str(Path(__file__).resolve()), *arguments, "--benchmark", benchmark]


def official_ids(benchmark: str) -> list[str]:
    kind = "path" if benchmark == "alfworld" else "id"
    return [str(row["id"]) for row in read(ROOT / "data" / f"{benchmark}_{kind}_split/test/items.json")]


def resource_check() -> dict:
    memory = {key: int(value.strip().split()[0]) * 1024 for key, value in
              (line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())}
    free = shutil.disk_usage(REPRO).free
    cpus = len(os.sched_getaffinity(0))
    if cpus < 6 or memory["MemAvailable"] < 8 * 1024**3 or free < 40 * 1024**3:
        raise RuntimeError("Insufficient resources for six workers: need 6 CPUs, 8 GiB available RAM, 40 GiB free disk")
    return {"checked_at": now(), "available_cpus": cpus,
            "available_memory_bytes": memory["MemAvailable"], "free_disk_bytes": free}


def snapshot(run_root: Path, benchmark: str) -> dict:
    source = REPRO / "outputs" / SOURCES[benchmark] / benchmark
    if read(source / "status.json")["phase"] not in ("completed", "completed_with_errors"):
        raise ValueError(f"Source experiment is not complete: {benchmark}")
    runtime = read(source / "runtime_state.json")
    initial = source / "skills/skill_v0000.md"
    final = Path(runtime["current_skill_path"])
    config = REPRO / "configs" / f"{benchmark}_terra_high.yaml"
    cfg = flatten_config(load_config(str(config)))
    previous = read(source / "config.json")
    keys = ("seed", "target_model", "reasoning_effort", "split_dir", "split_mode", "split_seed",
            "workers", "max_api_workers", "max_turns", "max_steps", "max_tool_turns", "max_completion_tokens",
            "exec_timeout", "test_env_num", "mode", "shuffle_choices", "use_theorem", "use_sketch",
            "search_mode", "use_local_tools", "data_dirs", "data_root")
    for key in keys:
        if cfg.get(key) != previous.get(key):
            raise ValueError(f"Evaluation setting differs from original experiment: {benchmark}/{key}")
    if cfg["target_model"] != MODEL or cfg["reasoning_effort"] != "high" or cfg["seed"] != 42 or cfg["test_env_num"]:
        raise ValueError("Expected Terra High, fixed seed 42, full test set")
    if digest(initial) != digest(ROOT / cfg["skill_init"]):
        raise ValueError("Saved initial skill differs from official initial skill")
    # Snapshots never acquire keys from environment; fail if a source YAML
    # contains an explicit credential instead of letting it leak into metadata.
    for key, value in cfg.items():
        if any(marker in key.lower() for marker in ("api_key", "password", "secret", "access_token")) and value:
            raise ValueError("Refusing to snapshot a configuration containing credentials")
    folder = run_root / "inputs" / benchmark
    folder.mkdir(parents=True)
    (folder / "none.md").write_bytes(b"")
    shutil.copyfile(initial, folder / "initial.md")
    shutil.copyfile(final, folder / "final.md")
    write_json(folder / "config.json", cfg)
    plan = {"benchmark": benchmark, "source_experiment": str(source.relative_to(ROOT)),
            "source_final_skill": str(final.relative_to(ROOT)), "source_initial_skill": str(initial.relative_to(ROOT)),
            "source_config_sha256": digest(config), "config_sha256": digest(folder / "config.json"),
            "skill_sha256": {variant: digest(folder / f"{variant}.md") for variant in VARIANTS},
            "test_ids": official_ids(benchmark), "test_count": len(official_ids(benchmark)),
            "test_manifest_sha256": digest(ROOT / "data" / f"{benchmark}_{'path' if benchmark == 'alfworld' else 'id'}_split/test/items.json"),
            "seed": 42, "repeats": 5, "model": MODEL, "reasoning_effort": "high",
            "evolution": False, "settings": {key: cfg.get(key) for key in keys}}
    write_json(folder / "plan.json", plan)
    return plan


def verify_inputs(run_root: Path, benchmark: str) -> dict:
    folder = run_root / "inputs" / benchmark
    plan = read(folder / "plan.json")
    if digest(folder / "config.json") != plan["config_sha256"]:
        raise ValueError("Frozen configuration changed")
    for variant in VARIANTS:
        if digest(folder / f"{variant}.md") != plan["skill_sha256"][variant]:
            raise ValueError("Frozen skill changed")
    if official_ids(benchmark) != plan["test_ids"]:
        raise ValueError("Official test manifest changed")
    return plan


def check(run_root: Path, benchmark: str) -> dict:
    verify_inputs(run_root, benchmark)
    if benchmark in ("searchqa", "livemathematicianbench"):
        from reproduction.prepare_official_data import prepare
    else:
        from reproduction.prepare_additional_data import prepare
    provenance = prepare(benchmark)
    from scripts.eval_only import get_adapter

    cfg = read(run_root / "inputs" / benchmark / "config.json")
    adapter = get_adapter(cfg)
    adapter.setup(cfg)
    loader = adapter.get_dataloader()
    rows = loader.load_split_items(str(Path(cfg["split_dir"]) / "test"))
    if [str(row["id"]) for row in rows] != official_ids(benchmark):
        raise ValueError("Prepared dataset differs from official test IDs/order")
    return {"benchmark": benchmark, "checked_at": now(), "test_count": len(rows),
            "data": provenance, "python": sys.version, "status": "ready"}


def trial(run_root: Path, benchmark: str, variant: str, repeat: int, smoke: bool) -> None:
    plan = verify_inputs(run_root, benchmark)
    parent = run_root / benchmark / ("smoke" if smoke else "evaluations")
    directory = parent / f"repeat_{repeat:02d}" / variant
    if directory.exists():
        raise ValueError("Trial output already exists; cached evaluations cannot count as fresh repeats")
    directory.mkdir(parents=True)
    state = {"benchmark": benchmark, "variant": variant, "repeat": repeat, "smoke": smoke,
             "phase": "running", "started_at": now(), "seed": 42}
    write_json(directory / "status.json", state)
    try:
        base, key = credentials()
        from skillopt.model import azure_openai

        azure_openai.configure_azure_openai(endpoint=base, api_key=key, auth_mode="openai_compatible")
        install_tool_reasoning()
        install_request_audit(directory / "request_metadata.jsonl")
        factory = azure_openai._make_client

        def target_only_factory(role):
            if role != "target":
                raise RuntimeError("Evolution/optimizer requests are forbidden in stability evaluation")
            return factory(role)

        azure_openai._make_client = target_only_factory
        from scripts import eval_only

        inputs = run_root / "inputs" / benchmark
        sys.argv = ["scripts/eval_only.py", "--config", str(inputs / "config.json"),
                    "--skill", str(inputs / f"{variant}.md"), "--split", "valid_unseen",
                    "--out_root", str(directory)]
        if smoke:
            sys.argv += ["--test_env_num", "1"]
        expected = plan["test_ids"] if not smoke else plan["test_ids"][:1]
        write_json(directory / "run_metadata.json", {
            **state, "config_sha256": plan["config_sha256"], "skill_sha256": plan["skill_sha256"][variant],
            "expected_test_ids": expected, "model": MODEL, "reasoning_effort": "high",
            "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "fresh_process": True, "optimizer_calls_allowed": False,
        })
        eval_only.main()
        metrics = collect_trial(directory, benchmark, expected)
        write_json(directory / "trial_metrics.json", metrics)
        state.update(phase="completed_with_errors" if metrics["execution_failures"] else "completed", finished_at=now())
    except BaseException as error:
        state.update(phase="failed", error_type=type(error).__name__, finished_at=now())
        raise
    finally:
        write_json(directory / "status.json", state)


def schedule() -> list[tuple[int, str]]:
    # Rotate the version order between rounds, with the same order for every
    # benchmark. This spreads the versions over time while retaining seed 42.
    return [(repeat, VARIANTS[(index + repeat - 1) % 3]) for repeat in range(1, 6) for index in range(3)]


def collect(run_root: Path, benchmark: str) -> dict:
    plan = verify_inputs(run_root, benchmark)
    folder = run_root / benchmark
    report = {"benchmark": benchmark, "updated_at": now(), "model": MODEL, "reasoning_effort": "high",
              "seed": 42, "evolution": False, "avg_at_5_definition": "mean accuracy of five fresh full-test runs, not pass@5",
              "test_count": plan["test_count"], "variants": {}}
    completed = 0
    for variant in VARIANTS:
        trials = [folder / "evaluations" / f"repeat_{repeat:02d}" / variant for repeat in range(1, 6)]
        available = [path for path in trials if (path / "trial_metrics.json").exists()]
        completed += len(available)
        item = {"completed_repeats": len(available), "required_repeats": 5,
                "trials": [{"repeat": int(path.parent.name.split("_")[-1]), **read(path / "trial_metrics.json")} for path in available]}
        if len(available) == 5:
            repetitions = [[json.loads(line) for line in (path / "item_fingerprints.jsonl").read_text().splitlines() if line] for path in trials]
            metrics, rows = aggregate_items(repetitions, plan["test_ids"])
            item.update(metrics)
            with (folder / f"{variant}_stability.jsonl").open("w") as stream:
                for row in rows:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        report["variants"][variant] = item
    report["completed_evaluations"] = completed
    report["required_evaluations"] = 15
    write_json(folder / "stability_summary.json", report)
    if completed == 15:
        lines = [f"# {benchmark}: Terra High avg@5", "", "Only official test split; fixed seed 42; no skill evolution.", "",
                 "| Skill | Avg@5 hard | Sample std | Always success | Always failure | Mixed | Execution failures |",
                 "|---|---:|---:|---:|---:|---:|---:|"]
        for variant, metrics in report["variants"].items():
            lines.append(f"| {variant} | {metrics['avg_at_5_hard']:.6f} | {metrics['hard_sample_std']:.6f} | {metrics['always_success']} | {metrics['always_failure']} | {metrics['mixed']} | {metrics['execution_failures']} |")
        lines += ["", "All failed executions remain in the accuracy denominator. Five-clean-execution statistics are reported separately.",
                  "Exact transcript variation can reflect wording changes; behavior hashes and turn counts are also retained.",
                  "Prompt fingerprints remove run output paths only. ALFWorld's official logger does not save initial prompts."]
        (folder / "REPORT.md").write_text("\n".join(lines) + "\n")
    return report


def worker(run_root: Path, benchmark: str) -> None:
    folder = run_root / benchmark
    folder.mkdir(exist_ok=True)
    with (folder / "worker.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = {"benchmark": benchmark, "started_at": now(), "phase": "running", "completed_evaluations": 0}
        failures = 0
        try:
            with (folder / "requirements.txt").open("w") as stream:
                subprocess.run([sys.executable, "-m", "pip", "freeze"], stdout=stream, check=True)
            for repeat, variant in schedule():
                state.update(phase="evaluating", repeat=repeat, variant=variant, updated_at=now())
                write_json(folder / "status.json", state)
                path = folder / "evaluations" / f"repeat_{repeat:02d}" / variant
                log = folder / f"repeat_{repeat:02d}_{variant}.log"
                print(f"[{now()}] START repeat={repeat} skill={variant}", flush=True)
                with log.open("x") as stream:
                    result = subprocess.run(command(benchmark, "trial", "--run-root", str(run_root),
                                                    "--variant", variant, "--repeat", str(repeat)), cwd=ROOT,
                                            stdout=stream, stderr=subprocess.STDOUT)
                if result.returncode:
                    failures += 1
                    print(f"[{now()}] FAILED repeat={repeat} skill={variant}; retained original failure", flush=True)
                else:
                    metrics = read(path / "trial_metrics.json")
                    print(f"[{now()}] DONE repeat={repeat} skill={variant} hard={metrics['hard']:.6f} errors={metrics['execution_failures']}", flush=True)
                report = collect(run_root, benchmark)
                state["completed_evaluations"] = report["completed_evaluations"]
            report = collect(run_root, benchmark)
            errors = sum(trial["execution_failures"] for item in report["variants"].values() for trial in item["trials"])
            state.update(phase="incomplete" if failures else "completed_with_errors" if errors else "completed", finished_at=now())
        except BaseException as error:
            state.update(phase="failed", error_type=type(error).__name__, finished_at=now())
            traceback.print_exc()
            raise
        finally:
            write_json(folder / "status.json", state)


def run_check(run_root: Path, benchmark: str) -> dict:
    result = subprocess.run(command(benchmark, "check", "--run-root", str(run_root)), cwd=ROOT,
                            capture_output=True, text=True, check=True)
    # Last line is a compact JSON object; data verification can print before it.
    return json.loads(result.stdout.splitlines()[-1])


def smoke_one(run_root: Path, benchmark: str) -> dict:
    folder = run_root / benchmark
    folder.mkdir()
    results = {}
    for variant in VARIANTS:
        with (folder / f"smoke_{variant}.log").open("x") as stream:
            subprocess.run(command(benchmark, "trial", "--run-root", str(run_root), "--variant", variant,
                                   "--repeat", "1", "--smoke"), cwd=ROOT, stdout=stream,
                           stderr=subprocess.STDOUT, check=True)
        metrics = read(folder / "smoke/repeat_01" / variant / "trial_metrics.json")
        # Wrong answers/code are genuine task failures; an API/adapter failure
        # at this stage means the worker wiring has not yet been verified.
        blocked = set(metrics["failure_flags"]) - {"code_ok", "exec_ok"}
        if blocked:
            raise RuntimeError(f"Smoke API/adapter failure for {benchmark}/{variant}; inspect local logs")
        results[variant] = metrics
    print(f"Smoke passed: {benchmark} (three skill variants)", flush=True)
    return {"benchmark": benchmark, "checked_at": now(), "variants": results}


def start(run_root: Path) -> None:
    if run_root.exists():
        raise ValueError("Choose a fresh run root; repetitions must not reuse cached outputs")
    credentials()
    resources = resource_check()
    run_root.mkdir(parents=True)
    plans = {benchmark: snapshot(run_root, benchmark) for benchmark in BENCHMARKS}
    with ThreadPoolExecutor(max_workers=6) as pool:
        checks = list(pool.map(lambda name: run_check(run_root, name), BENCHMARKS))
    write_json(run_root / "preflight.json", {"resources": resources, "checks": checks})
    # All 18 real-API checks finish before any of the 90 full evaluations start.
    with ThreadPoolExecutor(max_workers=6) as pool:
        smokes = list(pool.map(lambda name: smoke_one(run_root, name), BENCHMARKS))
    write_json(run_root / "smoke_checks.json", {"checked_at": now(), "checks": smokes})
    resource_check()
    socket = run_root / "tmux.sock"
    launch = {"created_at": now(), "socket": str(socket), "model": MODEL, "reasoning_effort": "high",
              "seed": 42, "repeats": 5, "variants": list(VARIANTS), "evolution": False,
              "total_item_executions": sum(plan["test_count"] for plan in plans.values()) * 15,
              "schedule": schedule(), "resources": resources, "benchmarks": {}}
    for benchmark in BENCHMARKS:
        session = f"max-avg5-{benchmark}"
        log = run_root / benchmark / "worker.log"
        shell = shlex.join(command(benchmark, "worker", "--run-root", str(run_root)))
        shell += " >> " + shlex.quote(str(log)) + " 2>&1"
        launch["benchmarks"][benchmark] = {"session": session, "directory": str(run_root / benchmark), "launched": False}
        write_json(run_root / "launch.json", launch)
        subprocess.run(["tmux", "-f", "/dev/null", "-S", str(socket), "new-session", "-d", "-s", session, shell], cwd=ROOT, check=True)
        launch["benchmarks"][benchmark]["launched"] = True
        write_json(run_root / "launch.json", launch)
        print(f"Started full avg@5: {benchmark}", flush=True)


def status(run_root: Path) -> None:
    launch = read(run_root / "launch.json")
    for benchmark, item in launch["benchmarks"].items():
        folder = Path(item["directory"])
        state = read(folder / "status.json") if (folder / "status.json").exists() else {"phase": "starting"}
        alive = subprocess.run(["tmux", "-S", launch["socket"], "has-session", "-t", item["session"]], capture_output=True).returncode == 0
        if not alive and state["phase"] not in TERMINAL:
            state["phase"] = "interrupted"
        directory = folder / "evaluations" / f"repeat_{state.get('repeat', 1):02d}" / state.get("variant", "none")
        audit = directory / "request_metadata.jsonl"
        requests = sum(1 for _ in audit.open()) if audit.exists() else 0
        print(json.dumps({**state, "session_alive": alive, "required_evaluations": 15,
                          "current_requests": requests, "test_items_per_evaluation": len(official_ids(benchmark))}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "check", "worker", "trial", "status", "collect"))
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--benchmark", choices=BENCHMARKS)
    parser.add_argument("--variant", choices=VARIANTS)
    parser.add_argument("--repeat", type=int, choices=range(1, 6))
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    root = args.run_root.resolve()
    if not root.is_relative_to(REPRO / "outputs"):
        parser.error("Keep all stability outputs under reproduction/outputs/")
    if args.command in ("check", "worker", "trial", "collect") and not args.benchmark:
        parser.error("--benchmark is required")
    if args.command == "start":
        start(root)
    elif args.command == "status":
        status(root)
    elif args.command == "check":
        print(json.dumps(check(root, args.benchmark), ensure_ascii=False))
    elif args.command == "worker":
        worker(root, args.benchmark)
    elif args.command == "collect":
        print(json.dumps(collect(root, args.benchmark), ensure_ascii=False))
    else:
        if not args.variant or args.repeat is None:
            parser.error("trial requires --variant and --repeat")
        trial(root, args.benchmark, args.variant, args.repeat, args.smoke)


if __name__ == "__main__":
    main()
