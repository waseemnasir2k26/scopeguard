"""What a client sentence DOES: ask for something, take it back, hedge, joke, or wonder out loud.

Only a plain request can ever be priced. Everything else is shown, never billed. Doubt is not a request.
Also: stateful withdrawal (a later message cancelling an earlier item) and freelancer free offers.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from .textutil import concepts

# ---------------------------------------------------------------- acts
_RETRACT = re.compile(
    r"\b(?:don'?t|do not|dont)\s+(?:add|need|want|include|build|make|do|bother|worry about)\b"
    r"|\bno need (?:for|of|to (?:add|include|build|make|do|have|worry))\b"
    r"|\bnever ?mind\b|\bchang(?:ed|ing) (?:my|our) minds?\b|\bcancel (?:that|this|it)\b"
    r"|\bscrap(?:ped)? (?:that|this|it)\b|\bscratch (?:that|this|it)\b|\bforget (?:it|that|about|the)\b"
    r"|\bignore (?:that|my|the (?:last|earlier|previous))\b|\bnot needed\b|\bwe don'?t need\b"
    r"|\bwon'?t (?:be )?(?:need|want)\w*\b|\bno longer (?:need|want)\b|\bnot after all\b|\bwithdraw\b"
    r"|\bleave (?:it|that) out\b|\boff the table\b|\blet'?s not\b|\bon second thought\b"
    r"|\bwe(?:'re| are) good without\b|\btoo expensive\b|\bhmm,? no\b|\bnot doing\b"
    r"|\bwe (?:won'?t|will not|are not going to|aren'?t going to) (?:be )?(?:do|doing|need|want|add|go|going|have|having|"
    r"build|building|proceed|proceeding|use|using)\b|\bno longer (?:required|needed|need|want|wanted|necessary|relevant)\b"
    r"|\bnot (?:required|necessary)\b|\bhold off\b|\bdisregard (?:that|this|it|my)\b|\bditch (?:that|this|it)\b"
    r"|\bcancel(?:le?d)? (?:that|this|it)\b|\bdrop (?:that|this)\b|\bskip (?:that|this|it)\b|\bignore (?:that|this|it)\b", re.I)
# "Cancel the shop", "Pls remove shop", "No shop please": verb + object. Only a retraction if it names something
# an EARLIER request was about, so "Please drop the files into the About page" stays a request.
SOFT_RETRACT = re.compile(
    r"^(?:(?:ok|okay|actually|sorry|hey|and|also|so|then|well|um)[,!.]?\s+)*(?:please\s+|pls\s+|plz\s+)?"
    r"(?:cancel|cancle|drop|skip|scratch|scrap|ignore|remove|disregard|ditch|nix|delete|pause|stop|hold off(?: on)?|"
    r"leave out|take out|forget|axe|bin)\b|^no\s+[\w -]{1,30}?\s+(?:please|pls|plz)\b", re.I)
# "yes please do add it after all" cancels a retraction cue in the same sentence
_AFFIRM = re.compile(r"\byes,? (?:please|do|let'?s)\b|\bplease do add\b|\bgo ahead\b|\blet'?s do it\b", re.I)
_SARCASM = re.compile(r"\boh,? sure\b|\bwhy not\b|\byeah,? right\b|\bas if\b", re.I)
_WHILE = re.compile(r"\bwhile (?:we|you)(?:'re| are) at it\b", re.I)
_COND = re.compile(
    r"\bif (?:the )?(?:budget|we have|there'?s|there is|we can afford|we get|money|time permits)\b"
    r"|\bshould the budget\b|\bbudget (?:allows|permits|stretches|lets)\b|\b(?:money|budget) left\b"
    r"|\bif (?:we|you|it) (?:can|could|have|has|get)\b[^.?!]{0,40}\b(?:money|budget|time|left|spare|afford)\b"
    r"|\bunless\b|\bphase (?:2|two|ii)\b|\bnext phase\b|\blater on\b|\beventually\b|\bdown the (?:road|line)\b"
    r"|\bno pressure\b|\bonly if\b|\bmaybe\b|\bperhaps\b|\bprobably\b|\bpossibly\b|\bi guess\b|\bhopefully\b"
    r"|\bif possible\b|\bif we ever\b|\bhoping\b", re.I)
_HYPO = re.compile(
    r"\bjust curious\b|\bout of curiosity\b|\bthinking (?:about|of)\b|\bwondering (?:if|whether|how)\b"
    r"|\bin the future\b|\bsome ?day\b|\bone day\b|\bat some point\b|\bfor later\b"
    r"|\bhow much (?:would|does|do|is|will)\b|\bwhat would (?:it|that|this) cost\b"
    r"|\bwhat'?s the (?:price|cost|rate)\b|\bwhat would (?:that|it|this) involve\b"
    r"|\b(?:do|does) (?:you|your team)(?: also| guys)? (?:do|offer|have|provide|handle|build|make|sell)\b"
    r"|\bare you (?:able|capable)\b|\bif (?:we|i) (?:ever|wanted|decide)\b|\bwe might\b|\bhypothetically\b"
    r"|\bis (?:a|an|the)\b[^.?!]{0,40}something you (?:could|can)\b", re.I)


def classify_act(text: str) -> str:
    """request | retracted | sarcastic | conditional | hypothetical. Doubt is not a request."""
    if _RETRACT.search(text) and not _AFFIRM.search(text):
        return "retracted"
    if _SARCASM.search(text) or (_WHILE.search(text) and (text.count(",") >= 2 or len(re.findall(r"\b(?:a|an) \w+", text)) >= 3)):
        return "sarcastic"
    if _COND.search(text):
        return "conditional"
    if _HYPO.search(text):
        return "hypothetical"
    return "request"


ACT_WHY = {
    "retracted": "retracted by the client",
    "sarcastic": "reads as sarcasm or a joke",
    "conditional": "conditional or hedged (budget, timing or 'maybe')",
    "hypothetical": "hypothetical, a price question or curiosity",
}


# ---------------------------------------------------------------- withdrawal across messages
_GENERIC = {"actually", "please", "thing", "things", "idea", "earlier", "before", "message", "request", "yesterday",
            "discussed", "mentioned", "want", "wants", "need", "needs", "think", "right", "sorry", "thanks", "about",
            "just", "like", "good", "well", "also", "more", "today", "back", "still", "though", "anyway", "really",
            "okay", "yeah", "expensive", "reflection", "second", "thought", "mind", "minds", "table", "forget",
            "scratch", "scrap", "cancel", "never", "mind", "ignore", "withdraw", "after", "that", "this", "with",
            "without", "have", "from", "your", "they", "them", "then", "than", "what", "when", "been", "were",
            "will", "would", "should", "could", "some", "such", "only", "very", "much", "make", "made", "does"}


def content_tokens(text: str) -> set:
    return {t for t in concepts(text) if (t.startswith("c:") or len(t) >= 4) and t not in _GENERIC}


_PRONOUN_RETRACT = re.compile(
    r"\b(?:scratch|scrap|cancel|forget|ignore) (?:it|that|this)\b|\bnever ?mind\b|\blet'?s not\b|\bon second thought\b"
    r"|\bhmm,? no\b|\btoo expensive\b|\boff the table\b|\bwe(?:'re| are) good without\b|\bchang(?:ed|ing) (?:my|our) minds?\b"
    r"|\bnot after all\b|\bleave (?:it|that) out\b", re.I)


def _near(a, b, order: dict, near_messages: int) -> bool:
    """Same message, or within a few messages and (when both have timestamps) a day."""
    if a.message_id == b.message_id:
        return True
    if abs(order[a.message_id] - order[b.message_id]) > near_messages:
        return False
    if a.ts and b.ts:
        try:
            from datetime import datetime
            return abs((datetime.fromisoformat(a.ts) - datetime.fromisoformat(b.ts)).total_seconds()) <= 86400
        except ValueError:
            return True
    return True


def apply_withdrawals(requests: list, near_messages: int = 3) -> list:
    """Stateful withdrawal. A retraction that references an earlier item (same message or any later message
    in the export, by shared content words) marks that item `withdrawn_by`. A retraction that names no
    specific thing ("scratch it", "never mind", "too expensive") refers to the item right before it.
    A retraction that found a target is consumed (recorded on the item); one that found nothing stays as an
    UNCLEAR row. A request about the same thing later in the same message as a retraction is marked
    `conflict` (ask the client), never billed. Returns the new list."""
    order = {}
    for r in requests:
        order.setdefault(r.message_id, len(order))
    consumed = set()
    ctoks = [content_tokens(r.text()) for r in requests]
    # verb + object retractions ("Cancel the shop", "Pls remove shop", "No shop please") count only when they
    # name something an earlier request was about
    seen_tokens: set = set()
    for pos, r in enumerate(requests):
        if r.act == "request" and SOFT_RETRACT.search(r.quote) and ctoks[pos] & seen_tokens:
            r.act, r.is_revision = "retracted", False
        elif r.act == "request":
            seen_tokens |= ctoks[pos]
    index: dict = {}                       # content word -> positions of live requests (so no O(n^2) scan)
    for pos, r in enumerate(requests):
        if r.act == "request":
            for t in ctoks[pos]:
                index.setdefault(t, []).append(pos)
    for i, r in enumerate(requests):
        if r.act in ("conditional", "hypothetical", "sarcastic"):
            # a bare hedge right after a request ("No pressure at all.") hedges that request
            if not any(t.startswith("c:") for t in content_tokens(r.quote)):
                for j in range(i - 1, -1, -1):
                    c = requests[j]
                    if c.act != "request":
                        continue
                    if c.message_id == r.message_id and not c.hedged:
                        c.hedged = r.quote
                        consumed.add(i)
                    break
            continue
        if r.act != "retracted":
            continue
        toks = content_tokens(r.quote)
        targets = sorted({j for t in toks for j in index.get(t, ()) if j < i})
        if not targets and not any(t.startswith("c:") for t in toks):
            for j in range(i - 1, -1, -1):          # names nothing specific: it refers to the item right before
                c = requests[j]
                if c.act != "request":
                    continue
                if _near(c, r, order, near_messages):
                    targets = [j]
                break
        if targets:
            for j in targets:
                if not requests[j].withdrawn_by:
                    requests[j].withdrawn_by = r.quote
            consumed.add(i)
        # a later request in the same message about the same thing contradicts the retraction
        for k in range(i + 1, len(requests)):
            c = requests[k]
            if c.message_id != r.message_id:
                break
            if c.act == "request" and not c.withdrawn_by and toks & ctoks[k]:
                c.conflict = r.quote
    return [r for i, r in enumerate(requests) if i not in consumed]


# ---------------------------------------------------------------- freelancer free offers
_FREE = re.compile(
    r"\bno (?:extra |additional )?(?:charge|cost|fee)\b|\bon the house\b|\bfor free\b|\bfree of charge\b"
    r"|\bcomplimentary\b|\bat no cost\b|\bthrow (?:it|that|this|them) in\b|\bbonus\b|\bmy treat\b|\bon me\b"
    r"|\b(?:is|are|will be) included\b|\bno charge\b|\bgift\b|\bfree\b", re.I)
_NOT_FREE = re.compile(r"\b(?:not|isn'?t|aren'?t|never|n'?t) (?:be )?included\b|\bnot free\b|\bnot for free\b|\bno free\b", re.I)
_REFER = re.compile(r"\b(?:that|it|this|that one|those|them)\b", re.I)


def free_offer_sentences(freelancer_messages: Iterable, split) -> list:
    """(message_index, sentence) pairs where the freelancer waives the charge."""
    out = []
    for k, m in enumerate(freelancer_messages):
        for sent in split(m.text):
            if _FREE.search(sent) and not _NOT_FREE.search(sent):
                out.append((k, m, sent))
    return out
