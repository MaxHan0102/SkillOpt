"""Ensure completed and failed trials cannot hang at interpreter shutdown."""
import subprocess
import sys
from pathlib import Path

import pytest

from reproduction import restart_docvqa_retry as restart


@pytest.mark.parametrize("fail", [False, True])
def test_trial_exits_after_durable_result_despite_blocked_request_thread(tmp_path, fail):
    program = r'''
import json
from pathlib import Path
import sys
import threading
from reproduction import restart_docvqa_retry as restart
root = Path(sys.argv[1])
fail = sys.argv[2] == "True"
def trial(*args):
    threading.Thread(target=lambda: threading.Event().wait(120), daemon=False).start()
    (root / "results.jsonl").write_text('{"id":"a","hard":0,"soft":0}\n')
    (root / "status.json").write_text(json.dumps({"phase":"failed" if fail else "completed_with_errors"}))
    print("durable trial result", flush=False)
    if fail:
        raise RuntimeError("simulated trial failure")
restart.engine.trial = trial
restart.exit_after_trial(root, 1, "none")
'''
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path), str(fail)],
                            cwd=restart.ROOT, capture_output=True, text=True, timeout=5)
    assert result.returncode == int(fail)
    assert "durable trial result" in result.stdout
    assert (tmp_path / "results.jsonl").exists()
    assert (tmp_path / "status.json").exists()
    assert ("simulated trial failure" in result.stderr) == fail


def test_restart_subprocesses_use_cleanup_entry_point_and_hash_it(monkeypatch):
    for name in ("BENCHMARK", "SOURCE_FILES", "command", "select_errors", "report", "status"):
        monkeypatch.setattr(restart.engine, name, getattr(restart.engine, name))
    restart.configure()
    command = restart.engine.command("trial", "--run-root", "/tmp/new-docvqa", "--repeat", "1", "--variant", "none")
    assert command[2] == "docvqa"
    assert Path(command[3]).name == "restart_docvqa_retry.py"
    assert "reproduction/restart_docvqa_retry.py" in restart.engine.SOURCE_FILES
    assert "skillopt/envs/docvqa/rollout.py" in restart.engine.SOURCE_FILES
