"""Workflow B: verify work Cowork reports as completed.

Cowork's report and this tool's independent check are kept in separate
columns. Differences between the pre- and post-edit exports are reported, not
attributed: someone else may have edited the CRM in the meantime.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import checks as C
from . import normalize as N
from .contacts import TPContact, TPExport, worklist_identity
from .loaders import Table
from .matching import MATCHED, MatchResult, resolve
from .reconcile import known_types

CLAIMS = {"DONE", "ALREADY_CORRECT"}

# Retention check outcomes (kept separate from whether the requested addition is present)
RETAINED = "Retained"
LOST = "Earlier information missing"
RETENTION_UNVERIFIABLE = "Not verifiable from export"
RETENTION_NOT_CHECKED = "Not checked"

# Addition check outcomes
ADDED_YES = "Yes"
ADDED_NO = "No"
ADDED_UNVERIFIABLE = "Not verifiable from export"
ADDED_UNCLEAR = "Unclear - needs review"

# Overall results that are not full verification
ADDED_INFO_MISSING = "ADDED_BUT_EARLIER_INFO_MISSING"
ADDED_RETENTION_UNVERIFIED = "ADDED_RETENTION_NOT_VERIFIABLE"


@dataclass
class VerifyRow:
    change_id: str
    contact_label: str
    field: str
    action: str
    proposed: str
    cowork_status: str
    cowork_after_value: str
    result: str
    reason: str
    tp_id: str = ""
    match_evidence: str = ""
    pre_value: str = ""
    post_value: str = ""
    addition_present: str = ""   # was the requested addition found after the edit?
    retention: str = ""          # RETAINED / LOST / RETENTION_UNVERIFIABLE / RETENTION_NOT_CHECKED
    retained: str = ""           # plain-language detail of the retention check
    other_changes: str = ""
    reviews: list[tuple[str, str]] = field(default_factory=list)


def _field_columns(export: TPExport, kind: str) -> set[str]:
    r = export.roles
    return set({
        "phone": r.get("phones", []) + r.get("phone_exts", []),
        "category": r.get("categories", []),
        "note": r.get("notes", []),
        "email": r.get("emails", []),
        "birthday": [r.get("birthday", "")],
    }.get(kind, []))


def _retention(kind: str, pre: TPContact | None, post: TPContact, pre_export: TPExport,
               post_export: TPExport, pre_problem: str = "") -> tuple[str, str]:
    """Were the values already in this field before the edit still there afterwards?

    Returns (status, detail). Missing export coverage is reported as not verifiable,
    never as retained.
    """
    if pre is None:
        return RETENTION_UNVERIFIABLE, ("Cannot check what was there before: " +
                                        (pre_problem or "contact not matched in the pre-edit export."))
    for label, x in (("pre-edit", pre_export), ("post-edit", post_export)):
        ok, why = x.field_available(kind)
        if not ok:
            return RETENTION_UNVERIFIABLE, f"Cannot check earlier values from the {label} export. {why}"
    lost = []
    if kind == "phone":
        post_keys = {p.key for _, p in post.phones}
        post_mains = post.phone_mains
        for _, p in pre.phones:
            if p.key not in post_keys:
                lost.append(p.raw + (" (extension changed)" if p.main in post_mains else ""))
        # The first phone column (usually the primary number) should keep its number.
        if pre.phones and post.phones and pre.phones[0][0] == post.phones[0][0] \
                and pre.phones[0][1].main != post.phones[0][1].main:
            lost.append(f"primary number in '{pre.phones[0][0]}' changed")
    elif kind == "category":
        post_set = {N.category_key(t) for t in post.categories}
        lost = [t for t in pre.categories if N.category_key(t) not in post_set]
    elif kind == "email":
        lost = [e for e in pre.emails if N.email_key(e) not in post.email_keys]
    elif kind == "note":
        if N.note_tokens(pre.notes) and not N.contains_sequence(N.note_tokens(pre.notes), N.note_tokens(post.notes)):
            lost = ["earlier note text is no longer contained in the notes"]
    elif kind == "birthday":
        col = pre_export.roles.get("birthday", "")
        before = N.birthday_parts(pre.raw.get(col, ""))
        after = N.birthday_parts(post.raw.get(post_export.roles.get("birthday", ""), ""))
        if before["month"] and (before["month"], before["day"]) != (after["month"], after["day"]):
            lost = [f"birthday {before['raw']} changed to {after['raw'] or '(blank)'}"]
    else:
        return RETENTION_NOT_CHECKED, "No retention check exists for this change type."
    if lost:
        return LOST, "Missing after the edit: " + "; ".join(lost)
    return RETAINED, "All values that were in this field before the edit are still there."


def _other_changes(pre: TPContact | None, post: TPContact, export: TPExport, kind: str) -> str:
    if pre is None:
        return ""
    skip = _field_columns(export, kind) | {export.roles.get("modified", "")}
    changed = [h for h in post.raw if h not in skip and h in pre.raw
               and N.clean(pre.raw.get(h)) != N.clean(post.raw.get(h))]
    if not changed:
        return ""
    return ("Other fields also differ: " + ", ".join(changed[:12]) + (" …" if len(changed) > 12 else "")
            + ". Cause unknown; another user may have edited the contact.")


def verify(worklist: Table, wl_roles: dict, pre: TPExport, post: TPExport, mapping: dict,
           confirmed: dict | None = None) -> list[VerifyRow]:
    confirmed = confirmed or {}
    g = lambda row, role: N.clean(row.get(wl_roles.get(role, ""), "")) if wl_roles.get(role) else ""  # noqa
    types = known_types(post, mapping) | known_types(pre, mapping)
    cache: dict[str, tuple[MatchResult, MatchResult]] = {}
    out = []
    for row in worklist.rows:
        ident = worklist_identity(row, wl_roles)
        cid, action = g(row, "change_id"), g(row, "action")
        status = g(row, "status").upper()
        proposed = row.get(wl_roles.get("proposed", ""), "") if wl_roles.get("proposed") else ""
        kind = C.SUPPORTED_ACTIONS.get(action.upper(), "")
        v = VerifyRow(change_id=cid, contact_label=ident.label, field=g(row, "field") or action, action=action,
                      proposed=proposed, cowork_status=status or "(blank)",
                      cowork_after_value=g(row, "verified_after"), result="", reason="")
        if status not in CLAIMS:
            v.result = "NO_COMPLETION_CLAIMED"
            v.reason = f"Cowork status is {status or 'blank'}; nothing to verify yet."
            out.append(v)
            continue
        if ident.key not in cache:
            conf = confirmed.get(ident.key, {})
            known = ident.tp_id or (conf.get("TP_ID") if conf.get("Decision") == "CONFIRMED" else "")
            src = "TP_ID column in completed worklist" if ident.tp_id else "confirmed match"
            ignore = {N.parse_phone(p).main for p in N.split_phones(proposed)} if kind == "phone" else set()
            cache[ident.key] = (resolve(ident, post, known, src, ignore), resolve(ident, pre, known, src, ignore))
        m_post, m_pre = cache[ident.key]
        if m_post.status != MATCHED:
            v.result = "MATCH_REVIEW" if m_post.status == "MATCH_REVIEW" else "NOT_FOUND"
            v.reason = "Post-edit export: " + m_post.reason
            v.addition_present, v.retention = "Not checked (contact not confirmed)", RETENTION_NOT_CHECKED
            v.reviews.append((C.R_DUP_ID if m_post.duplicate_id else C.R_MATCH, v.reason))
            out.append(v)
            continue
        post_c = m_post.contact
        pre_c = m_pre.contact if m_pre.status == MATCHED else None
        pre_problem = "" if pre_c is not None else m_pre.reason
        if post_c.tp_id and post_c.tp_id in pre.duplicate_ids:
            pre_c, pre_problem = None, pre.duplicate_id_reason(post_c.tp_id)
        elif pre_c is not None and post_c.tp_id and pre_c.tp_id and pre_c.tp_id != post_c.tp_id:
            pre_c = pre.by_id.get(post_c.tp_id)
            pre_problem = "" if pre_c else f"contact ID {post_c.tp_id} is not in the pre-edit export."
        v.tp_id, v.match_evidence = post_c.tp_id, m_post.evidence
        if not kind:
            v.addition_present, v.retention = ADDED_UNVERIFIABLE, RETENTION_NOT_CHECKED
            v.result = "NOT_VERIFIABLE_FROM_EXPORT"
            v.reason = f"No automatic check exists for change type '{action}'."
            v.reviews.append((C.R_UNSUPPORTED, v.reason))
            out.append(v)
            continue
        res_post = C.run_check(action, proposed, post_c, post, mapping, types)
        v.post_value = res_post.current
        if pre_c is not None and pre.field_available(kind)[0]:
            v.pre_value = C.run_check(action, proposed, pre_c, pre, mapping, types).current
        v.addition_present = {C.PRESENT: ADDED_YES, C.MISSING: ADDED_NO, C.PARTIAL: ADDED_NO,
                              C.UNVERIFIABLE: ADDED_UNVERIFIABLE}.get(res_post.state, ADDED_UNCLEAR)
        v.retention, v.retained = _retention(kind, pre_c, post_c, pre, post, pre_problem)
        if res_post.state == C.UNVERIFIABLE:
            v.result = "NOT_VERIFIABLE_FROM_EXPORT"
            v.reason = "Post-edit export: " + res_post.reason
            v.reviews.append((C.R_UNSUPPORTED, v.reason))
        elif res_post.state == C.PRESENT and v.retention == RETAINED:
            v.result = "VERIFIED_COMPLETE"
            v.reason = ("The post-edit export shows the requested addition on the matched contact, and the "
                        "earlier values in this field are still there.")
            if pre_c is not None and status == "DONE" and \
                    C.run_check(action, proposed, pre_c, pre, mapping, types).state == C.PRESENT:
                v.reason += " It was already present in the pre-edit export."
        elif res_post.state == C.PRESENT and v.retention == LOST:
            v.result = ADDED_INFO_MISSING
            v.reason = ("The requested addition is present, but information that was in this field before is "
                        f"missing now. {v.retained}. The exports cannot show who removed it (Cowork or another "
                        "user); check the contact before closing this item.")
            v.reviews.append((C.R_RETENTION, v.reason))
        elif res_post.state == C.PRESENT:
            v.result = ADDED_RETENTION_UNVERIFIED
            v.reason = ("The requested addition is present, but whether earlier information was kept cannot be "
                        f"checked. {v.retained} Not counted as fully verified.")
            v.reviews.append((C.R_UNSUPPORTED, v.reason))
        elif res_post.state in (C.MISSING, C.PARTIAL):
            v.result = "EXPECTED_CHANGE_NOT_FOUND"
            v.reason = "Still missing after the edit: " + ", ".join(res_post.still_missing)
            v.reviews.append((C.R_CONFLICT, f"Cowork reported {status}, but the post-edit export does not show "
                                            "it: " + ", ".join(res_post.still_missing)))
        else:
            v.result = "NEEDS_REVIEW"
            v.reason = res_post.reason
            v.reviews.extend(res_post.reviews)
        if v.retention == LOST and v.result != ADDED_INFO_MISSING:
            v.reviews.append((C.R_RETENTION, v.retained))
        v.other_changes = _other_changes(pre_c, post_c, post, kind)
        out.append(v)
    return out


def verification_counts(rows: list[VerifyRow]) -> dict:
    from collections import Counter
    c = Counter(r.result for r in rows)
    return {"Completion claims checked": sum(1 for r in rows if r.result != "NO_COMPLETION_CLAIMED"),
            "Verified completed": c["VERIFIED_COMPLETE"],
            "Added, earlier info missing": c[ADDED_INFO_MISSING],
            "Added, retention not verifiable": c[ADDED_RETENTION_UNVERIFIED],
            "Expected change not found": c["EXPECTED_CHANGE_NOT_FOUND"],
            "Not verifiable": c["NOT_VERIFIABLE_FROM_EXPORT"],
            "Match problems": c["MATCH_REVIEW"] + c["NOT_FOUND"],
            "Needs review": c["NEEDS_REVIEW"],
            "No completion claimed": c["NO_COMPLETION_CLAIMED"],
            "Retention problems": sum(1 for r in rows if r.retention == LOST),
            "by_result": dict(c)}
