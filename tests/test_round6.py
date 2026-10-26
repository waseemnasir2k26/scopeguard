"""v1.1.4: (1) two repeats of the same ask in ONE export are billed once: the later one is held as
"repeat of REQ-xx in this export"; `bill` refuses a batch whose items repeat each other. (2) a billed item the
client later withdrew: the report says a draft already exists, the summary has a CRITICAL line, and the draft is
renamed WITHDRAWN- (never deleted). (3) more withdrawal phrasings are recognised."""
from pathlib import Path

import pytest

from scopeguard.acts import classify_act
from scopeguard.bill import BillError, bill
from scopeguard.ledger import Ledger
from scopeguard.llm import NoLLM
from scopeguard.models import Message
from scopeguard.outputs import write_all
from scopeguard.pipeline import Options, analyze
from scopeguard.rates import RateCard
from scopeguard.sow import load_text, parse_sow

SOW_PATH = Path(__file__).parent.parent / "examples" / "sow.md"
BLOG = "Could we add a blog to the site?"
DAY1 = "2026-10-05T09:00:00"


def _run(msgs, ledger=None):
    """msgs: list of (text, iso_ts). Everything from one client, "Dana"."""
    sow_text = load_text(SOW_PATH)
    messages = [Message(id="", sender="Dana", text=t, ts=ts) for t, ts in msgs]
    return analyze(parse_sow(sow_text), messages=messages, sow_text=sow_text,
                   opts=Options(llm=NoLLM(), client=["Dana"], ledger=ledger))


# ---------------------------------------------------------------- 1. double bill in one run
def test_identical_request_ten_days_apart_is_billable_once(tmp_path):
    # identical wording more than 7 days apart is not consolidated, so both reach the repeat check
    res = _run([(BLOG, DAY1), (BLOG, "2026-10-16T09:00:00")])
    assert [f.status for f in res.findings] == ["extra", "call"]
    first, second = res.findings
    assert second.repeat_of == f'repeat of {first.id} in this export: "{BLOG}"'
    assert second.reason.startswith(f"looks like a repeat of {first.id} in this export")
    assert "--repeat-ok" in " ".join(second.notes)


def test_paraphrase_a_week_later_is_held_as_a_repeat_in_the_same_export():
    res = _run([(BLOG, DAY1), ("Could you put a blog on the site?", "2026-10-13T09:00:00")])
    assert [f.status for f in res.findings] == ["extra", "call"]
    assert res.findings[1].repeat_of.startswith(f"repeat of {res.findings[0].id} in this export")


def test_unrelated_second_ask_in_the_same_export_is_still_a_possible_extra():
    res = _run([(BLOG, DAY1), ("Can we add an online shop so people can buy our prints?", "2026-10-13T09:00:00")])
    assert [f.status for f in res.findings] == ["extra", "extra"]


def test_bill_refuses_a_batch_whose_items_repeat_each_other(tmp_path):
    out = tmp_path / "out"
    ledger = Ledger(out / "ledger.json")
    res = _run([(BLOG, DAY1), (BLOG, "2026-10-16T09:00:00")], ledger)
    write_all(res, out, RateCard())
    fps = [f.fingerprint for f in res.findings]
    with pytest.raises(BillError) as e:
        bill(out, fps, RateCard(), repeat_ok=True)
    assert fps[0] in str(e.value) and fps[1] in str(e.value)
    assert not (out / "change-orders").exists()          # nothing drafted, nothing recorded
    assert ledger.decisions() == {}


def test_bill_drafts_the_first_of_a_repeat_pair_on_its_own(tmp_path):
    out = tmp_path / "out"
    ledger = Ledger(out / "ledger.json")
    res = _run([(BLOG, DAY1), (BLOG, "2026-10-16T09:00:00")], ledger)
    write_all(res, out, RateCard())
    [written] = bill(out, [res.findings[0].fingerprint], RateCard())
    assert written.exists()


# ---------------------------------------------------------------- 2. billed, then withdrawn
def _billed_then_withdrawn(tmp_path):
    out = tmp_path / "out"
    ledger = Ledger(out / "ledger.json")
    res1 = _run([(BLOG, DAY1)], ledger)
    write_all(res1, out, RateCard())
    fp = res1.findings[0].fingerprint
    [draft] = bill(out, [fp], RateCard())
    res2 = _run([(BLOG, DAY1), ("No blog needed after all.", "2026-10-07T09:00:00")], ledger)
    return out, ledger, fp, draft, res2


def test_billed_then_withdrawn_says_a_draft_exists_not_never_billed(tmp_path):
    out, ledger, fp, draft, res = _billed_then_withdrawn(tmp_path)
    [f] = [x for x in res.findings if x.fingerprint == fp]
    assert f.status == "withdrawn"
    drafted = ledger.decisions()[fp]["at"][:10]
    assert f.reason == f"Withdrawn after a change order was drafted on {drafted}: check before sending change-orders/{draft.name}"
    assert "Never billed" not in f.reason


def test_billed_then_withdrawn_is_critical_and_the_draft_is_renamed_not_deleted(tmp_path):
    out, ledger, fp, draft, res = _billed_then_withdrawn(tmp_path)
    write_all(res, out, RateCard())
    renamed = out / "change-orders" / ("WITHDRAWN-" + draft.name)
    assert renamed.exists() and not draft.exists()
    [f] = [x for x in res.findings if x.fingerprint == fp]
    assert f.draft_file == f"change-orders/WITHDRAWN-{draft.name}"
    assert ledger.decisions()[fp]["draft"] == f.draft_file               # the ledger follows the new name
    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert "**CRITICAL**" in summary and "check before sending" in summary
    report = (out / "report.html").read_text(encoding="utf-8")
    assert "CRITICAL" in report and "Withdrawn after a change order was drafted on" in report
    assert "Never billed" not in report


def test_a_second_write_does_not_rename_twice(tmp_path):
    out, ledger, fp, draft, res = _billed_then_withdrawn(tmp_path)
    write_all(res, out, RateCard())
    res_again = _run([(BLOG, DAY1), ("No blog needed after all.", "2026-10-07T09:00:00")], ledger)
    write_all(res_again, out, RateCard())
    names = sorted(p.name for p in (out / "change-orders").iterdir())
    assert names == ["WITHDRAWN-" + draft.name]


def test_withdrawn_item_never_billed_keeps_never_billed_wording(tmp_path):
    res = _run([(BLOG, DAY1), ("No blog needed after all.", "2026-10-07T09:00:00")], Ledger(tmp_path / "l.json"))
    [f] = res.findings
    assert f.status == "withdrawn"
    assert f.reason.endswith("Never billed.")
    assert f.billed_on == ""


# ---------------------------------------------------------------- 3. withdrawal phrasings
PHRASINGS = [
    "We have decided against the blog.",              # decided against
    "No blog needed after all.",                      # no X needed after all
    "Blog not needed after all.",                     # not needed after all
    "Please leave the blog out.",                     # leave X out
    "Let's leave the blog out.",                      # leave X out
    "We'll pass on the blog.",                        # we'll pass on
]


@pytest.mark.parametrize("text", PHRASINGS)
def test_each_withdrawal_phrasing_is_a_retraction(text):
    assert classify_act(text) == "retracted"


@pytest.mark.parametrize("text", PHRASINGS)
def test_each_withdrawal_phrasing_withdraws_the_earlier_item(text):
    res = _run([(BLOG, DAY1), (text, "2026-10-07T09:00:00")])
    assert [f.status for f in res.findings] == ["withdrawn"], text


def test_a_plain_request_with_leave_or_pass_words_is_still_a_request():
    assert classify_act("Please add a blog to the site and leave the footer alone.") == "request"
    assert classify_act("Could we add a blog to the site?") == "request"
