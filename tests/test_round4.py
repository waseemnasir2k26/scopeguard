"""v1.1.2: a reworded repeat of a BILLED or DISMISSED item is held as "Needs your call" (never double-billed),
and a single-sender export ignores a non-matching --client with a warning instead of refusing."""
import json
from pathlib import Path

import pytest

from scopeguard.bill import BillError, bill
from scopeguard.cli import main
from scopeguard.ledger import Ledger
from scopeguard.llm import NoLLM
from scopeguard.models import Message
from scopeguard.outputs import write_all
from scopeguard.pipeline import ClientError, Options, analyze
from scopeguard.rates import RateCard
from scopeguard.repeat import is_repeat
from scopeguard.sow import load_text, parse_sow

ROOT = Path(__file__).parent.parent
SOW_PATH = ROOT / "examples" / "sow.md"
PRICING = "Please add a Pricing page for the studio to the site."
PRICING_REWORDED = "Could you add a Pricing page for the studio on the site?"


def _analyze(texts, ledger=None, client=("Dana",), senders=None):
    msgs = []
    for i, t in enumerate(texts):
        who = senders[i] if senders else "Dana"
        msgs.append(Message(id="", sender=who, text=t, ts=f"2026-10-05T09:{i:02d}:00"))
    sow_text = load_text(SOW_PATH)
    return analyze(parse_sow(sow_text), messages=msgs, sow_text=sow_text,
                   opts=Options(llm=NoLLM(), client=list(client), ledger=ledger))


def _record(led, f, decision):
    led.record(f.fingerprint, decision, quote=f.request.quote[:300], date=f.request.ts, sender=f.request.sender,
               clause=f.clause_id or "")


# ---------------------------------------------------------------- the matcher
def test_repeat_matcher_catches_a_reword_and_not_a_different_ask():
    assert is_repeat(PRICING_REWORDED, "C2", PRICING, "")                          # same content words: Jaccard 1.0
    assert is_repeat("Could you add a pricing table for the studio", "C2", PRICING, "C2")   # same clause, 2 shared words
    assert not is_repeat("Could you add a shop and a blog for the studio", "C3", PRICING, "C2")  # other clause, 1 shared word
    assert not is_repeat("Could we add a booking calendar to the Contact page?", "C2", PRICING, "C2")


# ---------------------------------------------------------------- pipeline
def test_reworded_repeat_of_a_billed_item_is_held_not_a_possible_extra(tmp_path):
    led = Ledger(tmp_path / "ledger.json")
    [first] = _analyze([PRICING], led).findings
    assert first.status == "extra"
    _record(led, first, "billed")
    [again] = _analyze([PRICING_REWORDED], led).findings
    assert again.status == "call"
    assert again.repeat_of.startswith(f'billed item {first.fingerprint} (2026-10-05): "{PRICING}"')
    assert again.reason == f"looks like a repeat of billed item {first.fingerprint} (2026-10-05): {PRICING}"


def test_repeat_of_a_dismissed_item_is_held_too(tmp_path):
    led = Ledger(tmp_path / "ledger.json")
    [first] = _analyze([PRICING], led).findings
    _record(led, first, "dismissed")
    [again] = _analyze([PRICING_REWORDED], led).findings
    assert again.status == "call"
    assert again.reason.startswith(f"looks like a repeat of dismissed item {first.fingerprint}")


def test_an_unrelated_request_is_not_held_as_a_repeat(tmp_path):
    led = Ledger(tmp_path / "ledger.json")
    [first] = _analyze([PRICING], led).findings
    _record(led, first, "billed")
    [other] = _analyze(["Please add a booking calendar to the Contact page."], led).findings
    assert other.status == "extra" and not other.repeat_of


def test_single_sender_non_matching_client_warns_and_is_ignored():
    res = _analyze([PRICING], client=("Alex",))                 # one sender: nothing to guess, so no refusal
    assert len(res.findings) == 1 and res.findings[0].status == "extra"
    assert any("matches no sender" in w and "ignored" in w for w in res.warnings)


def test_multi_sender_non_matching_client_is_still_refused():
    with pytest.raises(ClientError, match="matches no sender"):
        _analyze([PRICING, "ok"], client=("Zed",), senders=["Dana Okafor", "Sam Lee"])


# ---------------------------------------------------------------- CLI: the double bill, end to end
def _export(path: Path, line: str, sender="Dana Okafor"):
    path.write_text(f"05/10/2026, 09:00 - {sender}: {line}\n", encoding="utf-8")


def test_double_bill_is_blocked_end_to_end(tmp_path, capsys):
    out = tmp_path / "out"
    e1, e2 = tmp_path / "e1.txt", tmp_path / "e2.txt"
    _export(e1, PRICING)
    assert main(["analyze", "--sow", str(SOW_PATH), "--messages", str(e1), "--client", "Dana Okafor",
                 "--llm", "none", "--out", str(out)]) == 0
    findings = json.loads((out / "findings.json").read_text(encoding="utf-8"))["findings"]
    [fp] = [f["fingerprint"] for f in findings if f["status"] == "extra"]
    assert main(["bill", fp, "--out", str(out)]) == 0                       # you billed it

    _export(e2, PRICING_REWORDED)
    assert main(["analyze", "--sow", str(SOW_PATH), "--messages", str(e2), "--client", "Dana Okafor",
                 "--llm", "none", "--out", str(out)]) == 0
    capsys.readouterr()
    [again] = json.loads((out / "findings.json").read_text(encoding="utf-8"))["findings"]
    assert again["status"] == "call" and again["repeat_of"] and fp in again["repeat_of"]
    new_fp = again["fingerprint"]

    # the bulk path never drafts it, and the listing shows it
    assert main(["bill", "--all-strong", "--yes", "--out", str(out)]) == 1
    captured = capsys.readouterr()
    assert new_fp in captured.out and "--repeat-ok" in captured.out
    assert "No strong possible extras" in captured.err
    assert not list((out / "change-orders").glob(f"{new_fp}*"))

    # bill <id> refuses without the explicit flag
    assert main(["bill", new_fp, "--out", str(out)]) == 1
    assert "--repeat-ok" in capsys.readouterr().err
    assert not list((out / "change-orders").glob(f"{new_fp}*"))

    # with the explicit flag you can still draft it
    assert main(["bill", new_fp, "--repeat-ok", "--out", str(out)]) == 0
    assert list((out / "change-orders").glob(f"{new_fp}*"))


def test_bill_api_refuses_repeat_without_repeat_ok_and_records_the_clause(tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    led = Ledger(out / "ledger.json")
    [first] = _analyze([PRICING], led).findings
    _record(led, first, "billed")
    res = _analyze([PRICING_REWORDED], led)
    write_all(res, out, RateCard(), "friendly")
    fp = res.findings[0].fingerprint
    with pytest.raises(BillError, match="--repeat-ok"):
        bill(out, [fp], RateCard())
    bill(out, [fp], RateCard(), repeat_ok=True)
    assert list((out / "change-orders").glob(f"{fp}*"))
    entry = Ledger(out / "ledger.json").decisions()[fp]
    assert entry["decision"] == "billed" and entry["clause"] == res.findings[0].clause_id
