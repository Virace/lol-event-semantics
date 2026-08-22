"""跨平台、进程崩溃后自动释放的本地 advisory file lock。"""

from __future__ import annotations

import importlib
import os
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator


class AdvisoryLockUnavailableError(RuntimeError):
    """另一个进程持有 advisory lock。"""


@contextmanager
def advisory_file_lock(path: Path) -> Iterator[None]:
    """独占锁定 ``path`` 的首字节；进程退出时由操作系统释放。"""

    handle = path.open("a+b")
    try:
        _ensure_lock_byte(handle)
        _acquire_lock(handle)
    except OSError as exc:
        handle.close()
        raise AdvisoryLockUnavailableError(str(path)) from exc
    try:
        yield
    finally:
        try:
            _release_lock(handle)
        finally:
            handle.close()


def _ensure_lock_byte(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())
    handle.seek(0)


def _acquire_lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        msvcrt = importlib.import_module("msvcrt")
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    fcntl = importlib.import_module("fcntl")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt = importlib.import_module("msvcrt")
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl = importlib.import_module("fcntl")
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
