"""v1.1 critic round: withdrawals no longer depend on vocabulary, ids are stable, the ledger cannot be silently
wiped, watch/serve see the whole thread, and --client never guesses."""
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from scopeguard.bill import BillError, bill, ledger_for
from scopeguard.changeorder import draft
from scopeguard.cli import main
from scopeguard.extract import _same_intent, split_sentences
from scopeguard.ledger import Ledger, LedgerCorrupt
from scopeguard.llm import MockLLM, NoLLM
from scopeguard.messages import parse_text_blob
from scopeguard.models import Message, Request
from scopeguard.pipeline import ClientError, Options, analyze
from scopeguard.rates import RateCard
from scopeguard.server import make_server
from scopeguard.sow import load_text, parse_sow
from scopeguard.store import StoreCorrupt, ThreadStore

ROOT = Path(__file__).parent.parent
SOW_TEXT = load_text(ROOT / "examples" / "sow.md")
SOW = parse_sow(SOW_TEXT)


def run(lines, client=("Dana",), llm=None, sow_text=SOW_TEXT, **kw):
    msgs = []
    for i, ln in enumerate(lines):
        who, text = ln if isinstance(ln, tuple) else ("Dana", ln)
        msgs.append(Message(id="", sender=who, text=text, ts=f"2026-04-01T{10 + i // 6:02d}:{(i * 7) % 60:02d}:00"))
    return analyze(parse_sow(sow_text), messages=msgs, opts=Options(llm=llm or NoLLM(), client=list(client), **kw), sow_text=sow_text)


# ---------------------------------------------------------------- B1: retractions beyond a closed vocabulary
PHRASINGS = ["Cancel the shop.", "Drop the shop.", "Skip the shop.", "Scratch the shop.", "Ignore the shop.",
             "We won't do the shop.", "The shop is no longer required.", "No shop please.", "Hold off on the shop.",
             "Pls remove shop.", "Plz cancel the shop.", "Pause the shop.", "Disregard the shop.", "Please leave out the shop.",
             "We will not be doing the shop.", "Shop is cancelled.", "Let's park the shop for now.", "The shop isn't happening.",
             "Forget the shop.", "Nix the shop.", "About the shop: we decided against it.", "Scrap the online shop idea.",
             "Ditch the shop.", "Stop work on the shop.", "The shop can wait.", "Actually we're not going ahead with the shop.",
             "No more shop please.", "Shop is off.", "Shop? Not now.", "I take back the shop request.",
             "Remove the shop from the list.", "Take out the shop.", "We are no longer doing the online shop."]


@pytest.mark.parametrize("phrase", PHRASINGS)
def test_no_phrasing_of_a_retraction_leaves_a_billable_item(phrase):
    res = run(["Please add an online shop.", phrase])
    assert not [f for f in res.findings if f.status == "extra"], [(f.status, f.request.quote) for f in res.findings]


def test_safety_net_catches_wording_nobody_listed():
    res = run(["Please add an online shop.", "The shop can wait."])
    [f] = res.findings
    assert f.status == "call" and f.later_mention == "The shop can wait."
    assert f.reason.startswith('Later message mentions this: "The shop can wait."')


def test_a_plain_repeat_does_not_cancel_a_request_but_is_shown():
    res = run(["Please add an online shop.", "Thanks! Any news?", "Please add the online shop soon."])
    extras = [f for f in res.findings if f.status == "extra"]
    assert extras and all(f.latest_related for f in extras) and any("soon" in f.latest_related for f in extras)


def test_unrelated_later_messages_do_not_move_an_extra():
    res = run(["Please add an online shop.", "Could you make the footer text smaller?", "Thanks, looks great."])
    assert next(f for f in res.findings if "shop" in f.request.quote).status == "extra"


def test_every_client_sentence_ends_up_somewhere_visible():
    lines = ["Thanks for the update! Please add an online shop.", "Never mind, scratch that. Hope you had a good weekend.",
             "When will the draft be ready? Also, could we have a blog?", "Please send the invoice to accounts@harborlight.example.",
             "Can you make the logo bigger? ok"]
    res = run(lines)
    shown = []
    for f in res.findings:
        shown += f.request.all_quotes()
        if f.request.withdrawn_by:
            shown.append(f.request.withdrawn_by)
    shown += [o["quote"] for o in res.meta["other_client_messages"]]
    for ln in lines:
        for sent in split_sentences(ln):
            assert sent in shown, f"{sent!r} vanished"


