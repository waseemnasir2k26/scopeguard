"""Analyse a client's WHOLE accumulated thread (see store.py), with the ledger applied."""
from __future__ import annotations

import dataclasses
from pathlib import Path

from .outputs import write_all
from .pipeline import Options, analyze
from .store import ThreadStore


def analyze_thread(root: str | Path, names, new_messages, sow, sow_text: str, opts: Options):
    """Append the new messages to the client's thread, re-analyse everything, write the outputs into the
    client's folder. Returns (result, folder)."""
    store = ThreadStore(root, names)
    n_new = store.append(new_messages)
    msgs = store.load()
    o = dataclasses.replace(opts, ledger=store.ledger(), client=list(names) if names else opts.client)
    res = analyze(sow, messages=msgs, opts=o, sow_text=sow_text)
    res.meta["thread_messages"] = len(msgs)
    res.meta["new_messages"] = n_new
    write_all(res, store.dir, opts.rates, opts.tone)
    return res, store.dir
