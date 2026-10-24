"""Small text helpers shared by the parsers, the rule layer and the mock LLM."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_WS = re.compile(r"\s+")

_QUOTE_MAP = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", " ": " ", " ": " ",
    "‎": "", "‏": "", "﻿": "",
}


def normalize(text: str) -> str:
    """Casefold, unify quotes/dashes, collapse whitespace. Used for the evidence rule."""
    text = unicodedata.normalize("NFKC", text or "")
    for src, dst in _QUOTE_MAP.items():
        text = text.replace(src, dst)
    return _WS.sub(" ", text).strip().casefold()


STOP = set("""
a an the and or but if so of to in on at by for with from as is are was were be been being am do does did
can could would should will shall may might must you your yours we our ours us i me my mine it its this that
these those there here they them their he she his her not no yes ok okay please thanks thank hi hello hey
also just really very much more some any one two three up down out over into about what when where which who
how why get got let lets make makes made change changes changed add adds added adding want wants need needs
like new site website page pages include included including includes project work works additional extra
client provide provided have has had too still then than again all each both few other such only own same
setup functionality feature features system systems integration creation development sure maybe think look looks looking see give take put go going able way thing things something anything
""".split())

# word -> concept. A shared concept is a strong signal (weight 2); other shared words weigh 1.
_CONCEPT_GROUPS = {
    "shop": "shop store stores cart checkout ecommerce woocommerce sell selling sales purchase purchases payment payments",
    "blog": "blog blogs article articles news",
    "copywriting": "copywriting copywrite copy write writing written wording proofread proofreading",
    "translation": "translation translations translate translated multilingual bilingual language languages "
                   "spanish french german arabic italian portuguese urdu hindi",
    "logo": "logo logos brand branding identity rebrand",
    "production": "photography photographer photoshoot shoot video videos footage film drone",
    "seo": "seo backlink backlinks ranking rank keyword keywords title titles meta metadata",
    "ads": "ads ad advertising ppc campaign campaigns adwords paid",
    "hosting": "hosting domain domains dns renewal",
    "booking": "booking bookings appointment appointments reservation reservations calendly",
    "newsletter": "newsletter newsletters mailchimp mailing subscribe subscription",
    "social": "instagram facebook tiktok twitter linkedin social youtube pinterest",
    "maintenance": "maintenance maintain retainer ongoing",
    "app": "app apps android ios",
    "analytics": "analytics ga4 tracking pixel",
    "responsive": "responsive mobile phone phones tablet iphone",
    "form": "form forms enquiry enquiries inquiry inquiries",
    "gallery": "gallery galleries",
    "image": "image images photo photos picture pictures photograph photographs",
    "membership": "login logins member members membership account accounts",
    "chat": "chatbot chat whatsapp livechat",
}
CONCEPT: dict[str, str] = {}
for _c, _words in _CONCEPT_GROUPS.items():
    for _w in _words.split():
        CONCEPT[_w] = _c

_PHRASES = [
    (re.compile(r"e-?commerce", re.I), "ecommerce"),
    (re.compile(r"online (store|shop)s?|web ?shop|web ?store", re.I), "shop"),
    (re.compile(r"check-?out", re.I), "checkout"),
    (re.compile(r"e-?mail marketing", re.I), "newsletter"),
    (re.compile(r"google ads", re.I), "ads"),
]
_WORD = re.compile(r"[a-z][a-z0-9]+")


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


@lru_cache(maxsize=65536)
def concepts(text: str) -> dict[str, int]:
    """Return {token_or_concept: weight} for the content of text."""
    text = unicodedata.normalize("NFKC", text or "")
    for rx, rep in _PHRASES:
        text = rx.sub(rep, text)
    out: dict[str, int] = {}
    for raw in _WORD.findall(text.lower()):
        if raw in CONCEPT:
            out["c:" + CONCEPT[raw]] = 2
            continue
        if raw in STOP:
            continue
        w = _stem(raw)
        if w in STOP or len(w) < 3:
            continue
        out.setdefault(w, 1)
    return out


def overlap(a: str, b: str) -> int:
    """Weighted overlap of content words/concepts between two texts."""
    ca, cb = concepts(a), concepts(b)
    return sum(max(ca[k], cb[k]) for k in ca.keys() & cb.keys())


NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10, "a": 1, "single": 1}


def slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:n].strip("-")) or "request"


_PHRASE_SPLIT = re.compile(r"[,;:/()]|\b(?:or|and|including|such as|for example|e\.g\.)\b", re.I)


def phrases(text: str) -> list[str]:
    return [p.strip() for p in _PHRASE_SPLIT.split(text or "") if p and p.strip()]


def phrase_covered(request_tokens, phrase: str) -> bool:
    """True when the request contains the whole phrase (short phrases) or at least two of its
    content words (long phrases). A single shared word never matches a multi-word phrase."""
    toks = set(concepts(phrase))
    if not toks:
        return False
    hit = len(toks & set(request_tokens))
    return hit == len(toks) if len(toks) <= 2 else hit >= 2