def test_bill_listing_shows_the_latest_related_client_message(tmp_path, monkeypatch, capsys):
    chat = tmp_path / "c.txt"
    chat.write_text("\n".join([
        "05/10/2026, 09:00 - Dana Okafor: Can we add an online shop?",
        "05/10/2026, 09:30 - Dana Okafor: Please add the online shop soon.",
        "05/10/2026, 09:40 - Dana Okafor: Could we also have a blog?"]), encoding="utf-8")
    out = tmp_path / "o"
    assert main(["analyze", "--sow", str(ROOT / "examples" / "sow.md"), "--messages", str(chat), "--client", "Dana Okafor",
                 "--llm", "mock", "--out", str(out)]) == 0
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    main(["bill", "--all-strong", "--out", str(out)])
    text = capsys.readouterr().out
    assert "latest related client message" in text and "Please add the online shop soon." in text


def test_bill_all_strong_never_drafts_an_item_the_net_caught(tmp_path, capsys):
    chat = tmp_path / "c.txt"
    chat.write_text("\n".join([
        "05/10/2026, 09:00 - Dana Okafor: Please add an online shop.",
        "05/10/2026, 09:30 - Dana Okafor: The shop can wait.",
        "05/10/2026, 09:40 - Dana Okafor: Could we also have a blog?"]), encoding="utf-8")
    out = tmp_path / "o"
    main(["analyze", "--sow", str(ROOT / "examples" / "sow.md"), "--messages", str(chat), "--client", "Dana Okafor",
          "--llm", "none", "--out", str(out)])
    assert main(["bill", "--all-strong", "--yes", "--out", str(out)]) == 0
    drafts = list((out / "change-orders").glob("*.md"))
    assert len(drafts) == 1 and "blog" in drafts[0].read_text(encoding="utf-8").lower()
    assert "shop" not in drafts[0].read_text(encoding="utf-8").lower().split("you asked:")[1].split("\n")[0]


# ---------------------------------------------------------------- M1: ids that do not move
ANDROID_DMY = """13/03/2026, 14:05 - Dana Okafor: Can we add an online shop so people can buy our prints?
13/03/2026, 14:09 - Dana Okafor: Could we also have a blog?
14/03/2026, 09:30 - Alex Rivera: Let me check.
25/04/2026, 10:00 - Dana Okafor: Could we also have a blog?
"""
ANDROID_MDY = """3/13/26, 2:05 PM - Dana Okafor: Can we add an online shop so people can buy our prints?
3/13/26, 2:09 PM - Dana Okafor: Could we also have a blog?
3/14/26, 9:30 AM - Alex Rivera: Let me check.
4/25/26, 10:00 AM - Dana Okafor: Could we also have a blog?
"""
IOS = """[13/03/26, 2:05:33 PM] Dana Okafor: Can we add an online shop so people can buy our prints?
[13/03/26, 2:09:41 PM] Dana Okafor: Could we also have a blog?
[14/03/26, 9:30:02 AM] Alex Rivera: Let me check.
[25/04/26, 10:00:10 AM] Dana Okafor: Could we also have a blog?
"""


def _ids(text, extra=""):
    msgs = parse_text_blob(extra + text, "whatsapp")
    res = analyze(SOW, messages=msgs, opts=Options(llm=NoLLM(), client=["Dana Okafor"]), sow_text=SOW_TEXT)
    return {f.request.quote + f"#{f.request.occ}": f.fingerprint for f in res.findings}


def test_ids_survive_date_order_format_and_seconds():
    a, b, c = _ids(ANDROID_DMY), _ids(ANDROID_MDY), _ids(IOS)
    assert a == b == c and len(a) == 3                      # a 25 April repeat is a new item, with its own id
    blogs = [fp for k, fp in a.items() if k.startswith("Could we also have a blog?")]
    assert len(set(blogs)) == len(blogs) == 2            # the second identical ask has its own id


def test_adding_a_later_line_does_not_move_earlier_ids():
    before = _ids(ANDROID_DMY)
    after = _ids(ANDROID_DMY + "15/03/2026, 11:00 - Dana Okafor: Please add a booking calendar.\n")
    assert all(after[k] == v for k, v in before.items())
    assert len(after) == len(before) + 1


def test_a_billed_item_does_not_come_back_as_new(tmp_path):
    def go(text):
        chat = tmp_path / "c.txt"
        chat.write_text(text, encoding="utf-8")
        assert main(["analyze", "--sow", str(ROOT / "examples" / "sow.md"), "--messages", str(chat), "--client", "Dana Okafor",
                     "--llm", "none", "--out", str(tmp_path / "o")]) == 0
        return json.loads((tmp_path / "o" / "findings.json").read_text(encoding="utf-8"))["findings"]
    first = go(ANDROID_DMY)
    shop = next(f for f in first if "online shop" in f["quote"])
    assert main(["bill", shop["fingerprint"], "--out", str(tmp_path / "o")]) == 0
    again = go(ANDROID_MDY)                                   # same chat, exported month-first on another phone
    assert next(f for f in again if "online shop" in f["quote"])["status"] == "billed"
    assert [f for f in again if f["status"] == "extra" and "online shop" in f["quote"]] == []


