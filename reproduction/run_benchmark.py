"""Run official training/evaluation or a tiny real-API pipeline check.

Invoke using benchmark_python.sh so each benchmark uses its assigned environment.
OfficeQA downloads require a cached Hugging Face login with authorized access.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reproduction.prepare_additional_data import prepare  # noqa: E402
from reproduction.prepare_official_data import digest  # noqa: E402
from reproduction.run_experiments import collect, credentials, install_request_audit, now, write_json  # noqa: E402
from reproduction.tool_reasoning_compat import install_tool_reasoning  # noqa: E402
from reproduction.trajectory_compat import install_mapped_trajectory_reader  # noqa: E402

BENCHMARKS = ("docvqa", "officeqa", "spreadsheetbench", "alfworld")


def smoke_split(benchmark: str, source: Path, output: Path) -> None:
    """Use the first official example of each split; never relabel split membership."""
    for split in ("train", "val", "test"):
        folder = output / split
        folder.mkdir(parents=True, exist_ok=True)
        if benchmark == "docvqa":
            with (source / split / "items.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))[:1]
            with (folder / "items.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        else:
            rows = json.loads((source / split / "items.json").read_text())[:1]
            (folder / "items.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")


def collect_smoke(directory: Path, benchmark: str) -> dict:
    summary = json.loads((directory / "summary.json").read_text())
    audit = [json.loads(line) for line in (directory / "request_metadata.jsonl").read_text().splitlines() if line]
    history = json.loads((directory / "history.json").read_text())
    results = {}
    errors = 0
    for path in sorted(directory.rglob("results.jsonl")):
        rows = [json.loads(line) for line in path.read_text().splitlines() if line]
        errors += sum(any(row.get(key) is False for key in ("agent_ok", "llm_ok", "code_ok", "exec_ok")) for row in rows)
        results[str(path.parent.relative_to(directory))] = [
            {key: row[key] for key in ("id", "hard", "soft", "agent_ok", "llm_ok", "code_ok", "exec_ok", "n_turns",
                                      "oracle_parsed_pages_included", "oracle_parsed_pages_chars", "use_local_tools") if key in row}
            for row in rows
        ]
    # The official ALFWorld rollout reports agent_ok=True even on API fallback.
    for path in directory.rglob("conversation.json"):
        conversation = json.loads(path.read_text())
        if isinstance(conversation, list):
            errors += sum(isinstance(row, dict) and row.get("model_response") == "<think>error</think><action>look</action>" for row in conversation)
    all_high = bool(audit) and all(row["model"] == "gpt-6.1-terra" and row["reasoning_effort"] == "high" for row in audit)
    if not all_high or errors:
        raise RuntimeError(f"Smoke check has {errors} model/tool errors or mismatching request configuration")
    report = {
        "benchmark": benchmark, "scope": "functional smoke only; one real item per official split",
        "model": "gpt-6.1-terra", "reasoning_effort": "high", "request_attempts": len(audit),
        "all_requests_match": all_high, "model_tool_errors": errors,
        "steps": [{key: step.get(key) for key in ("step", "action", "n_patches", "selection_hard")} for step in history],
        "token_summary": summary["token_summary"], "results": results,
        "test_baseline_hard": summary["baseline_test_hard"], "test_best_hard": summary["test_hard"],
        "test_final_hard": summary["final_test_hard"],
        "note": "No-edit skips are legitimate optimizer decisions; slow/meta updates are disabled in this one-epoch smoke.",
    }
    write_json(directory / "smoke_report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "smoke", "train", "eval"))
    parser.add_argument("benchmark", choices=BENCHMARKS)
    parser.add_argument("--out-root", type=Path)
    parser.add_argument("--skill", type=Path)
    parser.add_argument("--split", default="valid_unseen", choices=("train", "valid_seen", "valid_unseen", "all"))
    args = parser.parse_args()
    os.chdir(ROOT)
    benchmark = args.benchmark
    profile = "alfworld" if benchmark == "alfworld" else "tools"
    if Path(sys.prefix).name != f"max-skillopt-{profile}":
        parser.error(f"Use: bash reproduction/benchmark_python.sh {benchmark} reproduction/run_benchmark.py ...")
    os.environ["ALFWORLD_DATA"] = str(ROOT / "reproduction/data/alfworld")
    config = ROOT / "reproduction/configs" / f"{benchmark}_terra_high.yaml"
    from skillopt.config import flatten_config, load_config

    cfg = flatten_config(load_config(str(config)))
    provenance = prepare(benchmark)
    if args.command == "check":
        from scripts.train import get_adapter

        adapter = get_adapter(cfg)
        adapter.setup(cfg)
        loader = adapter.get_dataloader()
        print(json.dumps({"benchmark": benchmark, "model": cfg["target_model"],
                          "reasoning_effort": cfg["reasoning_effort"], "data_counts": provenance.get("counts"),
                          "train_size": loader.get_train_size(), "status": "ready"}, indent=2))
        return

    suffix = now().replace(":", "").replace("+", "_")
    directory = (args.out_root or ROOT / "reproduction/outputs" / f"{benchmark}_{args.command}_{suffix}").resolve()
    if directory.exists() and any(directory.iterdir()):
        parser.error("Output directory is not empty; choose a new directory to avoid cached-result reuse.")
    directory.mkdir(parents=True, exist_ok=True)
    state = {"benchmark": benchmark, "command": args.command, "model": "gpt-6.1-terra",
             "reasoning_effort": "high", "started_at": now(), "phase": "starting"}
    write_json(directory / "status.json", state)
    try:
        base, key = credentials()
        # Data-loader imports may initialize the model module before credentials
        # are mapped into process env. Configure the runtime explicitly as well.
        from skillopt.model import azure_openai

        azure_openai.configure_azure_openai(endpoint=base, api_key=key, auth_mode="openai_compatible")
        install_tool_reasoning()
        install_request_audit(directory / "request_metadata.jsonl")
        install_mapped_trajectory_reader()
        overrides = []
        if args.command == "smoke":
            subset = directory / "smoke_splits"
            smoke_split(benchmark, Path(cfg["split_dir"]), subset)
            overrides = [f"env.split_dir={subset}", "env.workers=1", "gradient.analyst_workers=1",
                         "train.num_epochs=1", "train.train_size=0", "train.batch_size=1",
                         "gradient.minibatch_size=1", "gradient.merge_batch_size=2",
                         "evaluation.sel_env_num=1", "evaluation.test_env_num=1",
                         "optimizer.use_slow_update=false", "optimizer.use_meta_skill=false"]
            if benchmark == "alfworld":
                overrides += ["env.max_steps=12", "env.max_api_workers=1"]
            if benchmark == "spreadsheetbench":
                overrides += ["env.max_turns=3"]
        write_json(directory / "run_metadata.json", {
            **state, "config": str(config.relative_to(ROOT)), "config_sha256": digest(config),
            "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "scope": "one real example per official split, one training step" if args.command == "smoke" else "official full splits",
            "overrides": overrides, "counts": provenance.get("counts"), "data": provenance,
            "mapped_trajectory_reader": True,
            "terra_tool_reasoning": "high",
            "python": sys.version,
            "initial_skill_sha256": digest(Path(cfg["skill_init"])),
            "resolved_settings": {key: cfg.get(key) for key in (
                "num_epochs", "batch_size", "train_size", "seed", "minibatch_size", "merge_batch_size",
                "use_gate", "use_slow_update", "use_meta_skill", "slow_update_gate_with_selection",
                "sel_env_num", "test_env_num", "workers", "analyst_workers", "max_steps", "max_turns",
                "max_tool_turns", "max_completion_tokens", "search_mode", "mode",
            )},
        })
        with (directory / "requirements.txt").open("w") as requirements:
            subprocess.run([sys.executable, "-m", "pip", "freeze"], stdout=requirements, check=True)
        state.update(phase="running", updated_at=now())
        write_json(directory / "status.json", state)
        if args.command == "eval":
            from scripts import eval_only as entry

            skill = args.skill or Path(cfg["skill_init"])
            sys.argv = ["scripts/eval_only.py", "--config", str(config), "--skill", str(skill),
                        "--split", args.split, "--out_root", str(directory)]
        else:
            from scripts import train as entry

            sys.argv = ["scripts/train.py", "--config", str(config), "--out_root", str(directory)]
            if overrides:
                sys.argv += ["--cfg-options", *overrides]
        entry.main()
        if args.command != "eval":
            summary = json.loads((directory / "summary.json").read_text())
            if not summary.get("token_summary", {}).get("analyst", {}).get("calls", 0):
                raise RuntimeError("No actual analyst calls recorded; reflection check did not pass.")
        if args.command == "smoke":
            collect_smoke(directory, benchmark)
        failures = 0
        if args.command == "train":
            state.update(phase="collecting_results", updated_at=now())
            write_json(directory / "status.json", state)
            report = collect(directory, benchmark)
            failures = sum(row["api_failures"] + row["tool_failures"] for row in report["test"].values())
        state.update(phase="completed_with_errors" if failures else "completed", finished_at=now())
    except BaseException as error:
        state.update(phase="failed", error_type=type(error).__name__, finished_at=now())
        raise
    finally:
        write_json(directory / "status.json", state)


if __name__ == "__main__":
    main()
