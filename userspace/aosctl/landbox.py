"""Apply the task Landlock policy, then replace this process with the command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ..aosd.landlock import apply_workspace_policy


def _cgroup_path() -> str:
    for line in Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines():
        if line.startswith("0::"):
            return line[3:]
    raise RuntimeError("process is not in a cgroup v2 hierarchy")


def main() -> None:
    if len(sys.argv) < 5 or sys.argv[2] != "--result" or sys.argv[4] != "--":
        raise SystemExit("usage: python -m userspace.aosctl.landbox WORKSPACE --result FILE -- COMMAND [ARGS...]")
    workspace = Path(sys.argv[1]).resolve()
    result_file = Path(sys.argv[3])
    command = sys.argv[5:]
    if len(command) < 4 or command[0] != "--aos-socket":
        raise SystemExit("missing AOS service socket")
    socket_path = Path(command[1])
    task_id = command[2]
    command = command[3:]
    if not command:
        raise SystemExit("missing command")
    apply_workspace_policy(workspace)
    os.chdir(workspace / "working")
    result: dict[str, object] = {"exit_code": None}
    try:
        from .cli import request
        request(socket_path, {"op": "task.kernel.activate", "task_id": task_id,
                              "pid": os.getpid(), "cgroup": _cgroup_path()})
        completed = subprocess.run(command, check=False)
        result["exit_code"] = completed.returncode
    finally:
        try:
            request(socket_path, {"op": "task.kernel.finish", "task_id": task_id,
                                  "pid": os.getpid()})
        except (OSError, RuntimeError):
            result["kernel_finish_error"] = True
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_text(json.dumps(result) + "\n", encoding="ascii")
    raise SystemExit(int(result["exit_code"] or 0))


if __name__ == "__main__":
    main()
