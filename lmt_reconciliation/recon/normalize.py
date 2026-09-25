"""Normalization helpers.

Every helper returns a *comparison key*; raw values are always kept separately
by the callers so nothing the user sees is rewritten.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_APOSTROPHES = "’‘ʼ`´"
_SPLIT_MULTI = re.compile(r"\s*(?:;|\||\n|\r)\s*")


def clean(value) -> str:
    """Return a string with surrounding whitespace removed ('' for None)."""
    if value is None:
        return ""
    return str(value).strip()


def fold(text: str) -> str:
    """Accent-fold and casefold text for comparison only."""
    text = clean(text)
    for ch in _APOSTROPHES:
        text = text.replace(ch, "'")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.casefold()


def name_key(text: str) -> str:
    """Comparison key for a single name: letters/digits only, accent-folded.

    O'Brien, O’Brien and OBrien share a key; José and Jose share a key.
    """
    return re.sub(r"[^0-9a-z]", "", fold(text))


def header_key(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", fold(text))


def split_multi(value: str) -> list[str]:
    """Split a cell holding several values (semicolon, pipe or newline)."""
    value = clean(value)
    if not value:
        return []
    return [p for p in (s.strip() for s in _SPLIT_MULTI.split(value)) if p]


# ---------------------------------------------------------------- email
def email_key(value: str) -> str:
    return clean(value).casefold()


def split_emails(value: str) -> list[str]:
    parts = []
    for chunk in split_multi(value):
        parts.extend(p for p in re.split(r"[,\s]+", chunk) if "@" in p)
    return parts


# ---------------------------------------------------------------- phone
_EXT_RE = re.compile(r"(?:ext\.?|extension|x|#)\s*(\d{1,6})\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Phone:
    raw: str
    main: str          # digits used for comparison (country code 1 dropped only if present)
    ext: str           # explicit extension digits, '' if none
    problem: str = ""  # why the digits are ambiguous, '' if fine

    @property
    def key(self) -> tuple[str, str]:
        return (self.main, self.ext)


def parse_phone(raw: str) -> Phone:
    """Parse one phone string.

    * Formatting is ignored for comparison ("(206) 555-0100" == "206.555.0100").
    * A leading US country code "1" on an 11-digit number is dropped for
      comparison; no country code is ever added.
    * An extension is recognised only when explicitly marked (x, ext, #).
      Extra unmarked digits are reported as a problem, never guessed.
    """
    raw_s = clean(raw)
    text = raw_s
    ext = ""
    m = _EXT_RE.search(text)
    if m and len(re.sub(r"\D", "", text[: m.start()])) >= 7:
        ext = m.group(1)
        text = text[: m.start()]
    digits = re.sub(r"\D", "", text)
    problem = ""
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    elif len(digits) == 10 or (len(digits) == 7):
        pass
    elif len(digits) > 11:
        problem = "More digits than a phone number, with no marked extension"
    elif digits:
        problem = f"Unusual length ({len(digits)} digits)"
    if len(digits) == 7 and not problem:
        problem = "No area code"
    return Phone(raw=raw_s, main=digits, ext=ext, problem=problem)


def split_phones(value: str) -> list[str]:
    parts = []
    for chunk in split_multi(value):
        # Commas separate numbers only when each side looks like a full number.
        pieces = [p.strip() for p in chunk.split(",")]
        if len(pieces) > 1 and all(len(re.sub(r"\D", "", p)) >= 7 for p in pieces):
            parts.extend(pieces)
        else:
            parts.append(chunk)
    return [p for p in parts if re.search(r"\d", p)]


def phone_digits_in_text(text: str) -> set[str]:
    """Phone mains that appear anywhere in free text (e.g. notes)."""
    found = set()
    for m in re.finditer(r"\+?1?[\s.\-(]*\d{3}[\s.\-)]*\d{3}[\s.\-]*\d{4}", text or ""):
        found.add(parse_phone(m.group(0)).main)
    return found


# ---------------------------------------------------------------- address
_STREET_ABBR = {
    "street": "st", "avenue": "ave", "road": "rd", "drive": "dr", "lane": "ln",
    "court": "ct", "place": "pl", "boulevard": "blvd", "north": "n", "south": "s",
    "east": "e", "west": "w", "northeast": "ne", "northwest": "nw",
    "southeast": "se", "southwest": "sw", "apartment": "apt", "suite": "ste",
}


def address_key(street: str) -> str:
    words = re.findall(r"[0-9a-z]+", fold(street))
    words = [_STREET_ABBR.get(w, w) for w in words]
    return " ".join(words)


def zip_key(value: str) -> str:
    """ZIP as text; leading zeros preserved, ZIP+4 reduced to 5 for comparison."""
    digits = re.sub(r"\D", "", clean(value))
    return digits[:5] if len(digits) >= 5 else digits


# ---------------------------------------------------------------- categories
def category_key(label: str) -> str:
    return re.sub(r"\s+", " ", fold(label)).strip()


def split_categories(value: str, separators: str = ";|\n") -> list[str]:
    value = clean(value)
    if not value:
        return []
    pattern = "[" + re.escape(separators) + "]"
    return [p.strip() for p in re.split(pattern, value) if p.strip()]


# ---------------------------------------------------------------- notes
def note_tokens(text: str) -> list[str]:
    """Words of a note for comparison: case, accents, spacing, line breaks and punctuation ignored.

    '&' counts as 'and'. Word order is kept so relationships ("Agent: John Pinto") survive.
    """
    return re.findall(r"[0-9a-z]+", fold(text).replace("&", " and "))


def contains_sequence(needle: list[str], hay: list[str]) -> bool:
    """True when `needle` appears in `hay` as consecutive whole words, in the same order."""
    n = len(needle)
    if not n:
        return False
    first = needle[0]
    return any(hay[i] == first and hay[i:i + n] == needle for i in range(len(hay) - n + 1))


_SEGMENT_BREAK = re.compile(r"(?:\r?\n)+|(?<=[.!?;])\s+")


def note_segments(text: str) -> list[tuple[str, list[str]]]:
    """Split a note into sentences / lines: (original text, word list)."""
    out = []
    for seg in _SEGMENT_BREAK.split(text or ""):
        words = note_tokens(seg)
        if words:
            out.append((seg.strip(), words))
    return out




# ---------------------------------------------------------------- dates
PLACEHOLDER_YEARS = {"1800", "1900", "0001", "1", "1753", "1899"}


def birthday_parts(value: str) -> dict:
    """Split a birthday into month/day/year without trusting placeholder years."""
    raw = clean(value)
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", raw) or None
    if m:
        year, month, day = m.group(1), m.group(2), m.group(3)
    else:
        m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{1,4}))?$", raw)
        if not m:
            return {"raw": raw, "month": "", "day": "", "year": "", "year_known": False}
        month, day, year = m.group(1), m.group(2), m.group(3) or ""
    year_known = bool(year) and year.lstrip("0") not in {y.lstrip("0") for y in PLACEHOLDER_YEARS}
    return {"raw": raw, "month": month.zfill(2), "day": day.zfill(2),
            "year": year if year_known else "", "year_known": year_known}


# ---------------------------------------------------------------- spreadsheet safety
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_PHONE_OR_NUMBER = re.compile(r"^[+\-]?[\d\s().\-/x#ext]*$", re.IGNORECASE)


def spreadsheet_safe(value) -> str:
    """Neutralise a cell that a spreadsheet could execute as a formula.

    A leading apostrophe is added for anything starting with = @ tab or CR,
    and for + or - unless the value is purely numeric/phone-like
    (e.g. "+1 206 555 0100"), which cannot carry a formula.
    Used only when writing downloads; stored data keeps the original.
    """
    text = "" if value is None else str(value)
    if not text or not text.startswith(_FORMULA_START):
        return text
    if text[0] in "+-" and _PHONE_OR_NUMBER.match(text):
        return text
    return "'" + text
