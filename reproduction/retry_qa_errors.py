"""One-shot DocVQA/OfficeQA execution-error reruns with frozen original inputs."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reproduction import retry_spreadsheet_errors as engine  # noqa: E402

# Reuse the tested runner without changing files hashed by the active
# SpreadsheetBench campaign. Each benchmark runs in its own fresh process.
NATIVE_SELECTION = engine.select_errors
NATIVE_REPORT = engine.report
BASE_SOURCE_FILES = engine.SOURCE_FILES
BENCHMARKS = ("docvqa", "officeqa")
LABELS = {"docvqa": "DocVQA", "officeqa": "OfficeQA"}


def failure_kind(reason: str) -> str:
    reason = reason.casefold()
    if "connection error" in reason or "connection refused" in reason:
        return "api_connection"
    if "timed out" in reason or "task-timeout" in reason:
        return "timeout"
    if "502" in reason or "upstream service temporarily unavailable" in reason:
        return "api_upstream_502"
    if "neither produced" in reason:
        return "model_incomplete_response"
    return "other_execution"


def select_errors(rows: list[dict], items: list[dict], expected: list[str]) -> list[dict]:
    selected = NATIVE_SELECTION(rows, items, expected)
    raw = {str(row["id"]): row for row in rows}
    for item in selected:
        reason = str(raw[item["id"]].get("fail_reason") or "")
        item.update(failure_kind=failure_kind(reason), original_fail_reason=reason)
    return selected


def report(root: Path) -> dict:
    summary = NATIVE_REPORT(root)
    manifest = engine.read(root / "manifest.json")
    original = engine.read(root / "original_summary.json")
    benchmark = manifest["benchmark"]
    soft_label = "ANLS" if benchmark == "docvqa" else "F1"
    summary.update(benchmark=benchmark, original_failure_kinds=manifest["failure_kinds"], impact_assessment={})
    for variant in engine.VARIANTS:
        selected = [item for cell in manifest["cells"] if cell["variant"] == variant for item in cell["selection"]]
        summary["impact_assessment"][variant] = {
            "original_execution_failures": len(selected),
            "failure_kinds": dict(Counter(item["failure_kind"] for item in selected)),
            "maximum_possible_hard_gain_percentage_points": 100 * sum(1 - float(row["original_hard"]) for row in selected)
            / (5 * len(manifest["test_ids"])),
            "bound_definition": "Only if every selected occurrence becomes correct; not an expected improvement",
        }
        if summary["complete"]:
            scores = [statistics.mean(float(row["soft"]) for row in engine.jsonl(
                root / "composite" / f"repeat_{repeat:02d}" / variant / "results.jsonl")) for repeat in range(1, 6)]
            summary["variants"][variant].update(
                original_avg_at_5_soft=original["variants"][variant]["avg_at_5_soft"],
                composite_mean_soft=statistics.mean(scores), composite_soft_by_repeat=scores,
            )
    engine.write_json(root / "rerun_summary.json", summary)
    lines = [f"# {LABELS[benchmark]} 异常题目补测", "", manifest["protocol"], "", manifest["composite_definition"], "",
             f"已核验补测：{summary['verified_rerun_executions']}/{manifest['selected_executions']} 次执行，"
             f"涉及 {manifest['unique_task_ids']} 道题。", "",
             "原始结果完整保留。每个异常记录只补测一次，普通答错题不重测；再次异常也计入分母。",
             "API 故障与模型未形成有效答案分别记录。模型输出失败也是原实验表现的一部分；补测合并结果单独标注。", "",
             "| 技能 | 原始 hard avg@5 | 合并 hard 均值 | "
             f"原始 {soft_label} avg@5 | 合并 {soft_label} 均值 | 剩余异常执行数 |",
             "|---|---:|---:|---:|---:|---:|"]
    for variant in engine.VARIANTS:
        before = original["variants"][variant]
        after = summary["variants"].get(variant)
        hard = f"{after['composite_mean_hard']:.2%}" if after else "待全部完成"
        soft = f"{after['composite_mean_soft']:.2%}" if after else "待全部完成"
        errors = after["remaining_execution_failures"] if after else "待统计"
        lines.append(f"| {variant} | {before['avg_at_5_hard']:.2%} | {hard} | "
                     f"{before['avg_at_5_soft']:.2%} | {soft} | {errors} |")
    (root / "REPORT.md").write_text("\n".join(lines) + "\n")
    return summary


def status(root: Path) -> dict:
    state = engine.read(root / "status.json")
    summary = engine.read(root / "rerun_summary.json")
    progress = {}
    for cell in engine.read(root / "manifest.json")["cells"]:
        directory = engine.cell_path(root, cell["repeat"], cell["variant"])
        file = directory / "results.jsonl"
        if not directory.exists():
            continue
        rows = []
        if file.exists():
            # The native QA adapters append and flush each task. A simultaneous
            # reader may see the final JSON record before its write completes.
            lines = file.read_text().splitlines()
            for index, line in enumerate(lines):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    if index != len(lines) - 1:
                        raise
        selected = [item["id"] for item in cell["selection"]]
        ids = [str(row["id"]) for row in rows]
        if len(set(ids)) != len(ids) or not set(ids) <= set(selected):
            raise ValueError("Live output has duplicate or unselected task IDs")
        progress[f"repeat_{cell['repeat']:02d}_{cell['variant']}"] = {"finished_tasks": len(rows), "total": len(selected)}
    return {**state, "verified_rerun_executions": summary["verified_rerun_executions"], "task_progress": progress}


def check(root: Path) -> dict:
    manifest = engine.verify(root)
    from scripts.eval_only import get_adapter

    cfg = engine.read(root / "inputs" / manifest["benchmark"] / "config.json")
    adapter = get_adapter(cfg)
    adapter.setup(cfg)
    items = adapter.build_eval_env(0, "valid_unseen", 42)
    for cell in manifest["cells"]:
        engine.selected_items(items, manifest["test_ids"], [item["id"] for item in cell["selection"]])
    if manifest["benchmark"] == "docvqa":
        selected = {item["id"] for cell in manifest["cells"] for item in cell["selection"]}
        for item in items:
            if str(item["id"]) in selected:
                images = item.get("image_paths") or [item.get("image_path")]
                if not images or any(not path or not Path(path).is_file() for path in images):
                    raise ValueError("Selected DocVQA image is missing")
    else:
        if not cfg.get("data_dirs") or any(not Path(path).is_dir() for path in cfg["data_dirs"]):
            raise ValueError("Original OfficeQA document corpus is missing")
    return {"benchmark": manifest["benchmark"], "full_test_count": len(items),
            "selected_executions": manifest["selected_executions"], "subset_and_local_data_verified": True}


def configure(benchmark: str, *, setter=setattr) -> None:
    if benchmark not in BENCHMARKS:
        raise ValueError("Only DocVQA/OfficeQA are supported by this entry point")

    def command(*arguments: str) -> list[str]:
        return ["bash", str(ROOT / "reproduction/benchmark_python.sh"), benchmark, str(Path(__file__).resolve()),
                *arguments, "--benchmark", benchmark]

    sources = [name for name in BASE_SOURCE_FILES if "/spreadsheetbench/" not in name]
    sources += [str(Path(__file__).resolve().relative_to(ROOT))]
    sources += [str(path.relative_to(ROOT)) for path in sorted((ROOT / "skillopt/envs" / benchmark).glob("*.py"))]
    for name, value in {"BENCHMARK": benchmark, "SOURCE_FILES": tuple(sources), "command": command,
                        "select_errors": select_errors, "report": report, "status": status}.items():
        setter(engine, name, value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "start", "worker", "trial", "report", "status"))
    parser.add_argument("--benchmark", choices=BENCHMARKS, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--repeat", type=int)
    parser.add_argument("--variant", choices=engine.VARIANTS)
    args = parser.parse_args()
    engine.os.chdir(ROOT)
    configure(args.benchmark)
    root = args.run_root.resolve()
    if args.action == "prepare":
        if args.source_root is None:
            parser.error("prepare requires --source-root")
        manifest = engine.prepare(args.source_root.resolve(), root)
        result = {key: manifest[key] for key in ("benchmark", "selected_executions", "unique_task_ids", "failure_kinds")}
    elif args.action == "trial":
        if args.repeat is None or args.variant is None:
            parser.error("trial requires --repeat and --variant")
        result = engine.trial(root, args.repeat, args.variant)
    else:
        result = {"check": check, "start": engine.start, "worker": engine.worker,
                  "report": report, "status": status}[args.action](root)
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
