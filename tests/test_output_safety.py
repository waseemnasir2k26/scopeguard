import csv
import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conftest import FakeLLM
from scopeguard.changeorder import draft, estimate, money
from scopeguard.cli import main
from scopeguard.llm import MockLLM
from scopeguard.models import OUT_OF_SCOPE, Finding, Message, Request
from scopeguard.outputs import csv_safe, write_all
from scopeguard.pipeline import Options, analyze
from scopeguard.rates import RateCard, Task, load_rates
from scopeguard.redact import redact
from scopeguard.server import BindError, check_bind, make_server
from scopeguard.sow import parse_sow

SOW = parse_sow("## Deliverables\n- A five-page website.\n## Revisions\n- Two rounds of revisions.\n"
                "## Exclusions\n- E-commerce and online shop are not included.\n")


# ---------------------------------------------------------------- change-order maths
def test_estimate_llm_hours_rounded_up_to_quarter_hour():
    card = RateCard(hourly_rate=80, currency="EUR")
    e = estimate("add a podcast page", 1.1, card)
    assert e["hours"] == 1.25 and e["amount"] == 100.0 and e["is_estimate"] is True
    assert "LLM" in e["basis"]


def test_estimate_minimum_and_default_hours():
    card = RateCard(hourly_rate=100, minimum_hours=1.0, default_hours=3)
    assert estimate("tiny thing", 0.1, card)["hours"] == 1.0
    assert estimate("tiny thing", None, card)["hours"] == 3.0 and estimate("x", None, card)["amount"] == 300.0
    assert estimate("tiny thing", 9999, card)["hours"] == 3.0          # absurd LLM hours ignored
    assert estimate("tiny thing", True, card)["hours"] == 3.0          # bool is not a number


def test_estimate_rate_card_task_beats_llm_hours():
    card = RateCard(hourly_rate=60, tasks=[Task(match=["blog"], hours=6, label="Blog"),
                                           Task(match=["translation"], fixed=450, label="Translate")])
    assert estimate("Could we have a blog?", 1, card) == {
        "currency": "USD", "rate": 60.0, "is_estimate": True, "placeholder_rate": False,
        "hours": 6.0, "amount": 360.0, "basis": "rate card: Blog",
        "hours_low": 4.5, "hours_high": 9.0, "amount_low": 270.0, "amount_high": 540.0}
    fixed = estimate("a Spanish translation of the site", 1, card)
    assert fixed["amount"] == 450.0 and fixed["hours"] is None
    assert estimate("blog", 1, card, use_tasks=False)["hours"] == 1.0


def test_rates_yaml_validation(tmp_path):
    p = tmp_path / "rates.yaml"
    p.write_text("hourly_rate: 75\ncurrency: gbp\ntasks:\n  - match: [logo]\n    hours: 4\n")
    c = load_rates(p)
    assert c.hourly_rate == 75 and c.currency == "GBP" and c.tasks[0].hours == 4 and not c.placeholder
    p.write_text("hourly_rate: -5\n")
    with pytest.raises(ValueError):
        load_rates(p)
    p.write_text("tasks:\n  - match: [x]\n")
    with pytest.raises(ValueError):
        load_rates(p)
    assert load_rates(None).placeholder is True


def _finding(rule=None, quote="Can we add an online shop?"):
    req = Request(id="Q1", message_id="M1", sender="Sam Client", ts=None, quote=quote)
    f = Finding(id="REQ-01", request=req, cls=OUT_OF_SCOPE, confidence=0.9, clause_id="C3",
                clause_text="E-commerce is excluded", clause_ref="3.1", reason="excluded", title="Online shop",
                rule=rule)
    f.estimate = estimate(quote, 4, RateCard(hourly_rate=50))
    return f


def test_draft_content_and_tones():
    f = _finding()
    friendly, neutral, firm = (draft(f, RateCard(), t) for t in ("friendly", "neutral", "firm"))
    for d in (friendly, neutral, firm):
        assert d.startswith("> DRAFT. scopeguard never sends anything")
        assert 'Can we add an online shop?' in d and "4 h x $50.00/h = $200.00" in d
        assert "C3 (SOW 3.1)" in d and "Hi Sam," in d
    assert len({friendly, neutral, firm}) == 3
    assert "billable" in firm and "happy to help" in friendly
    f.estimate = estimate(f.request.quote, 4, RateCard(placeholder=True))
    assert "placeholder hourly rate" in draft(f, RateCard(placeholder=True))


