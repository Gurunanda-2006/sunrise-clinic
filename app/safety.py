"""Checks that must not depend on the LLM: phone validation and the emergency pre-filter."""
from __future__ import annotations

import re

from .config import ClinicConfig

_TELUGU_DIGITS = "౦౧౨౩౪౫౬౭౮౯"
_HINDI_DIGITS = "०१२३४५६७८९"
_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate(_TELUGU_DIGITS)}
_DIGIT_MAP.update({ord(c): str(i) for i, c in enumerate(_HINDI_DIGITS)})


def normalize_phone(value: str) -> str | None:
    """Return the 10-digit Indian mobile number, or None if it is not valid."""
    digits = re.sub(r"\D", "", str(value).translate(_DIGIT_MAP))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


# A booking is only made when the patient's latest message is a clear yes. This is checked in code.
_YES_WORDS = re.compile(
    r"\b(yes|yeah|yea|yep|yup|ya|ok|okay|sure|confirm|confirmed|correct|right|fine|alright|perfect|absolutely|definitely|done|"
    r"go ahead|please do|book it|do it|sounds good|that works|"
    r"haan|han|haa|ji|theek|thik|sahi|bilkul|kar do|kar dijiye|"
    r"avunu|avnu|sare|sarey|cheyandi|cheyyandi)\b"
)
_YES_NATIVE = ("అవును", "ఔను", "సరే", "చేయండి", "చెయ్యండి", "ఓకే", "ఒకే", "కరెక్ట్", "ఓకె",
               "हाँ", "हां", "जी", "ठीक", "सही", "बिलकुल", "बिल्कुल", "कर दो", "कर दीजिए", "ओके")
_NO_WORDS = re.compile(r"\b(no|nope|not|don't|dont|wait|stop|cancel|change|instead|nahi|nahin|mat|vaddu|voddu|kaadu|ledu)\b")
_NO_NATIVE = ("వద్దు", "కాదు", "లేదు", "ఆగండి", "नहीं", "मत", "रुको", "रुकिए")


def is_affirmative(text: str) -> bool:
    """True only for a clear yes, in English, Telugu or Hindi (either script)."""
    low = " ".join(text.lower().replace("’", "'").split())
    if _NO_WORDS.search(low) or any(w in low for w in _NO_NATIVE):
        return False
    return bool(_YES_WORDS.search(low)) or any(w in low for w in _YES_NATIVE)


def detect_language(text: str) -> str:
    """'te' / 'hi' if the text uses Telugu / Devanagari script, else 'en'."""
    if re.search(r"[ఀ-౿]", text):
        return "te"
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    return "en"


def is_emergency(cfg: ClinicConfig, text: str) -> bool:
    low = " ".join(text.lower().replace("’", "'").split())
    return any(k in low for k in cfg.emergency_keywords)


def emergency_reply(cfg: ClinicConfig, text: str) -> str:
    return cfg.emergency_reply.get(detect_language(text)) or cfg.emergency_reply["en"]
