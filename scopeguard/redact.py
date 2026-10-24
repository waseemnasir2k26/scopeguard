"""Mask emails and phone numbers before any text reaches an LLM."""
import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_PHONE = re.compile(r"(?<![\w.])\+?\(?\d[\d\s().\-]{5,}\d(?![\w])")
_DATE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$|^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$")


def redact(text: str) -> str:
    text = _EMAIL.sub("[EMAIL]", text or "")

    def _ph(m: re.Match) -> str:
        raw = m.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if _DATE.match(raw) or not 7 <= len(digits) <= 15:
            return m.group(0)
        return "[PHONE]"

    return _PHONE.sub(_ph, text)
