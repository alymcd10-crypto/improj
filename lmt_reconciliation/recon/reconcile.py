"""Workflow A: check what still needs correction."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import checks as C
from . import normalize as N
from .contacts import TPExport, worklist_identity
from .loaders import Table
from .matching import MATCH_REVIEW, MATCHED, NOT_FOUND, MatchResult, resolve, suspected_duplicates

ASSESSMENTS = {
    "ALREADY_CORRECT": "The current export already shows the proposed information on the matched contact. "
                       "Nothing to add.",
    "MISSING_IN_CURRENT_EXPORT": "The contact was matched and the proposed information is not in the export. "
                                 "It is a candidate for Cowork, who must still check the live record before "
                                 "writing.",
    "CONFLICT_REVIEW": "The export shows something that conflicts with the proposal (for example a newer "
                       "change, an opt-out, or a partly different value). A person decides.",
    "MATCH_REVIEW": "The contact could not be matched with confidence (several candidates, name only, or an "
                    "ID that points to a different name). A person confirms the right contact.",
    "NOT_FOUND": "No Top Producer contact in this export matches, or a stored ID is not in the export.",
    "NOT_VERIFIABLE_FROM_EXPORT": "The export does not include the field needed to check this item. This is "
                                  "not treated as blank or as a failure.",
    "NEEDS_REVIEW": "The item cannot be checked automatically (review-only item, unmapped category, unclear "
                    "phone digits or extension, unsupported change type, or missing source detail).",
    "VERIFIED_COMPLETE": "After the edit, the export independently shows the intended result on the right "
                         "contact.",
    "EXPECTED_CHANGE_NOT_FOUND": "Cowork reported the change, but the post-edit export does not show it on "
                                 "the matched contact.",
}

ELIGIBLE_STATUSES = {"", "PENDING_CURRENT_CHECK", "IN_PROGRESS"}
CLOSED_STATUSES = {"DONE", "ALREADY_CORRECT"}
HELD_STATUSES = {"HOLD_REVIEW", "NOT_FOUND", "MULTIPLE", "ERROR"}

RECON_COLUMNS = ["Recon_Assessment", "Recon_Reason", "Recon_Still_Missing", "Recon_Current_Value_In_Export",
                 "Recon_Matched_TP_ID", "Recon_Match_Basis", "Recon_Match_Evidence", "Recon_Run_ID",
                 "Recon_Export_File"]


@dataclass
class RowResult:
    index: int                      # position in the worklist
    source_line: int
    row: dict                       # original worklist row, untouched
    change_id: str
    contact_key: str
    contact_label: str
    field: str
    action: str
    source_value: str
    proposed: str
    queue: str
    cowork_status: str
    match: MatchResult
    assessment: str = ""
    reason: str = ""
    current_value: str = ""
    still_missing: list[str] = field(default_factory=list)
    reviews: list[tuple[str, str]] = field(default_factory=list)
    eligible: bool = False
    phone_count: int = 0


@dataclass
class ReconResult:
    rows: list[RowResult]
    extra_reviews: list[dict]       # reviews not tied to a worklist row (duplicates, etc.)
    warnings: list[str]
    worklist_headers: list[str]
    roles: dict

    def counts(self) -> dict:
        from collections import Counter
        a = Counter(r.assessment for r in self.rows)
        return {
            "Unique contacts": len({r.contact_key for r in self.rows}),
            "Change items checked": len(self.rows),
            "Phone numbers proposed": sum(r.phone_count for r in self.rows),
            "Already correct": a["ALREADY_CORRECT"],
            "Missing proposed additions": a["MISSING_IN_CURRENT_EXPORT"],
            "Ready for Cowork": sum(1 for r in self.rows if r.eligible),
            "Review needed": sum(1 for r in self.rows if r.reviews or r.assessment in (
                "CONFLICT_REVIEW", "MATCH_REVIEW", "NOT_FOUND", "NEEDS_REVIEW")),
            "Not verifiable": a["NOT_VERIFIABLE_FROM_EXPORT"],
            "by_assessment": dict(a),
        }


def _state_to_assessment(res: C.CheckResult) -> str:
    if res.state == C.PRESENT:
        return "ALREADY_CORRECT"
    if res.state in (C.MISSING, C.PARTIAL):
        return "MISSING_IN_CURRENT_EXPORT"
    if res.state == C.UNVERIFIABLE:
        return "NOT_VERIFIABLE_FROM_EXPORT"
    return "CONFLICT_REVIEW" if any(cat == C.R_CONFLICT for cat, _ in res.reviews) else "NEEDS_REVIEW"


def known_types(export: TPExport, mapping: dict) -> set[str]:
    types = {N.category_key(t) for c in export.contacts for t in c.categories}
    return types


def reconcile(worklist: Table, wl_roles: dict, export: TPExport, mapping: dict,
              confirmed: dict[str, dict] | None = None, prior_ids: dict[str, tuple[str, str]] | None = None,
              verified_before: dict[str, str] | None = None, holds: dict[str, str] | None = None
              ) -> ReconResult:
    confirmed = confirmed or {}
    prior_ids = prior_ids or {}
    verified_before = verified_before or {}
    holds = holds or {}
    warnings: list[str] = []
    types = known_types(export, mapping)
    g = lambda row, role: N.clean(row.get(wl_roles.get(role, ""), "")) if wl_roles.get(role) else ""  # noqa

    if not wl_roles.get("queue"):
        warnings.append("The worklist has no Queue column. Every row is treated as needing review, because "
                        "REVIEW_ONLY items cannot be told apart.")

    # Group rows by contact so each contact is resolved once.
    identities, phones_by_key, ids_by_key = {}, {}, {}
    for row in worklist.rows:
        ident = worklist_identity(row, wl_roles)
        kept = identities.setdefault(ident.key, ident)
        kept.change_ids.append(g(row, "change_id"))
        if ident.tp_id:
            ids_by_key.setdefault(ident.key, set()).add(ident.tp_id)
            kept.tp_id = kept.tp_id or ident.tp_id
        if g(row, "action").upper() == "ADD_MISSING_PHONE":
            phones_by_key.setdefault(ident.key, set()).update(
                N.parse_phone(p).main for p in N.split_phones(g(row, "proposed")))
    matches: dict[str, MatchResult] = {}
    for key, ident in identities.items():
        known, source = "", ""
        conf = confirmed.get(key)
        if conf and conf.get("Decision") == "CONFIRMED":
            known, source = conf["TP_ID"], f"confirmed by {conf.get('Decided_By')} on {conf.get('Decided_At_UTC')}"
        if not known and ident.tp_id:
            known, source = ident.tp_id, "TP_ID column in worklist"
        if not known:
            for cid in ident.change_ids:
                if cid in prior_ids:
                    known, source = prior_ids[cid]
        m = resolve(ident, export, known, source, ignore_phones=phones_by_key.get(key, set()))
        if len(ids_by_key.get(key, ())) > 1:
            m = MatchResult(MATCH_REVIEW, reason="Rows for this contact carry different TP_IDs: "
                            + ", ".join(sorted(ids_by_key[key])), candidates=sorted(ids_by_key[key]))
        if conf and conf.get("Decision") == "REJECTED" and m.status == MATCHED and m.tp_id == conf["TP_ID"] \
                and not ident.tp_id:
            m = MatchResult(MATCH_REVIEW, m.contact, m.basis, m.evidence,
                            f"A person previously rejected {m.tp_id} for this contact.", [m.tp_id])
        matches[key] = m

    results: list[RowResult] = []
    seen_ids: dict[str, int] = {}
    for i, (row, line) in enumerate(zip(worklist.rows, worklist.row_numbers)):
        ident = worklist_identity(row, wl_roles)
        cid = g(row, "change_id")
        action = g(row, "action")
        proposed = row.get(wl_roles.get("proposed", ""), "") if wl_roles.get("proposed") else ""
        r = RowResult(index=i, source_line=line, row=row, change_id=cid, contact_key=ident.key,
                      contact_label=ident.label, field=g(row, "field") or action, action=action,
                      source_value=g(row, "source_value"), proposed=proposed, queue=g(row, "queue").upper(),
                      cowork_status=g(row, "status").upper(), match=matches[ident.key])
        if action.upper() == "ADD_MISSING_PHONE":
            r.phone_count = len(N.split_phones(proposed))
        if not cid:
            r.reviews.append((C.R_SOURCE, f"Line {line}: no Change_ID. Rows without an ID cannot be tracked."))
        elif cid in seen_ids:
            r.reviews.append((C.R_SOURCE, f"Change_ID {cid} is repeated (also line {seen_ids[cid]})."))
        else:
            seen_ids[cid] = line

        m = r.match
        if m.status != MATCHED:
            r.assessment = "MATCH_REVIEW" if m.status == MATCH_REVIEW else "NOT_FOUND"
            r.reason = m.reason
            r.reviews.append((C.R_MATCH if m.status == MATCH_REVIEW else C.R_NOT_FOUND,
                              m.reason + (" Candidates: " + "; ".join(m.candidates) if m.candidates else "")))
        else:
            res = C.run_check(action, proposed, m.contact, export, mapping, types)
            r.assessment = _state_to_assessment(res)
            r.reason = res.reason
            r.current_value = res.current
            r.still_missing = res.still_missing
            r.reviews.extend(res.reviews)
            if m.contact.modified and r.assessment == "CONFLICT_REVIEW":
                r.reason += (f" (Contact last modified {m.contact.modified}; that date alone does not show which "
                             "field changed.)")

        # ---- execution-status and queue rules (never overwritten, only read)
        if wl_roles.get("queue") and r.queue == "REVIEW_ONLY":
            r.reviews.append((C.R_REVIEW_ONLY, "Marked REVIEW_ONLY in the worklist; never sent to Cowork "
                                               "automatically." + (" The export already shows it."
                                                                   if r.assessment == "ALREADY_CORRECT" else "")))
            if r.assessment == "MISSING_IN_CURRENT_EXPORT":
                r.assessment = "NEEDS_REVIEW"
        elif wl_roles.get("queue") and r.queue != "CORRECTION_CANDIDATE":
            r.reviews.append((C.R_SOURCE, f"Queue value '{r.queue or '(blank)'}' is not CORRECTION_CANDIDATE."))
        elif not wl_roles.get("queue"):
            r.reviews.append((C.R_SOURCE, "No Queue column; cannot confirm this is a correction candidate."))
        if r.cowork_status in CLOSED_STATUSES and r.assessment == "MISSING_IN_CURRENT_EXPORT":
            r.reviews.append((C.R_COWORK, f"Cowork marked this {r.cowork_status}, but this export does not "
                                          "show it. Check the export date, or verify on the Verify page."))
        elif r.cowork_status in HELD_STATUSES:
            note = g(row, "result_note")
            r.reviews.append((C.R_COWORK, f"Cowork status {r.cowork_status}" + (f": {note}" if note else "")))
        if cid in verified_before and r.assessment == "MISSING_IN_CURRENT_EXPORT":
            r.reviews.append((C.R_CONFLICT, f"Verified complete in run {verified_before[cid]}, but missing now. "
                                            "It may have been changed since; not re-queued automatically."))
        if cid in holds:
            r.reviews.append((C.R_CONFLICT, f"On the hold list: {holds[cid] or 'no reason given'}"))

        blocking = [x for x in r.reviews if x[0] not in (C.R_CATEGORY,)]
        r.eligible = (
            r.assessment == "MISSING_IN_CURRENT_EXPORT" and m.status == MATCHED and r.queue == "CORRECTION_CANDIDATE"
            and r.cowork_status in ELIGIBLE_STATUSES and cid not in verified_before and cid not in holds
            and bool(cid) and not blocking)
        results.append(r)

    # ---- review items not tied to one worklist row
    extra = []
    for d in suspected_duplicates(export):
        a, b = d["a"], d["b"]
        extra.append({"Review_Category": C.R_DUPLICATE, "Contact": a.label,
                      "TP_IDs": f"{a.tp_id or 'row ' + str(a.row_number)} / {b.tp_id or 'row ' + str(b.row_number)}",
                      "Reason": f"Two Top Producer profiles share the same name and {', '.join(d['shared'])}. "
                                "Could be a duplicate, or two people (e.g. parent and child). Review before merging."})
    return ReconResult(rows=results, extra_reviews=extra, warnings=warnings + export.warnings,
                       worklist_headers=worklist.headers, roles=wl_roles)
