"""Collect training traces, author one isolated Codex skill, then evaluate the frozen pair."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reproduction.prepare_official_data import digest, manifest_items, verify_materialized  # noqa: E402
from reproduction.run_experiments import MODEL, REPRO, credentials, install_request_audit, now, write_json  # noqa: E402
from reproduction.skill_creator_author import AUTHOR_MODEL, freeze_skill, packet_manifest  # noqa: E402
from reproduction.stability_metrics import fingerprint, paired_scores, task_directory, validate_coverage  # noqa: E402
from reproduction.tool_reasoning_compat import install_tool_reasoning  # noqa: E402
from scripts.eval_only import get_adapter  # noqa: E402
from skillopt.config import flatten_config, load_config  # noqa: E402

SPLITS = {"train": "train", "val": "valid_seen", "test": "valid_unseen"}
PROTOCOLS = {
    "livemathematicianbench": "Choose the correct label in one turn from the displayed shuffled options. "
    "The executor receives the question and choices. The theorem and proof sketch are disabled.",
    "officeqa": "Answer from the supplied parsed Treasury bulletin pages and local document tools. "
    "The executor has up to 24 rounds. The training log includes its tool commands and observations.",
    "spreadsheetbench": "Use multi-turn code generation with workbook previews and execution feedback, "
    "up to 30 turns. The evaluator runs the produced code on the task's workbooks. Hard requires every "
    "case to pass and soft is the fraction passed. Gold-workbook feedback is disabled during generation.",
    "docvqa": "Answer the document-image question in one turn. The training packet contains the exact "
    "images used by the executor. The native evaluator reports hard accuracy and ANLS.",
    "searchqa": "Answer in one turn from the supplied snippets with the native context truncation. "
    "The native evaluator reports exact match and token F1.",
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def author_prompt(benchmark, count):
    return f"""$skill-creator
