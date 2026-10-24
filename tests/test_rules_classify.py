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


def test_revision_allowance_then_limit_decided_before_the_llm():
    reqs = [rq(i, f"M{i}", "Can you change the heading colour?", f"2026-03-0{i}T10:00:00") for i in (1, 2, 3)]
    assign_revision_rounds(reqs)
    llm = FakeLLM(fn=lambda s, u: json.dumps({"class": "OUT-OF-SCOPE", "clause_id": "C1", "confidence": 1.0,
                                              "reason": "wrong", "estimated_hours": 1.5, "title": "heading colour"}))
    fs = classify_all(reqs, SOW, llm)
    assert [f.cls for f in fs] == [IN_SCOPE, IN_SCOPE, OUT_OF_SCOPE]
    assert [f.rule for f in fs] == ["revision_allowance", "revision_allowance", "revision_limit"]
    assert fs[0].clause_id == "C3" and fs[2].clause_id == "C3"
    assert "round 3" in fs[2].reason.lower() and "2" in fs[2].reason
    assert llm.prompts == []              # revisions are decided without the model; overages use the rate-card default
    assert fs[2].estimate["llm_hours"] is None


# ---------------------------------------------------------------- exclusions
def test_explicit_exclusion_beats_llm_in_scope_verdict():
    llm = FakeLLM(replies=[json.dumps({"class": "IN-SCOPE", "clause_id": "C1", "confidence": 0.95,
                                       "reason": "pages", "estimated_hours": 8, "title": "shop"})])
    r = rq(1, "M1", "Can we add an online shop to sell prints?")
    [f] = classify_all([r], SOW, llm)
    assert f.cls == OUT_OF_SCOPE and f.rule == "explicit_exclusion"
    assert f.clause_id == "C4" and f.confidence >= 0.9
    assert f.estimate["llm_hours"] == 8 and f.title == "shop"   # the model only supplies hours and a title


def test_revision_requests_are_not_blocked_by_exclusions():
    sow = parse_sow("## Deliverables\n- A five-page website.\n## Revisions\n- Two rounds of revisions.\n"
                    "## Exclusions\n- Logo or brand design is not included.\n")
    r = rq(1, "M1", "Can you make the logo bigger in the header?")
    assert r.is_revision
    assign_revision_rounds([r])
    [f] = classify_all([r], sow, MockLLM())
    assert f.rule == "revision_allowance" and f.cls == IN_SCOPE
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


