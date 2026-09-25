"""Optional historical sources: master workbook, audit workbook, earlier corrections.

These give context and review items. They never create corrections by themselves.
"""
from __future__ import annotations

from collections import defaultdict

from . import normalize as N
from .checks import R_DUPLICATE, R_MATCH
from .columns import detect_tp_columns, detect_worklist_columns
from .loaders import LoadedFile, Table

RESOLVED_WORDS = ("resolved", "closed", "done", "verified", "complete", "no action", "confirmed")


def _row_key(row: dict, roles: dict) -> str:
    g = lambda r: N.clean(row.get(roles.get(r) or "", ""))  # noqa: E731
    name = N.name_key(g("first")) + "." + N.name_key(g("last"))
    if name == ".":
        name = N.name_key(g("display_name")) or N.name_key(g("company"))
    if not name or name == ".":
        return ""
    ident = ""
    for h in roles.get("emails", []):
        e = N.split_emails(row.get(h, ""))
        if e:
            ident = N.email_key(e[0])
            break
    if not ident:
        for h in roles.get("phones", []):
            p = N.split_phones(row.get(h, ""))
            if p:
                ident = N.parse_phone(p[0]).main
                break
    return f"{name}|{ident}"


def analyze_master(lf: LoadedFile) -> tuple[dict, list[dict]]:
    """Distinguish overlapping tab rows from suspected duplicate contacts."""
    tables = [t for t in lf.tables if t.rows]
    if not tables:
        return {"note": "No data rows found."}, []
    all_tab = next((t for t in tables if "allcontacts" in N.header_key(t.sheet)), None) or \
        next((t for t in tables if "all" in N.fold(t.sheet)), None)
    summary = {"Sheets": [{"Sheet": t.sheet, "Header row": t.header_row, "Data rows": len(t.rows),
                           "Columns": len(t.headers)} for t in lf.tables]}
    reviews = []
    if all_tab is None:
        summary["note"] = "No 'All Contacts' sheet found; tab overlap was not analysed."
        return summary, reviews
    roles_all = detect_tp_columns(all_tab.headers)
    seen: dict[str, list[int]] = defaultdict(list)
    for row, rn in zip(all_tab.rows, all_tab.row_numbers):
        k = _row_key(row, roles_all)
        if k:
            seen[k].append(rn)
    for k, rows in seen.items():
        if len(rows) > 1:
            reviews.append({"Review_Category": R_DUPLICATE, "Contact": k.split("|")[0].replace(".", " ").strip(),
                            "Source_Reference": f"{all_tab.sheet} rows {', '.join(map(str, rows))}",
                            "Reason": "Same name and contact detail appear more than once on the All Contacts "
                                      "sheet. Suspected duplicate; review before acting."})
    overlap = not_in_all = 0
    for t in tables:
        if t is all_tab:
            continue
        roles = detect_tp_columns(t.headers)
        has_name = roles.get("last") or roles.get("display_name") or roles.get("company")
        if not (has_name and (roles.get("emails") or roles.get("phones"))):
            summary.setdefault("Sheets not treated as contact tabs", []).append(t.sheet)
            continue
        for row, rn in zip(t.rows, t.row_numbers):
            k = _row_key(row, roles)
            if not k:
                continue
            if k in seen:
                overlap += 1
            else:
                not_in_all += 1
                reviews.append({"Review_Category": R_MATCH, "Contact": k.split("|")[0].replace(".", " ").strip(),
                                "Source_Reference": f"{t.sheet} row {rn} (worksheet row, not a contact ID)",
                                "Reason": "Row on an agent/category tab has no matching row on All Contacts."})
    summary.update({"All Contacts sheet": all_tab.sheet, "All Contacts rows": len(all_tab.rows),
                    "Unique contacts on All Contacts": len(seen),
                    "Tab rows that repeat an All Contacts row (not duplicates)": overlap,
                    "Tab rows not on All Contacts": not_in_all})
    return summary, reviews


def analyze_audit(lf: LoadedFile) -> tuple[dict, list[dict]]:
    """List sheets and keep unresolved identity findings visible (no corrections invented)."""
    summary = {"Sheets": [{"Sheet": t.sheet, "Header row": t.header_row, "Data rows": len(t.rows),
                           "Columns": len(t.headers)} for t in lf.tables]}
    reviews = []
    for t in lf.tables:
        if "identity" not in N.fold(t.sheet):
            continue
        status_col = next((h for h in t.headers if N.header_key(h) in (
            "status", "resolution", "decision", "outcome", "reviewstatus")), "")
        open_count = 0
        for row, rn in zip(t.rows, t.row_numbers):
            status = N.fold(row.get(status_col, "")) if status_col else ""
            if status and any(w in status for w in RESOLVED_WORDS):
                continue
            open_count += 1
            detail = " | ".join(N.clean(v) for v in list(row.values())[:5] if N.clean(v))
            reviews.append({"Review_Category": R_MATCH, "Contact": "",
                            "Source_Reference": f"{lf.filename} › {t.sheet} row {rn}",
                            "Reason": "Unresolved identity finding from the audit (kept visible; no correction "
                                      "is generated for it).", "Detail": detail[:300]})
        summary[f"Open identity findings ({t.sheet})"] = open_count
    return summary, reviews


def prior_verified_ids(table: Table, filename: str) -> tuple[dict[str, tuple[str, str]], str]:
    """Change_ID -> (TP_ID, provenance) from an earlier corrections file, if it has both columns."""
    roles = detect_worklist_columns(table.headers)
    tp_col = roles.get("tp_id") or detect_tp_columns(table.headers).get("tp_id")
    if not roles.get("change_id") or not tp_col:
        return {}, (f"{filename}: no Change_ID and contact ID columns together, so it was inspected only and not "
                    "used for matching.")
    out = {}
    for row, rn in zip(table.rows, table.row_numbers):
        cid, tp = N.clean(row.get(roles["change_id"])), N.clean(row.get(tp_col))
        if cid and tp:
            out[cid] = (tp, f"{filename} line {rn}")
    return out, f"{filename}: {len(out)} earlier contact IDs available (identity is still re-checked)."
