"""Runs a fixed command (never a shell) and captures its output.

Agents can't choose commands; our check functions call this with commands we
wrote. Generated code runs during checks (e.g. pytest), so we also strip
secrets like API keys from the environment it sees.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_OUTPUT_CHARS = 6_000  # errors are at the end, so we keep the tail
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


@dataclass
class ProcessResult:
    code: int
    output: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out


def safe_env() -> dict[str, str]:
    """The current environment, minus anything that looks like a credential."""
    return {k: v for k, v in os.environ.items() if not any(m in k.upper() for m in _SECRET_MARKERS)}


async def run_process(cmd: list[str], cwd: Path, timeout: float = 300) -> ProcessResult:
    exe = shutil.which(cmd[0]) or cmd[0]  # finds npm.cmd on Windows
    proc = await asyncio.create_subprocess_exec(
        exe,
        *cmd[1:],
        # resolve() expands Windows short names like AKSHAN~1, which break Vite's path handling.
        cwd=cwd.resolve(),
        env=safe_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        kill_tree(proc.pid)
        await proc.wait()
        return ProcessResult(-1, f"Timed out after {timeout:.0f}s: {' '.join(cmd)}", timed_out=True)

    text = _ANSI.sub("", out.decode("utf-8", errors="replace")).strip()  # drop terminal colors
    if len(text) > MAX_OUTPUT_CHARS:
        text = "[...earlier output cut...]\n" + text[-MAX_OUTPUT_CHARS:]
    return ProcessResult(proc.returncode or 0, text)


def kill_tree(pid: int) -> None:
    """Kill a process and its children (npm spawns node, which can outlive npm)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass
