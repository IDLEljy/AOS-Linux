"""Minimal Landlock v1/v2 filesystem policy wrapper for Linux x86_64."""

from __future__ import annotations

import ctypes
import errno
import os
import platform
from pathlib import Path

SYS_LANDLOCK_CREATE_RULESET = 444
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446
LANDLOCK_RULE_TYPE_PATH_BENEATH = 1
LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12
LANDLOCK_ACCESS_FS_REFER = 1 << 13

READ_EXECUTE = LANDLOCK_ACCESS_FS_EXECUTE | LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_READ_DIR
WRITE = (LANDLOCK_ACCESS_FS_WRITE_FILE | LANDLOCK_ACCESS_FS_REMOVE_DIR |
         LANDLOCK_ACCESS_FS_REMOVE_FILE | LANDLOCK_ACCESS_FS_MAKE_DIR |
         LANDLOCK_ACCESS_FS_MAKE_REG | LANDLOCK_ACCESS_FS_MAKE_SOCK |
         LANDLOCK_ACCESS_FS_MAKE_FIFO | LANDLOCK_ACCESS_FS_MAKE_CHAR |
         LANDLOCK_ACCESS_FS_MAKE_BLOCK | LANDLOCK_ACCESS_FS_MAKE_SYM)


class LandlockError(OSError):
    pass


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int)]


_libc = ctypes.CDLL(None, use_errno=True) if platform.system() == "Linux" else None
if _libc is not None:
    _libc.syscall.restype = ctypes.c_long


def _syscall(number: int, *args: object) -> int:
    if _libc is None or platform.machine() != "x86_64":
        raise LandlockError(errno.ENOTSUP, "Landlock wrapper supports Linux x86_64 only")
    result = _libc.syscall(ctypes.c_long(number), *args)
    if result < 0:
        code = ctypes.get_errno()
        raise LandlockError(code, os.strerror(code))
    return int(result)


def supported_abi() -> int:
    try:
        return _syscall(SYS_LANDLOCK_CREATE_RULESET, 0, 0, LANDLOCK_CREATE_RULESET_VERSION)
    except LandlockError as exc:
        if exc.errno in (errno.ENOSYS, errno.EOPNOTSUPP):
            return 0
        raise


def apply_workspace_policy(workspace: Path) -> None:
    """Allow reads and execution globally; allow filesystem mutation only in workspace."""
    abi = supported_abi()
    if abi < 1:
        raise LandlockError(errno.EOPNOTSUPP, "Landlock is unavailable")
    handled = READ_EXECUTE | WRITE
    if abi >= 2:
        handled |= LANDLOCK_ACCESS_FS_REFER
    attr = _RulesetAttr(handled, 0)
    ruleset_fd = _syscall(
        SYS_LANDLOCK_CREATE_RULESET,
        ctypes.c_void_p(ctypes.addressof(attr)),
        ctypes.c_size_t(ctypes.sizeof(attr)),
        ctypes.c_uint(0),
    )
    root_fd = os.open("/", os.O_PATH | os.O_DIRECTORY)
    workspace_fd = os.open(workspace, os.O_PATH | os.O_DIRECTORY)
    try:
        root_rule = _PathBeneathAttr(READ_EXECUTE, root_fd)
        _syscall(SYS_LANDLOCK_ADD_RULE, ctypes.c_int(ruleset_fd),
                 ctypes.c_int(LANDLOCK_RULE_TYPE_PATH_BENEATH),
                 ctypes.c_void_p(ctypes.addressof(root_rule)), ctypes.c_uint(0))
        workspace_access = READ_EXECUTE | WRITE
        if abi >= 2:
            workspace_access |= LANDLOCK_ACCESS_FS_REFER
        workspace_rule = _PathBeneathAttr(workspace_access, workspace_fd)
        _syscall(SYS_LANDLOCK_ADD_RULE, ctypes.c_int(ruleset_fd),
                 ctypes.c_int(LANDLOCK_RULE_TYPE_PATH_BENEATH),
                 ctypes.c_void_p(ctypes.addressof(workspace_rule)), ctypes.c_uint(0))
        if abi >= 2:
            handled |= LANDLOCK_ACCESS_FS_REFER
        _set_no_new_privs()
        _syscall(SYS_LANDLOCK_RESTRICT_SELF, ctypes.c_int(ruleset_fd), ctypes.c_uint(0))
    finally:
        os.close(root_fd)
        os.close(workspace_fd)
        os.close(ruleset_fd)


def _set_no_new_privs() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        code = ctypes.get_errno()
        raise LandlockError(code, os.strerror(code))
