"""Rerun frozen SpreadsheetBench execution failures once, preserving originals."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reproduction.prepare_official_data import digest  # noqa: E402
from reproduction.run_experiments import MODEL, credentials, install_request_audit, now, write_json  # noqa: E402
from reproduction.run_stability import read, schedule, verify_inputs  # noqa: E402
from reproduction.stability_metrics import VARIANTS, collect_trial, validate_coverage  # noqa: E402
from reproduction.tool_reasoning_compat import install_tool_reasoning  # noqa: E402

BENCHMARK = "spreadsheetbench"
SOURCE_FILES = (
    "reproduction/retry_spreadsheet_errors.py", "reproduction/benchmark_python.sh",
    "reproduction/stability_metrics.py", "reproduction/run_stability.py",
    "reproduction/run_experiments.py", "reproduction/tool_reasoning_compat.py",
    "scripts/eval_only.py", "skillopt/config.py", "skillopt/model/azure_openai.py",
    "skillopt/envs/spreadsheetbench/adapter.py", "skillopt/envs/spreadsheetbench/dataloader.py",
    "skillopt/envs/spreadsheetbench/rollout.py", "skillopt/envs/spreadsheetbench/codegen_agent.py",
    "skillopt/envs/spreadsheetbench/evaluator.py", "skillopt/envs/spreadsheetbench/executor.py",
    "skillopt/envs/task_output.py",
)


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    temporary.replace(path)


def cell_path(root: Path, repeat: int, variant: str) -> Path:
    return root / "evaluations" / f"repeat_{repeat:02d}" / variant


def select_errors(rows: list[dict], items: list[dict], expected: list[str]) -> list[dict]:
    """Select saved execution flags, never a low score by itself."""
    validate_coverage(rows, expected)
    validate_coverage(items, expected)
    raw = {str(row["id"]): row for row in rows}
    fingerprints = {str(row["id"]): row for row in items}
    selected = []
    for task_id in expected:
        row, item = raw[task_id], fingerprints[task_id]
        if any(float(row[key]) != float(item[key]) for key in ("hard", "soft")):
            raise ValueError("Original scores and fingerprints disagree")
        if type(item.get("execution_ok")) is not bool:
            raise ValueError("Missing original execution classification")
        if item["execution_ok"]:
            continue
        reason = str(row.get("fail_reason", ""))
        kind = "connection" if "Connection error" in reason else "timeout" if "task-timeout" in reason else "other_execution"
        selected.append({"id": task_id, "original_hard": row["hard"], "original_soft": row["soft"],
                         "failure_kind": kind, "failure_flags": item["failure_flags"]})
    return selected


def selected_items(items: list[dict], expected: list[str], selected: list[str]) -> list[dict]:
    """Filter the original full split in its original order."""
    if [str(row["id"]) for row in items] != expected:
        raise ValueError("Full dataset differs from frozen test IDs/order")
    if not selected or len(set(selected)) != len(selected) or not set(selected) <= set(expected):
        raise ValueError("Invalid selected task IDs")
    chosen = set(selected)
    return [row for row in items if str(row["id"]) in chosen]


def merge_occurrence(original: list[dict], rerun: list[dict], selected: list[str], expected: list[str]) -> list[dict]:
    """Replace exactly this cell's selected IDs, regardless of rerun outcomes."""
    validate_coverage(original, expected)
    if len(set(selected)) != len(selected) or not set(selected) <= set(expected):
        raise ValueError("Invalid replacement IDs")
    validate_coverage(rerun, selected)
    replacements = {str(row["id"]): row for row in rerun}
    originals = {str(row["id"]): row for row in original}
    return [dict(replacements.get(task_id, originals[task_id])) for task_id in expected]


