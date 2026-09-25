"""Field-level checks shared by reconciliation and verification.

Each check answers one question: on this Top Producer contact, as exported,
is the proposed addition present, missing, or uncertain?
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import normalize as N
from .contacts import TPContact, TPExport

PRESENT = "present"
MISSING = "missing"
PARTIAL = "partial"          # some items present, some missing
UNCERTAIN = "uncertain"      # needs a person
UNVERIFIABLE = "unverifiable"

# Review categories (plain labels shown in the Exceptions view)
R_MATCH = "Ambiguous or unconfirmed match"
R_CONFLICT = "Conflicting or newer information"
R_SOURCE = "Missing source information"
R_CATEGORY = "Unresolved category mapping"
R_PHONE = "Phone label / extension question"
R_UNSUPPORTED = "Cannot be checked automatically"
R_DUPLICATE = "Suspected duplicate"
R_REVIEW_ONLY = "Review-only item"
R_COWORK = "Cowork status needs attention"
R_NOT_FOUND = "Contact not found"

PHONE_NEWER = re.compile(r"(disconnected|wrong number|new (cell|number|phone)|changed (his |her |their )?"
                         r"(number|phone|cell)|no longer|do not call|\bdnc\b|number (is )?(changed|bad))",
                         re.IGNORECASE)
CATEGORY_NEWER = re.compile(r"(opt(ed)?[\s\-]?out|unsubscrib|do not (contact|email|mail|call)|\bdnc\b|"
                            r"removed? (from|them|him|her)|remove from|deceased|passed away|"
                            r"no longer (a |an )?(client|interested))", re.IGNORECASE)
BLOCKING_TYPES = {"do not contact", "dnc", "opt out", "opted out", "deceased", "do not mail",
                  "do not email", "unsubscribed"}


@dataclass
class CheckResult:
    state: str
    current: str = ""                 # current value(s) shown from the export
    still_missing: list[str] = field(default_factory=list)
    reason: str = ""
    reviews: list[tuple[str, str]] = field(default_factory=list)  # (category, reason)
    notes: list[str] = field(default_factory=list)                # informational
    expected: list[str] = field(default_factory=list)             # what should appear (verification)


def _phones_text(c: TPContact) -> str:
    return "; ".join(f"{col}: {p.raw}" for col, p in c.phones) or "(no phone numbers)"


# ---------------------------------------------------------------- phones
def check_phone(proposed: str, c: TPContact, export: TPExport) -> CheckResult:
    ok, why = export.field_available("phone")
    if not ok:
        return CheckResult(UNVERIFIABLE, reason=why)
    raws = N.split_phones(proposed)
    if not raws:
        return CheckResult(UNCERTAIN, _phones_text(c), reason="No phone number in the proposed addition.",
                           reviews=[(R_SOURCE, "Proposed addition has no usable phone number.")])
    res = CheckResult(PRESENT, _phones_text(c))
    note_digits = N.phone_digits_in_text(c.notes)
    for raw in raws:
        p = N.parse_phone(raw)
        res.expected.append(p.raw)
        if p.problem:
            res.reviews.append((R_PHONE, f"{p.raw}: {p.problem}. Digits are not guessed; confirm the number "
                                         "and any extension."))
            continue
        same_main = [(col, q) for col, q in c.phones if q.main == p.main]
        if any(q.ext == p.ext for _, q in same_main):
            continue  # present (formatting may differ)
        if same_main:
            have = ", ".join(f"{col}: {q.raw}" for col, q in same_main)
            res.reviews.append((R_PHONE, f"{p.raw} is on the contact ({have}) but the extension differs. "
                                         "Confirm which extension is right; nothing is inferred."))
            continue
        others = [o for o in export.by_phone.get(p.main, []) if o is not c]
        if others:
            names = ", ".join(o.label for o in others[:3])
            res.reviews.append((R_PHONE, f"{p.raw} is on a different profile ({names}). "
                                         "Confirm who owns this number before adding it."))
            continue
        if p.main in note_digits:
            res.reviews.append((R_PHONE, f"{p.raw} appears in the notes but not in a phone field. "
                                         "Confirm whether it belongs in a phone field."))
            continue
        res.still_missing.append(p.raw)
    if PHONE_NEWER.search(c.notes or "") and (res.still_missing or res.reviews):
        res.reviews.append((R_CONFLICT, "Notes mention a changed, new or disconnected number. The older source "
                                        "value may have been superseded; do not add without checking."))
        res.still_missing = []
    res.state = _combine(res)
    res.reason = _phone_reason(res)
    return res


def _combine(res: CheckResult) -> str:
    if res.reviews:
        return UNCERTAIN
    if res.still_missing and len(res.still_missing) < len(res.expected):
        return PARTIAL
    return MISSING if res.still_missing else PRESENT


def _phone_reason(res: CheckResult) -> str:
    if res.state == PRESENT:
        return "Every proposed number is already in a phone field (formatting ignored)."
    if res.state in (MISSING, PARTIAL):
        present = len(res.expected) - len(res.still_missing)
        extra = f" {present} other proposed number(s) already present." if present else ""
        return (f"Not found in any exported phone field or the notes: {', '.join(res.still_missing)}.{extra} "
                "Add only; keep the primary number and existing labels.")
    return "; ".join(r for _, r in res.reviews)


# ---------------------------------------------------------------- email
def check_email(proposed: str, c: TPContact, export: TPExport) -> CheckResult:
    ok, why = export.field_available("email")
    if not ok:
        return CheckResult(UNVERIFIABLE, reason=why)
    emails = N.split_emails(proposed)
    current = "; ".join(c.emails) or "(no email)"
    if not emails:
        return CheckResult(UNCERTAIN, current, reason="No email address in the proposed addition.",
                           reviews=[(R_SOURCE, "Proposed addition has no usable email address.")])
    res = CheckResult(PRESENT, current, expected=emails)
    for e in emails:
        if N.email_key(e) in c.email_keys:
            continue
        others = [o for o in export.by_email.get(N.email_key(e), []) if o is not c]
        if others:
            res.reviews.append((R_CONFLICT, f"{e} is on a different profile ({others[0].label}). "
                                            "It may be a shared family email; confirm before adding."))
        else:
            res.still_missing.append(e)
    res.state = _combine(res)
    res.reason = ("Already present in an exported email field." if res.state == PRESENT else
                  "; ".join(r for _, r in res.reviews) if res.reviews else
                  f"Not in any exported email field: {', '.join(res.still_missing)}. Add only.")
    return res


# ---------------------------------------------------------------- categories
def split_source_categories(proposed: str) -> list[str]:
    seps = ";|\n" if re.search(r"[;|\n]", proposed or "") else ","
    return N.split_categories(proposed, seps)


def check_categories(proposed: str, c: TPContact, export: TPExport, mapping: dict[str, dict],
                     known_types: set[str]) -> CheckResult:
    ok, why = export.field_available("category")
    if not ok:
        return CheckResult(UNVERIFIABLE, reason=why)
    sources = split_source_categories(proposed)
    current = "; ".join(c.categories) or "(no contact types)"
    if not sources:
        return CheckResult(UNCERTAIN, current, reason="No category in the proposed addition.",
                           reviews=[(R_SOURCE, "Proposed addition has no category.")])
    have = {N.category_key(t) for t in c.categories}
    res = CheckResult(PRESENT, current)
    for src in sources:
        m = mapping.get(N.category_key(src))
        target, basis = "", ""
        if m and m.get("Status") == "APPROVED" and N.clean(m.get("TP_Contact_Type")):
            target, basis = m["TP_Contact_Type"], "approved mapping"
        elif m and m.get("Status") == "NO_TP_EQUIVALENT":
            res.notes.append(f"'{src}' has no Top Producer equivalent (per mapping); not proposed.")
            continue
        elif N.category_key(src) in known_types:
            target, basis = src, "identical Top Producer type"
        else:
            res.reviews.append((R_CATEGORY, f"'{src}' has no approved Top Producer contact type. "
                                            "Add a mapping on the Category mapping page; nothing is invented."))
            continue
        res.expected.append(target)
        if N.category_key(target) in have:
            continue
        conflicts = [x.strip() for x in N.split_categories(m.get("Conflicts_With", "") if m else "", ";|,")]
        clash = [t for t in c.categories if N.category_key(t) in {N.category_key(x) for x in conflicts}]
        if clash:
            res.reviews.append((R_CONFLICT, f"Adding '{target}' conflicts with existing type "
                                            f"'{clash[0]}' (per mapping). Hold for review."))
            continue
        res.still_missing.append(target)
    blocking = [t for t in c.categories if N.category_key(t) in BLOCKING_TYPES]
    if res.still_missing and (blocking or CATEGORY_NEWER.search(c.notes or "")):
        what = f"contact type '{blocking[0]}'" if blocking else "notes mentioning an opt-out or removal"
        res.reviews.append((R_CONFLICT, f"The contact has {what}. The category may have been removed on "
                                        "purpose; do not re-add without checking."))
        res.still_missing = []
    blocking_reviews = [r for r in res.reviews if r[0] != R_CATEGORY]
    if res.still_missing and not blocking_reviews:
        # Mapped part can proceed; unmapped labels stay in review alongside it.
        res.state = PARTIAL if len(res.still_missing) < len(res.expected) else MISSING
    elif res.reviews:
        res.state = UNCERTAIN
    elif res.expected:
        res.state = PRESENT
    else:
        res.state = UNCERTAIN
        res.reviews.append((R_CATEGORY, "None of the proposed categories has a Top Producer equivalent."))
    if res.state == PRESENT:
        res.reason = "All mapped contact types are already on the contact (order ignored)."
    elif res.still_missing:
        res.reason = (f"Missing contact type(s): {', '.join(res.still_missing)}. Add only these; keep every "
                      "existing type.")
        if res.reviews:
            res.reason += " Unmapped labels were sent to review."
    else:
        res.reason = "; ".join(r for _, r in res.reviews)
    return res


# ---------------------------------------------------------------- notes
def check_note(proposed: str, c: TPContact, export: TPExport) -> CheckResult:
    ok, why = export.field_available("note")
    if not ok:
        return CheckResult(UNVERIFIABLE, reason=why)
    detail = N.clean(proposed)
    current = c.notes
    if not detail:
        return CheckResult(UNCERTAIN, current, reason="No note detail in the proposed addition.",
                           reviews=[(R_SOURCE, "The missing note detail is not identified; a whole note should "
                                               "not be replaced.")])
    tokens = [t for t in N.note_tokens(detail) if len(t) > 1 or t.isdigit()]
    if len(tokens) > 80:
        return CheckResult(UNCERTAIN, current, expected=[detail],
                           reason="The proposed text is a whole note, not a specific missing detail.",
                           reviews=[(R_CONFLICT, "Proposed text looks like an entire note. Identify the specific "
                                                 "missing detail instead of replacing the note.")])
    if N.note_compact(detail) and N.note_compact(detail) in N.note_compact(current):
        return CheckResult(PRESENT, current, expected=[detail],
                           reason="The detail is already in the notes (spacing/punctuation ignored).")
    have = set(N.note_tokens(current))
    found = sum(1 for t in tokens if t in have)
    cover = found / len(tokens) if tokens else 0
    if cover == 1:
        return CheckResult(PRESENT, current, expected=[detail],
                           reason="Every word of the detail is already in the notes, in different wording "
                                  "or order. Not re-added, to avoid a duplicate.")
    if cover >= 0.5:
        return CheckResult(UNCERTAIN, current, expected=[detail],
                           reason=f"About {round(cover * 100)}% of the detail's words are already in the notes.",
                           reviews=[(R_CONFLICT, "Part of this detail is already in the notes with different "
                                                 "wording. Decide whether anything is actually missing.")])
    return CheckResult(MISSING, current, [detail], expected=[detail],
                       reason="Detail not found in the notes. Add it next to the existing note with a source "
                              "attribution; never replace the existing note.")


# ---------------------------------------------------------------- birthday
def check_birthday(proposed: str, c: TPContact, export: TPExport) -> CheckResult:
    ok, why = export.field_available("birthday")
    if not ok:
        return CheckResult(UNVERIFIABLE, reason=why)
    want = N.birthday_parts(proposed)
    have_raw = N.clean(c.raw.get(export.roles.get("birthday", ""), ""))
    have = N.birthday_parts(have_raw)
    if not want["month"]:
        return CheckResult(UNCERTAIN, have_raw, reason="Proposed birthday is not a recognisable date.",
                           reviews=[(R_SOURCE, f"Birthday '{proposed}' could not be read as a date.")])
    target = f"{want['month']}/{want['day']}" + (f"/{want['year']}" if want["year_known"] else "")
    note = "" if want["year_known"] else " The source year is a placeholder or missing, so only month/day is proposed."
    if not have["month"]:
        return CheckResult(MISSING, have_raw or "(blank)", [target], expected=[target],
                           reason=f"No birthday on the contact. Proposed: {target}.{note}")
    if (have["month"], have["day"]) != (want["month"], want["day"]):
        return CheckResult(UNCERTAIN, have_raw, expected=[target], reason="A different birthday is already on file.",
                           reviews=[(R_CONFLICT, f"Top Producer has {have_raw}; source has {proposed}. The CRM value "
                                                 "may be newer. Not overwritten.")])
    if want["year_known"] and have["year_known"] and want["year"] != have["year"]:
        return CheckResult(UNCERTAIN, have_raw, expected=[target], reason="Birth year differs.",
                           reviews=[(R_CONFLICT, f"Birth year differs: CRM {have_raw}, source {proposed}.")])
    if want["year_known"] and not have["year_known"]:
        return CheckResult(MISSING, have_raw, [want["year"]], expected=[target],
                           reason=f"Month/day match; the contact has no known birth year. Source year {want['year']}.")
    return CheckResult(PRESENT, have_raw, expected=[target], reason="Birthday already on file (month/day match)." + note)


SUPPORTED_ACTIONS = {
    "ADD_MISSING_BIRTHDAY": "birthday",
    "ADD_MISSING_PHONE": "phone",
    "ADD_MISSING_CATEGORIES": "category",
    "ADD_MISSING_CATEGORY": "category",
    "RESTORE_MISSING_NOTE_DETAIL": "note",
    "ADD_MISSING_EMAIL": "email",
}
OUT_OF_SCOPE_WORDS = ("REPLACE", "REMOVE", "DELETE", "RELABEL", "MERGE", "OVERWRITE", "PLAN", "MESSAGE")


def run_check(action: str, proposed: str, c: TPContact, export: TPExport, mapping: dict,
              known_types: set[str]) -> CheckResult:
    kind = SUPPORTED_ACTIONS.get(action.upper().strip())
    if kind == "phone":
        return check_phone(proposed, c, export)
    if kind == "category":
        return check_categories(proposed, c, export, mapping, known_types)
    if kind == "note":
        return check_note(proposed, c, export)
    if kind == "email":
        return check_email(proposed, c, export)
    if kind == "birthday":
        return check_birthday(proposed, c, export)
    why = (f"'{action}' would change or remove existing information, which is outside this tool's add-only "
           "scope." if any(w in action.upper() for w in OUT_OF_SCOPE_WORDS) else
           f"There is no automatic check for change type '{action or '(blank)'}'.")
    return CheckResult(UNCERTAIN, reason=why, reviews=[(R_UNSUPPORTED, why)])