def test_draft_revision_limit_wording_and_no_clause():
    f = _finding(rule="revision_limit")
    d = draft(f, RateCard(), "neutral", 2)
    assert "agreement includes 2" in d and "Correct me if I've miscounted" in d
    f.clause_id = None
    assert "couldn't find anything in the agreed scope" in draft(f, RateCard(), "friendly")


def test_money_format():
    assert money(1234.5, "USD") == "$1,234.50" and money(10, "CHF") == "CHF 10.00"


# ---------------------------------------------------------------- escaping, CSV, never-send
EVIL = '<script>alert(1)</script> "quoted" & =HYPERLINK("http://evil.example")'


def _run_evil(tmp_path):
    msgs = [Message(id="", sender="<b>Sam</b>", text=f"Can you add an online shop {EVIL} please?")]
    llm = FakeLLM(fn=lambda s, u: json.dumps({
        "class": "OUT-OF-SCOPE", "clause_id": "C1", "confidence": 0.9,
        "reason": "<img src=x onerror=alert(1)>", "estimated_hours": 2, "title": "=cmd|' /C calc'!A0"}))
    res = analyze(SOW, messages=msgs, opts=Options(llm=llm))
    write_all(res, tmp_path / "out", RateCard())
    return tmp_path / "out"


def test_html_report_is_escaped_and_self_contained(tmp_path):
    out = _run_evil(tmp_path)
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html and "<img src=x" not in html and "<b>Sam</b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert html.count("<script>") == 1                       # only our own filter script
    assert not re.search(r"(src|href)=[\"']?https?://", html)   # no external resources
    assert "Content-Security-Policy" in html and 'data-f=extra' in html and "Possible extras" in html


def test_csv_is_formula_injection_safe(tmp_path):
    out = _run_evil(tmp_path)
    rows = list(csv.reader((out / "scope-log.csv").open(encoding="utf-8-sig")))
    for row in rows[1:]:
        for cell in row:
            assert not cell.startswith(("=", "+", "-", "@", "\t", "\r")), cell
    assert csv_safe("=1+1") == "'=1+1" and csv_safe("@SUM(A1)") == "'@SUM(A1)" and csv_safe("-2") == "'-2"
    assert csv_safe("+1") == "'+1" and csv_safe("safe") == "safe" and csv_safe(None) == ""
    # the quote cell must keep its content, just neutralised where it would start with a formula char
    assert "HYPERLINK" in rows[1][7]


def test_never_sends_anything():
    src = "\n".join(p.read_text(encoding="utf-8") for p in Path(__file__).parent.parent.joinpath("scopeguard").glob("*.py"))
    for banned in ("smtplib", "sendmail", "imaplib", "poplib", "send_message", "twilio", "slack_sdk", "webhook"):
        assert banned not in src, banned
    # the only outbound HTTP is the LLM clients
    assert src.count("urllib.request.urlopen") <= 3 and not re.search(r"import requests|requests"+chr(92)+".(get|post)", src)


def test_analyze_never_writes_drafts_and_bill_drafts_only_what_you_name(tmp_path):
    from scopeguard.bill import BillError, bill
    out = _run_evil(tmp_path)
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert "report.html" in files and "scope-log.csv" in files and "summary.md" in files and "findings.json" in files
    assert not (out / "change-orders").exists()                       # analyze NEVER drafts
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    [item] = [f for f in data["findings"] if f["status"] == "extra"]
    with pytest.raises(BillError):
        bill(out, [], RateCard())                                      # no item named, nothing drafted
    [path] = bill(out, [item["fingerprint"]], RateCard())
    assert path.read_text(encoding="utf-8").startswith("> DRAFT")
    assert {p.name for p in tmp_path.iterdir()} == {"out"}


