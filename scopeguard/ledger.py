"""A small local ledger of the decisions YOU made about items (billed / dismissed).

scopeguard suggests; the human decides. Decisions live in `<out>/ledger.json` so a dismissed item does
not come back on the next run. Writes are atomic (fsynced temp file + rename), serialised by a lock
file, and the previous version is kept as `ledger.json.bak`. A ledger that cannot be read is never
treated as empty (the next write would wipe your history): scopeguard stops and tells you how to
restore it (`scopeguard ledger repair`)."""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from .fsutil import LockBusy, atomic_write, file_lock

LedgerBusy = LockBusy


class LedgerCorrupt(RuntimeError):
    pass


def _parse(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("decisions", {}), dict):
        raise ValueError("unexpected shape")
    return data.get("decisions", {})


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @property
    def lock_path(self) -> Path:
        return self.path.with_name(self.path.name + ".lock")

    def lock(self):
        return file_lock(self.lock_path)

    def decisions(self) -> dict:
        """The recorded decisions. A missing file is an empty ledger; an unreadable one is an error."""
        if not self.path.exists():
            return {}
        try:
            return _parse(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            bak = self.path.with_name(self.path.name + ".bak")
            hint = (f"A backup exists: run `scopeguard ledger repair --out {self.path.parent}` to restore it."
                    if bak.exists() else "No backup exists; move the file aside to start a new ledger.")
            raise LedgerCorrupt(f"{self.path} is unreadable ({type(e).__name__}). Refusing to continue, because the next "
                                f"write would wipe your history. {hint}") from None

    def _write(self, d: dict) -> None:
        atomic_write(self.path, json.dumps({"version": 1, "decisions": d}, indent=2, ensure_ascii=False), backup=True)

    def record(self, fingerprint: str, decision: str, **info) -> None:
        if decision not in ("billed", "dismissed"):
            raise ValueError("decision must be billed or dismissed")
        with self.lock():
            d = self.decisions()
            d[fingerprint] = {"decision": decision, "at": datetime.now().isoformat(timespec="seconds"), **info}
            self._write(d)

    def restore(self, fingerprint: str, previous: dict | None) -> None:
        """Put back the entry that was there before a record() (or remove it when there was none). Used to
        roll back a decision whose change-order draft could not be written."""
        with self.lock():
            d = self.decisions()
            if previous is None:
                d.pop(fingerprint, None)
            else:
                d[fingerprint] = previous
            self._write(d)

    def forget(self, fingerprint: str) -> bool:
        with self.lock():
            d = self.decisions()
            if fingerprint not in d:
                return False
            del d[fingerprint]
            self._write(d)
            return True

    def repair(self) -> str:
        """Restore ledger.json from ledger.json.bak. The unreadable file is kept as ledger.json.corrupt-<time>."""
        bak = self.path.with_name(self.path.name + ".bak")
        if not bak.exists():
            raise LedgerCorrupt(f"No backup at {bak}; nothing to restore from.")
        try:
            _parse(bak.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise LedgerCorrupt(f"The backup {bak} is unreadable too. Restore it from your own copy.") from None
        with self.lock():
            if self.path.exists():
                shutil.copy2(self.path, self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}"))
            shutil.copy2(bak, self.path)
        return f"restored {self.path} from {bak} ({len(self.decisions())} decisions)"
