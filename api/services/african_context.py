"""Lightweight African-context relevance scoring (regex + dictionaries).

No spaCy/fastText: zero extra memory or cold-start cost on a small instance.
Every post gets a 0-1 score, a primary country and the signals that matched.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass

MIN_SCORE = float(os.getenv("AFRICA_MIN_SCORE", "0.4"))

# places: matched lowercase | codes: ISO currency codes, matched case-sensitively
# words: currency names/symbols | terms: local slang, institutions, brands
COUNTRIES: dict[str, dict] = {
    "NG": {"name": "Nigeria", "places": ["nigeria", "lagos", "abuja", "port harcourt", "ibadan", "kano", "naija"],
           "codes": ["NGN"], "words": ["naira", "₦"],
           "terms": ["wahala", "abeg", "danfo", "okada", "cbn", "paystack", "flutterwave"]},
    "GH": {"name": "Ghana", "places": ["ghana", "accra", "kumasi", "tema", "takoradi", "tamale", "cape coast"],
           "codes": ["GHS"], "words": ["cedi", "cedis", "₵"],
           "terms": ["chale", "trotro", "tro tro", "dumsor", "momo", "bank of ghana"]},
    "KE": {"name": "Kenya", "places": ["kenya", "nairobi", "mombasa", "kisumu", "nakuru", "eldoret"],
           "codes": ["KES"], "words": ["ksh", "kenyan shilling"],
           "terms": ["matatu", "sheng", "m-pesa", "mpesa", "safaricom", "boda boda", "bodaboda", "kplc"]},
    "ZA": {"name": "South Africa", "places": ["south africa", "johannesburg", "cape town", "durban", "pretoria", "soweto", "mzansi"],
           "codes": ["ZAR"], "words": ["rands"],
           "terms": ["load shedding", "loadshedding", "eskom", "braai", "kasi", "sassa"]},
    "EG": {"name": "Egypt", "places": ["egypt", "cairo", "alexandria", "giza"],
           "codes": ["EGP"], "words": ["egyptian pound"], "terms": []},
    "UG": {"name": "Uganda", "places": ["uganda", "kampala", "entebbe", "jinja"],
           "codes": ["UGX"], "words": ["ugandan shilling"], "terms": ["boda boda", "bodaboda", "matooke"]},
    "TZ": {"name": "Tanzania", "places": ["tanzania", "dar es salaam", "dodoma", "arusha", "zanzibar", "mwanza"],
           "codes": ["TZS"], "words": ["tanzanian shilling"], "terms": ["daladala"]},
    "RW": {"name": "Rwanda", "places": ["rwanda", "kigali"], "codes": ["RWF"], "words": ["rwandan franc"], "terms": []},
    "ET": {"name": "Ethiopia", "places": ["ethiopia", "addis ababa", "hawassa", "dire dawa"],
           "codes": ["ETB"], "words": ["birr"], "terms": []},
    "SN": {"name": "Senegal", "places": ["senegal", "dakar"], "codes": ["XOF"], "words": ["fcfa"], "terms": []},
    "MA": {"name": "Morocco", "places": ["morocco", "casablanca", "rabat", "marrakech", "tangier"],
           "codes": ["MAD"], "words": ["dirham", "dirhams"], "terms": []},
    "ZM": {"name": "Zambia", "places": ["zambia", "lusaka", "ndola", "kitwe"], "codes": ["ZMW"], "words": ["kwacha"], "terms": []},
    "ZW": {"name": "Zimbabwe", "places": ["zimbabwe", "harare", "bulawayo"], "codes": ["ZWL"], "words": [], "terms": ["ecocash"]},
}

# Source hints: subreddit / channel / config "country" -> country code ("*" = pan-African).
HINTS = {
    "africa": "*", "nigeria": "NG", "lagos": "NG", "naija": "NG", "ghana": "GH", "accra": "GH",
    "kenya": "KE", "nairobi": "KE", "southafrica": "ZA", "capetown": "ZA", "johannesburg": "ZA",
    "egypt": "EG", "cairo": "EG", "uganda": "UG", "kampala": "UG", "tanzania": "TZ", "ethiopia": "ET",
    "rwanda": "RW", "zambia": "ZM", "zimbabwe": "ZW", "senegal": "SN", "morocco": "MA",
}


def _alt(words: list[str], flags: int = 0):
    if not words:
        return None
    body = "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{body})(?![\w-])", flags)


_PATTERNS = {
    code: {k: _alt(c[k]) for k in ("places", "words", "terms", "codes")}
    for code, c in COUNTRIES.items()
}
# "africa" but not "south africa" or "african american"; the country list handles South Africa.
_PAN_RE = re.compile(r"(?:(?<![\w-])(?<!south )africa(?![\w-])|sub-saharan|afcfta)")
_PAN_TERMS_RE = _alt(["mobile money"])


def _resolve_hint(hint: str | None) -> str | None:
    if not hint:
        return None
    raw = hint.strip()
    if len(raw) == 2 and raw.upper() in COUNTRIES:
        return raw.upper()
    key = re.sub(r"[^a-z]", "", raw.lower().removeprefix("r/"))
    return HINTS.get(key)


@dataclass(frozen=True)
class ContextResult:
    score: float
    is_relevant: bool
    primary_country: str | None
    countries: list[str]
    signals: dict


def analyze(text: str | None, hint: str | None = None, min_score: float | None = None) -> ContextResult:
    threshold = MIN_SCORE if min_score is None else min_score
    original = text or ""
    lowered = original.lower()
    votes: Counter[str] = Counter()
    signals: dict = {}
    score = 0.0

    resolved = _resolve_hint(hint)
    if resolved:
        score += 0.5
        signals["hint"] = resolved
        if resolved != "*":
            votes[resolved] += 3

    places_all: set[str] = set()
    currency_all: set[str] = set()
    terms_all: set[str] = set()
    for code, p in _PATTERNS.items():
        places = set(p["places"].findall(lowered))
        currency = set(p["words"].findall(lowered)) if p["words"] else set()
        if p["codes"]:
            currency |= set(p["codes"].findall(original))
        terms = set(p["terms"].findall(lowered)) if p["terms"] else set()
        if places:
            votes[code] += 2
        if currency:
            votes[code] += 2
        if terms:
            votes[code] += 1
        places_all |= places
        currency_all |= currency
        terms_all |= terms

    if places_all:
        score += 0.4 + min(0.2, 0.1 * (len(places_all) - 1))
        signals["places"] = sorted(places_all)
    if currency_all:
        score += 0.3
        signals["currency"] = sorted(currency_all)
    if _PAN_TERMS_RE.search(lowered):
        terms_all.add("mobile money")
    if terms_all:
        score += 0.25
        signals["terms"] = sorted(terms_all)
    if _PAN_RE.search(lowered):
        score += 0.4
        signals["pan_african"] = True

    countries = [c for c, _ in votes.most_common()]
    score = round(min(score, 1.0), 2)
    return ContextResult(
        score=score,
        is_relevant=score >= threshold,
        primary_country=countries[0] if countries else None,
        countries=countries,
        signals=signals,
    )