def test_ledger_rebinds_by_quote_similarity_with_a_warning(tmp_path):
    led = Ledger(tmp_path / "ledger.json")
    led.record("oldid123", "dismissed", quote="Can we add an online shop so people can buy our prints?")
    msgs = parse_text_blob(ANDROID_DMY, "whatsapp")
    res = analyze(SOW, messages=msgs, opts=Options(llm=NoLLM(), client=["Dana Okafor"], ledger=led), sow_text=SOW_TEXT)
    shop = next(f for f in res.findings if "online shop" in f.request.quote)
    assert shop.status == "dismissed" and any("re-bound" in w and "oldid123" in w for w in res.warnings)


# ---------------------------------------------------------------- M2: a corrupt ledger is never "empty"
def test_corrupt_ledger_stops_everything_and_keeps_a_backup(tmp_path, capsys):
    led = Ledger(tmp_path / "o" / "ledger.json")
    led.record("a1", "dismissed", quote="x")
    led.record("b2", "billed", quote="y")
    assert (tmp_path / "o" / "ledger.json.bak").exists()                # previous version kept on every write
    (tmp_path / "o" / "ledger.json").write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(LedgerCorrupt, match="Refusing to continue"):
        led.decisions()
    with pytest.raises(LedgerCorrupt):
        led.record("c3", "billed")                                        # the write that would have wiped history
    assert (tmp_path / "o" / "ledger.json").read_text(encoding="utf-8") == "{ this is not json"
    chat = tmp_path / "c.txt"
    chat.write_text(ANDROID_DMY, encoding="utf-8")
    rc = main(["analyze", "--sow", str(ROOT / "examples" / "sow.md"), "--messages", str(chat), "--client", "Dana Okafor",
               "--llm", "none", "--out", str(tmp_path / "o")])
    assert rc == 1 and "ledger.json" in capsys.readouterr().err
    assert main(["ledger", "repair", "--out", str(tmp_path / "o")]) == 0
    assert set(led.decisions()) == {"a1"}                                  # restored from the .bak
    assert list((tmp_path / "o").glob("ledger.json.corrupt-*"))            # the unreadable file is kept, not deleted


def test_atomic_writes_leave_no_temp_files_and_no_lock(tmp_path):
    out = tmp_path / "o"
    chat = tmp_path / "c.txt"
    chat.write_text(ANDROID_DMY, encoding="utf-8")
    main(["analyze", "--sow", str(ROOT / "examples" / "sow.md"), "--messages", str(chat), "--client", "Dana Okafor",
          "--llm", "none", "--out", str(out)])
    names = {p.name for p in out.iterdir()}
    assert not [n for n in names if n.endswith(".tmp") or n.endswith(".lock")], names


