"""v1.1.3: bill validates the ledger before writing anything, rolls back a decision whose draft failed,
retries os.replace on Windows, and parses a one-line (or unterminated) WhatsApp export."""
import json
import os
from pathlib import Path

import pytest

import scopeguard.bill as bill_mod
import scopeguard.fsutil as fsutil
from scopeguard.bill import bill, load_findings
from scopeguard.cli import main
from scopeguard.ledger import Ledger
from scopeguard.messages import parse_file
from scopeguard.rates import RateCard
from scopeguard.status import EXTRA

ROOT = Path(__file__).parent.parent


def _analyzed(tmp_path):
    chat = tmp_path / "chat.txt"
    chat.write_text("\n".join([
        "05/10/2026, 09:00 - Dana Okafor: Can we add an online shop so people can buy our prints?",
        "05/10/2026, 09:05 - Dana Okafor: Could you make the site feel more premium?"]), encoding="utf-8")
    out = tmp_path / "out"
    rc = main(["analyze", "--sow", str(ROOT / "examples" / "sow.md"), "--messages", str(chat), "--client", "Dana Okafor",
               "--llm", "mock", "--out", str(out)])
    assert rc == 0
    return out


def _extra_fp(out):
    _, findings = load_findings(out)
    return next(f for f in findings if f.status == EXTRA).fingerprint


def test_truncated_ledger_refuses_before_any_draft_or_folder(tmp_path):
    out = _analyzed(tmp_path)
    fp = _extra_fp(out)
    (out / "ledger.json").write_text('{"version": 1, "decisions": {"abc', encoding="utf-8")   # truncated
    rc = main(["bill", fp, "--out", str(out)])
    assert rc == 1
    assert not (out / "change-orders").exists()
    assert not [p for p in out.rglob("*") if p.name.startswith(fp)]     # no draft written
    assert (out / "ledger.json").read_text(encoding="utf-8") == '{"version": 1, "decisions": {"abc'   # untouched


def test_draft_failure_rolls_back_the_ledger_entry(tmp_path, monkeypatch):
    out = _analyzed(tmp_path)
    fp = _extra_fp(out)

    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(bill_mod, "draft", broken)
    with pytest.raises(OSError):
        bill(out, [fp], RateCard())
    assert fp not in Ledger(out / "ledger.json").decisions()
    assert not (out / "change-orders").exists()
    assert "billed" not in (out / "findings.json").read_text(encoding="utf-8")


def test_successful_bill_records_then_drafts(tmp_path):
    out = _analyzed(tmp_path)
    fp = _extra_fp(out)
    written = bill(out, [fp], RateCard())
    assert len(written) == 1 and written[0].exists()
    assert Ledger(out / "ledger.json").decisions()[fp]["decision"] == "billed"


def test_atomic_write_retries_permission_error(tmp_path, monkeypatch):
    target = tmp_path / "ledger.json"
    real = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise PermissionError(13, "held by another handle")
        return real(src, dst)

    monkeypatch.setattr(fsutil.os, "replace", flaky)
    fsutil.atomic_write(target, "{}")
    assert calls["n"] == 4 and target.read_text(encoding="utf-8") == "{}"
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_atomic_write_gives_up_after_ten_attempts(tmp_path, monkeypatch):
    def always(src, dst):
        raise PermissionError(13, "locked")

    monkeypatch.setattr(fsutil.os, "replace", always)
    monkeypatch.setattr(fsutil.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        fsutil.atomic_write(tmp_path / "x.json", "{}")
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


@pytest.mark.parametrize("body", [
    "10/03/2026, 09:30 - Dana Okafor: Please add a blog page to the site",
    "10/03/2026, 09:30 - Dana Okafor: Please add a blog page to the site\n",
    "10/03/2026, 09:30 - Dana Okafor: Please add a blog page to the site\r\n",
])
def test_single_line_whatsapp_export_is_parsed(tmp_path, body):
    p = tmp_path / "chat.txt"
    p.write_bytes(body.encode("utf-8"))
    msgs = parse_file(p)
    assert len(msgs) == 1
    assert msgs[0].sender == "Dana Okafor"
    assert msgs[0].text == "Please add a blog page to the site"
    assert msgs[0].ts == "2026-03-10T09:30:00"


def test_two_line_whatsapp_without_trailing_newline(tmp_path):
    p = tmp_path / "chat.txt"
    p.write_text("10/03/2026, 09:30 - Dana Okafor: Please add a blog\n10/03/2026, 09:31 - Dana Okafor: And a shop",
                 encoding="utf-8")
    msgs = parse_file(p)
    assert [m.sender for m in msgs] == ["Dana Okafor", "Dana Okafor"]
    assert msgs[1].text == "And a shop"


def test_plain_note_is_not_mistaken_for_whatsapp(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("Just a plain note about the project, no timestamps here.", encoding="utf-8")
    msgs = parse_file(p)
    assert msgs[0].sender == "client"