# ---------------------------------------------------------------- redaction
def test_redact_masks_emails_and_phones():
    t = "mail sam@client.example or call +1 555 0142 / (555) 010-0199, invoice 2026-03-12 total 12000"
    r = redact(t)
    assert "sam@client.example" not in r and "555 0142" not in r and "010-0199" not in r
    assert "[EMAIL]" in r and r.count("[PHONE]") == 2
    assert "2026-03-12" in r and "12000" in r


def test_redact_flag_keeps_pii_out_of_every_prompt():
    msgs = [Message(id="", sender="Sam Client",
                    text="Please add a careers page, and email accounts@client.example or call +1 555 0142 with questions.")]
    for flag in (True, False):
        llm = FakeLLM(fn=lambda s, u: json.dumps({"class": "UNCLEAR", "clause_id": None, "confidence": 0.5,
                                                  "reason": "", "estimated_hours": None, "title": "t"}))
        res = analyze(SOW, messages=[Message(id="", sender=m.sender, text=m.text) for m in msgs],
                      opts=Options(llm=llm, redact=flag))
        joined = "\n".join(llm.prompts)
        assert ("accounts@client.example" in joined) is (not flag)
        assert ("555 0142" in joined) is (not flag)
        assert "accounts@client.example" in res.findings[0].request.quote   # the report keeps the original


# ---------------------------------------------------------------- server
def test_bind_guard():
    check_bind("127.0.0.1", None)
    check_bind("localhost", None)
    check_bind("::1", None)
    for host in ("0.0.0.0", "192.168.1.20", "myhost.lan", "::"):
        with pytest.raises(BindError):
            check_bind(host, None)
        check_bind(host, "s3cret")


def test_cli_serve_refuses_public_bind_without_token(examples, capsys, monkeypatch):
    monkeypatch.delenv("SCOPEGUARD_TOKEN", raising=False)
    rc = main(["serve", "--sow", str(examples / "sow.md"), "--llm", "mock", "--host", "0.0.0.0"])
    assert rc == 2 and "require --token" in capsys.readouterr().err


