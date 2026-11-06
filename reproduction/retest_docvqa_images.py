"""Retest the three ordinary DocVQA input-limit failures with resized images."""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import math
import os
import subprocess
import sys
import traceback
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reproduction.prepare_official_data import digest  # noqa: E402
from reproduction.run_experiments import MODEL, credentials, install_request_audit, now, write_json  # noqa: E402
from reproduction.stability_metrics import collect_trial, validate_coverage  # noqa: E402

IDS = ("45837", "58310", "45842")
VARIANTS = {"baseline": "test_eval_baseline", "best": "test_eval", "final": "test_eval_final"}


def read(path: Path):
    return json.loads(path.read_text())


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def patches(width: int, height: int) -> int:
    return math.ceil(width / 32) * math.ceil(height / 32)


def resize_image(source: Path, destination: Path) -> dict:
    from PIL import Image

    with Image.open(source) as image:
        original = image.size
        width, height = original
        # Fixed, answer-independent rule, identical for every skill variant.
        # A margin below the 30,000-patch rejection threshold avoids rounding.
        if patches(width, height) > 30000:
            scale = 0.85
            while patches(max(1, int(original[0] * scale)), max(1, int(original[1] * scale))) > 28000:
                scale *= 0.85
            width, height = max(1, int(original[0] * scale)), max(1, int(original[1] * scale))
        image = image.convert("RGB")
        if image.size != (width, height):
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        image.save(destination, format="PNG")
    return {"original_size": list(original), "resized_size": [width, height],
            "original_patches_32px": patches(*original), "resized_patches_32px": patches(width, height),
            "source": str(source.resolve()), "path": str(destination.resolve()),
            "source_sha256": digest(source), "sha256": digest(destination)}


def merge(original: list[dict], replacements: list[dict], expected: list[str]) -> list[dict]:
    validate_coverage(original, expected)
    validate_coverage(replacements, list(IDS))
    mapping = {str(row["id"]): row for row in replacements}
    return [{**mapping.get(str(row["id"]), row),
             "provenance": "image_resize_retest" if str(row["id"]) in mapping else "original"}
            for row in original]


def prepare(source: Path, root: Path) -> dict:
    from scripts.eval_only import get_adapter

    cfg = read(source / "config.json")
    if (cfg["target_model"], cfg["reasoning_effort"], cfg["seed"]) != (MODEL, "high", 42):
        raise ValueError("Unexpected original experiment settings")
    adapter = get_adapter(cfg)
    adapter.setup(cfg)
    items = adapter.build_eval_env(0, "valid_unseen", 42)
    expected = [str(item["id"]) for item in items]
    originals = {}
    for variant, folder in VARIANTS.items():
        file = source / folder / "results.jsonl"
        data = rows(file)
        validate_coverage(data, expected)
        failed = {str(row["id"]) for row in data if row.get("agent_ok") is False}
        if failed != set(IDS):
            raise ValueError("Original execution failures differ from the authorized three IDs")
        originals[str(file)] = digest(file)
    root.mkdir(parents=True, exist_ok=False)
    inputs = root / "inputs"
    inputs.mkdir()
    (inputs / "images").mkdir()
    runtime = read(source / "runtime_state.json")
    skill_sources = {"baseline": source / "skills/skill_v0000.md", "best": source / "best_skill.md",
                     "final": Path(runtime["current_skill_path"])}
    for variant, file in skill_sources.items():
        (inputs / f"{variant}.md").write_bytes(file.read_bytes())
        originals[str(file)] = digest(file)
    (inputs / "config.json").write_bytes((source / "config.json").read_bytes())
    originals[str(source / "config.json")] = digest(source / "config.json")
    selected, images = [], {}
    for item in items:
        if str(item["id"]) not in IDS:
            continue
        info = resize_image(Path(item["image_path"]), inputs / "images" / f"{item['id']}.png")
        images[str(item["id"])] = info
        selected.append({**item, "image_path": info["path"], "image_paths": [info["path"]]})
        originals[info["source"]] = info["source_sha256"]
    write_json(inputs / "items.json", selected)
    source_names = ["reproduction/retest_docvqa_images.py", "reproduction/run_experiments.py",
                    "scripts/eval_only.py", "skillopt/model/azure_openai.py"]
    source_names += [str(file.relative_to(ROOT)) for file in sorted((ROOT / "skillopt/envs/docvqa").glob("*.py"))]
    manifest = {"created_at": now(), "benchmark": "docvqa", "source": str(source), "test_ids": expected,
                "selected_ids": list(IDS), "variants": list(VARIANTS), "selected_executions": 9,
                "model": MODEL, "reasoning_effort": "high", "credential_source": "reproduction/.env",
                "protocol": "One fresh evaluation per original failed (skill, task); keep every outcome, no best-of selection; "
                            "retain all 374 tasks; no training, evolution, or changes to previous results",
                "preprocessing": "If ceil(w/32)*ceil(h/32)>30000, multiply both original dimensions by 0.85 "
                                 "until <=28000 estimated patches; floor dimensions; RGB PNG with LANCZOS; no crop",
                "images": images, "original_sha256": originals,
                "input_sha256": {str(file): digest(file) for file in inputs.rglob("*") if file.is_file()},
                "code_sha256": {name: digest(ROOT / name) for name in source_names}}
    write_json(root / "manifest.json", manifest)
    (root / "manifest.sha256").write_text(digest(root / "manifest.json") + "\n")
    write_json(root / "status.json", {"phase": "prepared", "updated_at": now()})
    return manifest