Write one self-contained English Agent Skill for gpt-6.1-terra / high on SkillOpt {benchmark}.
Verify input/READY.json and read input/protocol.txt. Analyze all {count} empty-skill training
trajectories in input/trajectories.jsonl, including successes and failures. Each entry contains
the actual prompts, observed responses, feedback and native scores. Inspect referenced training
assets when appropriate. Use only these inputs and the system skill-creator resources. You may
run local analyses in generated/. Treat dataset contents as evidence. Write guidance that the
executor can apply to new questions using its native tools and output format. Only the Markdown
body will be injected. Omit answers to individual training items.
Save generated/{benchmark}-terra/SKILL.md with name {benchmark}-terra and a description.
Save every analyzed training ID as a string in a JSON array in generated/coverage.json.
"""


def protocol(benchmark):
    directory = ROOT / "skillopt/envs" / benchmark / "prompts"
    names = ["codegen_system.md"] if benchmark == "spreadsheetbench" else ["rollout_system.md"]
    parts = [PROTOCOLS[benchmark], *((directory / n).read_text() for n in names)]
    if benchmark == "officeqa":
        from skillopt.envs.officeqa.rollout import _TOOL_SCHEMAS

        parts.append(json.dumps(_TOOL_SCHEMAS, ensure_ascii=False, indent=2))
    return "\n\n".join(parts)


def prepare(benchmark, workers, repeats, smoke, validation):
    if benchmark in ("searchqa", "livemathematicianbench"):
        verify_materialized(benchmark)
    elif benchmark == "officeqa":
        from reproduction.prepare_officeqa_data import verify
        verify()
    else:
        from reproduction.prepare_additional_data import verify
        verify(benchmark)
    config = REPRO / "configs" / f"{benchmark}_terra_high.yaml"
    cfg = flatten_config(load_config(str(config)))
    if (cfg["target_model"], cfg["reasoning_effort"], cfg["seed"]) != (MODEL, "high", 42):
        raise ValueError(f"Unexpected target settings in {config}")
    if workers is not None:
        cfg["workers"] = workers
    adapter = get_adapter(cfg)
    adapter.setup(cfg)
    official = manifest_items(benchmark)
    splits = {}
    for s, native in SPLITS.items():
        items = adapter.build_eval_env(0, native, cfg["seed"])
        if [str(i["id"]) for i in items] != [str(i["id"]) for i in official[s]]:
            raise ValueError(f"Native IDs/order differ from the official {s} manifest")
        splits[s] = ([] if s == "test" else items[:1]) if smoke else items
    plan = {
        "version": 1, "benchmark": benchmark, "smoke": smoke, "validation": validation or smoke,
        "repeats": 1 if smoke else repeats, "target": MODEL, "author": AUTHOR_MODEL, "effort": "high",
        "seed": cfg["seed"], "workers": cfg["workers"], "config": str(config.relative_to(ROOT)),
        "config_sha256": digest(config), "resolved_config_sha256": fingerprint(cfg),
        "provenance_sha256": digest(Path(cfg["split_dir"]) / "provenance.json"),
        "settings": {k: cfg[k] for k in ("max_completion_tokens", "exec_timeout", "max_turns", "max_tool_turns",
                     "shuffle_choices", "use_sketch", "use_theorem", "search_mode", "mode", "image_detail")
                     if k in cfg},
        "splits": {s: [{"id": str(i["id"])} for i in items] for s, items in splits.items()},
        "split_payload_sha256": {s: fingerprint(items) for s, items in splits.items()},
        "protocol_sha256": text_hash(protocol(benchmark)),
        "author_prompt_sha256": text_hash(author_prompt(benchmark, len(splits["train"]))),
    }
    if benchmark == "docvqa":
        from reproduction import retest_docvqa_images
        plan["image_preprocessing_sha256"] = digest(Path(retest_docvqa_images.__file__))
    return plan, adapter, splits


def text_hash(value):
    from hashlib import sha256
    return sha256(value.encode("utf-8")).hexdigest()


def prepare_images(root, splits):
    from PIL import Image

    from reproduction.retest_docvqa_images import patches, resize_image

    directory = root / "processed_images"
    directory.mkdir(exist_ok=True)
    path = directory / "manifest.json"
    prior = read(path) if path.exists() else {}
    entries = {}
    for items in splits.values():
        for item in items:
            source = Path(item["image_path"]).resolve()
            with Image.open(source) as img:
                if patches(*img.size) <= 30000:
                    continue
            key = digest(source)
            dest = directory / f"{key}.png"
            if key not in entries:
                if key in prior and dest.is_file():
                    if digest(dest) != prior[key]["sha256"]:
                        raise ValueError(f"Processed image was modified: {dest}")
                    entries[key] = prior[key]
                else:
                    temp = directory / f"{key}.partial.png"
                    info = resize_image(source, temp)
                    temp.replace(dest)
                    entries[key] = {**info, "path": str(dest)}
            item.update(image_path=str(dest), image_paths=[str(dest)])
    if path.exists() and entries != prior:
        raise ValueError("Processed images differ from the published manifest")
    write_json(path, entries)


def configure_target(root, phase):
    import httpx
    from openai import DefaultHttpxClient

    from reproduction.retry_docvqa_proxy import configured_proxy, is_proxy_transport
    from skillopt import model
    from skillopt.model import azure_openai

    os.environ["SKILLOPT_CREDENTIAL_SOURCE"] = "env_file"
    base, key = credentials()
    model.configure_azure_openai(endpoint=base, api_key=key, auth_mode="openai_compatible")
    model.set_target_backend("openai_chat")
    model.set_target_deployment(MODEL)
    model.set_reasoning_effort("high")
    factory = azure_openai._make_client
    lock = threading.Lock()
    try:
        proxy_name, proxy_url = configured_proxy(base)
    except ValueError:
        proxy_name, proxy_url = "", ""
    write_json(root / "target_route.json", {"configured_proxy": proxy_name, "credential_source": "reproduction/.env"})

    def make_client(role):
        if role != "target":
            raise RuntimeError("Only the target should make rollout requests")
        client = factory(role)
        if proxy_url:
            client._client.close()
            client._client = DefaultHttpxClient(proxy=proxy_url, trust_env=False)
            if not is_proxy_transport(client._client._transport_for_url(httpx.URL(base))):
                raise RuntimeError("Expected the configured proxy for the target endpoint")
        complete = client.chat.completions.create

        def measured(**kwargs):
            label = phase["name"]
            response = complete(**kwargs)
            usage = response.usage
            record = {"time": now(), "phase": label, "usage": usage.model_dump() if usage else None}
            with lock, (root / "target_usage.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")
            return response

        client.chat.completions.create = measured
        return client

    azure_openai._make_client = make_client
    install_tool_reasoning()
    install_request_audit(root / "target_requests.jsonl")


class IncompleteRollout(RuntimeError):
    """A native API or adapter failure prevented an observable task execution."""


def native_files(raw, item_id, skill):
    results = raw / "results.jsonl"
    rows = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    if not rows:
        raise IncompleteRollout(f"Native result was not published for {item_id}: {raw}")
    validate_coverage(rows, [item_id])
    row = rows[0]
    reason = str(row.get("fail_reason") or "").lower()
    if (reason.startswith(("error: llm", "llm-call-failed:", "task-timeout-"))
            or row.get("phase") in ("error", "timeout")):
        raise IncompleteRollout(f"Native request or task timeout for {item_id}. See {results}")
    pred = task_directory(raw, item_id)
    if not pred:
        raise IncompleteRollout(f"Missing native prediction directory for {item_id}: {raw}")
    names = ["conversation.json", "target_system_prompt.txt", "target_user_prompt.txt"]
    files = [pred / n for n in names]
    if not all(f.is_file() for f in files) or not read(files[0]):
        raise IncompleteRollout(f"Missing native transcript for {item_id}: {raw}")
    if skill and skill not in files[1].read_text():
        raise ValueError(f"Frozen body missing from actual system prompt for {item_id}")
    return row, pred, [results, *files]


def run_item(adapter, item, folder, skill):
    item_id = str(item["id"])
    identity = {"id": item_id, "skill_sha256": text_hash(skill)}
    folder.mkdir(parents=True, exist_ok=True)
    receipt = folder / "complete.json"
    if receipt.is_file():
        record = read(receipt)
        row, pred, files = native_files(folder / record["attempt"], item_id, skill)
        hashes = {str(p.relative_to(folder)): digest(p) for p in files}
        if record["identity"] != identity or hashes != record["sha256"]:
            raise ValueError(f"Committed item changed: {folder}")
        return row, pred

    # Recover a complete native result if the process exited before publishing its receipt.
    prior = sorted((p for p in folder.glob("attempt-*") if (p / "identity.json").is_file()),
                   key=lambda p: (p / "identity.json").stat().st_mtime_ns)
    for attempt in [*prior, None, None]:
        if attempt is None:
            attempt = Path(tempfile.mkdtemp(prefix="attempt-", dir=folder))
            write_json(attempt / "identity.json", identity)
            try:
                adapter.rollout([item], skill, str(attempt))
            except RuntimeError:
                if not (attempt / "results.jsonl").is_file():
                    raise
        elif read(attempt / "identity.json") != identity:
            raise ValueError(f"Cannot recover a different item/configuration at {attempt}")
        if not (attempt / "results.jsonl").is_file():
            continue
        try:
            row, pred, files = native_files(attempt, item_id, skill)
        except (IncompleteRollout, json.JSONDecodeError) as exc:
            write_json(attempt / "incomplete.json", {"type": type(exc).__name__})
            continue
        write_json(receipt, {"identity": identity, "attempt": attempt.name,
                             "sha256": {str(p.relative_to(folder)): digest(p) for p in files}})
        return row, pred
    raise IncompleteRollout(f"Native rollout incomplete after technical retry: {folder}")


def run_split(root, label, items, skill, adapter, plan, phase):
    phase["name"] = label
    directory = root / label
    directory.mkdir(parents=True, exist_ok=True)
    expected = [str(i["id"]) for i in items]
    done = {}
    queue = iter(enumerate(items))
    status = {"phase": label, "total": len(items), "workers": plan["workers"]}
    write_json(root / "status.json", {**status, "completed": 0, "updated_at": now()})
    with ThreadPoolExecutor(max_workers=plan["workers"]) as pool:
        pending = set()

        def submit_next():
            next_item = next(queue, None)
            if next_item is not None:
                index, item = next_item
                pending.add(pool.submit(run_item, adapter, item, directory / f"task_{index:04d}", skill))

        for _ in range(min(plan["workers"], len(items))):
            submit_next()
        while pending:
            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in finished:
                row, pred = future.result()
                done[str(row["id"])] = (row, pred)
            write_json(root / "status.json", {**status, "completed": len(done), "updated_at": now()})
            for _ in finished:
                submit_next()
    ordered = [done[i] for i in expected]
    validate_coverage([r for r, _ in ordered], expected)
    return ordered


def training_packet(root, plan, episodes, items):
    packet = root / "training_input"
    ids = [r["id"] for r in plan["splits"]["train"]]
    if [str(row["id"]) for row, _ in episodes] != ids:
        raise ValueError("Training packet does not match the official training IDs")
    stage = Path(tempfile.mkdtemp(prefix=".packet-", dir=root))
    try:
        (stage / "protocol.txt").write_text(protocol(plan["benchmark"]))
        with (stage / "trajectories.jsonl").open("w", encoding="utf-8") as stream:
            for (row, pred), item in zip(episodes, items, strict=True):
                entry = {k: row[k] for k in ("id", "task_type", "hard", "soft", "fail_reason", "n_turns",
                         "correct_label", "correct_text", "ground_truth", "gold_answer", "gold_answers",
                         "predicted_answer", "n_cases", "n_pass", "cases") if k in row}
                entry["id"] = str(row["id"])
                entry["system_prompt"] = (pred / "target_system_prompt.txt").read_text()
                entry["user_prompt"] = (pred / "target_user_prompt.txt").read_text()
                entry["conversation"] = read(pred / "conversation.json")
                if plan["benchmark"] == "docvqa":
                    image = Path(item["image_path"])
                    asset = stage / "assets" / (digest(image) + image.suffix.lower())
                    asset.parent.mkdir(exist_ok=True)
                    if not asset.exists():
                        shutil.copyfile(image, asset)
                    entry["image"] = str(asset.relative_to(stage))
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        manifest = packet_manifest(stage, ids)
        if manifest["sha256"]["protocol.txt"] != plan["protocol_sha256"]:
            raise ValueError("Training protocol changed")
        if packet.exists():
            if read(packet / "READY.json") != manifest or packet_manifest(packet, ids) != manifest:
                raise ValueError("Published training packet differs from the committed trajectories")
        else:
            write_json(stage / "READY.json", manifest)
            stage.rename(packet)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return packet


def evaluate(root, split, repeats, body, adapter, items, plan, phase):
    rounds = []
    for repeat in range(1, repeats + 1):
        variants = ("none", "creator") if repeat % 2 else ("creator", "none")
        pair = {}
        for variant in variants:
            label = f"{split}/repeat_{repeat:02d}/{variant}"
            episodes = run_split(root, label, items, body if variant == "creator" else "", adapter, plan, phase)
            pair[variant] = {
                str(r["id"]): {"task_type": r.get("task_type") or plan["benchmark"], "hard": r["hard"], "soft": r["soft"]}
                for r, _ in episodes
            }
        rounds.append(pair)
    return {metric: paired_scores(rounds, metric) for metric in ("hard", "soft")}


def run(root, plan, adapter, splits):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan_path = root / "plan.json"
        if plan_path.exists() and read(plan_path) != plan:
            raise ValueError(f"Run inputs or settings changed. Choose another directory: {root}")
        write_json(plan_path, plan)
        source_record = root / "implementation.json"
        if not source_record.exists():
            sources = [Path(__file__).resolve(), REPRO / "skill_creator_author.py", REPRO / "stability_metrics.py",
                       REPRO / "tool_reasoning_compat.py", REPRO / "run_experiments.py"]
            if plan["settings"].get("workbook_execution") == "bubblewrap":
                sources.append(REPRO / "spreadsheet_isolation.py")
            sources.extend(sorted((ROOT / "skillopt/envs" / plan["benchmark"]).glob("*.py")))
            write_json(source_record, {
                "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "source_sha256": {str(p.relative_to(ROOT)): digest(p) for p in sources},
            })
        if plan["benchmark"] == "docvqa":
            splits = {s: [dict(item) for item in items] for s, items in splits.items()}
            prepare_images(root, splits)
        phase = {"name": "train"}
        try:
            author_env = os.environ.copy()
            configure_target(root, phase)
            train = run_split(root, "train", splits["train"], "", adapter, plan, phase)
            packet = training_packet(root, plan, train, splits["train"])
            phase["name"] = "author"
            write_json(root / "status.json", {"phase": "author", "training_items": len(train), "updated_at": now()})
            name = plan["benchmark"]
            body = freeze_skill(root, packet, plan, author_env, prompt=author_prompt(name, len(train)),
                                skill_name=f"{name}-terra")
            report = {"benchmark": name, "mode": "smoke" if plan["smoke"] else "full",
                      "training_items": len(train), "frozen": read(root / "frozen/manifest.json")}
            if plan["validation"]:
                report["val"] = evaluate(root, "val", 1, body, adapter, splits["val"], plan, phase)
            if not plan["smoke"]:
                report["test"] = evaluate(root, "test", plan["repeats"], body, adapter, splits["test"], plan, phase)
            usage = defaultdict(Counter)
            log = root / "target_usage.jsonl"
            if log.exists():
                for line in log.read_text().splitlines():
                    entry = json.loads(line)
                    values = entry["usage"] or {}
                    usage[entry["phase"]].update({k: values.get(k, 0) for k in
                                                  ("prompt_tokens", "completion_tokens", "total_tokens")})
                    usage[entry["phase"]]["calls"] += 1
            report["target_usage_by_phase"] = usage
            write_json(root / "summary.json", report)
            write_json(root / "status.json", {"phase": "completed", "updated_at": now()})
        except BaseException as exc:
            write_json(root / "status.json", {"phase": "interrupted", "stage": phase["name"],
                                              "error_type": type(exc).__name__, "updated_at": now()})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", choices=PROTOCOLS)
    parser.add_argument("--workers", type=int, help="Concurrent items per benchmark in both arms")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--validation", action="store_true")
    parser.add_argument("--check", action="store_true", help="Verify official data and settings without model calls")
    parser.add_argument("--smoke", action="store_true", help="One train item, one paired validation item")
    parser.add_argument("--isolate-workbooks", action="store_true",
                        help="Execute generated SpreadsheetBench Python in a private offline namespace")
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    if args.repeats < 1 or (args.workers is not None and args.workers < 1):
        parser.error("workers and repeats must be positive")
    if args.isolate_workbooks and args.benchmark != "spreadsheetbench":
        parser.error("--isolate-workbooks applies to SpreadsheetBench")
    os.chdir(ROOT)
    plan, adapter, splits = prepare(args.benchmark, args.workers, args.repeats, args.smoke, args.validation)
    if args.isolate_workbooks:
        from reproduction.spreadsheet_isolation import install

        install()
        plan["settings"]["workbook_execution"] = "bubblewrap"
    default = f"{args.benchmark}_skill_creator" + ("_smoke" if args.smoke else "")
    root = (args.run_dir or REPRO / "outputs" / default).resolve()
    if not shutil.which("codex"):
        parser.error("Codex must be on PATH")
    if args.check:
        os.environ["SKILLOPT_CREDENTIAL_SOURCE"] = "env_file"
        credentials()
        print(json.dumps({"benchmark": args.benchmark, "workers": plan["workers"], "repeats": plan["repeats"],
                          "counts": {k: len(v) for k, v in splits.items()}, "run_dir": str(root)}))
    else:
        run(root, plan, adapter, splits)


if __name__ == "__main__":
    main()