@pytest.fixture
def server(examples):
    from scopeguard.sow import load_text
    text = load_text(examples / "sow.md")
    srv = make_server("127.0.0.1", 0, parse_sow(text), text, Options(llm=MockLLM(), client=["Dana"]), token="tok")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def srv_noclient(examples):
    from scopeguard.sow import load_text
    text = load_text(examples / "sow.md")
    srv = make_server("127.0.0.1", 0, parse_sow(text), text, Options(llm=MockLLM()), token="tok")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _post(url, body, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url + "/analyze", data=json.dumps(body).encode(), headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_server_requires_token_and_validates_body(server):
    assert _post(server, {"messages": "x"})[0] == 401
    assert _post(server, {"messages": "x"}, token="wrong")[0] == 401
    assert _post(server, {"nope": 1}, token="tok")[0] == 400
    assert _post(server, {"messages": ""}, token="tok")[0] == 400
    req = urllib.request.Request(server + "/analyze", data=b"x" * 1_000_001,
                                 headers={"Authorization": "Bearer tok"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=20)
        raise AssertionError("oversize body was accepted")
    except urllib.error.HTTPError as e:      # 413, or the server may drop the connection mid-upload
        assert e.code == 413
    except (OSError, urllib.error.URLError):
        pass
    with pytest.raises(urllib.error.HTTPError) as ei:                 # /healthz needs the token too
        urllib.request.urlopen(server + "/healthz")
    assert ei.value.code == 401
    hz = urllib.request.Request(server + "/healthz", headers={"Authorization": "Bearer tok"})
    with urllib.request.urlopen(hz) as r:
        assert json.loads(r.read()) == {"ok": True}                   # minimal: no model name, no SOW details
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(server + "/nope")
    assert ei.value.code == 404


def test_server_analyze_returns_possible_extras_never_drafts(server):
    status, out = _post(server, {"messages": "02/09/2026, 09:12 - Dana Okafor: Can we add an online shop to sell prints?\n"
                                             "02/09/2026, 09:13 - Alex: ok\n", "tone": "firm"}, token="tok")
    assert status == 200 and out["possible_extras_count"] == 1 and "drafts" not in out
    assert out["possible_extras"][0]["rule"] == "explicit_exclusion"
    assert out["headline"].startswith("Possible extra work to discuss: 1 items")
    assert "suggests" in out["note"]


def test_server_refuses_multi_sender_without_a_client(srv_noclient):
    status, out = _post(srv_noclient, {"messages": "02/09/2026, 09:12 - Dana Okafor: Please add a shop.\n"
                                                  "02/09/2026, 09:13 - Alex Rivera: ok\n"}, token="tok")
    assert status == 400 and "--client" in out["error"]


# ---------------------------------------------------------------- CLI end to end + watch
def test_cli_analyze_mock_end_to_end(examples, tmp_path, capsys):
    out = tmp_path / "o"
    rc = main(["analyze", "--sow", str(examples / "sow.md"), "--messages", str(examples / "whatsapp-chat.txt"),
               "--client", "Dana", "--rates", str(examples / "rates.yaml"), "--llm", "mock", "--out", str(out)])
    assert rc == 0
    data = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    classes = {f["class"] for f in data["findings"]}
    assert classes == {"IN-SCOPE", "OUT-OF-SCOPE", "UNCLEAR"}
    by_quote = {f["quote"]: f for f in data["findings"]}
    shop = next(v for k, v in by_quote.items() if "online shop" in k)
    assert shop["class"] == "OUT-OF-SCOPE" and shop["rule"] == "explicit_exclusion"
    font = next(v for k, v in by_quote.items() if "heading font" in k)
    assert font["rule"] == "revision_limit" and font["clause_id"]
    for f in data["findings"]:                                    # every quote is verbatim in the chat
        assert f["quote"] in (examples / "whatsapp-chat.txt").read_text(encoding="utf-8")


def test_cli_errors_cleanly_without_llm(examples, capsys, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
    rc = main(["analyze", "--sow", str(examples / "sow.md"), "--messages", str(examples / "whatsapp-chat.txt")])
    assert rc == 1 and "No local Ollama" in capsys.readouterr().err


def test_watch_scan_once_processes_each_file_once(examples, tmp_path):
    import os
    import time

    from scopeguard.watch import scan_once
    inbox = tmp_path / "in"
    inbox.mkdir()
    f = inbox / "chat.txt"
    f.write_text((examples / "whatsapp-chat.txt").read_text(encoding="utf-8"), encoding="utf-8")
    old = time.time() - 10
    os.utime(f, (old, old))
    from scopeguard.sow import load_text
    text = load_text(examples / "sow.md")
    logs, seen = [], {}
    opts = Options(llm=MockLLM(), client=["Dana"])
    assert scan_once(inbox, parse_sow(text), text, opts, tmp_path / "out", seen, logs.append) == 1
    assert scan_once(inbox, parse_sow(text), text, opts, tmp_path / "out", seen, logs.append) == 0
    assert any("possible extras" in line for line in logs)
    folder = tmp_path / "out" / "dana"
    first = json.loads((folder / "findings.json").read_text(encoding="utf-8"))
    shop = next(f for f in first["findings"] if "online shop" in f["quote"])
    assert shop["status"] == "extra" and (folder / "report.html").exists()
    # a LATER file withdraws the shop: the watcher re-analyses the whole accumulated thread, so it is withdrawn now
    g = inbox / "later.txt"
    g.write_text("30/09/2026, 10:00 - Dana Okafor: Never mind about the online shop, scratch that.\n"
                 "30/09/2026, 10:01 - Alex Rivera: ok\n", encoding="utf-8")
    os.utime(g, (old, old))
    assert scan_once(inbox, parse_sow(text), text, opts, tmp_path / "out", seen, logs.append) == 1
    second = json.loads((folder / "findings.json").read_text(encoding="utf-8"))
    assert next(f for f in second["findings"] if f["fingerprint"] == shop["fingerprint"])["status"] == "withdrawn"
    assert second["meta"]["thread_messages"] > first["meta"]["messages"]
    assert (folder / "messages.jsonl").exists()