def verify(root: Path) -> dict:
    if digest(root / "manifest.json") != (root / "manifest.sha256").read_text().strip():
        raise ValueError("Manifest changed")
    manifest = read(root / "manifest.json")
    for group in ("original_sha256", "input_sha256", "code_sha256"):
        for name, expected in manifest[group].items():
            if digest(ROOT / name) != expected:
                raise ValueError(f"Frozen evidence changed: {name}")
    return manifest


def trial(root: Path, variant: str) -> None:
    manifest = verify(root)
    directory = root / "evaluations" / variant
    directory.mkdir(parents=True, exist_ok=False)
    state = {"phase": "running", "started_at": now(), "variant": variant}
    write_json(directory / "status.json", state)
    try:
        os.environ["SKILLOPT_CREDENTIAL_SOURCE"] = "env_file"
        base, key = credentials()
        hostname = urlsplit(base).hostname
        if not hostname:
            raise ValueError("Invalid API hostname")
        bypass = ",".join(os.environ.get(name, "") for name in ("NO_PROXY", "no_proxy"))
        bypass = ",".join(dict.fromkeys([v.strip() for v in bypass.split(",") if v.strip()] + [hostname]))
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = bypass
        from skillopt.model import azure_openai

        azure_openai.configure_azure_openai(endpoint=base, api_key=key, auth_mode="openai_compatible")
        install_request_audit(directory / "request_metadata.jsonl")
        factory = azure_openai._make_client
        image_hashes = {info["sha256"] for info in manifest["images"].values()}

        def verify_image(request):
            payload = json.loads(request.content)
            found = []
            for message in payload["messages"]:
                if not isinstance(message.get("content"), list):
                    continue
                for part in message["content"]:
                    if part.get("type") == "image_url":
                        data = base64.b64decode(part["image_url"]["url"].split(",", 1)[1], validate=True)
                        found.append(hashlib.sha256(data).hexdigest())
            if len(found) != 1 or found[0] not in image_hashes:
                raise ValueError("Outgoing image differs from frozen resized inputs")

        def target_factory(role):
            if role != "target":
                raise RuntimeError("Optimizer calls forbidden in evaluation-only retest")
            client = factory(role)
            client._client.event_hooks.setdefault("request", []).append(verify_image)
            return client

        azure_openai._make_client = target_factory
        from scripts import eval_only

        original_get_adapter = eval_only.get_adapter

        def get_adapter(cfg):
            adapter = original_get_adapter(cfg)
            original_build = adapter.build_eval_env

            def build(env_num, split, seed, **kwargs):
                if (env_num, split, seed) != (0, "valid_unseen", 42):
                    raise ValueError("Changed evaluation settings")
                full = original_build(env_num, split, seed, **kwargs)
                if [str(item["id"]) for item in full] != manifest["test_ids"]:
                    raise ValueError("Original full test IDs/order changed")
                return read(root / "inputs/items.json")

            adapter.build_eval_env = build
            return adapter

        eval_only.get_adapter = get_adapter
        sys.argv = ["scripts/eval_only.py", "--config", str(root / "inputs/config.json"),
                    "--skill", str(root / "inputs" / f"{variant}.md"), "--split", "valid_unseen",
                    "--out_root", str(directory)]
        eval_only.main()
        metrics = collect_trial(directory, "docvqa", list(IDS))
        write_json(directory / "trial_metrics.json", metrics)
        write_json(directory / "token_usage.json", azure_openai.tracker.summary())
        state.update(phase="completed_with_errors" if metrics["execution_failures"] else "completed", finished_at=now())
    except BaseException as error:
        state.update(phase="failed", error_type=type(error).__name__, finished_at=now())
        raise
    finally:
        write_json(directory / "status.json", state)


