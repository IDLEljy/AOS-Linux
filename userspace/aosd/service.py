#!/usr/bin/env python3
"""Local AOS task registry and JSON Unix-socket service."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess

try:
    import grp
except ImportError:
    grp = None
import socket
import socketserver
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024


class AOSServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class TaskStore:
    def __init__(self, state_dir: Path, workspace_root: Path, *, kernel_control: Any = None,
                 cgroup_root: Path = Path("/sys/fs/cgroup")) -> None:
        self.state_dir = state_dir
        self.workspace_root = workspace_root
        self.kernel_control = kernel_control
        self.cgroup_root = cgroup_root
        self._kernel_tasks: dict[tuple[str, int], int] = {}
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.database = self.state_dir / "aosd.sqlite3"
        self.audit_path = self.state_dir / "audit.jsonl"
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                workspace TEXT NOT NULL,
                metadata TEXT NOT NULL,
                cgroup_unit TEXT
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
            if "cgroup_unit" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN cgroup_unit TEXT")
            db.execute("""CREATE TABLE IF NOT EXISTS kernel_execs (
                task_id TEXT NOT NULL,
                pid INTEGER NOT NULL,
                kernel_task_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (task_id, pid)
            )""")

    @contextlib.contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def _audit(self, task_id: str | None, agent_id: str | None, action: str,
               decision: str, details: dict[str, Any] | None = None) -> None:
        event = {
            "timestamp": time.time(),
            "task_id": task_id,
            "agent_id": agent_id,
            "pid": os.getpid(),
            "action": action,
            "decision": decision,
            "details": details or {},
        }
        with self._lock, self.audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def create_task(self, agent_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        agent_id = agent_id.strip()
        if not agent_id or len(agent_id) > 128:
            raise AOSServiceError("invalid_agent_id", "agent_id must be 1-128 characters")
        task_id = str(uuid.uuid4())
        task_workspace = self.workspace_root / task_id
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task_workspace.mkdir(mode=0o700)
            try:
                for name in ("input", "working", "output", "logs"):
                    (task_workspace / name).mkdir(mode=0o700)
                db.execute(
                    "INSERT INTO tasks (task_id, agent_id, state, created_at, updated_at, workspace, metadata, cgroup_unit) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                    (task_id, agent_id, "RUNNING", now, now, str(task_workspace),
                     json.dumps(metadata, ensure_ascii=False)),
                )
            except Exception:
                shutil.rmtree(task_workspace, ignore_errors=True)
                raise
        self._audit(task_id, agent_id, "task.create", "allow")
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise AOSServiceError("not_found", "task not found", 404)
        return self._serialize(row)

    def list_tasks(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as db:
            if agent_id:
                rows = db.execute("SELECT * FROM tasks WHERE agent_id = ? ORDER BY created_at DESC",
                                  (agent_id,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [self._serialize(row) for row in rows]

    def finish_task(self, task_id: str) -> dict[str, Any]:
        now = time.time()
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                raise AOSServiceError("not_found", "task not found", 404)
            if row["state"] == "RUNNING":
                db.execute("UPDATE tasks SET state = 'FINISHED', updated_at = ? WHERE task_id = ?",
                           (now, task_id))
        result = self.get_task(task_id)
        self._audit(task_id, result["agent_id"], "task.finish", "allow")
        return result

    def record_event(self, task_id: str, action: str, decision: str,
                     details: dict[str, Any]) -> None:
        task = self.get_task(task_id)
        if decision not in {"allow", "deny", "error"}:
            raise AOSServiceError("invalid_decision", "invalid audit decision")
        if action == "task.exec.policy" and decision != "deny":
            raise AOSServiceError("invalid_decision", "policy failure must be denied")
        if action == "task.exec.result" and decision != "error":
            raise AOSServiceError("invalid_decision", "nonzero command result must be an error")
        if action not in {"task.exec.policy", "task.exec.result"}:
            raise AOSServiceError("invalid_action", "unsupported audit action")
        self._audit(task_id, task["agent_id"], action, decision, details)

    def authorize_exec(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["state"] != "RUNNING":
            self._audit(task_id, task["agent_id"], "task.exec", "deny",
                        {"reason": "task_not_running"})
            raise AOSServiceError("task_not_running", "task is not RUNNING", 409)
        self._audit(task_id, task["agent_id"], "task.exec", "allow",
                    {"workspace": task["workspace"]})
        return task

    def set_cgroup_unit(self, task_id: str, unit: str) -> None:
        with self._lock, self._connect() as db:
            cursor = db.execute("UPDATE tasks SET cgroup_unit = ?, updated_at = ? WHERE task_id = ? AND state = 'RUNNING'",
                                (unit, time.time(), task_id))
            if cursor.rowcount != 1:
                raise AOSServiceError("task_not_running", "task is not RUNNING", 409)

    @staticmethod
    def _process_cgroup(pid: int) -> str:
        try:
            for line in Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii").splitlines():
                if line.startswith("0::"):
                    return line[3:]
        except OSError as exc:
            raise AOSServiceError("invalid_pid", "cannot inspect process cgroup") from exc
        raise AOSServiceError("invalid_cgroup", "process is not in a cgroup v2 hierarchy")

    def activate_kernel_exec(self, task_id: str, pid: int, cgroup_path: str) -> dict[str, Any]:
        task = self.authorize_exec(task_id)
        if pid <= 0:
            raise AOSServiceError("invalid_pid", "pid must be positive")
        actual_cgroup = self._process_cgroup(pid)
        if actual_cgroup != cgroup_path:
            raise AOSServiceError("cgroup_mismatch", "pid is not in the requested cgroup")
        if self.kernel_control is None:
            raise AOSServiceError("kernel_unavailable", "AOS kernel control is not enabled", 503)
        cgroup_path = cgroup_path.strip()
        if not cgroup_path.startswith("/") or ".." in Path(cgroup_path).parts:
            raise AOSServiceError("invalid_cgroup", "invalid cgroup path")
        key = (task_id, pid)
        with self._lock:
            if key in self._kernel_tasks:
                raise AOSServiceError("kernel_task_active", "kernel task is already active", 409)
            try:
                cgroup_dir = (self.cgroup_root / cgroup_path.lstrip("/")).resolve(strict=True)
                if (not cgroup_dir.is_dir() or
                        not cgroup_dir.is_relative_to(self.cgroup_root.resolve())):
                    raise AOSServiceError("invalid_cgroup", "cgroup path is outside the cgroup mount")
                executable = task.get("metadata", {}).get("kernel_exec_path", "")
                kernel_task_id = self.kernel_control.create_task(
                    f"aos-{task_id[:8]}-{pid}", Path(task["workspace"]), executable,
                    restrict_flags=self.kernel_control.restrict_write,
                    audit_level=self.kernel_control.audit_deny)
                cgroup_fd = os.open(cgroup_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                                    getattr(os, "O_CLOEXEC", 0))
                try:
                    self.kernel_control.bind_task(kernel_task_id, cgroup_fd)
                finally:
                    os.close(cgroup_fd)
            except AOSServiceError:
                raise
            except (OSError, ValueError) as exc:
                if "kernel_task_id" in locals():
                    try:
                        self.kernel_control.finish_task(kernel_task_id)
                    except OSError:
                        pass
                raise AOSServiceError("kernel_activation_failed", str(exc), 503) from exc
            self._kernel_tasks[key] = kernel_task_id
            with self._connect() as db:
                db.execute("INSERT INTO kernel_execs (task_id, pid, kernel_task_id, created_at) "
                           "VALUES (?, ?, ?, ?)", (task_id, pid, kernel_task_id, time.time()))
        self._audit(task_id, task["agent_id"], "task.kernel.bind", "allow",
                    {"kernel_task_id": kernel_task_id, "pid": pid, "cgroup": cgroup_path})
        return {"kernel_task_id": kernel_task_id, "pid": pid, "cgroup": cgroup_path}

    def finish_kernel_exec(self, task_id: str, pid: int) -> dict[str, Any]:
        key = (task_id, pid)
        with self._lock:
            kernel_task_id = self._kernel_tasks.pop(key, None)
        if kernel_task_id is None:
            with self._connect() as db:
                row = db.execute("SELECT kernel_task_id FROM kernel_execs WHERE task_id = ? AND pid = ?",
                                 (task_id, pid)).fetchone()
            if row is None:
                return {"finished": False, "kernel_task_id": None}
            kernel_task_id = int(row["kernel_task_id"])
        self.kernel_control.finish_task(kernel_task_id)
        with self._connect() as db:
            db.execute("DELETE FROM kernel_execs WHERE task_id = ? AND pid = ?", (task_id, pid))
        task = self.get_task(task_id)
        self._audit(task_id, task["agent_id"], "task.kernel.finish", "allow",
                    {"kernel_task_id": kernel_task_id, "pid": pid})
        return {"finished": True, "kernel_task_id": kernel_task_id}

    def cgroup_usage(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        unit = task.get("cgroup_unit")
        if not unit:
            raise AOSServiceError("cgroup_missing", "task has no active resource scope", 404)
        result = subprocess.run(["systemctl", "show", unit, "--property=ControlGroup", "--value"],
                                capture_output=True, text=True, check=False, timeout=5)
        cgroup = result.stdout.strip()
        if result.returncode or not cgroup or not cgroup.startswith("/"):
            raise AOSServiceError("cgroup_inactive", "task resource scope is not active", 404)
        path = Path("/sys/fs/cgroup") / cgroup.lstrip("/")
        values: dict[str, Any] = {"unit": unit}
        for filename, key in (("cpu.stat", "cpu_stat"), ("memory.current", "memory_current"),
                              ("memory.max", "memory_max"), ("memory.events", "memory_events"),
                              ("pids.current", "pids_current"), ("pids.max", "pids_max")):
            try:
                text = (path / filename).read_text(encoding="ascii").strip()
            except OSError:
                continue
            if filename.endswith(".stat") or filename.endswith(".events"):
                values[key] = {name: int(value) for name, value in
                               (line.split() for line in text.splitlines())}
            else:
                values[key] = int(text) if text.isdigit() else text
        return values

    def task_status(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute("SELECT state, COUNT(*) AS count FROM tasks GROUP BY state").fetchall()
        return {row["state"].lower(): row["count"] for row in rows}

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "task_id": row["task_id"],
            "agent_id": row["agent_id"],
            "state": row["state"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "workspace": row["workspace"],
            "cgroup_unit": row["cgroup_unit"],
            "metadata": json.loads(row["metadata"]),
        }


def dispatch(store: TaskStore, request: dict[str, Any]) -> dict[str, Any]:
    if request.get("version") != PROTOCOL_VERSION:
        raise AOSServiceError("unsupported_version", "protocol version must be 1")
    operation = request.get("op")
    if operation == "task.create":
        return {"task": store.create_task(request.get("agent_id", ""),
                                           request.get("metadata", {}))}
    if operation == "task.get":
        return {"task": store.get_task(request.get("task_id", ""))}
    if operation == "task.list":
        return {"tasks": store.list_tasks(request.get("agent_id"))}
    if operation == "task.finish":
        return {"task": store.finish_task(request.get("task_id", ""))}
    if operation == "task.exec":
        return {"task": store.authorize_exec(request.get("task_id", ""))}
    if operation == "task.audit":
        store.record_event(request.get("task_id", ""), request.get("action", ""),
                           request.get("decision", ""), request.get("details", {}))
        return {"recorded": True}
    if operation == "task.cgroup.set":
        store.set_cgroup_unit(request.get("task_id", ""), request.get("unit", ""))
        return {"recorded": True}
    if operation == "task.kernel.activate":
        return store.activate_kernel_exec(request.get("task_id", ""),
                                          int(request.get("pid", 0)),
                                          request.get("cgroup", ""))
    if operation == "task.kernel.finish":
        return store.finish_kernel_exec(request.get("task_id", ""),
                                        int(request.get("pid", 0)))
    if operation == "task.cgroup.usage":
        return {"usage": store.cgroup_usage(request.get("task_id", ""))}
    if operation == "status":
        return {"protocol_version": PROTOCOL_VERSION, "tasks": store.task_status()}
    raise AOSServiceError("unknown_operation", "unsupported operation")


class RequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        try:
            if len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
                raise AOSServiceError("request_too_large", "request must be one JSON line under 64 KiB")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise AOSServiceError("invalid_request", "request must be a JSON object")
            response = {"ok": True, "result": dispatch(self.server.store, request)}
        except AOSServiceError as exc:
            response = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
        except (json.JSONDecodeError, UnicodeDecodeError):
            response = {"ok": False, "error": {"code": "invalid_json", "message": "invalid JSON request"}}
        except Exception:
            response = {"ok": False, "error": {"code": "internal_error", "message": "internal service error"}}
        self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode())


if hasattr(socketserver, "UnixStreamServer"):
    class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True
        allow_reuse_address = True
else:
    UnixServer = None


def serve(socket_path: Path, store: TaskStore, group_name: str | None) -> None:
    if UnixServer is None:
        raise RuntimeError("Unix socket service requires a Unix host")
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        if not socket_path.is_socket():
            raise RuntimeError(f"refusing to replace non-socket path: {socket_path}")
        socket_path.unlink()
    server = UnixServer(str(socket_path), RequestHandler)
    server.store = store
    os.chmod(socket_path, 0o660)
    if group_name and grp is not None:
        os.chown(socket_path, -1, grp.getgrnam(group_name).gr_gid)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        socket_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="AOS task control daemon")
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/aos"))
    parser.add_argument("--workspace-root", type=Path, default=Path("/var/lib/aos/workspaces"))
    parser.add_argument("--socket", type=Path, default=Path("/run/aosd.sock"))
    parser.add_argument("--socket-group", default="aos")
    parser.add_argument("--kernel-control", action="store_true",
                        help="enable the AOS kernel ioctl backend")
    parser.add_argument("--kernel-device", type=Path, default=Path("/dev/aosctl"))
    args = parser.parse_args()
    group_name = None
    if args.socket_group and grp is not None:
        try:
            grp.getgrnam(args.socket_group)
            group_name = args.socket_group
        except KeyError:
            pass
    kernel_control = None
    if args.kernel_control:
        from .kernel import AOSKernelControl
        kernel_control = AOSKernelControl(args.kernel_device)
    serve(args.socket, TaskStore(args.state_dir, args.workspace_root,
                                 kernel_control=kernel_control), group_name)


if __name__ == "__main__":
    main()
