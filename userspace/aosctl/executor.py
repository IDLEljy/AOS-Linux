"""Run a task command under the task's Landlock filesystem policy."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from ..aosd.landlock import LandlockError, apply_workspace_policy


def run_task_command(workspace: Path, command: list[str], *,
                     scope_runner: Callable[[list[str]], int] | None = None) -> int:
    working = workspace / "working"
    working.mkdir(mode=0o700, exist_ok=True)
    if sys.platform != "linux":
        raise RuntimeError("task execution policy requires Linux Landlock")

    def restrict() -> None:
        apply_workspace_policy(workspace)

    try:
        if scope_runner is not None:
            return scope_runner(command)
        completed = subprocess.run(command, cwd=working, preexec_fn=restrict, check=False)
    except (LandlockError, OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"cannot apply task execution policy: {exc}") from exc
    return completed.returncode
