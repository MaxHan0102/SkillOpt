"""Retry only timed-out records from the ordinary resized-image DocVQA test."""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reproduction import retest_docvqa_images as base  # noqa: E402


def select_timeouts(records: list[dict]) -> list[str]:
    return [str(row["id"]) for row in records
            if row.get("agent_ok") is False and
            (row.get("phase") == "timeout" or str(row.get("fail_reason", "")).startswith("task-timeout-"))]


def replace_selected(previous: list[dict], fresh: list[dict], selected: list[str], expected: list[str]) -> list[dict]:
    base.validate_coverage(previous, expected)
    base.validate_coverage(fresh, selected)
    mapping = {str(row["id"]): row for row in fresh}
    return [{**mapping[str(row["id"])], "provenance": "user_requested_timeout_retry"}
            if str(row["id"]) in mapping else row for row in previous]


def save_manifest(root: Path, manifest: dict) -> None:
    base.write_json(root / "manifest.json", manifest)
    (root / "manifest.sha256").write_text(base.digest(root / "manifest.json") + "\n")


def prepare(previous: Path, root: Path) -> dict:
    original = base.verify(previous)
    if not base.read(previous / "retest_summary.json")["complete"]:
        raise ValueError("Previous image retest is incomplete")
    selections = {variant: select_timeouts(base.rows(previous / "evaluations" / variant / "results.jsonl"))
                  for variant in base.VARIANTS}
    selections = {variant: ids for variant, ids in selections.items() if ids}
    if not selections:
        raise ValueError("No timeout records to retry")
    root.mkdir(parents=True, exist_ok=False)
    prior_hashes = {str(previous / name): base.digest(previous / name)
                    for name in ("manifest.json", "manifest.sha256", "retest_summary.json")}
    for variant in base.VARIANTS:
        for file in (previous / "evaluations" / variant).glob("*.json*"):
            prior_hashes[str(file)] = base.digest(file)
        file = previous / "composite" / variant / "results.jsonl"
        prior_hashes[str(file)] = base.digest(file)
    template = copy.deepcopy(original)
    template["original_sha256"].update(prior_hashes)
    template["code_sha256"][str(Path(__file__).resolve().relative_to(ROOT))] = base.digest(Path(__file__))
    template.update(previous_root=str(previous), created_at=base.now(), selections=selections,
                    selected_executions=sum(map(len, selections.values())),
                    protocol="User-authorized second attempt for exactly the four saved timeout occurrences; "
                             "one fresh evaluation each; retain every new outcome; keep all previous successful records; "
                             "same images, skills, model, reasoning, seed and timeout; no training or evolution")
    for variant, selected in selections.items():
        cell = root / "cells" / variant
        inputs = cell / "inputs"
        inputs.mkdir(parents=True)
        for name in ("config.json", f"{variant}.md"):
            (inputs / name).write_bytes((previous / "inputs" / name).read_bytes())
        items = [row for row in base.read(previous / "inputs/items.json") if str(row["id"]) in selected]
        if len(items) != len(selected) or {str(row["id"]) for row in items} != set(selected):
            raise ValueError("Frozen task inputs do not cover the selected timeout IDs")
        base.write_json(inputs / "items.json", items)
        manifest = copy.deepcopy(template)
        manifest.update(variants=[variant], selected_ids=selected, selected_executions=len(selected))
        manifest["input_sha256"].update({str(file): base.digest(file) for file in inputs.iterdir()})
        save_manifest(cell, manifest)
        template["original_sha256"][str(cell / "manifest.json")] = base.digest(cell / "manifest.json")
    save_manifest(root, template)
    base.write_json(root / "status.json", {"phase": "prepared", "updated_at": base.now(), "selections": selections})
    return template


def trial(root: Path, variant: str) -> None:
    manifest = base.verify(root)
    cell = root / "cells" / variant
    selected = manifest["selections"][variant]
    base.IDS = tuple(selected)
    from skillopt.model import azure_openai

    factory = azure_openai._make_client
    lock = threading.Lock()
    log = cell / "evaluations" / variant / "http_responses.jsonl"

    def request_hook(request):
        request.extensions["reproduction_timeout_retry_start"] = time.monotonic()

    def response_hook(response):
        elapsed = time.monotonic() - response.request.extensions["reproduction_timeout_retry_start"]
        # Only status/timing metadata; never headers, credentials or bodies.
        with lock, log.open("a") as stream:
            stream.write(json.dumps({"time": base.now(), "status_code": response.status_code,
                                     "elapsed_seconds": round(elapsed, 3), "path": response.request.url.path}) + "\n")

    def response_factory(role):
        client = factory(role)
        client._client.event_hooks.setdefault("request", []).append(request_hook)
        client._client.event_hooks.setdefault("response", []).append(response_hook)
        return client

    azure_openai._make_client = response_factory
    base.trial(cell, variant)


def report(root: Path) -> dict:
    manifest = base.verify(root)
    previous = Path(manifest["previous_root"])
    prior_summary = base.read(previous / "retest_summary.json")
    summary = {"updated_at": base.now(), "protocol": manifest["protocol"], "test_count": len(manifest["test_ids"]),
               "selected_executions": manifest["selected_executions"], "verified_executions": 0,
               "complete": True, "variants": {}}
    lines = ["# DocVQA 普通实验：超时记录再次补测", "", manifest["protocol"], "",
             "仅重测上轮超时的4条执行，保留已成功的5条；旧结果完整保留。超时仍计0分，分母保持374。",
             "这是按用户要求进行的第二轮异常补测合并，不是独立全量评测，也不是 avg@5。", "",
             "| Skill | 本轮题数 | 本轮答对 | 本轮执行异常 | 上轮合并 hard | 本轮合并 hard | 本轮合并 ANLS |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for variant in base.VARIANTS:
        old = base.rows(previous / "composite" / variant / "results.jsonl")
        selected = manifest["selections"].get(variant, [])
        directory = root / "cells" / variant / "evaluations" / variant
        if selected and not (directory / "trial_metrics.json").exists():
            summary["complete"] = False
            continue
        fresh = base.rows(directory / "results.jsonl") if selected else []
        combined = replace_selected(old, fresh, selected, manifest["test_ids"])
        destination = root / "composite" / variant
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in combined))
        errors = base.read(directory / "trial_metrics.json")["execution_failures"] if selected else 0
        result = {"selected_ids": selected, "rerun_count": len(fresh), "rerun_correct": sum(r["hard"] for r in fresh),
                  "rerun_execution_failures": errors, "prior_composite_hard": prior_summary["variants"][variant]["composite_hard"],
                  "composite_hard": sum(r["hard"] for r in combined) / len(combined),
                  "composite_soft": sum(r["soft"] for r in combined) / len(combined), "denominator": len(combined)}
        summary["variants"][variant] = result
        summary["verified_executions"] += len(fresh)
        lines.append(f"| {variant} | {len(fresh)} | {result['rerun_correct']} | {errors} | "
                     f"{result['prior_composite_hard']:.2%} | {result['composite_hard']:.2%} | {result['composite_soft']:.2%} |")
    base.write_json(root / "retry_summary.json", summary)
    (root / "REPORT.md").write_text("\n".join(lines) + "\n")
    return summary


def run(root: Path) -> None:
    with (root / "worker.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = base.verify(root)
        state = {"phase": "running", "started_at": base.now(), "worker_pid": os.getpid()}
        try:
            for variant, selected in manifest["selections"].items():
                state.update(phase="evaluating", variant=variant, selected_ids=selected, updated_at=base.now())
                base.write_json(root / "status.json", state)
                with (root / f"{variant}.log").open("x") as log:
                    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "trial", "--run-root", str(root),
                                             "--variant", variant], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                summary = report(root)
                print(f"[{base.now()}] {variant} returncode={result.returncode} verified={summary['verified_executions']}/4", flush=True)
                if result.returncode:
                    raise RuntimeError("Retry child failed; evidence preserved")
            errors = sum(r["rerun_execution_failures"] for r in summary["variants"].values())
            state.update(phase="completed_with_errors" if errors else "completed", finished_at=base.now())
        except BaseException as error:
            state.update(phase="failed", error_type=type(error).__name__, finished_at=base.now())
            raise
        finally:
            base.write_json(root / "status.json", state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "run", "trial", "report"))
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--previous-root", type=Path, default=ROOT / "reproduction/outputs/docvqa_image_resize_retest_20261106")
    parser.add_argument("--variant", choices=base.VARIANTS)
    args = parser.parse_args()
    os.chdir(ROOT)
    root = args.run_root.resolve()
    if not root.is_relative_to(ROOT / "reproduction/outputs"):
        parser.error("Keep outputs under reproduction/outputs")
    if args.action == "prepare":
        manifest = prepare(args.previous_root.resolve(), root)
        print(json.dumps({"selections": manifest["selections"], "selected_executions": manifest["selected_executions"]}))
    elif args.action == "check":
        manifest = base.verify(root)
        for variant in manifest["selections"]:
            base.verify(root / "cells" / variant)
        print(json.dumps({"frozen_inputs_verified": True, "selections": manifest["selections"]}))
    elif args.action == "trial":
        if not args.variant:
            parser.error("trial requires --variant")
        code = 0
        try:
            trial(root, args.variant)
        except BaseException:
            code = 1
            traceback.print_exc()
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(code)
    elif args.action == "run":
        run(root)
    else:
        print(json.dumps(report(root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