def prepare(source: Path, root: Path) -> dict:
    plan = verify_inputs(source, BENCHMARK)
    if read(source / BENCHMARK / "status.json")["phase"] not in ("completed", "completed_with_errors"):
        raise ValueError("Original campaign must be finished")
    expected = plan["test_ids"]
    cells, source_hashes = [], {}
    for repeat, variant in schedule():
        directory = cell_path(source / BENCHMARK, repeat, variant)
        selection = select_errors(jsonl(directory / "results.jsonl"), jsonl(directory / "item_fingerprints.jsonl"), expected)
        for name in ("results.jsonl", "item_fingerprints.jsonl", "trial_metrics.json"):
            source_hashes[str(directory / name)] = digest(directory / name)
        if selection:
            cells.append({"repeat": repeat, "variant": variant, "source_directory": str(directory), "selection": selection})
    if not cells:
        raise ValueError("No saved execution failures to rerun")
    source_summary = source / BENCHMARK / "stability_summary.json"
    source_hashes[str(source_summary)] = digest(source_summary)
    code_hashes = {name: digest(ROOT / name) for name in SOURCE_FILES}
    root.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source / "inputs" / BENCHMARK, root / "inputs" / BENCHMARK)
    shutil.copyfile(source_summary, root / "original_summary.json")
    failures = [item for cell in cells for item in cell["selection"]]
    manifest = {"schema": 1, "created_at": now(), "benchmark": BENCHMARK, "source_root": str(source),
                "selection_rule": "original item_fingerprints.execution_ok is false; ordinary wrong answers excluded",
                "protocol": "one fresh rerun per selected (repeat, variant, task); keep all outcomes; no best-of selection",
                "composite_definition": "Unchanged original occurrences plus the single rerun of each selected occurrence; not five fresh full evaluations",
                "network_change": "Append only the configured API hostname to this process's NO_PROXY/no_proxy; verify direct httpx transport",
                "model": MODEL, "reasoning_effort": "high", "seed": 42,
                "selected_executions": len(failures), "unique_task_ids": len({row["id"] for row in failures}),
                "failure_kinds": dict(Counter(row["failure_kind"] for row in failures)),
                "test_ids": expected, "cells": cells, "original_files_sha256": source_hashes,
                "code_sha256": code_hashes, "git_revision": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
    write_json(root / "manifest.json", manifest)
    (root / "manifest.sha256").write_text(digest(root / "manifest.json") + "\n")
    write_json(root / "status.json", {"phase": "prepared", "updated_at": now(),
                                      "selected_executions": len(failures), "selected_cells": len(cells)})
    report(root)
    return manifest


def verify(root: Path) -> dict:
    if digest(root / "manifest.json") != (root / "manifest.sha256").read_text().strip():
        raise ValueError("Frozen selection manifest changed")
    manifest = read(root / "manifest.json")
    plan = verify_inputs(root, BENCHMARK)
    if plan["test_ids"] != manifest["test_ids"]:
        raise ValueError("Frozen input and rerun manifest disagree")
    for path, expected in manifest["original_files_sha256"].items():
        if digest(Path(path)) != expected:
            raise ValueError(f"Original evidence changed: {path}")
    for name, expected in manifest["code_sha256"].items():
        if digest(ROOT / name) != expected:
            raise ValueError(f"Evaluation source changed: {name}")
    return manifest


def bypass_api_proxy(base: str) -> str:
    hostname = urlsplit(base).hostname
    if not hostname:
        raise ValueError("Configured API has no hostname")
    existing = ",".join(os.environ.get(key, "") for key in ("NO_PROXY", "no_proxy"))
    entries = list(dict.fromkeys(part.strip() for part in existing.split(",") if part.strip()))
    if hostname not in entries:
        entries.append(hostname)
    for key in ("NO_PROXY", "no_proxy"):
        os.environ[key] = ",".join(entries)
    return hostname


def trial(root: Path, repeat: int, variant: str) -> None:
    manifest = verify(root)
    matches = [cell for cell in manifest["cells"] if (cell["repeat"], cell["variant"]) == (repeat, variant)]
    if len(matches) != 1:
        raise ValueError("Trial is not a selected original cell")
    cell = matches[0]
    selected = [row["id"] for row in cell["selection"]]
    directory = cell_path(root, repeat, variant)
    directory.mkdir(parents=True, exist_ok=False)
    state = {"phase": "running", "repeat": repeat, "variant": variant, "started_at": now(), "selected_count": len(selected)}
    write_json(directory / "status.json", state)
    try:
        os.environ["SKILLOPT_CREDENTIAL_SOURCE"] = "env_file"
        base, key = credentials()
        hostname = bypass_api_proxy(base)
        from skillopt.model import azure_openai

        azure_openai.configure_azure_openai(endpoint=base, api_key=key, auth_mode="openai_compatible")
        install_tool_reasoning()
        install_request_audit(directory / "request_metadata.jsonl")
        factory = azure_openai._make_client
        response_lock = threading.Lock()

        def response_audit(response):
            with response_lock, (directory / "response_metadata.jsonl").open("a") as stream:
                stream.write(json.dumps({"time": now(), "status_code": response.status_code,
                                         "path": response.request.url.path}) + "\n")

        def target_only_factory(role):
            if role != "target":
                raise RuntimeError("Optimizer requests forbidden during targeted evaluation")
            client = factory(role)
            import httpx

            transport = client._client._transport_for_url(httpx.URL(base))
            if transport is not client._client._transport:
                raise RuntimeError("API still routes through a proxy")
            client._client.event_hooks.setdefault("response", []).append(response_audit)
            write_json(directory / "transport.json", {"checked_at": now(), "direct_transport_verified": True,
                                                      "configured_api_host_only": True})
            return client

        azure_openai._make_client = target_only_factory
        from scripts import eval_only

        original_get_adapter = eval_only.get_adapter

        def filtered_adapter(cfg):
            adapter = original_get_adapter(cfg)
            original_build = adapter.build_eval_env

            def build(env_num, split, seed, **kwargs):
                if (env_num, split, seed) != (0, "valid_unseen", 42):
                    raise ValueError("Evaluation settings differ from original")
                return selected_items(original_build(env_num, split, seed, **kwargs), manifest["test_ids"], selected)

            adapter.build_eval_env = build
            return adapter

        eval_only.get_adapter = filtered_adapter
        inputs = root / "inputs" / BENCHMARK
        write_json(directory / "run_metadata.json", {**state, "source_directory": cell["source_directory"],
                    "expected_test_ids": selected, "model": MODEL, "reasoning_effort": "high", "seed": 42,
                    "manifest_sha256": digest(root / "manifest.json"), "fresh_process": True,
                    "optimizer_calls_allowed": False, "api_hostname_bypasses_inherited_proxy": bool(hostname)})
        sys.argv = ["scripts/eval_only.py", "--config", str(inputs / "config.json"),
                    "--skill", str(inputs / f"{variant}.md"), "--split", "valid_unseen", "--out_root", str(directory)]
        eval_only.main()
        write_json(directory / "token_usage.json", azure_openai.tracker.summary())
        metrics = collect_trial(directory, BENCHMARK, selected)
        write_json(directory / "trial_metrics.json", metrics)
        state.update(phase="completed_with_errors" if metrics["execution_failures"] else "completed", finished_at=now())
    except BaseException as error:
        state.update(phase="failed", error_type=type(error).__name__, finished_at=now())
        raise
    finally:
        write_json(directory / "status.json", state)


def report(root: Path) -> dict:
    manifest = verify(root)
    cells = {(cell["repeat"], cell["variant"]): cell for cell in manifest["cells"]}
    complete = [cell for key, cell in cells.items() if (cell_path(root, *key) / "trial_metrics.json").exists()]
    summary = {"updated_at": now(), "definition": manifest["composite_definition"],
               "original_summary": "original_summary.json", "selected_executions": manifest["selected_executions"],
               "verified_rerun_executions": sum(len(cell["selection"]) for cell in complete),
               "verified_rerun_cells": len(complete), "required_cells": len(cells),
               "complete": len(complete) == len(cells), "variants": {}}
    original_summary = read(root / "original_summary.json")
    if summary["complete"]:
        for variant in VARIANTS:
            scores, failures, replaced = [], 0, 0
            for repeat in range(1, 6):
                source = cell_path(Path(manifest["source_root"]) / BENCHMARK, repeat, variant)
                key = (repeat, variant)
                cell = cells.get(key)
                rerun = cell_path(root, repeat, variant)
                selected = [row["id"] for row in cell["selection"]] if cell else []
                rows = merge_occurrence(jsonl(source / "results.jsonl"), jsonl(rerun / "results.jsonl") if cell else [],
                                        selected, manifest["test_ids"])
                items = merge_occurrence(jsonl(source / "item_fingerprints.jsonl"),
                                         jsonl(rerun / "item_fingerprints.jsonl") if cell else [], selected, manifest["test_ids"])
                chosen = set(selected)
                provenance = {task_id: {"kind": "targeted_rerun" if task_id in chosen else "original",
                                        "directory": str(rerun if task_id in chosen else source),
                                        "original_directory": str(source), "repeat": repeat, "variant": variant}
                              for task_id in manifest["test_ids"]}
                destination = root / "composite" / f"repeat_{repeat:02d}" / variant
                for name, records in (("results.jsonl", rows), ("item_fingerprints.jsonl", items)):
                    write_jsonl(destination / name, [{**row, "provenance": provenance[str(row["id"])]} for row in records])
                scores.append(statistics.mean(float(row["hard"]) for row in rows))
                failures += sum(not row["execution_ok"] for row in items)
                replaced += len(selected)
            summary["variants"][variant] = {"original_avg_at_5_hard": original_summary["variants"][variant]["avg_at_5_hard"],
                                           "composite_mean_hard": statistics.mean(scores), "composite_hard_by_repeat": scores,
                                           "remaining_execution_failures": failures, "replaced_occurrences": replaced,
                                           "denominator_per_repeat": len(manifest["test_ids"])}
    write_json(root / "rerun_summary.json", summary)
    lines = ["# SpreadsheetBench 异常题目补测", "", manifest["protocol"], "", manifest["composite_definition"], "",
             f"已核验补测：{summary['verified_rerun_executions']}/{manifest['selected_executions']} 次执行。",
             "原始结果全部保留；每次异常执行只补测一次，再次异常也计入分母。", "",
             "| 技能 | 原始 avg@5 | 补测合并均值 | 合并后异常执行数 |", "|---|---:|---:|---:|"]
    for variant in VARIANTS:
        original = original_summary["variants"][variant]["avg_at_5_hard"]
        result = summary["variants"].get(variant)
        values = f"{result['composite_mean_hard']:.2%} | {result['remaining_execution_failures']}" if result else "待全部补测完成 | 待统计"
        lines.append(f"| {variant} | {original:.2%} | {values} |")
    (root / "REPORT.md").write_text("\n".join(lines) + "\n")
    return summary


def command(*args: str) -> list[str]:
    return ["bash", str(ROOT / "reproduction/benchmark_python.sh"), BENCHMARK, str(Path(__file__).resolve()), *args]


def worker(root: Path) -> None:
    with (root / "worker.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = verify(root)
        state = {"phase": "running", "started_at": now(), "worker_pid": os.getpid(),
                 "selected_executions": manifest["selected_executions"]}
        try:
            for cell in manifest["cells"]:
                repeat, variant = cell["repeat"], cell["variant"]
                state.update(phase="evaluating", repeat=repeat, variant=variant, updated_at=now())
                write_json(root / "status.json", state)
                directory = cell_path(root, repeat, variant)
                # Never automatically retry an already-started occurrence again.
                if directory.exists():
                    raise ValueError("Cell already started; preserve it instead of repeating it")
                print(f"[{now()}] START repeat={repeat} variant={variant} n={len(cell['selection'])}", flush=True)
                with (root / f"repeat_{repeat:02d}_{variant}.log").open("x") as log:
                    result = subprocess.run(command("trial", "--run-root", str(root), "--repeat", str(repeat),
                                                    "--variant", variant), cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                summary = report(root)
                state.update(verified_rerun_executions=summary["verified_rerun_executions"],
                             verified_rerun_cells=summary["verified_rerun_cells"], last_returncode=result.returncode)
                print(f"[{now()}] FINISHED repeat={repeat} variant={variant} returncode={result.returncode} "
                      f"verified={summary['verified_rerun_executions']}/{manifest['selected_executions']}", flush=True)
            summary = report(root)
            remaining = sum(item["remaining_execution_failures"] for item in summary["variants"].values())
            state.update(phase=("completed_with_errors" if remaining else "completed") if summary["complete"] else "incomplete",
                         finished_at=now())
        except BaseException as error:
            state.update(phase="failed", error_type=type(error).__name__, finished_at=now())
            raise
        finally:
            write_json(root / "status.json", state)


def start(root: Path) -> dict:
    verify(root)
    with (root / "launch.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (root / "launch.json").exists():
            raise ValueError("Campaign already launched; inspect its status instead of duplicating it")
        with (root / "console.log").open("x") as log:
            process = subprocess.Popen(command("worker", "--run-root", str(root)), cwd=ROOT,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        launch = {"launched_at": now(), "pid": process.pid, "root": str(root)}
        write_json(root / "launch.json", launch)
    return launch


def status(root: Path) -> dict:
    state = read(root / "status.json")
    summary = read(root / "rerun_summary.json")
    # The official adapter saves results.jsonl at the end of a cell. Its
    # per-task progress lines show completed work inside a running cell.
    progress = {}
    for cell in read(root / "manifest.json")["cells"]:
        name = f"repeat_{cell['repeat']:02d}_{cell['variant']}"
        log = root / f"{name}.log"
        if log.exists():
            completed = re.findall(r"^\s+(\d+)/(\d+) id=", log.read_text(errors="replace"), re.MULTILINE)
            progress[name] = {"finished_tasks": int(completed[-1][0]) if completed else 0, "total": len(cell["selection"])}
    return {**state, "verified_rerun_executions": summary["verified_rerun_executions"], "task_progress": progress}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "start", "worker", "trial", "report", "status"))
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--repeat", type=int)
    parser.add_argument("--variant", choices=VARIANTS)
    args = parser.parse_args()
    os.chdir(ROOT)
    root = args.run_root.resolve()
    if args.action == "prepare":
        if args.source_root is None:
            parser.error("prepare requires --source-root")
        result = prepare(args.source_root.resolve(), root)
        result = {key: result[key] for key in ("selected_executions", "unique_task_ids", "failure_kinds")}
    elif args.action == "trial":
        if args.repeat is None or args.variant is None:
            parser.error("trial requires --repeat and --variant")
        result = trial(root, args.repeat, args.variant)
    else:
        result = {"start": start, "worker": worker, "report": report, "status": status}[args.action](root)
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
