"""Collect ALFWorld train traces, author one Codex skill, and run paired evaluations."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reproduction.prepare_official_data import digest  # noqa: E402
from reproduction.run_experiments import MODEL, credentials, install_request_audit, write_json  # noqa: E402
from reproduction.skill_creator_author import (  # noqa: E402
    AUTHOR_MODEL,
    SKILL_NAME,
    author,
    instructions,
    packet_manifest,
)
from reproduction.skill_creator_author import freeze_skill as freeze_authored_skill  # noqa: E402
from reproduction.stability_metrics import paired_scores, validate_coverage  # noqa: E402
from skillopt.config import flatten_config, load_config  # noqa: E402
from skillopt.envs.alfworld.rollout import SYSTEM_PROMPT, build_alfworld_env, run_alfworld_batch  # noqa: E402

REPRO = ROOT / "reproduction"
CONFIG = REPRO / "configs/alfworld_terra_high.yaml"
PROMPTS = ROOT / "skillopt/envs/alfworld/prompts"
SPLITS = {"train": ("train", 39), "val": ("valid_seen", 18), "test": ("valid_unseen", 134)}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def text_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_plan(repeats, smoke, validation):
    cfg = flatten_config(load_config(str(CONFIG)))
    if (cfg["target_model"], cfg["reasoning_effort"]) != (MODEL, "high"):
        raise ValueError(f"Expected {MODEL} / high in {CONFIG}")
    data = Path(os.environ.get("ALFWORLD_DATA") or REPRO / "data/alfworld").resolve()
    os.environ["ALFWORLD_DATA"] = str(data)
    items = {}
    for split, (subdir, count) in SPLITS.items():
        rows = read(ROOT / cfg["split_dir"] / split / "items.json")
        if len(rows) != count or len({r["id"] for r in rows}) != count or len({r["gamefile"] for r in rows}) != count:
            raise ValueError(f"Unexpected official {split} manifest")
        if smoke:
            rows = [] if split == "test" else rows[:1]
        items[split] = rows
        for row in items[split]:
            path = Path(row["gamefile"])
            if path.parts[:2] != ("json_2.1.1", subdir) or not (data / path).is_file():
                raise ValueError(f"Missing or misplaced {split} gamefile: {path}")
    return {
        "version": 1,
        "smoke": smoke,
        "repeats": repeats,
        "validation": validation,
        "target": MODEL,
        "author": AUTHOR_MODEL,
        "effort": "high",
        "seed": cfg["seed"],
        "max_steps": cfg["max_steps"],
        "max_completion_tokens": cfg["max_completion_tokens"],
        "train_workers": cfg["workers"],
        "test_workers": 8,
        "splits": items,
        "protocol_sha256": text_digest(protocol_text(cfg["max_steps"])),
        "author_prompt_sha256": text_digest(instructions(len(items["train"]))),
        "gamefiles_sha256": {r["id"]: digest(data / r["gamefile"]) for rows in items.values() for r in rows},
    }


def protocol_text(max_steps):
    parts = [
        SYSTEM_PROMPT,
        f"Each episode allows {max_steps} actions. After reset the prompt includes "
        "the current observation. Later prompts include the two most recent observations and actions.",
    ]
    for filename in ("rollout_no_history.md", "rollout_with_history.md"):
        parts.append((PROMPTS / filename).read_text())
    return "\n\n".join(parts)


def configure_target(root):
    from skillopt import model

    base, key = credentials()
    model.configure_azure_openai(endpoint=base, api_key=key, auth_mode="openai_compatible")
    model.set_target_backend("openai_chat")
    model.set_target_deployment(MODEL)
    model.set_reasoning_effort("high")
    install_request_audit(root / "target_requests.jsonl")


def run_chunk(folder, tasks, split, seed, plan, skill):
    """Only publish complete chunks. A retry gets a fresh native output directory."""
    identity = {"ids": [r["id"] for r in tasks], "split": split, "seed": seed, "skill_sha256": text_digest(skill)}
    if not folder.exists():
        stage = Path(tempfile.mkdtemp(prefix=f".{folder.name}-", dir=folder.parent))
        dataset = {"train": "train", "val": "eval_in_distribution", "test": "eval_out_of_distribution"}[split]
        env = build_alfworld_env(
            len(tasks),
            eval_dataset=dataset,
            is_train=split == "train",
            seed=seed,
            specific_gamefiles=[r["gamefile"] for r in tasks],
        )
        try:
            rows = run_alfworld_batch(
                env,
                skill,
                max_steps=plan["max_steps"],
                out_root=str(stage),
                result_ids=identity["ids"],
                max_api_workers=len(tasks),
                max_completion_tokens=plan["max_completion_tokens"],
                record_model_io=True,
                raise_on_api_error=True,
            )
        finally:
            env.close()
        validate_coverage(rows, identity["ids"])
        hashes = {}
        for task, row in zip(tasks, rows, strict=True):
            if row["id"] != task["id"] or Path(row["gamefile"]) != Path(os.environ["ALFWORLD_DATA"]) / task["gamefile"]:
                raise ValueError(f"Simulator ran a different game for {row['id']}")
            rel = f"predictions/{row['id']}/conversation.json"
            steps = read(stage / rel)
            if not steps or len(steps) != row["n_turns"] or any("user_prompt" not in step for step in steps):
                raise ValueError(f"Incomplete trace for {row['id']}")
            hashes[rel] = digest(stage / rel)
            row["format_fallbacks"] = sum(
                step["raw_model_response"] is None or step["raw_model_response"].strip() != step["model_response"]
                for step in steps
            )
            row["tokens"] = {
                key: sum((s["usage"] or {}).get(key, 0) for s in steps)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            }
        write_json(stage / "results.json", rows)
        hashes["results.json"] = digest(stage / "results.json")
        write_json(stage / "complete.json", {"identity": identity, "sha256": hashes})
        stage.rename(folder)
    receipt = read(folder / "complete.json")
    expected_files = {"results.json"} | {f"predictions/{i}/conversation.json" for i in identity["ids"]}
    if (
        receipt["identity"] != identity
        or set(receipt["sha256"]) != expected_files
        or any(digest(folder / rel) != h for rel, h in receipt["sha256"].items())
    ):
        raise ValueError(f"Committed batch differs from its manifest: {folder}")
    return read(folder / "results.json")


def run_split(folder, rows, split, plan, skill):
    folder.mkdir(parents=True, exist_ok=True)
    workers = plan["train_workers" if split == "train" else "test_workers"]
    collected = []
    for start in range(0, len(rows), workers):
        chunk = folder / f"batch_{start:03d}"
        batch = rows[start : start + workers]
        print(f"[{folder.name}] {start + 1}–{start + len(batch)} / {len(rows)}", flush=True)
        results = run_chunk(chunk, batch, split, plan["seed"] + start, plan, skill)
        for r in results:
            trace = chunk / "predictions" / r["id"] / "conversation.json"
            collected.append((r, trace))
    validate_coverage([r for r, _ in collected], [r["id"] for r in rows])
    return collected


def packet_lines(episodes):
    for row, trace in episodes:
        entry = {k: row[k] for k in ("id", "task_description", "task_type", "hard", "n_turns", "fail_reason")}
        entry["steps"] = read(trace)
        yield json.dumps(entry, ensure_ascii=False) + "\n"


def training_packet(root, episodes, plan):
    packet = root / "training_input"
    ids = [r["id"] for r, _ in episodes]
    if ids != [r["id"] for r in plan["splits"]["train"]]:
        raise ValueError("Training packet requires exactly the planned training IDs in order")
    if not packet.exists():
        stage = Path(tempfile.mkdtemp(prefix=".packet-", dir=root))
        (stage / "protocol.txt").write_text(protocol_text(plan["max_steps"]))
        with (stage / "trajectories.jsonl").open("w", encoding="utf-8") as out:
            out.writelines(packet_lines(episodes))
        write_json(stage / "READY.json", packet_manifest(stage, ids))
        stage.rename(packet)
    else:
        with (packet / "trajectories.jsonl").open(encoding="utf-8") as saved:
            if any(a != b for a, b in zip(saved, packet_lines(episodes), strict=True)):
                raise ValueError("Training packet differs from the committed rollouts")
    manifest = packet_manifest(packet, ids)
    if read(packet / "READY.json") != manifest or manifest["sha256"]["protocol.txt"] != plan["protocol_sha256"]:
        raise ValueError("Training packet changed after publication")
    return packet


def freeze_skill(root, packet, plan, author_env):
    return freeze_authored_skill(
        root, packet, plan, author_env, prompt=instructions(len(plan["splits"]["train"])),
        skill_name=SKILL_NAME, generate=author,
    )


def paired_report(rounds):
    report = paired_scores(rounds)
    report["execution"] = {
        variant: execution_metrics([row for trial in rounds for row in trial[variant].values()])
        for variant in ("none", "creator")
    }
    return report


def execution_metrics(rows):
    return {
        "episodes": len(rows),
        "steps": sum(row["n_turns"] for row in rows),
        "format_fallbacks": sum(row["format_fallbacks"] for row in rows),
        "failures": dict(Counter(row["fail_reason"] for row in rows if not row["hard"])),
        "tokens": {
            key: sum(row["tokens"][key] for row in rows)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
    }


def evaluate(root, plan, split, repeats, body):
    rounds = []
    for repeat in range(1, repeats + 1):
        trial = {}
        order = ("none", "creator") if repeat % 2 else ("creator", "none")
        for variant in order:
            folder = root / split / f"repeat_{repeat:02d}" / variant
            print(f"[{split}] repeat {repeat}/{repeats}, {variant}", flush=True)
            rows = run_split(folder, plan["splits"][split], split, plan, body if variant == "creator" else "")
            trial[variant] = {row["id"]: row for row, _ in rows}
        rounds.append(trial)
    return paired_report(rounds)


def run(root, plan):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"Another experiment already owns {root}") from exc
        path = root / "plan.json"
        if not path.exists():
            write_json(path, plan)
        elif read(path) != plan:
            raise ValueError(f"Run configuration or data changed; use a separate --run-dir from {root}")
        # The target's credential loader may replace provider variables. Codex
        # uses the author's original login/provider independently of the target.
        author_env = os.environ.copy()
        configure_target(root)
        train = run_split(root / "train", plan["splits"]["train"], "train", plan, "")
        packet = training_packet(root, train, plan)
        print(f"Training packet ready ({len(train)} episodes). Preparing frozen Codex skill.", flush=True)
        body = freeze_skill(root, packet, plan, author_env)
        report = {
            "mode": "smoke" if plan["smoke"] else "full",
            "training_episodes": len(train),
            "training_successes": sum(r["hard"] for r, _ in train),
            "training_execution": execution_metrics([r for r, _ in train]),
            "frozen": read(root / "frozen/manifest.json"),
        }
        if plan["validation"] or plan["smoke"]:
            report["val"] = evaluate(root, plan, "val", 1, body)
        if not plan["smoke"]:
            report["test"] = evaluate(root, plan, "test", plan["repeats"], body)
        write_json(root / "summary.json", report)
        score = report["val" if plan["smoke"] else "test"]
        print(
            f"none={score['success_rate']['none']:.2%}, creator={score['success_rate']['creator']:.2%}, "
            f"delta={score['delta_pp']:+.2f} pp. Full results: {root / 'summary.json'}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, help="Output directory; repeat the same command to resume")
    parser.add_argument("--repeats", type=int, default=5, help="Paired full test runs per condition (default: 5)")
    parser.add_argument("--validation", action="store_true", help="Also evaluate all 18 validation tasks once per arm")
    parser.add_argument("--smoke", action="store_true", help="One train task and one paired validation task")
    parser.add_argument(
        "--check", action="store_true", help="Check local data, config and prerequisites without model calls"
    )
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    root = (
        args.run_dir or REPRO / "outputs" / ("alfworld_skill_creator_smoke" if args.smoke else "alfworld_skill_creator")
    ).resolve()
    os.chdir(ROOT)
    plan = make_plan(1 if args.smoke else args.repeats, args.smoke, args.validation)
    if shutil.which("codex") is None:
        parser.error("The Codex CLI must be available on PATH")
    if args.check:
        credentials()
        test_repeats = 0 if args.smoke else plan["repeats"]
        print(
            json.dumps(
                {
                    "target": plan["target"],
                    "author": plan["author"],
                    "effort": plan["effort"],
                    "tasks": {s: len(rows) for s, rows in plan["splits"].items()},
                    "test_repeats": test_repeats,
                    "train_episodes": len(plan["splits"]["train"]),
                    "val_episodes_per_arm": len(plan["splits"]["val"]) if args.validation or args.smoke else 0,
                    "test_episodes_per_arm": len(plan["splits"]["test"]) * test_repeats,
                    "run_dir": str(root),
                },
                indent=2,
            )
        )
        return
    run(root, plan)


if __name__ == "__main__":
    main()
