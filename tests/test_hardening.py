"""Critic round 1: the tool must refuse to bill for retracted, hypothetical, included or client-supplied work."""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from conftest import FakeLLM
from scopeguard.changeorder import draft, draftable, headline, md_escape, one_line
from scopeguard.classify import classify_all, classify_one, short_title
from scopeguard.extract import EvidenceIndex, classify_act, heuristic_requests
from scopeguard.llm import MockLLM
from scopeguard.models import IN_SCOPE, OUT_OF_SCOPE, UNCLEAR, Message, Request
from scopeguard.outputs import write_all
from scopeguard.pipeline import MAX_MESSAGES, Options, analyze
from scopeguard.rates import RateCard
from scopeguard.redact import redact
from scopeguard.server import make_server
from scopeguard.sow import load_text, parse_sow

pytest.importorskip("yaml")


@pytest.fixture(scope="module")
def sow(examples=None):
    from pathlib import Path
    return parse_sow(load_text(Path(__file__).parent.parent / "examples" / "sow.md"))


def always_out(cls="OUT-OF-SCOPE", clause="C12", conf=0.99):
    """A reckless model that bills everything with high confidence."""
    return FakeLLM(fn=lambda s, u: json.dumps({"class": cls, "clause_id": clause, "confidence": conf, "reason": "r",
                                               "estimated_hours": 3, "title": "t"}))


def run_chat(sow, lines, llm=None, tmp=None):
    msgs = [Message(id="", sender="Dana Okafor", text=t, ts=f"2026-03-0{1 + i // 3}T1{i % 3}:00:00") for i, t in enumerate(lines)]
    res = analyze(sow, messages=msgs, opts=Options(llm=llm or always_out(), client=["Dana"]))
    return res


# ---------------------------------------------------------------- B1: retractions and hypotheticals
RETRACTED = ["Actually please don't add an online shop, I changed my mind.",
             "No need for a booking calendar after all.",
             "Never mind about the newsletter, cancel that.",
             "Scrap that, we don't need a Spanish version."]
HYPOTHETICAL = ["Do you also do blog writing? Just curious, we don't need one.",
                "We are thinking about a blog maybe later.",
                "How much would a Spanish version cost?",
                "Wondering whether you could add a members area some day."]


@pytest.mark.parametrize("text", RETRACTED + HYPOTHETICAL)
def test_retractions_and_hypotheticals_are_never_requests_or_billed(sow, text, tmp_path):
    res = run_chat(sow, [text])                       # even a reckless model cannot bill these
    assert res.findings, "kept visible as UNCLEAR, not silently dropped"
    for f in res.findings:
        assert f.cls == UNCLEAR and f.status == "call" and f.rule == "not_a_request" and f.request.act != "request"
        assert not draftable(f) and f.estimate is None
    write_all(res, tmp_path / "o", RateCard())
    assert list((tmp_path / "o" / "change-orders").glob("*.md")) == []
    assert "No possible extra work" in headline(res.findings, "USD")


def test_act_detection_does_not_swallow_real_requests():
    for t in ["Could we also add a blog?", "Please add a booking calendar.", "Can you do a Spanish version too?",
              "We need an online shop. Can you add it?"]:
        assert classify_act(t) == "request", t


# ---------------------------------------------------------------- B1: client-supplied assets, page counter
def test_using_client_supplied_photos_is_included(sow):
    for text in ["Please use the photos I sent for the gallery.",
                 "I will send the logo files and photographs tomorrow, can you place them in the header?"]:
        [f] = run_chat(sow, [text]).findings
        assert f.cls == IN_SCOPE and f.rule == "client_supplied" and f.clause_id == "C9", text
        assert not draftable(f)


def test_creating_new_assets_is_not_client_supplied(sow):
    [f] = run_chat(sow, ["Can you shoot some new photos of the studio?"]).findings
    assert f.rule == "explicit_exclusion"            # photography is excluded, and nobody supplied it


def test_gallery_is_never_an_extra_page(sow):
    for text in ["Please add a gallery page with the photos I sent.", "Can you add a contact form page?",
                 "Please add the gallery section to the Portfolio page."]:
        [f] = run_chat(sow, [text], llm=always_out("UNCLEAR", None, 0.3)).findings
        assert f.rule != "page_count", text
    [g] = run_chat(sow, ["Please add a Pricing page."]).findings
    assert g.rule == "page_count" and g.cls == OUT_OF_SCOPE          # a genuinely new page still counts


def test_included_work_merged_with_an_extra_is_not_billed_together(sow, tmp_path):
    res = run_chat(sow, ["Could you add alt text to all the gallery images?",
                         "Please add an online shop for the gallery."],
                   llm=FakeLLM(fn=lambda s, u: json.dumps({"class": "IN-SCOPE", "clause_id": "C8", "confidence": 0.9,
                                                           "reason": "SEO alt text", "estimated_hours": None, "title": "alt"})))
    assert len(res.findings) == 2
    included = next(f for f in res.findings if "alt text" in f.request.quote)
    shop = next(f for f in res.findings if "shop" in f.request.quote)
    assert included.cls == IN_SCOPE and not included.request.extra_quotes and shop.cls == OUT_OF_SCOPE


# ---------------------------------------------------------------- numeric caps (Judge A)
def test_numeric_caps_parsed_from_the_sow(sow):
    assert {(c["unit"], c["n"], c["clause"]) for c in sow.caps} >= {("image", 24, "C2"), ("email", 1, "C3")}


@pytest.mark.parametrize("text,cls,rule,clause", [
    ("Can the gallery show 30 photos?", OUT_OF_SCOPE, "numeric_cap", "C2"),
    ("Could the contact form send enquiries to two email addresses?", OUT_OF_SCOPE, "numeric_cap", "C3"),
    ("Could the gallery show 24 photos?", IN_SCOPE, "numeric_cap_ok", "C2"),
    ("Can the contact form email one address?", IN_SCOPE, "numeric_cap_ok", "C3"),
])
def test_numbers_in_requests_are_compared_with_sow_caps(sow, text, cls, rule, clause):
    [f] = run_chat(sow, [text], llm=always_out("UNCLEAR", None, 0.2)).findings
    assert (f.cls, f.rule, f.clause_id) == (cls, rule, clause)


# ---------------------------------------------------------------- drafts
def test_extra_needs_out_clause_confidence_and_a_real_request(sow, tmp_path):
    from scopeguard.models import Finding
    from scopeguard.status import assign_status
    req = Request(id="Q1", message_id="M1", sender="Dana Okafor", ts=None, quote="Can you add a podcast page?")
    base = dict(id="REQ-01", request=req, cls=OUT_OF_SCOPE, confidence=0.9, clause_id="C1")
    f = Finding(**base)
    f.estimate = {"hours": 1}
    assert assign_status(f) == "extra" and draftable(f)
    for change in (dict(confidence=0.7), dict(clause_id=None), dict(cls=UNCLEAR)):
        g = Finding(**{**base, **change})
        g.estimate = {"hours": 1}
        assert assign_status(g) != "extra" and not draftable(g), change
    # end to end: a model that says OUT at 0.7 is Needs your call, not a possible extra
    res = run_chat(sow, ["Could you add a podcast player somewhere?"], llm=always_out("OUT-OF-SCOPE", "C12", 0.7))
    assert res.findings[0].status == "call" and not draftable(res.findings[0])


def test_draft_opens_with_a_soft_check_and_hedges_revision_counts(sow):
    msgs = [Message(id="", sender="Dana Okafor", text=t, ts=f"2026-03-0{d}T10:00:00")
            for d, t in zip((1, 2, 3), ["Can you change the heading font?", "Please make the footer colour darker.",
                                       "Move the banner above the menu please."])]
    res = analyze(sow, messages=msgs, opts=Options(llm=MockLLM(), client=["Dana"]))
    third = res.findings[-1]
    assert third.rule == "revision_limit" and len(third.round_log) == 3
    d = draft(third, RateCard(), "friendly", sow.revision_limit)
    assert "Just checking: is this something you'd like added? If so, here's what it would cost." in d
    assert "By my count this is revision round 3" in d and "Correct me if I've miscounted" in d
    assert "Round 1 (2026-03-01)" in d and "Round 3 (2026-03-03)" in d


def test_drafts_escape_markdown_and_strip_newlines_from_the_subject(sow):
    msgs = [Message(id="", sender="Dana Okafor", text="Can we add an online shop [click](http://evil.example) *now* <b>x</b>?")]
    res = analyze(sow, messages=msgs, opts=Options(llm=MockLLM(), client=["Dana"]))
    f = res.findings[0]
    f.title = "shop\nBcc: attacker@evil.example\r\n"
    d = draft(f, RateCard(), "friendly")
    assert "[click](http" not in d and "\\[click\\]" in d and "<b>" not in d and "\\*now\\*" in d
    subject = next(line for line in d.splitlines() if line.startswith("**Subject:**"))
    assert "Bcc" in subject and "\n" not in subject and d.count("Bcc: attacker") == 1
    assert md_escape("a|b#c") == "a\\|b\\#c" and one_line("a\nb\r\nc") == "a b c"


