"""Offline checks for the actual High request payload and full split wiring."""
from __future__ import annotations

import json

import httpx
import pytest
from openai import OpenAI

from reproduction import run_experiments as runner
from reproduction.trajectory_compat import install_mapped_trajectory_reader
from skillopt.config import flatten_config, load_config
from skillopt.envs.task_output import task_output_segment
from skillopt.gradient import reflect
from skillopt.model import azure_openai


def test_both_model_roles_send_high_and_audit_excludes_secrets(tmp_path, monkeypatch):
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "offline", "object": "chat.completion", "created": 0,
            "model": runner.MODEL,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    def factory(role):
        return OpenAI(
            base_url="https://test.invalid/v1", api_key="test-secret-do-not-record",
            http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        )

    monkeypatch.setattr(azure_openai, "_make_client", factory)
    monkeypatch.setattr(azure_openai, "_optimizer_client", None)
    monkeypatch.setattr(azure_openai, "_target_client", None)
    monkeypatch.setattr(azure_openai, "OPTIMIZER_DEPLOYMENT", runner.MODEL)
    monkeypatch.setattr(azure_openai, "TARGET_DEPLOYMENT", runner.MODEL)
    monkeypatch.setattr(azure_openai, "REASONING_EFFORT", "high")
    audit_path = tmp_path / "requests.jsonl"
    runner.install_request_audit(audit_path)
    for call in (azure_openai.chat_optimizer, azure_openai.chat_target):
        answer, _ = call(system="private-system", user="private-prompt", retries=1, max_completion_tokens=16384)
        assert answer == "OK"
    assert len(captured) == 2
    assert all(p["model"] == runner.MODEL and p["reasoning_effort"] == "high" for p in captured)
    audit_text = audit_path.read_text()
    assert len(audit_text.splitlines()) == 2
    assert not any(value in audit_text for value in ("test-secret", "private-system", "private-prompt"))
    # Fail closed before network I/O if a call overrides the requested effort.
    with pytest.raises(RuntimeError, match="without gpt-6.1-terra and high"):
        azure_openai.chat_target(system="", user="", reasoning_effort="low", retries=1)
    assert len(captured) == 2


@pytest.mark.parametrize("benchmark,count", [("searchqa", 400), ("livemathematicianbench", 35)])
def test_official_split_and_optimizer_settings(benchmark, count):
    cfg = flatten_config(load_config(str(runner.REPRO / "configs" / f"{benchmark}_terra_high.yaml")))
    assert cfg["optimizer_model"] == cfg["target_model"] == runner.MODEL
    assert cfg["optimizer_backend"] == cfg["target_backend"] == "openai_chat"
    assert cfg["azure_openai_auth_mode"] == "openai_compatible"
    assert cfg["reasoning_effort"] == "high"
    assert cfg["num_epochs"] == 4
    assert cfg["use_gate"] and cfg["use_slow_update"] and cfg["use_meta_skill"] and cfg["eval_test"]
    assert cfg["sel_env_num"] == cfg["test_env_num"] == cfg["limit"] == 0
    manifest = runner.ROOT / "data" / f"{benchmark}_id_split/train/items.json"
    assert len(json.loads(manifest.read_text())) == count


@pytest.mark.parametrize("same_skill", [True, False])
def test_final_test_reuse_requires_identical_skill_bytes(tmp_path, monkeypatch, same_skill):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    manifest = tmp_path / "data/searchqa_id_split/test/items.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps([{"id": "one"}, {"id": "two"}]))
    run = tmp_path / "run"
    run.mkdir()
    summary = {
        "baseline_test_hard": 1.0, "test_hard": 1.0, "final_test_hard": 1.0,
        "baseline_selection_hard": 0.5, "best_selection_hard": 1.0,
        "total_steps": 2, "total_accepts": 1, "total_rejects": 1,
        "token_summary": {"analyst": {"calls": 1}}, "total_wall_time_s": 1.0,
    }
    (run / "summary.json").write_text(json.dumps(summary))
    (run / "best_skill.md").write_text("best")
    (run / "current.md").write_text("best" if same_skill else "different")
    (run / "runtime_state.json").write_text(json.dumps({"current_skill_path": str(run / "current.md")}))
    for folder in ("test_eval_baseline", "test_eval"):
        (run / folder).mkdir()
        (run / folder / "results.jsonl").write_text("".join(
            json.dumps({"id": key, "hard": 1.0, "soft": 1.0, "agent_ok": True}) + "\n"
            for key in ("one", "two")
        ))
    if same_skill:
        report = runner.collect(run, "searchqa")
        assert report["test"]["final"]["reused_from"] == "best"
    else:
        with pytest.raises(ValueError, match="distinct final skill"):
            runner.collect(run, "searchqa")


def test_mapped_math_trajectory_reaches_the_analyst_with_original_id(tmp_path, monkeypatch):
    task_id = "202602:22"
    directory = tmp_path / task_output_segment(task_id, map_unsafe=True)
    directory.mkdir()
    (directory / "conversation.json").write_text(json.dumps([{"content": "<answer>B</answer>"}]))
    (directory / "target_user_prompt.txt").write_text("Full math question and choices")
    item = {"id": task_id, "hard": 0, "n_turns": 1, "fail_reason": "Wrong answer"}
    assert reflect.fmt_minibatch_trajectories([item], str(tmp_path)) == ""
    monkeypatch.setattr(reflect, "fmt_minibatch_trajectories", reflect.fmt_minibatch_trajectories)
    install_mapped_trajectory_reader()
    calls = []

    def analyst(**kwargs):
        calls.append(kwargs)
        return json.dumps({"patch": {"edits": [{"op": "add", "content": "Inspect every choice"}]}}), {}

    monkeypatch.setattr(reflect, "chat_optimizer", analyst)
    result = reflect.run_error_analyst_minibatch("# Skill", [item], str(tmp_path))
    assert result["patch"]["edits"]
    assert len(calls) == 1
    prompt = calls[0]["user"]
    assert f"(id={task_id})" in prompt
    assert "<answer>B</answer>" in prompt and "Full math question and choices" in prompt
    assert item["id"] == task_id


def test_safe_id_reflection_prompt_remains_byte_identical(tmp_path, monkeypatch):
    directory = tmp_path / "safe-id"
    directory.mkdir()
    (directory / "conversation.json").write_text(json.dumps([{"content": "answer"}]))
    items = [{"id": "safe-id", "n_turns": 1}]
    expected = reflect.fmt_minibatch_trajectories(items, str(tmp_path))
    monkeypatch.setattr(reflect, "fmt_minibatch_trajectories", reflect.fmt_minibatch_trajectories)
    install_mapped_trajectory_reader()
    assert reflect.fmt_minibatch_trajectories(items, str(tmp_path)) == expected
