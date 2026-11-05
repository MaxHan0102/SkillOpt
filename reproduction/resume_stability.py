"""Resume a stability run: execute only the (repeat, variant) trials that are missing.

The stock `worker` entry point refuses to restart on an existing run root: logs are
opened with mode "x" and `trial` rejects pre-existing output directories. This entry
point enumerates completed trials from `trial_metrics.json`, runs only the gaps, and
appends to existing logs. Partial directories (no trial_metrics.json) are reported and
only removed when --drop-partial is passed, so a killed run is never silently mixed.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reproduction.run_experiments import now  # noqa: E402
from reproduction.run_stability import BENCHMARKS, ROOT, command, schedule  # noqa: E402

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--benchmark", required=True, choices=BENCHMARKS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--drop-partial", action="store_true")
    args = parser.parse_args()

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
    if stale and not args.drop_partial:
        raise SystemExit("Refusing to start: partial directories exist; rerun with --drop-partial")
    for directory in stale:
        print(f"[{now()}] dropping partial {directory.relative_to(folder)}", flush=True)
        shutil.rmtree(directory)

    failures = 0
    for repeat, variant in todo:
        log = folder / f"repeat_{repeat:02d}_{variant}.log"
        mode = "a" if log.exists() else "x"
        print(f"[{now()}] START repeat={repeat} skill={variant}", flush=True)
        with log.open(mode) as stream:
            result = subprocess.run(
                command(args.benchmark, "trial", "--run-root", str(args.run_root),
                        "--variant", variant, "--repeat", str(repeat)),
                cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            failures += 1
            print(f"[{now()}] FAILED repeat={repeat} skill={variant}", flush=True)
        else:
            print(f"[{now()}] DONE repeat={repeat} skill={variant}", flush=True)
    print(f"[{now()}] finished: {len(todo) - failures}/{len(todo)} trials, {failures} failed", flush=True)


if __name__ == "__main__":
    main()
