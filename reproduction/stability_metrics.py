"""Repeated-test statistics and normalized fingerprints for saved trajectories."""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path

from skillopt.envs.task_output import task_output_segment

VARIANTS = ("none", "initial", "final")
REPEATS = 5


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def normalize(value, directory: Path):
    """Remove only run paths, transport metadata and random tool-call IDs."""
    ids = {}

    def visit(obj):
        if isinstance(obj, str):
            text = obj.replace(str(directory.resolve()), "<RUN>")
            text = text.replace(str(directory.resolve().relative_to(Path.cwd())), "<RUN>") if directory.resolve().is_relative_to(Path.cwd()) else text
            return text.replace("\r\n", "\n")
        if isinstance(obj, list):
            return [visit(item) for item in obj]
        if isinstance(obj, dict):
            result = {}
            for key, item in obj.items():
                if key in ("response_metadata", "usage", "timestamp", "elapsed", "latency"):
                    continue
                if key == "tool_call_id" or (key == "id" and obj.get("type") == "function"):
                    result[key] = ids.setdefault(str(item), f"tool_{len(ids) + 1}")
                else:
                    result[key] = visit(item)
            return result
        return obj

    return visit(value)


def task_directory(directory: Path, task_id: str) -> Path | None:
    root = directory / "predictions"
    mapped = root / task_output_segment(task_id, map_unsafe=True)
    if mapped.is_dir():
        return mapped
    raw = root / task_id
    if Path(task_id).name == task_id and "\\" not in task_id and raw.resolve().is_relative_to(root.resolve()) and raw.is_dir():
        return raw
    return None


def describe_item(row: dict, directory: Path, benchmark: str) -> dict:
    task_id = str(row["id"])
    path = task_directory(directory, task_id)
    conversation_path = path / "conversation.json" if path else None
    conversation = json.loads(conversation_path.read_text()) if conversation_path and conversation_path.exists() else None
    normalized = normalize(conversation, directory) if conversation is not None else None
    api_fallbacks = output_fallbacks = 0
    actions = None
    if benchmark == "alfworld" and isinstance(conversation, list):
        api_fallbacks = sum(event.get("model_response") == "<think>error</think><action>look</action>" for event in conversation)
        output_fallbacks = sum(event.get("model_response") in (
            "<think>empty model response</think><action>look</action>",
            "<think>missing action tag</think><action>look</action>",
        ) for event in conversation)
        actions = [event.get("action") for event in conversation]
    elif isinstance(normalized, list):
        # OfficeQA stores exact executed commands and observations. Other
        # adapters store assistant answer/code and tool feedback in the log.
        actions = [event for event in normalized if event.get("type") == "tool_call"]
        if not actions:
            actions = [event for event in normalized if event.get("role") == "assistant" or event.get("type") == "message"]
    prompts = {}
    if path:
        for name in ("target_system_prompt.txt", "target_user_prompt.txt"):
            file = path / name
            if file.exists():
                prompts[name] = normalize(file.read_text(), directory)
    reason = str(row.get("fail_reason") or "")
    flags = [key for key in ("agent_ok", "llm_ok", "code_ok", "exec_ok") if row.get(key) is False]
    if api_fallbacks:
        flags.append("alfworld_api_fallback")
    if output_fallbacks:
        flags.append("alfworld_output_fallback")
    if row.get("phase") in ("timeout", "error") or "task-timeout" in reason:
        flags.append("adapter_timeout_or_error")
    if row.get("ok") is False and not flags and not row.get("n_cases"):
        flags.append("unclassified_execution_failure")
    answer = row.get("predicted_label") or row.get("predicted_answer")
    if answer is not None:
        answer = re.sub(r"\s+", " ", str(answer)).strip().casefold()
    return {
        "id": task_id, "hard": float(row["hard"]), "soft": float(row["soft"]),
        "n_turns": row.get("n_turns"), "execution_ok": not flags, "failure_flags": flags,
        "api_fallbacks": api_fallbacks, "output_fallbacks": output_fallbacks,
        "answer_sha256": fingerprint(answer) if answer is not None else None,
        "trajectory_sha256": fingerprint(normalized) if normalized is not None else None,
        "behavior_sha256": fingerprint(actions) if actions else None,
        "prompt_sha256": fingerprint(prompts) if prompts else None,
        "conversation": str(conversation_path.relative_to(directory)) if conversation_path and conversation_path.exists() else None,
    }


def validate_coverage(rows: list[dict], expected_ids: list[str]) -> None:
    ids = [str(row["id"]) for row in rows]
    if len(ids) != len(expected_ids) or len(set(ids)) != len(ids) or set(ids) != set(expected_ids):
        raise ValueError("Results do not cover exactly the expected unique test IDs")
    if any(not math.isfinite(float(row[key])) or not 0 <= float(row[key]) <= 1 for row in rows for key in ("hard", "soft")):
        raise ValueError("Invalid official score")


