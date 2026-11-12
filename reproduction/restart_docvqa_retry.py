"""User-authorized DocVQA retry restart with bounded trial-process cleanup."""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reproduction import retry_qa_errors as qa  # noqa: E402

engine = qa.engine


def configure() -> None:
    qa.configure("docvqa")

    def command(*arguments: str) -> list[str]:
        return ["bash", str(ROOT / "reproduction/benchmark_python.sh"), "docvqa",
                str(Path(__file__).resolve()), *arguments]

    engine.command = command
    engine.SOURCE_FILES = (*engine.SOURCE_FILES, str(Path(__file__).resolve().relative_to(ROOT)))


def prepare(source: Path, root: Path, previous: Path) -> dict:
    if source == root or previous == root:
        raise ValueError("Restart requires a fresh output directory")
    previous_manifest = engine.read(previous / "manifest.json")
    if previous_manifest["benchmark"] != "docvqa":
        raise ValueError("Previous attempt is not DocVQA")
    manifest = engine.prepare(source, root)
    old_cells = {(cell["repeat"], cell["variant"]): [item["id"] for item in cell["selection"]]
                 for cell in previous_manifest["cells"]}
    new_cells = {(cell["repeat"], cell["variant"]): [item["id"] for item in cell["selection"]]
                 for cell in manifest["cells"]}
    if new_cells != old_cells:
        raise ValueError("Restart selection differs from previous fixed anomaly list")
    manifest.update(
        restart_of=str(previous),
        restart_authorization="User requested restarting DocVQA anomaly reruns after raising upstream concurrency",
        protocol="User-authorized restart campaign; rerun each original selected occurrence once in this campaign; "
                 "preserve prior attempts separately; no best-of selection",
        process_cleanup="After native evaluation saves results, audit, metrics and terminal status, "
                        "flush output and exit the fresh trial process without waiting for obsolete API threads. "
                        "Native scoring and timeout settings are unchanged.",
    )
    engine.write_json(root / "manifest.json", manifest)
    (root / "manifest.sha256").write_text(engine.digest(root / "manifest.json") + "\n")
    engine.report(root)
    return {"benchmark": "docvqa", "selected_executions": manifest["selected_executions"],
            "unique_task_ids": manifest["unique_task_ids"], "restart_of": str(previous)}


def exit_after_trial(root: Path, repeat: int, variant: str) -> None:
    # The native adapter can time out a Future without cancelling the SDK's
    # synchronous request. Python otherwise waits for that obsolete non-daemon
    # thread during interpreter shutdown, even after all results are durable.
    # This entry point runs only in the fresh trial child, never a coordinator.
    code = 0
    try:
        engine.trial(root, repeat, variant)
    except BaseException:
        code = 1
        traceback.print_exc()
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            os._exit(code)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "start", "worker", "trial", "report", "status"))
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--previous-root", type=Path)
    parser.add_argument("--repeat", type=int)
    parser.add_argument("--variant", choices=engine.VARIANTS)
    args = parser.parse_args()
    os.chdir(ROOT)
    configure()
    root = args.run_root.resolve()
    if args.action == "prepare":
        if args.source_root is None or args.previous_root is None:
            parser.error("prepare requires --source-root and --previous-root")
        result = prepare(args.source_root.resolve(), root, args.previous_root.resolve())
    elif args.action == "trial":
        if args.repeat is None or args.variant is None:
            parser.error("trial requires --repeat and --variant")
        exit_after_trial(root, args.repeat, args.variant)
        return
    else:
        result = {"check": qa.check, "start": engine.start, "worker": engine.worker,
                  "report": engine.report, "status": qa.status}[args.action](root)
    if result is not None:
        print(engine.json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
