"""`scopeguard watch <folder>`: add each new or changed export to the client's thread, then re-analyse the whole thread."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from .messages import load_messages
from .pipeline import Options
from .thread import analyze_thread

EXTS = {".txt", ".eml", ".mbox", ".json"}


def _key(p: Path) -> str:
    st = p.stat()
    return f"{p.name}:{st.st_size}:{int(st.st_mtime)}"


def scan_once(folder: Path, sow, sow_text: str, opts: Options, out_root: Path, seen: dict,
              log: Callable[[str], None] = print) -> int:
    done = 0
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.suffix.lower() not in EXTS or p.name.startswith("."):
            continue
        if time.time() - p.stat().st_mtime < 1.0:   # still being written
            continue
        k = _key(p)
        if seen.get(p.name) == k:
            continue
        try:
            msgs = load_messages([p])
            res, dest = analyze_thread(out_root, opts.client, msgs, sow, sow_text, opts)
            outs = sum(1 for f in res.findings if f.status == "extra")
            log(f"[watch] {p.name}: {res.meta['new_messages']} new messages, thread of {res.meta['thread_messages']}, "
                f"{outs} possible extras -> {dest}")
        except Exception as e:  # one bad file must not stop the watcher
            log(f"[watch] {p.name}: skipped ({type(e).__name__}: {e})")
        seen[p.name] = k
        done += 1
    return done


def watch(folder: str | Path, sow, sow_text: str, opts: Options, out_root: str | Path, interval: float = 5.0,
          once: bool = False, log: Callable[[str], None] = print) -> None:
    folder, out_root = Path(folder), Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    state = out_root / ".watch-state.json"
    try:
        seen = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    while True:
        scan_once(folder, sow, sow_text, opts, out_root, seen, log)
        state.write_text(json.dumps(seen), encoding="utf-8")
        if once:
            return
        time.sleep(interval)
