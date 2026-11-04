"""Validate, launch and inspect four concurrent official Terra High pipelines."""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reproduction.prepare_official_data import digest  # noqa: E402
from reproduction.run_experiments import MODEL, REPRO, credentials, now, write_json  # noqa: E402

BENCHMARKS = ("docvqa", "officeqa", "spreadsheetbench", "alfworld")
SMOKES = REPRO / "artifacts/readiness_20261105"
TERMINAL = {"completed", "completed_with_errors", "failed"}


def validate_smoke(benchmark: str) -> dict:
    report_path = SMOKES / f"{benchmark}_smoke.json"
    report = json.loads(report_path.read_text())
    if (report["benchmark"] != benchmark or report["model"] != MODEL or
            report["reasoning_effort"] != "high" or not report["all_requests_match"] or
            report["model_tool_errors"] != 0 or not report["token_summary"]["analyst"]["calls"]):
        raise ValueError(f"Missing successful real-API smoke: {benchmark}")
    raw_root = "readiness_20261105_officeqa_v2" if benchmark == "officeqa" else "readiness_20261105_v2"
    raw = REPRO / "outputs" / raw_root / benchmark
    metadata = json.loads((raw / "run_metadata.json").read_text())
    if metadata["config_sha256"] != digest(REPRO / "configs" / f"{benchmark}_terra_high.yaml"):
        raise ValueError(f"Configuration changed since smoke: {benchmark}")
    if json.loads((raw / "status.json").read_text())["phase"] != "completed":
        raise ValueError(f"Smoke did not complete: {benchmark}")
    if json.loads((raw / "smoke_report.json").read_text()) != report:
        raise ValueError(f"Published smoke differs from actual run: {benchmark}")
    return {"report": str(report_path.relative_to(ROOT)), "sha256": digest(report_path),
            "request_attempts": report["request_attempts"], "model_tool_errors": 0}


def check_one(benchmark: str) -> dict:
    smoke = validate_smoke(benchmark)
    command = ["bash", str(REPRO / "benchmark_python.sh"), benchmark,
               str(REPRO / "run_benchmark.py"), "check", benchmark]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)
    # Data verification may print a short line before the JSON object.
    lines = result.stdout.splitlines()
    json_start = next(index for index, line in enumerate(lines) if line.startswith("{"))
    ready = json.loads("\n".join(lines[json_start:]))
    if ready["status"] != "ready" or ready["model"] != MODEL or ready["reasoning_effort"] != "high":
        raise ValueError(f"Readiness mismatch: {benchmark}")
    return {"benchmark": benchmark, "checked_at": now(), "smoke": smoke, "environment_and_data": ready}


def alive(socket: Path, session: str) -> bool:
    return subprocess.run(["tmux", "-S", str(socket), "has-session", "-t", session],
                          capture_output=True).returncode == 0


def start(run_root: Path) -> None:
    if (run_root / "launch.json").exists():
        raise ValueError("Run already launched; inspect status or choose a new run root.")
    if any((run_root / name).exists() for name in BENCHMARKS):
        raise ValueError("Benchmark output already exists; choose a new run root.")
    credentials()
    # All checks must pass before any benchmark can start using the API.
    with ThreadPoolExecutor(max_workers=4) as pool:
        checks = list(pool.map(check_one, BENCHMARKS))
    run_root.mkdir(parents=True, exist_ok=True)
    write_json(run_root / "preflight_checks.json", {"checked_at": now(), "checks": checks})
    # A separate tmux server inherits current credentials in memory, including
    # updated keys. No secrets are passed in command arguments or written to disk.
    socket = run_root / "tmux.sock"
    launch = {"created_at": now(), "model": MODEL, "reasoning_effort": "high",
              "scope": "official full splits, 4 epochs, baseline/best/final evaluation",
              "socket": str(socket), "benchmarks": {}}
    for benchmark in BENCHMARKS:
        session = f"max-skillopt-{benchmark}"
        directory = run_root / benchmark
        log = run_root / f"{benchmark}.log"
        command = shlex.join(["bash", str(REPRO / "benchmark_python.sh"), benchmark,
                              str(REPRO / "run_benchmark.py"), "train", benchmark,
                              "--out-root", str(directory)])
        command += " >> " + shlex.quote(str(log)) + " 2>&1"
        item = {"session": session, "directory": str(directory), "log": str(log)}
        launch["benchmarks"][benchmark] = {**item, "launched": False}
        write_json(run_root / "launch.json", launch)
        subprocess.run(["tmux", "-f", "/dev/null", "-S", str(socket), "new-session",
                        "-d", "-s", session, command], cwd=ROOT, check=True)
        launch["benchmarks"][benchmark]["launched"] = True
        write_json(run_root / "launch.json", launch)
        print(f"Started {benchmark}: {directory}", flush=True)


def inspect(run_root: Path) -> None:
    launch = json.loads((run_root / "launch.json").read_text())
    for benchmark, item in launch["benchmarks"].items():
        directory = Path(item["directory"])
        state = json.loads((directory / "status.json").read_text()) if (directory / "status.json").exists() else {"phase": "starting"}
        session_alive = alive(Path(launch["socket"]), item["session"])
        if not session_alive and state["phase"] not in TERMINAL:
            state["phase"] = "interrupted"
        history_path = directory / "history.json"
        history = json.loads(history_path.read_text()) if history_path.exists() else []
        # Rollouts accumulate one row per completed item; reading only results
        # files also works while a rollout is still in progress.
        progress = {}
        for path in sorted(directory.rglob("results.jsonl")):
            with path.open() as stream:
                progress[str(path.parent.relative_to(directory))] = sum(bool(line.strip()) for line in stream)
        audit = directory / "request_metadata.jsonl"
        attempts = 0
        if audit.exists():
            with audit.open() as stream:
                attempts = sum(bool(line.strip()) for line in stream)
        print(json.dumps({**state, "benchmark": benchmark, "session_alive": session_alive,
                          "completed_steps": len(history), "request_attempts": attempts,
                          "completed_items": progress}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "start", "status"))
    parser.add_argument("--run-root", type=Path)
    args = parser.parse_args()
    if args.command == "check":
        with ThreadPoolExecutor(max_workers=4) as pool:
            for result in pool.map(check_one, BENCHMARKS):
                print(json.dumps(result, ensure_ascii=False))
        return
    if args.run_root is None:
        parser.error("--run-root is required for start/status")
    run_root = args.run_root.resolve()
    if not run_root.is_relative_to(REPRO / "outputs"):
        parser.error("Keep run roots under reproduction/outputs/")
    if args.command == "start":
        start(run_root)
    else:
        inspect(run_root)


if __name__ == "__main__":
    main()
