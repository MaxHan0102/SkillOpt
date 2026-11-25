"""Exercise the five native adapters against local model doubles and a training-only author."""

import json
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from reproduction import run_benchmark_skill_creator as runner
from reproduction import skill_creator_author


@pytest.mark.parametrize("benchmark", list(runner.PROTOCOLS))
def test_native_paired_smoke_and_resume(benchmark, tmp_path, monkeypatch):
    monkeypatch.chdir(runner.ROOT)
    if not (runner.REPRO / "data" / f"{benchmark}_official/provenance.json").is_file():
        pytest.skip("materialized benchmark data unavailable")
    native = import_module(f"skillopt.envs.{benchmark}.rollout")
    answer = "<answer>A</answer>"
    if benchmark == "spreadsheetbench":
        from skillopt.envs.spreadsheetbench import codegen_agent

        code = "```python\nimport shutil\nshutil.copyfile(INPUT_PATH, OUTPUT_PATH)\n```"
        target = Mock(return_value=code)
        monkeypatch.setattr(codegen_agent, "_chat_call", target)
        monkeypatch.setattr(codegen_agent, "get_target_client", lambda: None)
    elif benchmark in ("officeqa", "docvqa"):
        response = SimpleNamespace(content=answer, tool_calls=[]) if benchmark == "officeqa" else answer
        target = Mock(return_value=(response, {}))
        monkeypatch.setattr(native, "chat_target_messages", target)
    else:
        target = Mock(return_value=(answer, {}))
        monkeypatch.setattr(native, "chat_target", target)
    monkeypatch.setattr(runner, "configure_target", lambda root, phase: None)
    plan, adapter, splits = runner.prepare(benchmark, 2, 1, True, False)
    train_id = str(splits["train"][0]["id"])
    val_id = str(splits["val"][0]["id"])
    root = tmp_path / "experiment"
    marker = "Use the verified local guidance."
    author_inputs = []

    async def author(work, attempt, prompt, env):
        inputs = work / "input"
        manifest = runner.read(inputs / "READY.json")
        trajectory = json.loads((inputs / "trajectories.jsonl").read_text())
        assert manifest["ids"] == [train_id] and trajectory["id"] == train_id != val_id
        assert trajectory["system_prompt"] and trajectory["user_prompt"] and trajectory["conversation"]
        if benchmark == "docvqa":
            assert (inputs / trajectory["image"]).read_bytes() == Path(splits["train"][0]["image_path"]).read_bytes()
        name = f"{benchmark}-terra"
        assert f"generated/{name}/SKILL.md" in prompt
        directory = work / "generated" / name
        directory.mkdir()
        (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Local test\n---\n{marker}\n")
        (directory.parent / "coverage.json").write_text(json.dumps([train_id]))
        validator = tmp_path / "validator.py"
        validator.touch()
        runner.write_json(
            attempt / "completed.json", {"validator": str(validator), "validator_sha256": runner.digest(validator)}
        )
        author_inputs.append(train_id)

    def freeze(*args, **kwargs):
        return skill_creator_author.freeze_skill(*args, **kwargs, generate=author)

    monkeypatch.setattr(runner, "freeze_skill", freeze)
    runner.run(root, plan, adapter, splits)
    first_calls = target.call_count
    assert first_calls >= 3
    for arm in ("none", "creator"):
        arm_dir = root / "val/repeat_01" / arm
        prompt = next(arm_dir.rglob("target_system_prompt.txt")).read_text()
        assert (marker in prompt) == (arm == "creator")
        result = json.loads(next(arm_dir.rglob("results.jsonl")).read_text())
        assert str(result["id"]) == val_id
    report = runner.read(root / "summary.json")
    for metric in ("hard", "soft"):
        assert report["val"][metric]["tasks"] == 1
        assert report["val"][metric]["paired_tasks"][0]["id"] == val_id
    # If a crash occurs after native results are written but before publishing
    # the receipt, keep the observed outcome without drawing another answer.
    next((root / "val/repeat_01/creator").rglob("complete.json")).unlink()
    runner.run(root, plan, adapter, splits)
    assert target.call_count == first_calls and author_inputs == [train_id]


def test_fresh_attempt_after_partial_native_write(tmp_path):
    item = {"id": "one"}
    path = tmp_path / "task_0000"
    prior = path / "attempt-1"
    prior.mkdir(parents=True)
    runner.write_json(prior / "identity.json", {"id": "one", "skill_sha256": runner.text_hash("")})
    (prior / "results.jsonl").touch()

    def rollout(items, skill, raw):
        pred = Path(raw) / "predictions/one"
        pred.mkdir(parents=True)
        row = {
            "id": "one",
            "hard": 0,
            "soft": 0,
            "agent_ok": False,
            "fail_reason": "Model neither produced a tool request nor a final answer",
        }
        (Path(raw) / "results.jsonl").write_text(json.dumps(row) + "\n")
        (pred / "target_system_prompt.txt").write_text("system")
        (pred / "target_user_prompt.txt").write_text("question")
        (pred / "conversation.json").write_text('[{"type":"message","content":"missing answer"}]')

    adapter = SimpleNamespace(rollout=Mock(side_effect=rollout))
    outcome, _ = runner.run_item(adapter, item, path, "")
    assert outcome["hard"] == 0 and (prior / "incomplete.json").exists()
    assert runner.run_item(adapter, item, path, "")[0] == outcome
    assert adapter.rollout.call_count == 1