def collect_trial(directory: Path, benchmark: str, expected_ids: list[str]) -> dict:
    rows = [json.loads(line) for line in (directory / "results.jsonl").read_text().splitlines() if line]
    validate_coverage(rows, expected_ids)
    summary = json.loads((directory / "eval_summary.json").read_text())
    hard = statistics.mean(float(row["hard"]) for row in rows)
    soft = statistics.mean(float(row["soft"]) for row in rows)
    if summary["n_items"] != len(rows) or abs(summary["hard"] - hard) > 1e-12 or abs(summary["soft"] - soft) > 1e-12:
        raise ValueError("Official evaluation summary disagrees with per-item scores")
    items = [describe_item(row, directory, benchmark) for row in rows]
    with (directory / "item_fingerprints.jsonl").open("w") as stream:
        for row in items:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    audit = [json.loads(line) for line in (directory / "request_metadata.jsonl").read_text().splitlines() if line]
    if not audit or any(row["model"] != "gpt-6.1-terra" or row["reasoning_effort"] != "high" for row in audit):
        raise ValueError("Actual request audit does not match Terra / High")
    return {"n": len(rows), "hard": hard, "soft": soft, "request_attempts": len(audit),
            "execution_failures": sum(not row["execution_ok"] for row in items),
            "failure_flags": dict(Counter(flag for row in items for flag in row["failure_flags"])),
            "missing_trajectories": sum(row["trajectory_sha256"] is None for row in items)}


def aggregate_items(repetitions: list[list[dict]], expected_ids: list[str]) -> tuple[dict, list[dict]]:
    if len(repetitions) != REPEATS:
        raise ValueError("avg@5 requires exactly five complete fresh evaluations")
    for rows in repetitions:
        validate_coverage(rows, expected_ids)
    maps = [{str(row["id"]): row for row in rows} for rows in repetitions]
    items = []
    for task_id in expected_ids:
        rows = [mapping[task_id] for mapping in maps]
        successes = sum(row["hard"] == 1 for row in rows)
        item = {"id": task_id, "hard_by_repeat": [row["hard"] for row in rows],
                "soft_by_repeat": [row["soft"] for row in rows], "successes_of_5": successes,
                "outcome": "always_success" if successes == 5 else "always_failure" if successes == 0 else "mixed",
                "execution_failures": sum(not row["execution_ok"] for row in rows),
                "n_turns_by_repeat": [row["n_turns"] for row in rows],
                "pairwise_outcome_disagreement": successes * (5 - successes) / 10,
                "conversations": [row["conversation"] for row in rows]}
        for field in ("answer", "trajectory", "behavior", "prompt"):
            hashes = [row[field + "_sha256"] for row in rows]
            item[f"{field}_hashes"] = hashes
            item[f"{field}_observations"] = sum(value is not None for value in hashes)
            item[f"unique_{field}s"] = len({value for value in hashes if value is not None})
        items.append(item)
    scores = [statistics.mean(row["hard"] for row in rows) for rows in repetitions]
    soft_scores = [statistics.mean(row["soft"] for row in rows) for rows in repetitions]
    counts = Counter(row["outcome"] for row in items)
    report = {"n": len(items), "repeats": 5, "avg_at_5_hard": statistics.mean(scores),
              "avg_at_5_soft": statistics.mean(soft_scores), "hard_by_repeat": scores,
              "soft_by_repeat": soft_scores, "hard_sample_std": statistics.stdev(scores),
              "success_count_histogram": {str(k): sum(row["successes_of_5"] == k for row in items) for k in range(6)},
              "always_success": counts["always_success"], "always_failure": counts["always_failure"],
              "mixed": counts["mixed"], "mixed_fraction": counts["mixed"] / len(items),
              "pairwise_outcome_disagreement": statistics.mean(row["pairwise_outcome_disagreement"] for row in items),
              "items_with_execution_failure": sum(row["execution_failures"] > 0 for row in items),
              "execution_failures": sum(row["execution_failures"] for row in items)}
    clean = [row for row in items if row["execution_failures"] == 0]
    report["five_clean_executions"] = {"n": len(clean), "mixed": sum(row["outcome"] == "mixed" for row in clean)}
    for field in ("answer", "trajectory", "behavior", "prompt"):
        complete = [row for row in items if row[f"{field}_observations"] == 5]
        varied = sum(row[f"unique_{field}s"] > 1 for row in complete)
        report[field + "_variation"] = {"complete_items": len(complete), "varied_items": varied,
                                         "varied_fraction": varied / len(complete) if complete else None}
    return report, items
