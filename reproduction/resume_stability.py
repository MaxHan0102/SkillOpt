"""Resume a stability run: execute only the (repeat, variant) trials that are missing.

The stock `worker` entry point refuses to restart on an existing run root: logs are
opened with mode "x" and `trial` rejects pre-existing output directories. This entry
point enumerates completed trials from `trial_metrics.json`, runs only the gaps, and
appends to existing logs. Partial directories are archived before explicitly rerunning
an interrupted trial. Completed trials are never replaced.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reproduction.prepare_official_data import digest  # noqa: E402
from reproduction.run_experiments import credentials, now, write_json  # noqa: E402
from reproduction.run_stability import BENCHMARKS, REPRO, ROOT, collect, command, read, schedule, verify_inputs  # noqa: E402

TOTAL = 15


def completed(folder: Path) -> set[tuple[int, str]]:
    done: set[tuple[int, str]] = set()
    for metrics in folder.glob("evaluations/repeat_*/*/trial_metrics.json"):
        directory = metrics.parent
        repeat = int(directory.parent.name.split("_")[1])
        done.add((repeat, directory.name))
    return done


def missing(folder: Path) -> list[tuple[int, str]]:
    done = completed(folder)
    return [(repeat, variant) for repeat, variant in schedule() if (repeat, variant) not in done]


def partial(folder: Path) -> list[Path]:
    return [d for d in sorted(folder.glob("evaluations/repeat_*/*/"))
            if d.is_dir() and not (d / "trial_metrics.json").exists()]


def prepare_alfworld_restart(source: Path, destination: Path, workers: int) -> dict:
    if destination.exists():
        raise ValueError("ALFWorld restart requires a new output root")
    previous = verify_inputs(source, "alfworld")
    inputs = destination / "inputs/alfworld"
    inputs.parent.mkdir(parents=True)
    shutil.copytree(source / "inputs/alfworld", inputs)
    cfg = read(inputs / "config.json")
    original = {key: cfg[key] for key in ("workers", "max_api_workers")}
    cfg.update(workers=workers, max_api_workers=workers)
    write_json(inputs / "config.json", cfg)
    plan = {**previous, "config_sha256": digest(inputs / "config.json"),
            "settings": {**previous["settings"], "workers": workers, "max_api_workers": workers},
            "restart_of": str(source.relative_to(ROOT)), "restarted_at": now(),
            "concurrency_change": {"previous": original, "workers": workers, "max_api_workers": workers},
            "credential_source": "reproduction/.env"}
    write_json(inputs / "plan.json", plan)
    return plan


def resume(run_root: Path, benchmark: str, archive_partial: bool) -> None:
    verify_inputs(run_root, benchmark)
    folder = run_root / benchmark
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        todo = missing(folder)
        stale = partial(folder)
        if stale and not archive_partial:
            raise ValueError("Partial trials exist; pass --archive-partial to preserve them and rerun those trials")
        stamp = now().replace(":", "").replace("+", "_")
        for directory in stale:
            archived = folder / "interrupted_archive" / stamp / directory.relative_to(folder)
            archived.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(directory), str(archived))
        state = {"benchmark": benchmark, "phase": "resuming", "resumed_at": now(),
                 "completed_evaluations": TOTAL - len(todo), "remaining_at_resume": len(todo),
                 "credential_source": "reproduction/.env" if os.environ.get("SKILLOPT_CREDENTIAL_SOURCE") == "env_file" else "environment"}
        failures = 0
        try:
            credentials()
            with (folder / f"requirements_resume_{stamp}.txt").open("w") as stream:
                subprocess.run([sys.executable, "-m", "pip", "freeze"], stdout=stream, check=True)
            for repeat, variant in todo:
                state.update(phase="evaluating", repeat=repeat, variant=variant, updated_at=now())
                write_json(folder / "status.json", state)
                log = folder / f"repeat_{repeat:02d}_{variant}.log"
                print(f"[{now()}] START repeat={repeat} skill={variant}", flush=True)
                with log.open("a") as stream:
                    result = subprocess.run(command(benchmark, "trial", "--run-root", str(run_root),
                                                    "--variant", variant, "--repeat", str(repeat)),
                                            cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
                failures += result.returncode != 0
                print(f"[{now()}] {'FAILED' if result.returncode else 'DONE'} repeat={repeat} skill={variant}", flush=True)
                report = collect(run_root, benchmark)
                state["completed_evaluations"] = report["completed_evaluations"]
            report = collect(run_root, benchmark)
            errors = sum(trial["execution_failures"] for item in report["variants"].values() for trial in item["trials"])
            state.update(phase="incomplete" if failures else "completed_with_errors" if errors else "completed", finished_at=now())
        except BaseException as error:
            state.update(phase="failed", error_type=type(error).__name__, finished_at=now())
            raise
        finally:
            write_json(folder / "status.json", state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--benchmark", required=True, choices=BENCHMARKS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--drop-partial", action="store_true")
    parser.add_argument("--archive-partial", action="store_true")
    parser.add_argument("--env-file-only", action="store_true", help="Use only credentials in reproduction/.env, with no environment fallback")
    parser.add_argument("--fresh-from", type=Path, help="Restart ALFWorld in a new run root using these frozen inputs")
    parser.add_argument("--workers", type=int, default=8, choices=range(1, 17))
    args = parser.parse_args()
    os.chdir(ROOT)
    args.run_root = args.run_root.resolve()
    if not args.run_root.is_relative_to(REPRO / "outputs"):
        parser.error("Keep run roots under reproduction/outputs/")
    if args.env_file_only:
        os.environ["SKILLOPT_CREDENTIAL_SOURCE"] = "env_file"
    if args.fresh_from:
        if args.benchmark != "alfworld":
            parser.error("--fresh-from is supported only for ALFWorld")
        if not args.dry_run:
            prepare_alfworld_restart(args.fresh_from.resolve(), args.run_root, args.workers)
    folder = args.run_root / args.benchmark
    todo = missing(folder)
    stale = partial(folder)

    print(f"[{now()}] {args.benchmark}: {TOTAL - len(todo)}/{TOTAL} complete, "
          f"{len(todo)} missing, {len(stale)} partial")
    for repeat, variant in todo:
        print(f"  missing repeat_{repeat:02d}/{variant}")
    for directory in stale:
        print(f"  partial {directory.relative_to(folder)}")

    if args.dry_run:
        return
    resume(args.run_root, args.benchmark, args.archive_partial or args.drop_partial)


if __name__ == "__main__":
    main()