# ---------------------------------------------------------------- M3: watch / serve see the whole thread
@pytest.fixture
def thread_server(tmp_path):
    srv = make_server("127.0.0.1", 0, SOW, SOW_TEXT, Options(llm=NoLLM(), client=["Dana Okafor"]), token="tok",
                      data_dir=str(tmp_path / "data"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path / "data"
    srv.shutdown()
    srv.server_close()


def _post(url, body):
    req = urllib.request.Request(url + "/analyze", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer tok"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_server_accumulates_the_thread_so_a_later_email_withdraws_an_earlier_one(thread_server):
    url, data = thread_server
    s1, r1 = _post(url, {"messages": "05/10/2026, 09:00 - Dana Okafor: Please add an online shop.\n", "format": "whatsapp"})
    assert s1 == 200 and r1["possible_extras_count"] == 1
    s2, r2 = _post(url, {"messages": "06/10/2026, 09:00 - Dana Okafor: Never mind the shop, cancel it.\n", "format": "whatsapp"})
    assert s2 == 200 and r2["possible_extras_count"] == 0 and r2["thread"]["messages"] == 2
    assert next(f for f in r2["findings"] if "online shop" in f["quote"])["status"] == "withdrawn"
    # the same email delivered twice is not counted twice
    s3, r3 = _post(url, {"messages": "06/10/2026, 09:00 - Dana Okafor: Never mind the shop, cancel it.\n", "format": "whatsapp"})
    assert r3["thread"]["messages"] == 2 and r3["thread"]["new_messages"] == 0
    assert (data / "dana-okafor" / "messages.jsonl").exists() and (data / "dana-okafor" / "report.html").exists()


def test_server_applies_the_ledger_you_wrote_with_bill_and_dismiss(thread_server):
    url, data = thread_server
    _, r1 = _post(url, {"messages": "05/10/2026, 09:00 - Dana Okafor: Could we also have a blog?\n", "format": "whatsapp"})
    fp = r1["possible_extras"][0]["fingerprint"]
    assert main(["dismiss", fp, "--out", str(data / "dana-okafor")]) == 0
    _, r2 = _post(url, {"messages": "05/10/2026, 10:00 - Dana Okafor: Thanks for the update.\n", "format": "whatsapp"})
    assert r2["possible_extras_count"] == 0
    assert next(f for f in r2["findings"] if f["fingerprint"] == fp)["status"] == "dismissed"


def test_store_refuses_a_thread_with_a_hole_in_it(tmp_path):
    st = ThreadStore(tmp_path, ["Dana Okafor"])
    st.append([Message(id="", sender="Dana Okafor", text="hello there", ts="2026-01-01T10:00:00")])
    with open(st.path, "a", encoding="utf-8") as fh:
        fh.write("not json\n")
    with pytest.raises(StoreCorrupt):
        st.load()
    with pytest.raises(StoreCorrupt):
        st.append([Message(id="", sender="Dana Okafor", text="again", ts="2026-01-02T10:00:00")])


# ---------------------------------------------------------------- M4: --client refuses, never guesses
def test_client_matching_several_senders_is_refused_with_the_list():
    with pytest.raises(ClientError) as ei:
        run([("Dana Okafor", "Please add a shop."), ("Dana Smith", "ok")], client=("Dana",))
    assert "Dana Okafor" in str(ei.value) and "Dana Smith" in str(ei.value)


def test_client_that_is_the_freelancer_named_in_the_sow_is_refused():
    with pytest.raises(ClientError, match="named as the freelancer"):
        run([("Dana Okafor", "Please add a shop."), ("Alex Rivera", "ok")], client=("Alex Rivera",))
    with pytest.raises(ClientError, match="named as the freelancer"):
        run([("Dana Okafor", "Please add a shop."), ("Alex Rivera", "ok")], client=("Alex",))
    res = run([("Dana Okafor", "Can you add a blog?"), ("Alex Rivera", "Sure, on the house.")], client=("Dana Okafor",))
    assert res.findings[0].status == "included_by_you"                   # the freelancer's offers still apply


# ---------------------------------------------------------------- minor items
def test_different_deliverables_do_not_merge():
    def req(text, mid):
        from scopeguard.extract import is_revision
        return Request(id="Q", message_id=mid, sender="Dana", ts="2026-04-01T10:00:00", quote=text, is_revision=is_revision(text))
    order = {"M1": 0, "M2": 1}
    assert not _same_intent(req("Can you add a second form for job applications?", "M1"), req("Please add a contact form.", "M2"), order)
    assert not _same_intent(req("Could you add alt text to the gallery images?", "M1"), req("Do the images load fast?", "M2"), order)
    assert _same_intent(req("Can we add an online shop?", "M1"), req("Please add the online shop.", "M2"), order)


def test_store_locator_is_not_e_commerce():
    [f] = run(["Can you add a store locator to the Contact page?"]).findings
    assert f.rule != "explicit_exclusion" and f.status != "extra"


@pytest.mark.parametrize("text", ["Please make the heading sharper.", "Make the buttons rounder.", "Could you make the logo bolder?",
                                  "Tweak the spacing between the sections."])
def test_more_revision_words(text):
    [f] = run([text]).findings
    assert f.rule == "revision_allowance"


def test_bill_refuses_a_ledger_outside_the_output_folder(tmp_path):
    out = tmp_path / "o"
    chat = tmp_path / "c.txt"
    chat.write_text(ANDROID_DMY, encoding="utf-8")
    main(["analyze", "--sow", str(ROOT / "examples" / "sow.md"), "--messages", str(chat), "--client", "Dana Okafor",
          "--llm", "none", "--out", str(out)])
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    data["meta"]["ledger"] = str(tmp_path / "elsewhere" / "ledger.json")
    (out / "findings.json").write_text(json.dumps(data), encoding="utf-8")
    fp = next(f for f in data["findings"] if f["status"] == "extra")["fingerprint"]
    with pytest.raises(BillError, match="outside the output folder"):
        bill(out, [fp], RateCard())
    assert not (tmp_path / "elsewhere").exists()


def test_huge_quotes_are_cut_in_drafts():
    res = run(["Please add an online shop " + "with lots of detail " * 80 + "."])
    f = res.findings[0]
    d = draft(f, RateCard(), "friendly")
    asked = d.split("You asked:")[1].split("\n")[0]
    assert len(asked) < 620 and "…" in asked


def test_redact_help_text_matches_what_redaction_does(capsys):
    with pytest.raises(SystemExit):
        main(["analyze", "--help"])
    text = capsys.readouterr().out
    assert "IBAN" in text and "card numbers" in text and "[at]" in text.replace("\n", " ") or "name [at] host" in text.replace("\n", " ")
    assert "none" in text and "rules only" in text
