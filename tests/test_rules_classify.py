import json

from conftest import FakeLLM
from scopeguard.classify import classify_all, validate
from scopeguard.extract import find_evidence, heuristic_requests, is_revision, llm_requests
from scopeguard.llm import LLMError, MockLLM, extract_json
from scopeguard.models import IN_SCOPE, OUT_OF_SCOPE, UNCLEAR, Message, Request
from scopeguard.rules import assign_revision_rounds, match_exclusion
from scopeguard.sow import parse_sow

SOW = parse_sow("""## Deliverables
- A five-page website with a contact form.
- Google Analytics installation.
## Revisions
- Two rounds of revisions are included.
## Exclusions
- E-commerce and online shop functionality are not included.
- Blog setup and copywriting are not included.
- Advanced analytics dashboards are not included.
""")


def rq(i, mid, text, ts=None, rev=None):
    return Request(id=f"Q{i}", message_id=mid, sender="Sam", ts=ts, quote=text,
                   is_revision=is_revision(text) if rev is None else rev)


# ---------------------------------------------------------------- revision counter
def test_revision_rounds_group_by_message_and_gap():
    reqs = [rq(1, "M1", "Can you change the banner colour?", "2026-03-01T10:00:00"),
            rq(2, "M1", "Please make the logo bigger.", "2026-03-01T10:00:00"),
            rq(3, "M2", "Also swap the font please.", "2026-03-01T10:10:00"),      # 10 min later: same round
            rq(4, "M3", "Change the footer colour please.", "2026-03-02T09:00:00"),  # next day: round 2
            rq(5, "M4", "Add a new careers page.", "2026-03-02T09:02:00"),          # not a revision
            rq(6, "M5", "Please tweak the heading size.", "2026-03-05T09:00:00")]   # round 3
    n = assign_revision_rounds(reqs)
    assert n == 3
    assert [r.revision_round for r in reqs] == [1, 1, 1, 2, 0, 3]


def test_revision_rounds_without_timestamps_use_messages():
    reqs = [rq(1, "M1", "Change the colour please."), rq(2, "M2", "Change the font please.")]
    assert assign_revision_rounds(reqs) == 2


def test_revision_limit_rule_overrides_llm():
    reqs = [rq(i, f"M{i}", "Can you change the heading colour?", f"2026-03-0{i}T10:00:00") for i in (1, 2, 3)]
    assign_revision_rounds(reqs)
    fs = classify_all(reqs, SOW, MockLLM())
    third = fs[2]
    assert third.cls == OUT_OF_SCOPE and third.rule == "revision_limit"
    assert third.clause_id == "C3"
    assert "round 3" in third.reason and "2" in third.reason
    assert fs[0].rule is None and fs[1].rule is None


# ---------------------------------------------------------------- exclusions
def test_explicit_exclusion_beats_llm_in_scope_verdict():
    llm = FakeLLM(replies=[json.dumps({"class": "IN-SCOPE", "clause_id": "C1", "confidence": 0.95,
                                       "reason": "pages", "estimated_hours": None, "title": "shop"})])
    r = rq(1, "M1", "Can we add an online shop to sell prints?")
    [f] = classify_all([r], SOW, llm)
    assert f.cls == OUT_OF_SCOPE and f.rule == "explicit_exclusion"
    assert f.clause_id == "C4" and f.confidence >= 0.9
    assert any("overrode" in n for n in f.notes)


def test_revision_requests_are_not_blocked_by_exclusions():
    sow = parse_sow("## Deliverables\n- A five-page website.\n## Revisions\n- Two rounds of revisions.\n"
                    "## Exclusions\n- Logo or brand design is not included.\n")
    r = rq(1, "M1", "Can you make the logo bigger in the header?")
    assert r.is_revision
    assign_revision_rounds([r])
    [f] = classify_all([r], sow, MockLLM())
    assert f.rule is None
    r2 = rq(2, "M2", "Could you design a new logo for us?")
    assert not r2.is_revision
    [f2] = classify_all([r2], sow, MockLLM())
    assert f2.rule == "explicit_exclusion"


def test_exclusion_not_triggered_when_a_deliverable_covers_it_better():
    # 'analytics' appears in both the deliverable and the advanced-analytics exclusion
    assert match_exclusion("Please install Google Analytics", SOW) is None


def test_exclusion_matching_needs_real_overlap():
    assert match_exclusion("Can you make the logo bigger?", SOW) is None
    ex = match_exclusion("Could we have a blog with three posts?", SOW)
    assert ex is not None and ex.id == "C5"


# ---------------------------------------------------------------- validation / evidence
def test_validate_rejects_unknown_clause_and_downgrades_in_scope():
    v = validate({"class": "IN-SCOPE", "clause_id": "C99", "confidence": 0.9, "reason": "x"}, SOW, 0.5)
    assert v["cls"] == UNCLEAR and v["clause"] is None and v["confidence"] <= 0.4
    assert any("unknown clause" in n for n in v["notes"])