def report(root: Path) -> dict:
    manifest = verify(root)
    summary = {"updated_at": now(), "protocol": manifest["protocol"], "test_count": len(manifest["test_ids"]),
               "verified_executions": 0, "variants": {}, "complete": True}
    lines = ["# DocVQA 普通实验：超限图片处理后补测", "", manifest["protocol"], "",
             "仅替换每个 Skill 原始失败的3题，其他371题原样保留；属于补测合并结果，并非重新全量评测。",
             "原图与原始结果不修改；图片预处理方式改变，补测分数单独报告。", "",
             "| Skill | 原始 hard | 补测3题正确数 | 合并 hard（374题） | 原始 ANLS | 合并 ANLS | 补测执行异常 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for variant, folder in VARIANTS.items():
        directory = root / "evaluations" / variant
        if not (directory / "trial_metrics.json").exists():
            summary["complete"] = False
            continue
        old = rows(Path(manifest["source"]) / folder / "results.jsonl")
        fresh = rows(directory / "results.jsonl")
        combined = merge(old, fresh, manifest["test_ids"])
        destination = root / "composite" / variant
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in combined))
        errors = read(directory / "trial_metrics.json")["execution_failures"]
        result = {"original_hard": sum(row["hard"] for row in old) / len(old),
                  "composite_hard": sum(row["hard"] for row in combined) / len(combined),
                  "original_soft": sum(row["soft"] for row in old) / len(old),
                  "composite_soft": sum(row["soft"] for row in combined) / len(combined),
                  "retest_correct": sum(row["hard"] for row in fresh), "retest_execution_failures": errors,
                  "replaced_ids": list(IDS), "denominator": len(combined)}
        summary["variants"][variant] = result
        summary["verified_executions"] += len(fresh)
        lines.append(f"| {variant} | {result['original_hard']:.2%} | {result['retest_correct']}/3 | "
                     f"{result['composite_hard']:.2%} | {result['original_soft']:.2%} | {result['composite_soft']:.2%} | {errors} |")
    write_json(root / "retest_summary.json", summary)
    (root / "REPORT.md").write_text("\n".join(lines) + "\n")
    return summary


def run(root: Path) -> None:
    with (root / "worker.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify(root)
        state = {"phase": "running", "started_at": now(), "worker_pid": os.getpid()}
        try:
            for variant in VARIANTS:
                state.update(phase="evaluating", variant=variant, updated_at=now())
                write_json(root / "status.json", state)
                with (root / f"{variant}.log").open("x") as log:
                    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "trial", "--run-root", str(root),
                                             "--variant", variant], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                summary = report(root)
                print(f"[{now()}] {variant} returncode={result.returncode} verified={summary['verified_executions']}/9", flush=True)
                if result.returncode:
                    raise RuntimeError("Retest subprocess failed; original evidence preserved")
            errors = sum(item["retest_execution_failures"] for item in summary["variants"].values())
            state.update(phase="completed_with_errors" if errors else "completed", finished_at=now())
        except BaseException as error:
            state.update(phase="failed", error_type=type(error).__name__, finished_at=now())
            raise
        finally:
            write_json(root / "status.json", state)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check", "run", "trial", "report"))
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=ROOT / "reproduction/outputs/terra_high_four_20261105/docvqa")
    parser.add_argument("--variant", choices=VARIANTS)
    args = parser.parse_args()
    os.chdir(ROOT)
    root = args.run_root.resolve()
    if not root.is_relative_to(ROOT / "reproduction/outputs"):
        parser.error("Keep outputs under reproduction/outputs")
    if args.action == "prepare":
        result = prepare(args.source.resolve(), root)
        print(json.dumps({"root": str(root), "selected_executions": 9, "images": result["images"]}, indent=2))
    elif args.action == "check":
        result = verify(root)
        print(json.dumps({"frozen_inputs_verified": True, "selected_ids": result["selected_ids"]}))
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
            # Native timed-out request threads must not hold the fresh child
            # alive after its durable status/results have been written.
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(code)
    elif args.action == "run":
        run(root)
    else:
        print(json.dumps(report(root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
