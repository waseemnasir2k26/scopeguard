"""Mask personal data before any text reaches an LLM.

Masked: email addresses (also written as "name [at] host [dot] com"), card numbers (Luhn-valid,
13 to 19 digits), IBANs (mod-97 valid), phone numbers, and a person's name in a "From:" header or
in a sign-off ("Best,\\nDana Okafor"). Pattern-based: it will miss names inside sentences, street
addresses, order numbers and anything unusual. It is a seatbelt, not a guarantee.
"""
import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_EMAIL_OBF = re.compile(
    r"[A-Za-z0-9._%+\-]+\s*[\[(\{]\s*at\s*[\])\}]\s*[A-Za-z0-9\-]+(?:\s*[\[(\{]?\s*(?:dot|\.)\s*[\])\}]?\s*[A-Za-z0-9\-]+)+",
    re.I)
_CARD = re.compile(r"(?<![\w.])\d(?:[ \-]?\d){12,18}(?![\w])")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")
_PHONE = re.compile(r"(?<![\w.])\+?\(?\d[\d\s().\-]{5,}\d(?![\w])")
_DATE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$|^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$")
_FROM = re.compile(r"(?im)^(from|to|cc|bcc|reply-to|sender):[ \t]*(?:\"?[^<\n\"]+\"?[ \t]*)?(?=<|$)")
_SIGNOFF = re.compile(
    r"(?im)^(best|thanks|thank you|regards|kind regards|best regards|warm regards|cheers|sincerely|many thanks)[,.!]?[ \t]*\n"
    r"[ \t]*([A-Z][\w'.\-]+(?:[ \t]+[A-Z][\w'.\-]+){0,3})[ \t]*$")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _iban_ok(raw: str) -> bool:
    s = raw.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    num = "".join(str(int(c, 36)) for c in moved)
    return int(num) % 97 == 1


def redact(text: str) -> str:
    text = text or ""
    text = _FROM.sub(lambda m: f"{m.group(1)}: [NAME] ", text)
    text = _SIGNOFF.sub(lambda m: f"{m.group(1)},\n[NAME]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _EMAIL_OBF.sub("[EMAIL]", text)

    def _iban(m: re.Match) -> str:
        return "[IBAN]" if _iban_ok(m.group(0)) else m.group(0)

    text = _IBAN.sub(_iban, text)

    def _card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return "[CARD]" if 13 <= len(digits) <= 19 and _luhn(digits) else m.group(0)

    text = _CARD.sub(_card, text)

    def _ph(m: re.Match) -> str:
        raw = m.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if _DATE.match(raw) or not 7 <= len(digits) <= 15:
            return m.group(0)
        return "[PHONE]"

    return _PHONE.sub(_ph, text)
