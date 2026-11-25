import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from reproduction import run_alfworld_skill_creator as experiment
from reproduction import skill_creator_author as writer
from skillopt.envs.alfworld import rollout


def test_paired_run_recovers_interrupted_batch_and_freezes_once(tmp_path, monkeypatch):
    data = tmp_path / "games"
    root = tmp_path / "run"
    monkeypatch.setenv("ALFWORLD_DATA", str(data))
    monkeypatch.setattr(experiment, "configure_target", lambda root: None)
    plan = {
        "splits": {s: [{"id": s, "gamefile": f"{s}/pick_and_place/game.tw-pddl"}] for s in ("train", "test")},
        "train_workers": 1,
        "test_workers": 1,
        "smoke": False,
        "validation": False,
        "repeats": 2,
        "seed": 42,
        "max_steps": 50,
        "max_completion_tokens": 16384,
        "author_prompt_sha256": experiment.text_digest(writer.instructions(1)),
        "protocol_sha256": experiment.text_digest(experiment.protocol_text(50)),
    }
    simulations = []

    def environment(count, *, eval_dataset, is_train, seed, specific_gamefiles):
        assert count == 1 and seed == 42
        path = specific_gamefiles[0]
        split = "train" if is_train else "test"
        simulations.append(split)
        observation = {"text": [split], "anchor": [f"Your task is to: {split}"]}

        def step(responses):
            won = "<action>take</action>" in responses[0]
            return {"anchor": ["feedback"]}, [int(won)], [True], [{"won": won}]

        return SimpleNamespace(
            reset=lambda _: (observation, [{"extra.gamefile": str(data / path)}]), step=step, close=lambda: None
        )

    monkeypatch.setattr(experiment, "build_alfworld_env", environment)
    fail_next_creator = True

    def chat(*, user, **kwargs):
        nonlocal fail_next_creator
        if "Frozen guidance" in user:
            if fail_next_creator:
                fail_next_creator = False
                raise RuntimeError("service interrupted")
            answer = "<action>take</action>"
        else:
            answer = "no action tag"
        return answer, {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}

    monkeypatch.setattr(rollout, "chat_target", chat)
    attempts = []

    async def generate(work, attempt, prompt, env):
        assert {p.name for p in (work / "input").iterdir()} == {"READY.json", "protocol.txt", "trajectories.jsonl"}
        episode = json.loads((work / "input/trajectories.jsonl").read_text())
        assert (episode["id"], episode["hard"]) == ("train", 0)
        assert episode["steps"][0]["raw_model_response"] == "no action tag"
        assert episode["steps"][0]["action"] == "look"
        assert episode["steps"][0]["user_prompt"] == "train"
        attempts.append(attempt)
        directory = work / "generated" / writer.SKILL_NAME
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            "---\nname: alfworld-terra\ndescription: Test skill\n---\nFrozen guidance\n"
        )
        # The first completed turn claimed the wrong coverage. Its candidate
        # must never be evaluated or reused as a valid checkpoint.
        (work / "generated/coverage.json").write_text(json.dumps(["test" if len(attempts) == 1 else "train"]))
        validator = tmp_path / "validator.py"
        validator.write_text("")
        experiment.write_json(
            attempt / "completed.json",
            {
                "validator": str(validator),
                "validator_sha256": experiment.digest(validator),
            },
        )

    monkeypatch.setattr(experiment, "author", generate)
    with pytest.raises(RuntimeError, match="service interrupted"):
        experiment.run(root, plan)
    assert len(attempts) == 2 and (attempts[0] / "invalid.json").is_file()
    assert (root / "frozen/body.md").read_text() == "Frozen guidance"
    assert not (root / "test/repeat_01/creator/batch_000").exists()
    assert not (root / "summary.json").exists()

    experiment.run(root, plan)
    report = experiment.read(root / "summary.json")["test"]
    assert report["success_rate"] == {"none": 0, "creator": 1}
    assert report["paired_tasks"][0]["creator"] == [1, 1]
    assert report["paired_bootstrap_95_pp"] == [100, 100]
    assert report["execution"]["none"]["format_fallbacks"] == 2
    assert report["execution"]["creator"]["tokens"]["total_tokens"] == 10
    assert simulations.count("train") == 1 and simulations.count("test") == 5
    experiment.run(root, plan)
    assert len(simulations) == 6 and len(attempts) == 2
    (root / "frozen/body.md").write_text("changed")
    with pytest.raises(ValueError, match="Frozen skill"):
        experiment.run(root, plan)


def test_author_binds_system_skill_and_handles_early_completion(tmp_path):
    skill = tmp_path / "system/skill-creator/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("system skill")
    (skill.parent / "scripts").mkdir()
    (skill.parent / "scripts/quick_validate.py").touch()
    work = tmp_path / "work"
    work.mkdir()
    turn = {"id": "turn-id", "status": "completed"}
    messages = [
        {"id": 1, "result": {"codexHome": str(tmp_path), "userAgent": "test"}},
        {
            "id": 2,
            "result": {
                "data": [
                    {
                        "skills": [
                            {"name": "skill-creator", "enabled": True, "scope": "system", "path": str(skill)},
                            {"name": "skill-creator", "enabled": True, "scope": "repo", "path": "other"},
                        ]
                    }
                ]
            },
        },
        {"id": 3, "result": {"config": {}}},
        {
            "id": 4,
            "result": {
                "thread": {"id": "thread-id"},
                "model": writer.AUTHOR_MODEL,
                "activePermissionProfile": {"id": writer.PROFILE},
            },
        },
        {"method": "turn/completed", "params": {"threadId": "thread-id", "turn": turn}},
        {"id": 5, "result": {"turn": turn}},
    ]
    proc = SimpleNamespace(
        stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()),
        stdout=SimpleNamespace(readline=AsyncMock(side_effect=[json.dumps(m).encode() for m in messages])),
    )
    receipt = asyncio.run(writer._author_session(proc, work, tmp_path, "fixed prompt"))
    assert receipt["model"] == writer.AUTHOR_MODEL and receipt["turn_id"] == turn["id"]
    requests = {
        p["method"]: p.get("params") for call in proc.stdin.write.call_args_list for p in [json.loads(call.args[0])]
    }
    assert requests["thread/start"]["allowProviderModelFallback"] is False
    assert requests["thread/start"]["ephemeral"] is True
    assert requests["turn/start"]["effort"] == "high"
    assert requests["turn/start"]["input"] == [
        {"type": "text", "text": "fixed prompt"},
        {"type": "skill", "name": "skill-creator", "path": str(skill)},
    ]
