#!/usr/bin/env python3
"""Command-line client for aosd."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1


def request(socket_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(str(socket_path))
        connection.sendall((json.dumps({"version": PROTOCOL_VERSION, **payload}) + "\n").encode())
        chunks = bytearray()
        while not chunks.endswith(b"\n"):
            block = connection.recv(4096)
            if not block:
                raise RuntimeError("aosd closed the connection before replying")
            chunks.extend(block)
            if len(chunks) > 64 * 1024:
                raise RuntimeError("aosd response exceeds 64 KiB")
    response = json.loads(chunks)
    if not response.get("ok"):
        error = response.get("error", {})
        raise RuntimeError(f"{error.get('code', 'error')}: {error.get('message', 'request failed')}")
    return response["result"]


def main() -> int:
    parser = argparse.ArgumentParser(prog="aosctl")
    parser.add_argument("--socket", type=Path, default=Path(os.environ.get("AOS_SOCKET", "/run/aosd.sock")))
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="create a task")
    create.add_argument("--agent", required=True)
    create.add_argument("--metadata", default="{}")
    get = sub.add_parser("get", help="query a task")
    get.add_argument("task_id")
    listing = sub.add_parser("list", help="list tasks")
    listing.add_argument("--agent")
    finish = sub.add_parser("finish", help="mark a task finished")
    finish.add_argument("task_id")
    execute = sub.add_parser("exec", help="run a command under the task filesystem policy")
    execute.add_argument("task_id")
    execute.add_argument("--memory-max", default="512M")
    execute.add_argument("--cpu-quota", default="100%")
    execute.add_argument("--tasks-max", type=int, default=64)
    execute.add_argument("argv", nargs=argparse.REMAINDER)
    usage = sub.add_parser("resources", help="show task cgroup resource usage")
    usage.add_argument("task_id")
    sub.add_parser("status", help="show service task counts")
    args = parser.parse_args()

    try:
        if args.command == "create":
            metadata = json.loads(args.metadata)
            if not isinstance(metadata, dict):
                raise ValueError("metadata must be a JSON object")
            result = request(args.socket, {"op": "task.create", "agent_id": args.agent,
                                           "metadata": metadata})
        elif args.command == "get":
            result = request(args.socket, {"op": "task.get", "task_id": args.task_id})
        elif args.command == "list":
            result = request(args.socket, {"op": "task.list", "agent_id": args.agent})
        elif args.command == "finish":
            result = request(args.socket, {"op": "task.finish", "task_id": args.task_id})
        elif args.command == "exec":
            if not args.argv:
                raise ValueError("exec requires a command after task_id")
            from .executor import run_task_command
            task = request(args.socket, {"op": "task.exec", "task_id": args.task_id})["task"]
            argv = args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv
            unit = f"aos-task-{args.task_id.replace('-', '')}.scope"

            def run_in_scope(command: list[str]) -> int:
                request(args.socket, {"op": "task.cgroup.set", "task_id": args.task_id,
                                      "unit": unit})
                systemd_command = ["systemd-run", "--quiet", "--scope",
                                   f"--unit={unit}", f"--property=MemoryMax={args.memory_max}",
                                   f"--property=CPUQuota={args.cpu_quota}",
                                   f"--property=TasksMax={args.tasks_max}", "--"]
                result_path = Path(task["workspace"]) / "logs" / f"command-{uuid.uuid4().hex}.exitcode"
                result_path.parent.mkdir(parents=True, exist_ok=True)
                result_path.write_text("", encoding="ascii")
                try:
                    sandbox = [sys.executable, "-m", "userspace.aosctl.landbox",
                               task["workspace"], "--result", str(result_path), "--",
                               "--aos-socket", str(args.socket), args.task_id, *command]
                    subprocess.run(systemd_command + sandbox,
                                   cwd=Path(__file__).resolve().parents[2],
                                   check=False)
                    if not result_path.exists():
                        raise RuntimeError("task scope exited before recording a command result")
                    result_data = json.loads(result_path.read_text(encoding="ascii"))
                    if result_data.get("kernel_finish_error"):
                        raise RuntimeError("AOS kernel task could not be finalized")
                    return int(result_data["exit_code"])
                finally:
                    result_path.unlink(missing_ok=True)

            try:
                return_code = run_task_command(Path(task["workspace"]), argv,
                                               scope_runner=run_in_scope)
            except RuntimeError as exc:
                request(args.socket, {"op": "task.audit", "task_id": args.task_id,
                                      "action": "task.exec.policy", "decision": "deny",
                                      "details": {"error": str(exc)}})
                raise
            if return_code:
                request(args.socket, {"op": "task.audit", "task_id": args.task_id,
                                      "action": "task.exec.result", "decision": "error",
                                      "details": {"returncode": return_code}})
            return return_code
        elif args.command == "resources":
            result = request(args.socket, {"op": "task.cgroup.usage", "task_id": args.task_id})
        else:
            result = request(args.socket, {"op": "status"})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"aosctl: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
