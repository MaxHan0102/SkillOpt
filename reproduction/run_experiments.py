"""Start, resume and inspect two complete Terra High benchmark pipelines."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shlex
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reproduction.prepare_official_data import BENCHMARKS, digest, prepare  # noqa: E402

REPRO = ROOT / "reproduction"
SOCKET = REPRO / ".cache/experiments.sock"
MODEL = "gpt-6.1-terra"


def now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def credentials() -> tuple[str, str]:
    # Read simple KEY=value settings without printing or persisting credentials.
    env_file = REPRO / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                key, separator, value = line.partition("=")
                if separator:
                    os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    base = os.environ.get("OPENAI_COMPATIBLE_BASE_URL") or os.environ.get("SUB2API_BASE_URL", "")
    key = os.environ.get("OPENAI_COMPATIBLE_API_KEY") or os.environ.get("MAXAPI_API_KEY", "")
    if not base or not key:
        raise ValueError("Set OPENAI_COMPATIBLE_BASE_URL and OPENAI_COMPATIBLE_API_KEY (or MAXAPI_API_KEY)")
    # Role-specific Azure settings must not silently override the selected gateway.
    for prefix in ("", "OPTIMIZER_", "TARGET_"):
        os.environ[prefix + "AZURE_OPENAI_ENDPOINT"] = base
        os.environ[prefix + "AZURE_OPENAI_API_KEY"] = key
        os.environ[prefix + "AZURE_OPENAI_AUTH_MODE"] = "openai_compatible"
    return base, key


def install_request_audit(path: Path) -> None:
    """Check the actual outgoing payload; record only non-sensitive metadata."""
    from skillopt.model import azure_openai

    original_factory = azure_openai._make_client
    lock = threading.Lock()

    def audit(request) -> None:
        payload = json.loads(request.content)
        if payload.get("model") != MODEL or payload.get("reasoning_effort") != "high":
            raise ValueError("Refusing a request without gpt-6.1-terra and high reasoning")
        metadata = {
            "time": now(), "path": request.url.path,
            "model": payload["model"], "reasoning_effort": payload["reasoning_effort"],
            "max_completion_tokens": payload.get("max_completion_tokens"),
        }
        with lock, path.open("a") as output:
            output.write(json.dumps(metadata) + "\n")

    def audited_factory(role):
        client = original_factory(role)
        client._client.event_hooks.setdefault("request", []).append(audit)
        return client

    azure_openai._make_client = audited_factory


def preflight() -> dict:
    from skillopt.model import azure_openai

    base, key = credentials()
    azure_openai.configure_azure_openai(endpoint=base, api_key=key, auth_mode="openai_compatible")
    azure_openai.set_optimizer_deployment(MODEL)
    azure_openai.set_reasoning_effort("high")
    answer, usage = azure_openai.chat_optimizer(
        system="Reply briefly.", user="Reply with exactly OK.",
        max_completion_tokens=256, retries=1, timeout=90,
    )
    if answer.strip() != "OK":
        raise RuntimeError("Preflight returned an unexpected answer")
    return {"time": now(), "model": MODEL, "reasoning_effort": "high", "answer": answer, "usage": usage}


def collect(run_dir: Path, benchmark: str) -> dict:
    summary = json.loads((run_dir / "summary.json").read_text())
    results = {}
    for name, directory in (("baseline", "test_eval_baseline"), ("best", "test_eval"), ("final", "test_eval_final")):
        path = run_dir / directory / "results.jsonl"
        reused_from = None
        # The official trainer reuses best-test scores without writing rows
        # when final and best skills are byte-identical.
        if name == "final" and not path.exists():
            runtime = json.loads((run_dir / "runtime_state.json").read_text())
            if digest(Path(runtime["current_skill_path"])) != digest(run_dir / "best_skill.md"):
                raise ValueError("Missing final results for a distinct final skill")
            path = run_dir / "test_eval/results.jsonl"
            reused_from = "best"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line]
        expected = json.loads((ROOT / "data" / f"{benchmark}_id_split/test/items.json").read_text())
        if len(rows) != len(expected) or {str(row["id"]) for row in rows} != {str(row["id"]) for row in expected}:
            raise ValueError(f"Incomplete test coverage: {name}")
        results[name] = {
            "n": len(rows), "hard": sum(row["hard"] for row in rows) / len(rows),
            "soft": sum(row["soft"] for row in rows) / len(rows),
            "api_failures": sum(row.get("agent_ok") is False for row in rows),
        }
        if reused_from:
            results[name]["reused_from"] = reused_from
    for name, key in (("baseline", "baseline_test_hard"), ("best", "test_hard"), ("final", "final_test_hard")):
        if abs(results[name]["hard"] - summary[key]) > 1e-12:
            raise ValueError(f"Summary aggregation mismatch: {name}")
    report = {
        "benchmark": benchmark, "model": MODEL, "reasoning_effort": "high",
        "completed_at": now(), "test": results,
        **{key: summary[key] for key in ("baseline_selection_hard", "best_selection_hard", "total_steps", "total_accepts", "total_rejects", "token_summary", "total_wall_time_s")},
    }
    write_json(run_dir / "recorded_results.json", report)
    lines = [f"# {benchmark}: GPT-6.1 Terra / High", "", "| Skill | Test count | EM/accuracy | F1/soft | API failures |", "|---|---:|---:|---:|---:|"]
    for name, score in results.items():
        lines.append(f"| {name} | {score['n']} | {score['hard']:.6f} | {score['soft']:.6f} | {score['api_failures']} |")
    lines += ["", f"Steps: {summary['total_steps']}; accepted: {summary['total_accepts']}; rejected: {summary['total_rejects']}.",
              "", "Official ID splits and optimizer flow; custom model and operational concurrency/timeouts. This is a new experiment, not the paper's original model result."]
    (run_dir / "REPORT.md").write_text("\n".join(lines) + "\n")
    return report


def worker(benchmark: str, run_root: Path) -> None:
    os.chdir(ROOT)
    directory = run_root / benchmark
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "worker.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = {"benchmark": benchmark, "model": MODEL, "reasoning_effort": "high", "started_at": now()}

        def phase(name: str, **extra) -> None:
            state.update(phase=name, updated_at=now(), **extra)
            write_json(directory / "status.json", state)

        try:
            phase("preparing_data")
            credentials()
            provenance = prepare(benchmark)
            config = REPRO / "configs" / f"{benchmark}_terra_high.yaml"
            write_json(directory / "run_metadata.json", {
                "model": MODEL, "reasoning_effort": "high", "started_at": now(),
                "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "config": str(config.relative_to(ROOT)), "config_sha256": digest(config),
                "data": provenance, "python": sys.version,
            })
            subprocess.run([sys.executable, "-m", "pip", "freeze"], stdout=(directory / "requirements.txt").open("w"), check=True)
            install_request_audit(directory / "request_metadata.jsonl")
            phase("training_and_evaluation")
            from scripts import train

            sys.argv = ["scripts/train.py", "--config", str(config), "--out_root", str(directory)]
            train.main()
            phase("collecting_results")
            report = collect(directory, benchmark)
            failures = sum(item["api_failures"] for item in report["test"].values())
            phase("completed_with_errors" if failures else "completed", finished_at=now())
        except BaseException as error:
            phase("failed", error_type=type(error).__name__, finished_at=now())
            traceback.print_exc()
            raise


def tmux_alive(name: str) -> bool:
    return subprocess.run(["tmux", "-S", str(SOCKET), "has-session", "-t", name], capture_output=True).returncode == 0


def start(run_root: Path, resume: bool, benchmarks=BENCHMARKS) -> None:
    os.chdir(ROOT)
    credentials()
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    registry = run_root / "launch.json"
    if registry.exists() and not resume:
        raise ValueError("Run already exists; use --resume or choose a new --run-root")
    run_root.mkdir(parents=True, exist_ok=True)
    launch = json.loads(registry.read_text()) if registry.exists() else {
        "created_at": now(), "model": MODEL, "reasoning_effort": "high", "benchmarks": {},
    }
    for benchmark in benchmarks:
        directory = run_root / benchmark
        directory.mkdir(exist_ok=True)
        name = f"max-skillopt-{benchmark}-{run_root.name}"
        launch["benchmarks"][benchmark] = {"session": name, "directory": str(directory)}
        status_path = directory / "status.json"
        if tmux_alive(name) or (status_path.exists() and json.loads(status_path.read_text())["phase"] == "completed"):
            print(f"Already running/completed: {benchmark}")
            continue
        command = shlex.join([sys.executable, "-u", str(Path(__file__).resolve()), "worker", "--benchmark", benchmark, "--run-root", str(run_root)])
        command += " >> " + shlex.quote(str(directory / "console.log")) + " 2>&1"
        subprocess.run(["tmux", "-f", "/dev/null", "-S", str(SOCKET), "new-session", "-d", "-s", name, command], check=True)
        print(f"Started {benchmark}: {directory}")
    write_json(registry, launch)


def status(run_root: Path) -> None:
    launch = json.loads((run_root / "launch.json").read_text())
    for benchmark, item in launch["benchmarks"].items():
        directory = Path(item["directory"])
        state = json.loads((directory / "status.json").read_text()) if (directory / "status.json").exists() else {"phase": "starting"}
        history = json.loads((directory / "history.json").read_text()) if (directory / "history.json").exists() else []
        alive = tmux_alive(item["session"])
        if not alive and state["phase"] not in ("completed", "completed_with_errors", "failed"):
            state["phase"] = "interrupted"
        print(json.dumps({"benchmark": benchmark, "session_alive": alive, "completed_steps": len(history), **state}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "prepare", "start", "worker", "status"))
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--benchmark", choices=BENCHMARKS)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    default = REPRO / "outputs" / ("terra_high_" + datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S"))
    run_root = (args.run_root or default).resolve()
    if args.command == "preflight":
        print(json.dumps(preflight(), ensure_ascii=False))
    elif args.command == "prepare":
        for benchmark in [args.benchmark] if args.benchmark else BENCHMARKS:
            prepare(benchmark)
    elif args.command == "start":
        start(run_root, args.resume, [args.benchmark] if args.benchmark else BENCHMARKS)
    elif args.command == "worker":
        if not args.benchmark:
            parser.error("worker requires --benchmark")
        worker(args.benchmark, run_root)
    else:
        if not args.run_root:
            parser.error("status requires --run-root")
        status(run_root)


if __name__ == "__main__":
    main()
