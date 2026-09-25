"""Build and save output files for a run. Every run gets its own folder."""
from __future__ import annotations

import csv
import io
import json
import platform
from pathlib import Path

from . import store
from .normalize import spreadsheet_safe
from .reconcile import ASSESSMENTS, RECON_COLUMNS, ReconResult
from .verify import VerifyRow, verification_counts

TOOL_VERSION = "1.0.0"
WORKLIST_LABEL = "COWORK WORKLIST - NOT A TOP PRODUCER IMPORT FILE"
REVIEW_COLUMNS = ["Review_Category", "Change_ID", "Contact", "TP_IDs", "Field", "Source_Value",
                  "Current_TP_Value", "Proposed_Addition", "Assessment", "Reason", "Source_Reference", "Detail"]
VERIFY_COLUMNS = ["Change_ID", "Contact", "Field", "Action", "Proposed_Addition", "Cowork_Reported_Status",
                  "Cowork_Verified_After_Value", "Independent_Result", "Requested_Addition_Present",
                  "Earlier_Info_Retained", "Reason", "Retention_Detail", "Matched_TP_ID", "Match_Evidence",
                  "Pre_Edit_Value", "Post_Edit_Value", "Other_Differences_On_Contact"]


def csv_bytes(headers: list[str], rows: list[dict]) -> bytes:
    """UTF-8 (with BOM so Excel shows accents) CSV, formula-injection safe."""
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow([spreadsheet_safe(h) for h in headers])
    for r in rows:
        w.writerow([spreadsheet_safe(r.get(h, "")) for h in headers])
    return buf.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------- tables
def detail_rows(res: ReconResult) -> list[dict]:
    out = []
    for r in res.rows:
        out.append({
            "Change_ID": r.change_id, "Contact": r.contact_label,
            "Match evidence": (r.match.evidence or r.match.reason) if r.match else "",
            "Field": r.field, "Historical/source value": r.source_value,
            "Current TP value": r.current_value, "Proposed addition": r.proposed,
            "Still missing": "; ".join(r.still_missing), "Assessment": r.assessment,
            "Reason": r.reason + ("" if not r.reviews else " | Review: " + " | ".join(x for _, x in r.reviews)),
            "Ready for Cowork": "Yes" if r.eligible else "No",
        })
    return out


def remaining_rows(res: ReconResult, run_id: str, export_name: str) -> tuple[list[str], list[dict]]:
    headers = ["Worklist_Type"] + list(res.worklist_headers) + RECON_COLUMNS
    rows = []
    for r in res.rows:
        if not r.eligible:
            continue
        rows.append({"Worklist_Type": WORKLIST_LABEL, **r.row,
                     "Recon_Assessment": r.assessment, "Recon_Reason": r.reason,
                     "Recon_Still_Missing": "; ".join(r.still_missing),
                     "Recon_Current_Value_In_Export": r.current_value,
                     "Recon_Matched_TP_ID": r.match.tp_id, "Recon_Match_Basis": r.match.basis,
                     "Recon_Match_Evidence": r.match.evidence, "Recon_Run_ID": run_id,
                     "Recon_Export_File": export_name})
    return headers, rows


def review_rows(res: ReconResult, source_reviews: list[dict] | None = None) -> list[dict]:
    out = []
    for r in res.rows:
        for cat, reason in r.reviews:
            out.append({"Review_Category": cat, "Change_ID": r.change_id, "Contact": r.contact_label,
                        "TP_IDs": r.match.tp_id or "; ".join(r.match.candidates), "Field": r.field,
                        "Source_Value": r.source_value, "Current_TP_Value": r.current_value,
                        "Proposed_Addition": r.proposed, "Assessment": r.assessment, "Reason": reason,
                        "Source_Reference": f"worklist line {r.source_line}"})
        if not r.reviews and r.assessment in ("NOT_VERIFIABLE_FROM_EXPORT",):
            out.append({"Review_Category": "Cannot be checked from this export", "Change_ID": r.change_id,
                        "Contact": r.contact_label, "TP_IDs": r.match.tp_id, "Field": r.field,
                        "Proposed_Addition": r.proposed, "Assessment": r.assessment, "Reason": r.reason,
                        "Source_Reference": f"worklist line {r.source_line}"})
    out.extend(res.extra_reviews)
    out.extend(source_reviews or [])
    return out


def verification_rows(rows: list[VerifyRow]) -> list[dict]:
    return [{"Change_ID": v.change_id, "Contact": v.contact_label, "Field": v.field, "Action": v.action,
             "Proposed_Addition": v.proposed, "Cowork_Reported_Status": v.cowork_status,
             "Cowork_Verified_After_Value": v.cowork_after_value, "Independent_Result": v.result,
             "Reason": v.reason, "Matched_TP_ID": v.tp_id, "Match_Evidence": v.match_evidence,
             "Pre_Edit_Value": v.pre_value, "Post_Edit_Value": v.post_value,
             "Requested_Addition_Present": v.addition_present, "Earlier_Info_Retained": v.retention,
             "Retention_Detail": v.retained, "Other_Differences_On_Contact": v.other_changes}
            for v in rows]


