"""Resolve a worklist contact to exactly one Top Producer contact, or explain why not.

Rules implemented (see README "How contacts are matched"):
  * A previously verified ID is preferred, but its name is still checked.
  * Otherwise a name/household or business name match PLUS a corroborating
    email, phone or address is required. Name alone never matches.
  * Shared household identifiers corroborate but do not identify on their own.
  * Fuzzy names and identifier-only hits are suggestions for review, never matches.
  * More than one plausible candidate always goes to review.
  * A master worksheet row number is never treated as a contact ID.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from . import normalize as N
from .contacts import TPContact, TPExport, WorklistIdentity

MATCHED = "MATCHED"
MATCH_REVIEW = "MATCH_REVIEW"
NOT_FOUND = "NOT_FOUND"

BASIS_VERIFIED_ID = "Previously verified contact ID"
BASIS_RULE = "Name + corroborating contact details (software match; Cowork re-checks identity live)"


@dataclass
class MatchResult:
    status: str
    contact: TPContact | None = None
    basis: str = ""
    evidence: str = ""
    reason: str = ""
    candidates: list[str] = field(default_factory=list)  # "TP_ID (name)" strings for review
    duplicate_id: str = ""                               # set when a TP ID used here is on several rows

    @property
    def tp_id(self) -> str:
        return self.contact.tp_id if self.contact else ""


def _name_strength(w: WorklistIdentity, c: TPContact) -> str:
    """'strong' | 'household' | 'business' | 'fuzzy' | ''"""
    tp_pairs = {(p.first_key, p.last_key) for p in c.persons}
    tp_lasts = {p.last_key for p in c.persons if p.last_key}
    tp_firsts = {p.first_key for p in c.persons if p.first_key}
    for p in w.persons:
        if p.first_key and p.last_key and (p.first_key, p.last_key) in tp_pairs:
            return "strong"
        # Spouse stored with a different last name on the shared profile.
        if p.first_key and p.last_key in tp_lasts and p.first_key in tp_firsts:
            return "strong"
    w_company = N.name_key(w.company)
    tp_company_keys = {N.name_key(c.company)} - {""}
    if not tp_firsts:
        tp_company_keys |= tp_lasts
    if w_company and w_company in tp_company_keys:
        return "business"
    if not any(p.first_key for p in w.persons):
        w_lasts = {p.last_key for p in w.persons if p.last_key}
        if w_lasts & tp_company_keys:
            return "business"
        if w_lasts & tp_lasts:
            return "household"
    for p in w.persons:
        for q in c.persons:
            if not (p.last_key and q.last_key):
                continue
            last_sim = difflib.SequenceMatcher(None, p.last_key, q.last_key).ratio()
            first_sim = difflib.SequenceMatcher(None, p.first_key, q.first_key).ratio() \
                if p.first_key and q.first_key else 0
            if (last_sim == 1 and first_sim >= 0.6) or (last_sim >= 0.85 and first_sim >= 0.85) \
                    or (last_sim == 1 and p.first_key and q.first_key
                        and (p.first_key.startswith(q.first_key) or q.first_key.startswith(p.first_key))):
                return "fuzzy"
    return ""


def _corroboration(w: WorklistIdentity, c: TPContact, export: TPExport) -> tuple[list[str], list[str]]:
    """Return (unique_hits, shared_hits) as readable evidence strings."""
    unique, shared = [], []
    for e in w.email_keys & c.email_keys:
        (shared if len(export.by_email.get(e, [])) > 1 else unique).append(f"email {e}")
    for m in w.phone_mains & c.phone_mains:
        (shared if len(export.by_phone.get(m, [])) > 1 else unique).append(f"phone ending {m[-4:]}")
    if w.address_key and w.address_key == c.address_key:
        (shared if len(export.by_address.get(c.address_key, [])) > 1 else unique).append("street address")
    return unique, shared


def _conflicts(w: WorklistIdentity, c: TPContact, ignore_phones: set[str]) -> list[str]:
    conflicts = []
    if w.email_keys and c.email_keys and not (w.email_keys & c.email_keys):
        conflicts.append("email")
    wp = w.phone_mains - ignore_phones
    if wp and c.phone_mains and not (wp & c.phone_mains):
        conflicts.append("phone")
    return conflicts


def _cand(c: TPContact) -> str:
    return f"{c.tp_id or 'row ' + str(c.row_number)} ({c.label})"


def resolve(w: WorklistIdentity, export: TPExport, known_id: str = "", known_id_source: str = "",
            ignore_phones: set[str] | None = None) -> MatchResult:
    """Resolve, then hold any result that relies on a Top Producer ID carried by several export rows."""
    tp_id = known_id or w.tp_id
    if tp_id and tp_id in export.duplicate_ids:
        return MatchResult(MATCH_REVIEW, reason=export.duplicate_id_reason(tp_id),
                           candidates=[_cand(c) for c in export.duplicate_ids[tp_id]], duplicate_id=tp_id)
    m = _resolve(w, export, known_id, known_id_source, ignore_phones)
    if m.contact is not None and m.contact.tp_id in export.duplicate_ids:
        # Found through name + email/phone/address, but that profile's ID is on several rows.
        dup = m.contact.tp_id
        return MatchResult(MATCH_REVIEW, reason=export.duplicate_id_reason(dup) + (
                               f" (Found via {m.evidence}.)" if m.evidence else ""),
                           candidates=[_cand(c) for c in export.duplicate_ids[dup]], duplicate_id=dup)
    dup = next((c.split(" ")[0] for c in m.candidates if c.split(" ")[0] in export.duplicate_ids), "")
    if dup:  # already in review; keep its candidates and add the duplicate-ID warning
        m.reason += " " + export.duplicate_id_reason(dup)
        m.duplicate_id = dup
    return m


def _resolve(w: WorklistIdentity, export: TPExport, known_id: str = "", known_id_source: str = "",
             ignore_phones: set[str] | None = None) -> MatchResult:
    ignore_phones = ignore_phones or set()
    tp_id = known_id or w.tp_id
    id_source = known_id_source or ("TP_ID column in worklist" if w.tp_id else "")

    # ---- 1. previously verified ID
    if tp_id:
        c = export.by_id.get(tp_id)
        if c is None:
            hint = ""
            if tp_id.isdigit() and len(tp_id) <= 6 and export.by_id and not any(
                    k.isdigit() and len(k) <= 6 for k in list(export.by_id)[:50]):
                hint = " It looks like a worksheet row number, which is not a contact ID."
            if export.field_status.get("id") != "present":
                return MatchResult(MATCH_REVIEW, reason=f"Worklist has ID {tp_id} ({id_source}) but this "
                                   "export has no ID column, so it cannot be checked.")
            return MatchResult(NOT_FOUND, reason=f"Contact ID {tp_id} ({id_source}) is not in this export. "
                               f"The contact may have been merged or deleted, or the export is filtered.{hint}")
        strength = _name_strength(w, c)
        unique, shared = _corroboration(w, c, export)
        conflicts = _conflicts(w, c, ignore_phones)
        evidence = f"ID {tp_id} from {id_source}; name match: {strength or 'none'}"
        if unique or shared:
            evidence += "; also matches " + ", ".join(unique + shared)
        if strength in ("strong", "business", "household"):
            if len(conflicts) == 2 and not (unique or shared):
                return MatchResult(MATCH_REVIEW, c, BASIS_VERIFIED_ID, evidence,
                                   "Verified ID found, but its email and phone both differ from the worklist.",
                                   [_cand(c)])
            return MatchResult(MATCHED, c, BASIS_VERIFIED_ID, evidence)
        return MatchResult(MATCH_REVIEW, c, BASIS_VERIFIED_ID, evidence,
                           f"Verified ID points to '{c.label}', whose name does not match the worklist "
                           f"contact '{w.label}'. Confirm identity before using this ID.", [_cand(c)])

    # ---- 2. name + corroboration
    pool: dict[int, TPContact] = {}
    for p in w.persons:
        for c in export.by_last.get(p.last_key, []):
            pool[id(c)] = c
        for c in export.by_company.get(p.last_key, []):
            pool[id(c)] = c
    for c in export.by_company.get(N.name_key(w.company), []):
        pool[id(c)] = c
    # identifier hits (for fuzzy / identifier-only suggestions)
    id_hits: dict[int, TPContact] = {}
    for e in w.email_keys:
        for c in export.by_email.get(e, []):
            id_hits[id(c)] = c
    for m in w.phone_mains:
        for c in export.by_phone.get(m, []):
            id_hits[id(c)] = c
    if w.address_key:
        for c in export.by_address.get(w.address_key, []):
            id_hits[id(c)] = c

    solid, name_only, fuzzy = [], [], []
    for c in {**pool, **id_hits}.values():
        strength = _name_strength(w, c)
        unique, shared = _corroboration(w, c, export)
        if strength in ("strong", "business", "household"):
            if unique or shared:
                solid.append((c, strength, unique, shared))
            else:
                name_only.append(c)
        elif strength == "fuzzy" or (unique or shared):
            fuzzy.append((c, strength, unique, shared))

    if len(solid) == 1:
        c, strength, unique, shared = solid[0]
        evidence = f"{strength} name match + " + ", ".join(unique + shared)
        if strength == "household":
            return MatchResult(MATCH_REVIEW, c, BASIS_RULE, evidence,
                               "Only the family name matches (no first name to compare).", [_cand(c)])
        conflicts = _conflicts(w, c, ignore_phones)
        if not unique and shared:
            evidence += " (shared household detail)"
        if len(conflicts) == 2:
            return MatchResult(MATCH_REVIEW, c, BASIS_RULE, evidence,
                               "Name and one detail match, but both email and phone differ.", [_cand(c)])
        return MatchResult(MATCHED, c, BASIS_RULE, evidence)
    if len(solid) > 1:
        return MatchResult(MATCH_REVIEW, reason=f"{len(solid)} Top Producer contacts match the name and "
                           "share contact details. Pick the right one.",
                           candidates=[_cand(c) for c, *_ in solid])
    if fuzzy:
        return MatchResult(MATCH_REVIEW, reason="Possible match only: similar name or matching contact details "
                           "on a differently named profile. Not matched automatically.",
                           candidates=[f"{_cand(c)} — " + ", ".join(
                               ([f"{s} name"] if s else ["different name"]) + u + sh) for c, s, u, sh in fuzzy])
    if name_only:
        return MatchResult(MATCH_REVIEW, reason="Name found, but no email, phone or address matches. "
                           "Name alone is not enough to match.", candidates=[_cand(c) for c in name_only])
    return MatchResult(NOT_FOUND, reason="No Top Producer contact matches this name and contact details.")


def suspected_duplicates(export: TPExport) -> list[dict]:
    """TP contacts that share a full name and a contact detail (for human review only)."""
    groups: dict[tuple, list[TPContact]] = {}
    for c in export.contacts:
        for p in c.persons[:1]:
            if p.first_key and p.last_key:
                groups.setdefault((p.first_key, p.last_key), []).append(c)
        if not c.persons and c.company:
            groups.setdefault(("", N.name_key(c.company)), []).append(c)
    out = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                shared = []
                if a.email_keys & b.email_keys:
                    shared.append("email")
                if a.phone_mains & b.phone_mains:
                    shared.append("phone")
                if a.address_key and a.address_key == b.address_key:
                    shared.append("address")
                if shared:
                    out.append({"a": a, "b": b, "shared": shared})
    return out
