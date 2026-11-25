"""Run one explicitly selected system skill in a fresh Codex App Server thread."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

from reproduction.prepare_official_data import digest
from reproduction.run_experiments import write_json

AUTHOR_MODEL = "gpt-6.1-sol"
SKILL_NAME = "alfworld-terra"
PROFILE = "alfworld-skill-author"


def instructions(count: int) -> str:
    return f"""$skill-creator
Write an English Agent Skill for gpt-6.1-terra / high operating in the SkillOpt
ALFWorld text environment. Verify input/READY.json and read input/protocol.txt.
Analyze all {count} training episodes in input/trajectories.jsonl, including
normal failures. The task IDs, actual inputs, responses, environment feedback
and outcomes are included. You may use local tools in generated/ for analysis.
Use only the provided training packet and the system skill-creator resources.
Write actionable, transferable guidance compatible with the action protocol,
the two-step history and the 50-action limit. The executor receives only the
Markdown body, so keep it self-contained. Do not memorize individual puzzles.
Write generated/{SKILL_NAME}/SKILL.md with valid name and description metadata
(name: {SKILL_NAME}). Write generated/coverage.json as a JSON array of all
the training ID strings you analyzed. Finish after saving the files.
"""


async def _author_session(proc, work: Path, attempt: Path, prompt: str) -> dict:
    next_id = 0
    usage = {}
    completed_turns = {}
    events = attempt / "events.jsonl"

    async def send(message):
        proc.stdin.write((json.dumps(message) + "\n").encode())
        await proc.stdin.drain()

    async def receive():
        line = await proc.stdout.readline()
        if not line:
            raise RuntimeError(f"Codex closed its connection. See {attempt / 'codex.stderr.log'}")
        message = json.loads(line)
        method = message.get("method")
        params = message.get("params", {})
        if method and "id" in message:
            raise RuntimeError(f"Unexpected interactive Codex request: {method}")
        if method == "thread/tokenUsage/updated":
            usage.update(params["tokenUsage"])
        if method == "turn/completed":
            turn = params["turn"]
            completed_turns[params["threadId"], turn["id"]] = turn
        if method in ("thread/started", "turn/started", "item/completed", "turn/completed", "error"):
            item = params.get("item") or {}
            if item.get("type") == "agentMessage":
                (attempt / "last_message.txt").write_text(item["text"], encoding="utf-8")
            with events.open("a", encoding="utf-8") as out:
                out.write(
                    json.dumps(
                        {
                            "method": method,
                            "threadId": params.get("threadId"),
                            "itemType": item.get("type"),
                            "command": item.get("command"),
                            "tool": item.get("tool"),
                            "status": item.get("status"),
                            "error": params.get("error"),
                        }
                    )
                    + "\n"
                )
        return message

    async def request(method, params):
        nonlocal next_id
        next_id += 1
        await send({"id": next_id, "method": method, "params": params})
        while True:
            message = await receive()
            if message.get("id") == next_id:
                if "error" in message:
                    raise RuntimeError(f"Codex {method} failed: {message['error']}")
                return message["result"]

    server = await request(
        "initialize",
        {"clientInfo": {"name": "skillopt-experiment", "version": "1"}, "capabilities": {"experimentalApi": True}},
    )
    await send({"method": "initialized"})
    listing = await request("skills/list", {"cwds": [str(work)]})
    entries = [
        s
        for row in listing["data"]
        for s in row["skills"]
        if s["name"] == "skill-creator" and s["scope"] == "system" and s["enabled"]
    ]
    if len(entries) != 1:
        raise RuntimeError("Expected one enabled system skill-creator in the Codex installation")
    skill = Path(entries[0]["path"]).resolve(strict=True)
    validator = skill.parent / "scripts/quick_validate.py"
    skill_hash = digest(skill)
    fs = {":minimal": "read", str(work): "read", str(work / "generated"): "write", str(skill.parent): "read"}
    # Codex re-executes its Linux sandbox helper inside the restricted filesystem.
    # npm installs may place its executable outside the minimal system directories.
    alias_root = Path(server["codexHome"]) / "tmp/arg0-linux"
    if alias_root.is_dir():
        fs[str(alias_root)] = "read"
        for alias in alias_root.glob("*/codex-linux-sandbox"):
            if alias.is_file():
                fs[str(alias.resolve())] = "read"
    config = {
        "permissions": {PROFILE: {"filesystem": fs, "network": {"enabled": False}}},
        "project_doc_max_bytes": 0,
        "web_search": "disabled",
        "shell_environment_policy": {"inherit": "none", "set": {"PATH": "/usr/local/bin:/usr/bin:/bin"}},
        "features": {"memories": False, "multi_agent": False, "apps": False},
    }
    # Disable any user-configured MCP servers in this isolated authoring turn.
    inherited = await request("config/read", {"cwd": str(work)})
    servers = inherited["config"].get("mcp_servers") or {}
    for name in servers:
        config[f"mcp_servers.{name}.enabled"] = False
    thread = await request(
        "thread/start",
        {
            "cwd": str(work),
            "model": AUTHOR_MODEL,
            "config": config,
            "permissions": PROFILE,
            "approvalPolicy": "never",
            "ephemeral": True,
            "allowProviderModelFallback": False,
        },
    )
    profile = thread.get("activePermissionProfile") or {}
    if thread["model"] != AUTHOR_MODEL or profile.get("id") != PROFILE:
        raise RuntimeError("Codex started with unexpected model or filesystem permissions")
    thread_id = thread["thread"]["id"]
    turn = await request(
        "turn/start",
        {
            "threadId": thread_id,
            "model": AUTHOR_MODEL,
            "effort": "high",
            "input": [{"type": "text", "text": prompt}, {"type": "skill", "name": "skill-creator", "path": str(skill)}],
        },
    )
    turn_id = turn["turn"]["id"]
    while (thread_id, turn_id) not in completed_turns:
        await receive()
    result = completed_turns[thread_id, turn_id]
    if result["status"] != "completed":
        raise RuntimeError(f"Codex authoring {result['status']}: {result.get('error')}")
    return {
        "thread_id": thread_id,
        "turn_id": turn_id,
        "model": AUTHOR_MODEL,
        "effort": "high",
        "codex_version": server["userAgent"],
        "system_skill": str(skill),
        "system_skill_sha256": skill_hash,
        "validator": str(validator),
        "validator_sha256": digest(validator),
        "token_usage": usage,
    }


async def author(work: Path, attempt: Path, prompt: str, env: dict[str, str]) -> dict:
    started = time.monotonic()
    with (attempt / "codex.stderr.log").open("wb") as log:
        proc = await asyncio.create_subprocess_exec(
            "codex",
            "app-server",
            "--listen",
            "stdio://",
            cwd=work,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=log,
            limit=32 * 1024 * 1024,
        )
        try:
            receipt = await asyncio.wait_for(_author_session(proc, work, attempt, prompt), timeout=3600)
            receipt["elapsed_seconds"] = round(time.monotonic() - started, 2)
            write_json(attempt / "completed.json", receipt)
            return receipt
        finally:
            proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


def validated_body(work: Path, receipt: dict, ids: list[str], skill_name: str = SKILL_NAME) -> tuple[Path, str]:
    generated = work / "generated"
    source = generated / skill_name / "SKILL.md"
    coverage = generated / "coverage.json"
    for path in (source, coverage):
        if path.resolve(strict=True) != path or not path.is_file():
            raise ValueError(f"Expected a regular author output: {path}")
    validator = Path(receipt["validator"])
    if digest(validator) != receipt["validator_sha256"]:
        raise RuntimeError("The skill validator changed during authoring")
    subprocess.run([sys.executable, str(validator), str(source.parent)], check=True)
    reported = json.loads(coverage.read_text())
    if (
        not isinstance(reported, list)
        or not all(isinstance(i, str) for i in reported)
        or sorted(reported) != sorted(ids)
    ):
        raise ValueError("Author coverage differs from the training IDs")
    text = source.read_text()
    header, delimiter, body = text.removeprefix("---\n").partition("\n---\n")
    if not text.startswith("---\n") or not delimiter or not body.strip():
        raise ValueError("SKILL.md requires YAML frontmatter and a nonempty body")
    metadata = yaml.safe_load(header)
    if not isinstance(metadata, dict) or metadata.get("name") != skill_name or not str(metadata.get("description") or "").strip():
        raise ValueError("Unexpected skill name or empty description")
    return source, body.strip()


def packet_manifest(folder, ids):
    names = ["protocol.txt", "trajectories.jsonl"]
    names.extend(str(p.relative_to(folder)) for p in sorted((folder / "assets").glob("*")) if p.is_file())
    return {
        "training_only": True,
        "ids": ids,
        "sha256": {name: digest(folder / name) for name in names},
    }


def freeze_skill(root, packet, plan, author_env, *, prompt, skill_name, generate=author):
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))

    frozen = root / "frozen"
    packet_hash = digest(packet / "READY.json")
    if not frozen.exists():
        attempts = root / "author_attempts"
        attempts.mkdir(exist_ok=True)
        ids = [r["id"] for r in plan["splits"]["train"]]
        # First recover any completed turn. Only invalid/missing artifacts permit
        # a new authoring turn, before either evaluation arm has been started.
        pending = [
            p.parent
            for p in sorted(attempts.glob("*/completed.json"), key=lambda p: p.stat().st_mtime_ns)
            if not (p.parent / "invalid.json").exists()
        ]
        # Allow one technical retry if a new turn returns unusable artifacts.
        for candidate in [*pending, None, None]:
            attempt = candidate
            if attempt is None:
                attempt = Path(tempfile.mkdtemp(prefix="attempt-", dir=attempts))
                work = attempt / "workspace"
                (work / "input").mkdir(parents=True)
                manifest = packet_manifest(packet, ids)
                if manifest != read(packet / "READY.json"):
                    raise ValueError("Training packet changed before authoring")
                for filename in ("READY.json", *manifest["sha256"]):
                    destination = work / "input" / filename
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(packet / filename, destination)
                (work / "generated").mkdir()
                (attempt / "prompt.txt").write_text(prompt, encoding="utf-8")
                asyncio.run(generate(work, attempt, prompt, author_env))
            work = attempt / "workspace"
            if (
                digest(work / "input/READY.json") != packet_hash
                or packet_manifest(work / "input", ids) != read(packet / "READY.json")
                or digest(attempt / "prompt.txt") != plan["author_prompt_sha256"]
            ):
                raise ValueError("Author workspace inputs differ from the plan")
            receipt = read(attempt / "completed.json")
            try:
                source, body = validated_body(work, receipt, ids, skill_name)
            except (ValueError, FileNotFoundError, subprocess.CalledProcessError, yaml.YAMLError) as exc:
                write_json(attempt / "invalid.json", {"reason": str(exc)})
                print(f"Invalid author output in {attempt}: {exc}", flush=True)
                continue
            break
        else:
            raise RuntimeError(f"Codex did not produce a valid skill. See {attempts}")
        stage = Path(tempfile.mkdtemp(prefix=".freeze-", dir=root))
        shutil.copyfile(source, stage / "SKILL.md")
        (stage / "body.md").write_text(body)
        write_json(
            stage / "manifest.json",
            {
                "training_packet_sha256": packet_hash,
                "author": receipt,
                "attempt": str(attempt.relative_to(root)),
                "skill_sha256": digest(stage / "SKILL.md"),
                "body_sha256": digest(stage / "body.md"),
            },
        )
        stage.rename(frozen)
    manifest = read(frozen / "manifest.json")
    if (
        manifest["training_packet_sha256"] != packet_hash
        or digest(frozen / "body.md") != manifest["body_sha256"]
        or digest(frozen / "SKILL.md") != manifest["skill_sha256"]
    ):
        raise ValueError("Frozen skill or training packet changed")
    return (frozen / "body.md").read_text()