def test_validate_in_scope_without_citation_is_unclear():
    v = validate({"class": "IN-SCOPE", "clause_id": None, "confidence": 0.9}, SOW, 0.5)
    assert v["cls"] == UNCLEAR


def test_validate_out_of_scope_keeps_null_clause():
    v = validate({"class": "OUT-OF-SCOPE", "clause_id": None, "confidence": 0.8, "estimated_hours": 3}, SOW, 0.5)
    assert v["cls"] == OUT_OF_SCOPE and v["clause"] is None and v["hours"] == 3.0


def test_validate_low_confidence_and_bad_values():
    assert validate({"class": "OUT-OF-SCOPE", "confidence": 0.2}, SOW, 0.5)["cls"] == UNCLEAR
    assert validate({"class": "banana"}, SOW, 0.5)["cls"] == UNCLEAR
    v = validate({"class": "out_of_scope", "confidence": "high", "estimated_hours": -4}, SOW, 0.0)
    assert v["confidence"] == 0.0 and v["hours"] is None
    assert validate({"class": "OUT-OF-SCOPE", "confidence": 7}, SOW, 0.0)["confidence"] == 1.0


def test_evidence_rule_drops_invented_quotes():
    msgs = [Message(id="M1", sender="Sam", text="Hi!  Could you ADD a careers page?\nThanks")]
    fake = FakeLLM(replies=[json.dumps({"requests": [
        {"message_id": "M1", "quote": "could you add a careers page?"},          # verbatim modulo case/whitespace
        {"message_id": "M1", "quote": "Please build me a mobile app"},            # invented
        {"message_id": "M9", "quote": "Hi! Could you add a careers page?"},       # wrong message id, still verbatim
        {"message_id": "M1", "quote": "page"}, {"message_id": "M1"}, "junk"]})])
    reqs, dropped = llm_requests(fake, msgs)
    assert [r.message_id for r in reqs] == ["M1", "M1"]
    assert dropped == 4
    assert find_evidence("Please build me a mobile app", msgs) is None
    assert find_evidence("could you add a careers page", msgs).id == "M1"


def test_evidence_rule_applies_through_the_pipeline():
    from scopeguard.pipeline import Options, analyze
    msgs = [Message(id="", sender="Sam Client", text="Can you add a members area with logins?")]
    fake = FakeLLM(fn=lambda s, u: json.dumps({"requests": [{"message_id": "M1", "quote": "Add a pony please"}]})
                   if "EXTRACT_JSON" in u else
                   json.dumps({"class": "OUT-OF-SCOPE", "clause_id": None, "confidence": 0.7, "reason": "r",
                               "estimated_hours": 3, "title": "t"}))
    res = analyze(SOW, messages=msgs, opts=Options(llm=fake, llm_extract=True))
    assert [f.request.quote for f in res.findings] == ["Can you add a members area with logins?"]
    assert res.meta["dropped_by_evidence_rule"] == 1
    assert any("Evidence rule" in w for w in res.warnings)


# ---------------------------------------------------------------- mock LLM + json handling
def test_mock_llm_classification_mix():
    reqs = [rq(1, "M1", "Can we add an online shop to sell prints?", rev=False),
            rq(2, "M2", "Please install Google Analytics for us.", rev=False),
            rq(3, "M3", "Could you make the site feel more premium?", rev=False)]
    fs = classify_all(reqs, SOW, MockLLM())
    assert [f.cls for f in fs] == [OUT_OF_SCOPE, IN_SCOPE, UNCLEAR]
    assert fs[1].clause_id == "C2"


def test_json_extraction_and_retry():
    assert extract_json('Sure!\n```json\n{"a": {"b": "}"}}\n``` bye') == {"a": {"b": "}"}}
    good = json.dumps({"class": "OUT-OF-SCOPE", "clause_id": None, "confidence": 0.8, "reason": "r",
                       "estimated_hours": 2, "title": "t"})
    llm = FakeLLM(replies=["I think it is out of scope", good])
    [f] = classify_all([rq(1, "M1", "Please add a podcast page for us.", rev=False)], SOW, llm)
    assert f.cls == OUT_OF_SCOPE and len(llm.prompts) == 2
    assert "not valid JSON" in llm.prompts[1]


def test_total_llm_failure_becomes_unclear_not_a_crash():
    llm = FakeLLM(replies=["nope", "still nope"])
    [f] = classify_all([rq(1, "M1", "Please add a podcast page for us.", rev=False)], SOW, llm)
    assert f.cls == UNCLEAR and f.confidence == 0.0 and "LLM call failed" in f.reason


def test_heuristic_extraction_skips_chatter():
    msgs = [Message(id="M1", sender="Sam", text="Thanks so much! Looks great.\nCould you also add a careers page?\nWhen will it be ready?")]
    reqs = heuristic_requests(msgs)
    assert [r.quote for r in reqs] == ["Could you also add a careers page?"]
    assert LLMError  # exported