def test_titles_never_cut_a_quote_in_half():
    t = short_title('Could you also add a "Meet the Team" section to the About page? We have four people.')
    assert '"' not in t and len(t) <= 63 and t.endswith("...")


# ---------------------------------------------------------------- scale
def test_evidence_index_matches_a_naive_scan():
    import random
    random.seed(3)
    words = "can you add the logo bigger blog shop page gallery please make footer colour".split()
    msgs = [Message(id=f"M{i}", sender="c", text=" ".join(random.choice(words) for _ in range(random.randint(3, 12))))
            for i in range(300)]
    idx = EvidenceIndex(msgs)
    from scopeguard.textutil import normalize
    for _ in range(300):
        q = " ".join(random.choice(words) for _ in range(random.randint(2, 6)))
        naive = next((m for m in msgs if len(normalize(q)) >= 8 and normalize(q) in normalize(m.text)), None)
        got = idx.find(q)
        assert (got is None) == (naive is None)
        if got:
            assert normalize(q) in normalize(got.text)


def test_fifty_thousand_messages_run_in_seconds(sow):
    import random
    random.seed(1)
    reqs = ["Can you make the logo bigger in the header?", "Could we also have a blog?", "Please add a booking calendar.",
            "Can the gallery show 30 photos?", "Please install Google Analytics."]
    msgs = [Message(id="", sender="Dana Okafor" if i % 2 == 0 else "Alex Rivera",
                    text=(random.choice(reqs) + f" (ref {i})") if i % 2 == 0 and random.random() < 0.35 else "Thanks, sounds good.",
                    ts=f"2026-{1 + i // 5000 % 12:02d}-{1 + i % 27:02d}T{i % 24:02d}:{i % 60:02d}:00") for i in range(50000)]
    t0 = time.time()
    res = analyze(sow, messages=msgs, opts=Options(llm=MockLLM(), client=["Dana"]))
    assert time.time() - t0 < 10 and len(res.findings) > 1000


def test_input_size_cap_gives_a_clear_message(sow, monkeypatch):
    import scopeguard.pipeline as pl
    monkeypatch.setattr(pl, "MAX_MESSAGES", 10)
    msgs = [Message(id="", sender="Dana", text="Can you add a page?") for _ in range(11)]
    with pytest.raises(ValueError, match="limit is 10 per run"):
        analyze(sow, messages=msgs, opts=Options(llm=MockLLM()))
    assert MAX_MESSAGES == 200_000


# ---------------------------------------------------------------- redaction
def test_redaction_cards_ibans_obfuscated_emails_and_names():
    t = ("pay with 4111 1111 1111 1111 or IBAN GB82 WEST 1234 5698 7654 32, write dana [at] harbor [dot] example "
         "or dana(at)harbor.example. Order 4111 1111 1111 1112 stays.\nFrom: Dana Okafor <d@x.example>\nBest,\nDana Okafor")
    r = redact(t)
    assert "[CARD]" in r and "4111 1111 1111 1111" not in r and "4111 1111 1111 1112" in r
    assert "[IBAN]" in r and "WEST" not in r
    assert r.count("[EMAIL]") >= 3 and "harbor" not in r.replace("[EMAIL]", "")
    assert "Dana Okafor" not in r and "[NAME]" in r


# ---------------------------------------------------------------- server hardening
@pytest.fixture
def srv(sow):
    s = make_server("127.0.0.1", 0, sow, "", Options(llm=MockLLM(), client=["Dana"]), token="tok")
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def _get(srv, path, host=None, token="tok"):
    h = {"Authorization": "Bearer " + token}
    if host:
        h["Host"] = host
    req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}{path}", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_server_rejects_foreign_host_headers_and_has_a_read_timeout(srv):
    assert _get(srv, "/healthz") == 200
    assert _get(srv, "/healthz", host="localhost:1234") == 200
    assert _get(srv, "/healthz", host="evil.example") == 403            # DNS-rebinding style request
    assert _get(srv, "/healthz", host="127.0.0.1.evil.example") == 403
    assert srv.RequestHandlerClass.timeout == 30
