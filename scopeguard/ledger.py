"""A small local ledger of the decisions YOU made about items (billed / dismissed).

scopeguard suggests; the human decides. Decisions live in `<out>/ledger.json` so a dismissed item does
not come back on the next run. Writes are atomic (temp file + rename) and serialised by a lock file,
so two terminals (or `watch` plus a manual `bill`) cannot corrupt it."""
from __future__ import annotations

import contextlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

LOCK_WAIT = 10.0
LOCK_STALE = 60.0


class LedgerBusy(RuntimeError):
    pass


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def decisions(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            d = data.get("decisions", {}) if isinstance(data, dict) else {}
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    @contextlib.contextmanager
    def _lock(self):
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + LOCK_WAIT
        while True:
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > LOCK_STALE:   # a crashed process left it behind
                        lock.unlink()
                        continue
                except OSError:
                    continue
                if time.time() > deadline:
                    raise LedgerBusy(f"{lock} is held by another scopeguard process; try again in a moment")
                time.sleep(0.05)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                lock.unlink()

    def record(self, fingerprint: str, decision: str, **info) -> None:
        if decision not in ("billed", "dismissed"):
            raise ValueError("decision must be billed or dismissed")
        with self._lock():
            d = self.decisions()
            d[fingerprint] = {"decision": decision, "at": datetime.now().isoformat(timespec="seconds"), **info}
            tmp = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"version": 1, "decisions": d}, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)

    def forget(self, fingerprint: str) -> bool:
        with self._lock():
            d = self.decisions()
            if fingerprint not in d:
                return False
            del d[fingerprint]
            tmp = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"version": 1, "decisions": d}, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
            return True