# ---------------------------------------------------------------- summary
def count_checks(res: ReconResult, remaining: list[dict], reviews: list[dict]) -> list[tuple[str, bool]]:
    c = res.counts()
    return [
        ("Assessment counts add up to change items",
         sum(c["by_assessment"].values()) == c["Change items checked"]),
        ("Remaining_Corrections rows equal 'Ready for Cowork'", len(remaining) == c["Ready for Cowork"]),
        ("Every row needing review appears in Review_Required",
         {r["Change_ID"] for r in reviews if r.get("Change_ID")} >=
         {x.change_id for x in res.rows if x.reviews and x.change_id}),
        ("No row is both ready for Cowork and blocked for review",
         not any(x.eligible and any(cat != "Unresolved category mapping" for cat, _ in x.reviews)
                 for x in res.rows)),
    ]


def summary_markdown(kind: str, run_id: str, run_at: str, inputs: list[dict], counts: dict,
                     checks: list[tuple[str, bool]], limitations: list[str], files: list[str]) -> str:
    lines = [f"# Run summary: {run_id}", "", f"- Run type: {kind}", f"- Run at (UTC): {run_at}",
             f"- Tool version: {TOOL_VERSION} (Python {platform.python_version()})",
             "- This tool did not read or change Top Producer. All work was local.", "", "## Inputs", "",
             "| Role | File | SHA-256 | Rows |", "|---|---|---|---|"]
    for i in inputs:
        lines.append(f"| {i['role']} | {i['file']} | `{i['sha256']}` | {i.get('rows', '')} |")
    lines += ["", "## Totals", ""]
    for k, v in counts.items():
        if not isinstance(v, dict):
            lines.append(f"- {k}: {v}")
    by = counts.get("by_assessment") or counts.get("by_result") or {}
    if by:
        lines += ["", "## By result", ""]
        for k, v in sorted(by.items()):
            lines.append(f"- {k}: {v} — {ASSESSMENTS.get(k, '')}".rstrip(" —"))
    lines += ["", "## Count checks", ""]
    lines += [f"- [{'PASS' if ok else 'FAIL'}] {name}" for name, ok in checks]
    lines += ["", "## Limitations", ""] + [f"- {x}" for x in limitations] if limitations else []
    lines += ["", "## Files written", ""] + [f"- {f}" for f in files]
    return "\n".join(lines) + "\n"


def save_run(run_dir: Path, run_id: str, kind: str, run_at: str, inputs: list[dict], counts: dict,
             checks: list[tuple[str, bool]], limitations: list[str], outputs: dict[str, bytes],
             snapshots: dict[str, bytes]) -> dict[str, bytes]:
    """Write outputs, input snapshots and summary into a new run folder."""
    files = list(outputs)
    for name, data in outputs.items():
        (run_dir / name).write_bytes(data)
    snap_dir = run_dir / "input_snapshots"
    if snapshots:
        snap_dir.mkdir()
        for name, data in snapshots.items():
            (snap_dir / name).write_bytes(data)
        files.append("input_snapshots/ (exact copies of the inputs, for audit)")
    md = summary_markdown(kind, run_id, run_at, inputs, counts, checks, limitations, files + ["Run_Summary.md",
                                                                                              "run_summary.json"])
    (run_dir / "Run_Summary.md").write_text(md, encoding="utf-8")
    (run_dir / "run_summary.json").write_text(json.dumps({
        "run_id": run_id, "run_type": kind, "run_at_utc": run_at, "tool_version": TOOL_VERSION,
        "inputs": inputs, "counts": counts, "count_checks": [{"check": n, "pass": ok} for n, ok in checks],
        "limitations": limitations}, indent=2), encoding="utf-8")
    return {**outputs, "Run_Summary.md": md.encode("utf-8")}


def ledger_entries_recon(res: ReconResult, run_id: str, run_at: str, export_sha: str) -> list[dict]:
    return [{"Run_ID": run_id, "Run_Type": "reconcile", "Run_At_UTC": run_at, "Change_ID": r.change_id,
             "Result": r.assessment, "Cowork_Status": r.cowork_status, "Matched_TP_ID": r.match.tp_id,
             "Export_SHA256": export_sha} for r in res.rows]


def ledger_entries_verify(rows: list[VerifyRow], run_id: str, run_at: str, export_sha: str) -> list[dict]:
    return [{"Run_ID": run_id, "Run_Type": "verify", "Run_At_UTC": run_at, "Change_ID": v.change_id,
             "Result": v.result, "Cowork_Status": v.cowork_status, "Matched_TP_ID": v.tp_id,
             "Export_SHA256": export_sha} for v in rows]


def verify_checks(rows: list[VerifyRow], reviews: list[dict] | None = None) -> list[tuple[str, bool]]:
    from .verify import ADDED_YES, RETAINED
    c = verification_counts(rows)
    checks = [("Result counts add up to worklist rows", sum(c["by_result"].values()) == len(rows)),
              ("Only Cowork-claimed rows can be verified complete",
               all(v.cowork_status in ("DONE", "ALREADY_CORRECT") for v in rows if v.result == "VERIFIED_COMPLETE")),
              ("Verified complete only when the addition is present AND earlier information was retained",
               all(v.addition_present == ADDED_YES and v.retention == RETAINED
                   for v in rows if v.result == "VERIFIED_COMPLETE"))]
    if reviews is not None:
        needs = {v.change_id for v in rows if v.reviews and v.change_id}
        checks.append(("Every verification needing review appears in Review_Required",
                       {r["Change_ID"] for r in reviews if r.get("Change_ID")} >= needs))
    return checks


__all__ = ["store"]
