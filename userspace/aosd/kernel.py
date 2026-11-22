"""Linux AOS kernel-control ioctl client."""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable
from pathlib import Path

AOS_TASK_ID_LEN = 64
AOS_PATH_MAX = 1024
AOS_RESTRICT_EXEC = 1 << 0
AOS_RESTRICT_READ = 1 << 1
AOS_RESTRICT_WRITE = 1 << 2
AOS_RESTRICT_ALL = AOS_RESTRICT_EXEC | AOS_RESTRICT_READ | AOS_RESTRICT_WRITE
AOS_AUDIT_NONE = 0
AOS_AUDIT_DENY = 1
AOS_AUDIT_ALL = 2
AOS_IOC_MAGIC = 0xA9


class _TaskCreate(ctypes.Structure):
    _fields_ = [
        ("label", ctypes.c_char * AOS_TASK_ID_LEN),
        ("workspace_path", ctypes.c_char * AOS_PATH_MAX),
        ("exec_path", ctypes.c_char * AOS_PATH_MAX),
        ("restrict_flags", ctypes.c_uint64),
        ("audit_level", ctypes.c_uint64),
        ("task_id", ctypes.c_uint64),
    ]


class _TaskBind(ctypes.Structure):
    _fields_ = [("task_id", ctypes.c_uint64), ("cgroup_fd", ctypes.c_int32),
                ("flags", ctypes.c_uint32)]


class _TaskPolicy(ctypes.Structure):
    _fields_ = [("task_id", ctypes.c_uint64), ("flags", ctypes.c_uint64),
                ("restrict_flags", ctypes.c_uint64), ("audit_level", ctypes.c_uint64)]


class _TaskQuery(ctypes.Structure):
    _fields_ = [("task_id", ctypes.c_uint64), ("flags", ctypes.c_uint64),
                ("n_threads", ctypes.c_uint64), ("n_finished", ctypes.c_uint64),
                ("restrict_flags", ctypes.c_uint64), ("audit_level", ctypes.c_uint64)]


class _TaskFinish(ctypes.Structure):
    _fields_ = [("task_id", ctypes.c_uint64), ("flags", ctypes.c_uint64)]


def _ioc(direction: int, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (AOS_IOC_MAGIC << 8) | number


_IOC_WRITE = 1
_IOC_READ = 2
_IOC_GET_ABI_VERSION = _ioc(_IOC_READ, 0x00, ctypes.sizeof(ctypes.c_uint32))
_IOC_CREATE = _ioc(_IOC_READ | _IOC_WRITE, 0x01, ctypes.sizeof(_TaskCreate))
_IOC_BIND = _ioc(_IOC_WRITE, 0x02, ctypes.sizeof(_TaskBind))
_IOC_POLICY = _ioc(_IOC_WRITE, 0x03, ctypes.sizeof(_TaskPolicy))
_IOC_QUERY = _ioc(_IOC_READ | _IOC_WRITE, 0x04, ctypes.sizeof(_TaskQuery))
_IOC_FINISH = _ioc(_IOC_WRITE, 0x05, ctypes.sizeof(_TaskFinish))


class AOSKernelControl:
    def __init__(self, device: Path = Path("/dev/aosctl"), *,
                 ioctl_fn: Callable[[int, int, bytearray, bool], object] | None = None) -> None:
        self.device = device
        self._ioctl_fn = ioctl_fn

    restrict_write = AOS_RESTRICT_WRITE
    audit_deny = AOS_AUDIT_DENY
    restrict_exec = AOS_RESTRICT_EXEC
    restrict_read = AOS_RESTRICT_READ
    restrict_all = AOS_RESTRICT_ALL
    audit_none = AOS_AUDIT_NONE
    audit_all = AOS_AUDIT_ALL

    def _ioctl(self, command: int, value: ctypes.Structure) -> ctypes.Structure:
        if sys.platform != "linux":
            raise OSError("AOS kernel control requires Linux")
        raw = bytearray(ctypes.string_at(ctypes.addressof(value), ctypes.sizeof(value)))
        if self._ioctl_fn is not None:
            self._ioctl_fn(-1, command, raw, True)
        else:
            import fcntl
            fd = os.open(self.device, os.O_RDWR | os.O_CLOEXEC)
            try:
                fcntl.ioctl(fd, command, raw, True)
            finally:
                os.close(fd)
        return type(value).from_buffer_copy(raw)

    @staticmethod
    def _encode(value: str, capacity: int, name: str) -> bytes:
        encoded = value.encode("utf-8")
        if not encoded or b"\0" in encoded or len(encoded) >= capacity:
            raise ValueError(f"{name} must be 1-{capacity - 1} UTF-8 bytes without NUL")
        return encoded

    def abi_version(self) -> int:
        value = ctypes.c_uint32()
        self._ioctl(_IOC_GET_ABI_VERSION, value)
        return int(value.value)

    def create_task(self, label: str, workspace: Path, executable: str = "", *,
                    restrict_flags: int = AOS_RESTRICT_WRITE,
                    audit_level: int = AOS_AUDIT_DENY) -> int:
        args = _TaskCreate()
        args.label = self._encode(label, AOS_TASK_ID_LEN, "label")
        args.workspace_path = self._encode(str(workspace), AOS_PATH_MAX, "workspace")
        if executable:
            args.exec_path = self._encode(executable, AOS_PATH_MAX, "executable")
        args.restrict_flags = restrict_flags
        args.audit_level = audit_level
        return int(self._ioctl(_IOC_CREATE, args).task_id)

    def bind_task(self, task_id: int, cgroup_fd: int) -> None:
        args = _TaskBind(task_id=task_id, cgroup_fd=cgroup_fd)
        self._ioctl(_IOC_BIND, args)

    def set_policy(self, task_id: int, restrict_flags: int, audit_level: int) -> None:
        args = _TaskPolicy(task_id=task_id, restrict_flags=restrict_flags,
                           audit_level=audit_level)
        self._ioctl(_IOC_POLICY, args)

    def query_task(self, task_id: int) -> dict[str, int]:
        result = self._ioctl(_IOC_QUERY, _TaskQuery(task_id=task_id))
        return {"task_id": int(result.task_id), "n_threads": int(result.n_threads),
                "n_finished": int(result.n_finished),
                "restrict_flags": int(result.restrict_flags),
                "audit_level": int(result.audit_level)}

    def finish_task(self, task_id: int) -> None:
        self._ioctl(_IOC_FINISH, _TaskFinish(task_id=task_id))
