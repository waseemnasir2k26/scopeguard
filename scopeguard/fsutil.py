"""Small, boring file helpers: a cross-platform lock file and an atomic, fsynced write with a backup."""
from __future__ import annotations

import contextlib
import os
import shutil
import time
import uuid
from pathlib import Path

LOCK_WAIT = 10.0
LOCK_STALE = 60.0


class LockBusy(RuntimeError):
    pass


@contextlib.contextmanager
def file_lock(lock_path: str | Path, wait: float = LOCK_WAIT, stale: float = LOCK_STALE):
    """Exclusive lock via O_CREAT|O_EXCL. A lock older than `stale` seconds is taken to be left by a crash."""
    lock = Path(lock_path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + wait
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale:
                    lock.unlink()
                    continue
            except OSError:
                continue
            if time.time() > deadline:
                raise LockBusy(f"{lock} is held by another scopeguard process; try again in a moment")
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock.unlink()


def _replace(src: Path, dst: Path, attempts: int = 10) -> None:
    """os.replace with retries: on Windows a reader or antivirus scan can hold the target handle for a
    few milliseconds, which surfaces as PermissionError(13). Back off briefly and try again."""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.02 * (i + 1))


def atomic_write(path: str | Path, text: str, backup: bool = False) -> None:
    """Write `text` to `path` through a uniquely named temp file in the same folder: fsync, then os.replace
    (retried on Windows). With backup=True the previous version is first copied to `<path>.bak`, so one bad
    write is never fatal. The temp name is unique per call, so threads in one process never share it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if backup and path.exists():
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        _replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
