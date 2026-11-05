"""Statistical and trajectory regressions for repeated, independent tests."""
import json

import pytest

from reproduction.run_stability import schedule
from reproduction.stability_metrics import aggregate_items, describe_item, fingerprint, normalize, validate_coverage


def item(task_id, hard, *, ok=True, trace="same"):
    return {"id": task_id, "hard": hard, "soft": hard, "execution_ok": ok,
            "n_turns": 1, "conversation": f"predictions/{task_id}/conversation.json",
            "trajectory_sha256": trace, "behavior_sha256": trace,
            "answer_sha256": str(hard), "prompt_sha256": "fixed"}


def test_avg5_is_mean_accuracy_not_at_least_one_success():
    repetitions = [[item("always", 1), item("never", 0), item("mixed", int(index < 2))] for index in range(5)]
    # Different completion order must not change the pairing of individual tasks.
    repetitions[2].reverse()
    summary, rows = aggregate_items(repetitions, ["always", "never", "mixed"])
    assert summary["avg_at_5_hard"] == pytest.approx(7 / 15)
    assert summary["always_success"] == summary["always_failure"] == summary["mixed"] == 1
    assert summary["success_count_histogram"] == {"0": 1, "1": 0, "2": 1, "3": 0, "4": 0, "5": 1}
    assert rows[2]["pairwise_outcome_disagreement"] == pytest.approx(0.6)
    assert summary["pairwise_outcome_disagreement"] == pytest.approx(0.2)
    with pytest.raises(ValueError, match="exactly five"):
        aggregate_items(repetitions[:4], ["always", "never", "mixed"])


def test_execution_failures_and_missing_traces_are_not_silently_excluded():
    repetitions = [[item("a", int(i != 0), ok=i != 0, trace=None if i == 0 else str(i))] for i in range(5)]
    summary, rows = aggregate_items(repetitions, ["a"])
    assert summary["avg_at_5_hard"] == 0.8
    assert summary["execution_failures"] == summary["items_with_execution_failure"] == 1
    assert summary["five_clean_executions"] == {"n": 0, "mixed": 0}
    assert summary["trajectory_variation"]["complete_items"] == 0
    assert rows[0]["trajectory_observations"] == 4
    assert summary["prompt_variation"]["varied_items"] == 0


def test_coverage_rejects_missing_duplicate_or_foreign_test_items():
    for rows in ([item("a", 1)], [item("a", 1), item("a", 0)], [item("a", 1), item("c", 0)]):
        with pytest.raises(ValueError, match="expected unique test IDs"):
            validate_coverage(rows, ["a", "b"])


def test_fingerprints_ignore_transport_noise_and_run_paths_but_keep_actions(tmp_path):
    first, second = tmp_path / "repeat1", tmp_path / "repeat2"

    def conversation(path, call_id, action):
        return [{"role": "assistant", "content": str(path / "output.xlsx"), "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": action, "arguments": "{}"}},
        ], "response_metadata": {"request_id": call_id}},
            {"role": "tool", "tool_call_id": call_id, "content": "OK", "timestamp": call_id}]

    a = normalize(conversation(first, "random1", "read"), first)
    b = normalize(conversation(second, "random2", "read"), second)
    assert fingerprint(a) == fingerprint(b)
    changed = normalize(conversation(second, "random3", "write"), second)
    assert fingerprint(a) != fingerprint(changed)


def test_alfworld_fallback_is_counted_despite_official_agent_ok(tmp_path):
    folder = tmp_path / "predictions/test:0000"
    folder.mkdir(parents=True)
    (folder / "conversation.json").write_text(json.dumps([
        {"action": "look", "model_response": "<think>error</think><action>look</action>"},
        {"action": "look", "model_response": "<think>missing action tag</think><action>look</action>"},
    ]))
    row = describe_item({"id": "test:0000", "hard": 0, "soft": 0, "agent_ok": True}, tmp_path, "alfworld")
    assert row["api_fallbacks"] == row["output_fallbacks"] == 1
    assert not row["execution_ok"]
    assert row["prompt_sha256"] is None
    assert row["behavior_sha256"] == fingerprint(["look", "look"])


def test_schedule_has_fifteen_independent_cells_and_rotates_versions():
    cells = schedule()
    assert len(cells) == len(set(cells)) == 15
    assert cells[:3] == [(1, "none"), (1, "initial"), (1, "final")]
    assert cells[3:6] == [(2, "initial"), (2, "final"), (2, "none")]
