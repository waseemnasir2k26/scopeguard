"""Per-client accumulated thread, for `watch` and `serve`.

Analysing one email at a time cannot see a retraction two emails later, a free offer in a reply, the
revision rounds so far, or what you already decided. So every message the watcher or the server sees is
appended to `<data>/<client>/messages.jsonl` and the WHOLE thread is re-analysed, with the ledger applied."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .fsutil import file_lock
from .ledger import Ledger
from .models import Message
from .textutil import normalize


class StoreCorrupt(RuntimeError):
    pass


def client_slug(names) -> str:
    base = "-".join(normalize(n) for n in names if n) or "client"
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:60] or "client"


def _key(m: Message) -> str:
    return hashlib.sha1(f"{m.sender}|{m.ts or ''}|{m.text}".encode("utf-8")).hexdigest()


class ThreadStore:
    def __init__(self, root: str | Path, names):
        self.dir = Path(root) / client_slug(names)
        self.path = self.dir / "messages.jsonl"
        self.ledger_path = self.dir / "ledger.json"

    def load(self) -> list[Message]:
        if not self.path.exists():
            return []
        out: list[Message] = []
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                out.append(Message(id="", sender=d["sender"], text=d["text"], ts=d.get("ts"),
                                   source=d.get("source", ""), subject=d.get("subject", "")))
            except (ValueError, KeyError, TypeError):
                raise StoreCorrupt(f"{self.path} line {n} is unreadable; refusing to analyse a thread with a hole in it. "
                                   "Fix or remove that line.") from None
        if out and all(m.ts for m in out):
            out.sort(key=lambda m: m.ts)          # stable: same-second messages keep arrival order
        for i, m in enumerate(out, 1):
            m.id = f"M{i}"
        return out

    def append(self, messages) -> int:
        """Add messages not already in the thread (a re-sent email is not added twice). Returns how many were new."""
        self.dir.mkdir(parents=True, exist_ok=True)
        with file_lock(self.path.with_name(self.path.name + ".lock")):
            seen = set()
            if self.path.exists():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    try:
                        d = json.loads(line)
                        seen.add(_key(Message(id="", sender=d["sender"], text=d["text"], ts=d.get("ts"))))
                    except (ValueError, KeyError, TypeError):
                        raise StoreCorrupt(f"{self.path} has an unreadable line; refusing to append.") from None
            fresh = []
            for m in messages:
                k = _key(m)
                if k not in seen:
                    seen.add(k)
                    fresh.append(m)
            if fresh:
                with open(self.path, "a", encoding="utf-8") as fh:
                    for m in fresh:
                        fh.write(json.dumps({"sender": m.sender, "ts": m.ts, "text": m.text, "source": m.source,
                                             "subject": m.subject}, ensure_ascii=False) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
            return len(fresh)

    def ledger(self) -> Ledger:
        return Ledger(self.ledger_path)