def test_validate_out_of_scope_without_clause_is_unclear():
    v = validate({"class": "OUT-OF-SCOPE", "clause_id": None, "confidence": 0.8, "estimated_hours": 3}, SOW, 0.5)
    assert v["cls"] == UNCLEAR and v["clause"] is None and v["hours"] == 3.0
    assert any("without a valid clause" in n for n in v["notes"])
    ok = validate({"class": "OUT-OF-SCOPE", "clause_id": "C4", "confidence": 0.8, "estimated_hours": 3}, SOW, 0.5)
    assert ok["cls"] == OUT_OF_SCOPE and ok["clause"].id == "C4"


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
    good = json.dumps({"class": "OUT-OF-SCOPE", "clause_id": "C1", "confidence": 0.8, "reason": "r",
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


# ---------------------------------------------------------------- consolidation / phrases / headline / eval
def test_split_request_is_consolidated_into_one_item():
    from scopeguard.extract import consolidate
    msg = Message(id="M1", sender="Dana", text="Could we also have a blog? I'd like you to set it up and write the first three posts.",
                  ts="2026-03-01T10:00:00")
    reqs = heuristic_requests([msg])
    assert len(reqs) == 2
    [one] = consolidate(reqs, [msg])
    assert one.quote == "Could we also have a blog?" and len(one.extra_quotes) == 1
    assert "write the first three posts" in one.text()


def test_duplicate_across_messages_within_window_but_not_beyond():
    from scopeguard.extract import consolidate
    msgs = [Message(id="M1", sender="Dana", text="Can we add an online shop for prints?", ts="2026-03-01T10:00:00"),
            Message(id="M2", sender="Dana", text="Any news on the online shop idea? Please add the shop soon.", ts="2026-03-03T10:00:00"),
            Message(id="M3", sender="Dana", text="Please add the online shop now.", ts="2026-04-20T10:00:00")]
    items = consolidate(heuristic_requests(msgs), msgs)
    assert len(items) == 2 and len(items[0].extra_quotes) >= 1       # M3 is 7+ days later: separate item


def test_different_requests_and_revisions_are_not_merged():
    from scopeguard.extract import consolidate
    msgs = [Message(id="M1", sender="Dana", text="Can you make the logo bigger? Also please make the footer colour match the logo.",
                    ts="2026-03-01T10:00:00")]
    assert len(consolidate(heuristic_requests(msgs), msgs)) == 2


def test_exclusion_needs_a_phrase_not_one_word():
    sow = parse_sow("## Exclusions\n- Logo or brand design is not included.\n- Advanced analytics dashboards are not included.\n"
                    "- Paid advertising is not included.\n".replace("\\n", chr(10)))
    assert match_exclusion("Please install Google Analytics", sow) is None            # one shared word
    assert match_exclusion("We need a new logo designed", sow) is not None
    assert match_exclusion("Can you run paid Google Ads", sow) is not None
    assert match_exclusion("Can you add an advanced chart to the page", sow) is None   # one word of a 3-word phrase


def test_headline_counts_each_consolidated_item_once_and_gives_a_range():
    from scopeguard.changeorder import estimate, headline
    from scopeguard.models import Finding
    from scopeguard.rates import RateCard
    card = RateCard(hourly_rate=100)
    r = rq(1, "M1", "Could we also have a blog?", rev=False)
    r.extra_quotes = ["I'd like you to set it up and write the first three posts."]
    f = Finding(id="REQ-01", request=r, cls=OUT_OF_SCOPE, confidence=0.9, clause_id="C5")
    f.estimate = estimate(r.text(), 4, card)
    nocite = Finding(id="REQ-02", request=rq(2, "M2", "Add a podcast", rev=False), cls=OUT_OF_SCOPE, confidence=0.9)
    nocite.estimate = estimate("x", 10, card)
    h = headline([f, nocite], "USD")
    assert h.startswith("Estimated extra work (review before sending): $300.00 to $600.00 across 1 items (3-6 h")


def test_eval_rules_only_gets_every_revision_case_right(examples):
    from scopeguard.evaluate import load_labels, run_eval
    from scopeguard.llm import NoLLM
    sow = parse_sow((examples / "sow.md").read_text(encoding="utf-8"))
    res = run_eval(sow, load_labels(examples / "eval" / "labels.json"), NoLLM())
    assert res["revision_cases"]["all_correct"] and res["revision_cases"]["n"] >= 12
    assert res["per_class"][OUT_OF_SCOPE]["precision"] == 1.0 and res["per_class"][IN_SCOPE]["precision"] == 1.0


def test_eval_mock_accuracy_floor_and_cli(examples, capsys):
    from scopeguard.cli import main
    rc = main(["eval", "--sow", str(examples / "sow.md"), "--labels", str(examples / "eval" / "labels.json"), "--llm", "mock"])
    out = capsys.readouterr().out
    assert rc == 0 and "revision cases: 14/14 correct" in out
    acc = float(out.split("overall accuracy:")[1].split("%")[0])
    assert acc >= 85.0


# ---------------------------------------------------------------- support check and page counter
PAGES_SOW = parse_sow("""## Deliverables
- A five-page responsive website: Home, About, Services, Portfolio and Contact.
- A contact form.
## Revisions
- Two rounds of revisions are included.
## Exclusions
- A blog, and the writing of blog posts or any other copywriting, are not included.
- Hosting is not included.
""")


def _llm_says(cls, clause, conf=0.95, hours=4):
    return FakeLLM(fn=lambda s, u: json.dumps({"class": cls, "clause_id": clause, "confidence": conf,
                                               "reason": "r", "estimated_hours": hours, "title": "t"}))


def test_out_citing_an_exclusion_without_textual_support_is_unclear():
    # the Meet-the-Team mistake: model cites an exclusion (C5, hosting) for a request that never mentions it
    r = rq(1, "M1", "Could you add a team photo carousel on the homepage?", rev=False)
    [f] = classify_all([r], PAGES_SOW, _llm_says("OUT-OF-SCOPE", "C5"))
    assert f.cls == UNCLEAR and f.clause_id is None
    assert "cited exclusion C5 but the request doesn't mention it" in f.reason
    assert any("downgraded" in n for n in f.notes)


def test_out_citing_an_exclusion_with_support_is_kept():
    r = rq(1, "M1", "Can you post our weekly update?", rev=False)   # shares 'post' with the blog exclusion, no phrase match
    [f] = classify_all([r], PAGES_SOW, _llm_says("OUT-OF-SCOPE", "C4"))
    assert f.cls == OUT_OF_SCOPE and f.clause_id == "C4" and f.rule is None


def test_page_counter_named_listed_page_is_in_scope():
    for text in ['Could you also add a "Meet the Team" section to the About page?',
                 "Please add a short FAQ section to the Home page.", "Can we add a Services page?"]:
        [f] = classify_all([rq(1, "M1", text, rev=False)], PAGES_SOW, _llm_says("OUT-OF-SCOPE", "C5"))
        assert f.cls == IN_SCOPE and f.rule == "page_listed" and f.clause_id == "C1", text


def test_page_counter_pushing_past_the_count_is_out_of_scope():
    for text in ["Please add a sixth page for our Careers section.", "Can you add a Pricing page?",
                 "Could you add a testimonials page as well?"]:
        [f] = classify_all([rq(1, "M1", text, rev=False)], PAGES_SOW, _llm_says("IN-SCOPE", "C1"))
        assert f.cls == OUT_OF_SCOPE and f.rule == "page_count" and f.clause_id == "C1", text


def test_page_counter_counts_free_slots_across_requests():
    sow = parse_sow("## Deliverables\n- A four-page website: Home, About and Contact.\n- A contact form.\n")
    reqs = [rq(1, "M1", "Please add a Pricing page.", rev=False), rq(2, "M2", "Please add a Careers page too.", rev=False)]
    fs = classify_all(reqs, sow, _llm_says("UNCLEAR", None))
    assert [f.cls for f in fs] == [IN_SCOPE, OUT_OF_SCOPE]          # one free slot, then past the count


def test_page_rule_never_overrides_revisions_or_exclusions():
    r = rq(1, "M1", "Could you change the heading on the Home page?", "2026-03-01T10:00:00")
    assert r.is_revision
    assign_revision_rounds([r])
    [f] = classify_all([r], PAGES_SOW, _llm_says("OUT-OF-SCOPE", "C5"))
    assert f.rule == "revision_allowance"
    [g] = classify_all([rq(2, "M2", "Please add a blog page", rev=False)], PAGES_SOW, _llm_says("IN-SCOPE", "C1"))
    assert g.rule == "explicit_exclusion"


def test_revision_overage_uses_rate_card_default_not_model_hours():
    from scopeguard.pipeline import Options, analyze
    from scopeguard.rates import RateCard
    texts = ["Can you change the heading font please?", "Please make the footer colour darker.", "Move the banner above the menu."]
    msgs = [Message(id="", sender="Dana Okafor", text=t, ts=f"2026-03-0{d}T10:00:00") for d, t in zip((1, 2, 3), texts)]
    res = analyze(PAGES_SOW, messages=msgs, opts=Options(llm=_llm_says("OUT-OF-SCOPE", "C1", hours=9),
                                                         rates=RateCard(hourly_rate=100, default_hours=1.5)))
    third = res.findings[-1] if len(res.findings) == 3 else None
    assert third is not None and third.rule == "revision_limit"
    assert third.estimate["hours"] == 1.5 and "default hours" in third.estimate["basis"]
