"""Run SpreadsheetBench workbook transformations in a per-case Linux namespace."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from skillopt.envs.spreadsheetbench import codegen_agent, executor, rollout


def run_generated_code(code: str, input_path: str, output_path: str, timeout: int | None = 120):
    source = Path(input_path).resolve(strict=True)
    target = Path(output_path).absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="skillopt-workbook-") as tmp:
        scratch = Path(tmp)
        out = scratch / "out"
        out.mkdir()
        runner = scratch / "runner.py"
        runner.write_text(executor.RUNNER_TEMPLATE.format(
            input_path=f"/input/{source.name}", output_path=f"/work/{target.name}",
            user_code_indented=textwrap.indent(executor._strip_path_assignments(code), "    "),
        ))
        command = [
            "/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
            "--clearenv", "--setenv", "LANG", "C.UTF-8", "--setenv", "PATH", "/opt/python/bin:/usr/bin:/bin",
            "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--ro-bind", sys.prefix, "/opt/python", "--ro-bind", str(runner), "/runner.py",
            "--ro-bind", str(source), f"/input/{source.name}", "--bind", str(out), "/work",
            "--chdir", "/work", "/opt/python/bin/python", "-I", "/runner.py",
        ]
        try:
            proc = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  errors="replace", timeout=timeout if timeout and timeout > 0 else None)
        except subprocess.TimeoutExpired:
            return False, f"timeout after {timeout}s"
        if proc.returncode:
            return False, (proc.stdout + "\n" + proc.stderr).strip()
        # Copy only the case output back after namespace teardown. A workbook
        # script cannot make the coordinator follow a symlink or read a FIFO.
        try:
            fd = os.open(out / target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            return False, "output file was not created as a regular file"
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return False, "output is not a regular file"
            with os.fdopen(fd, "rb", closefd=False) as data, tempfile.NamedTemporaryFile(
                dir=target.parent, prefix=".workbook-", delete=False
            ) as staged:
                try:
                    shutil.copyfileobj(data, staged)
                    staged.flush()
                    os.replace(staged.name, target)
                finally:
                    Path(staged.name).unlink(missing_ok=True)
        finally:
            os.close(fd)
        return True, ""


def install() -> None:
    codegen_agent.run_generated_code = run_generated_code
    rollout.run_generated_code = run_generated_code
