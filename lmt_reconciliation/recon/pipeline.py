"""Orchestrate a full run from file bytes. Shared by the app, the CLI and tests."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import outputs as O
from . import store
from .columns import KNOWN_HEADER_KEYS, REQUIRED_WORKLIST, detect_tp_columns, detect_worklist_columns
from .contacts import TPExport, build_tp_export
from .loaders import LoadedFile, Table, load_file
from .reconcile import ReconResult, reconcile
from .sources import analyze_audit, analyze_master, prior_verified_ids
from .verify import VerifyRow, verification_counts, verify


@dataclass
class InputFile:
    role: str
    name: str
    data: bytes
    sheet: str = ""                 # chosen sheet for workbooks ('' = best guess)
    loaded: LoadedFile | None = None

    def load(self) -> LoadedFile:
        if self.loaded is None:
            self.loaded = load_file(self.data, self.name, KNOWN_HEADER_KEYS)
        return self.loaded


def pick_table(lf: LoadedFile, sheet: str = "", prefer: str = "tp") -> Table:
    tables = [t for t in lf.tables if t.headers]
    if not tables:
        raise ValueError(f"{lf.filename}: no readable table found.")
    if sheet:
        for t in tables:
            if t.sheet == sheet:
                return t
        raise ValueError(f"{lf.filename}: sheet '{sheet}' not found.")
    if prefer == "worklist":
        return max(tables, key=lambda t: (sum(1 for v in detect_worklist_columns(t.headers).values() if v),
                                          len(t.rows)))
    return max(tables, key=lambda t: (sum(1 for v in detect_tp_columns(t.headers).values() if v), len(t.rows)))


def validate_worklist_roles(roles: dict) -> list[str]:
    missing = [r for r in REQUIRED_WORKLIST if not roles.get(r)]
    return [f"The worklist needs a column for '{r}'. Choose it on the column mapping step." for r in missing]


def build_export(f: InputFile, roles: dict | None = None) -> tuple[TPExport, Table]:
    lf = f.load()
    table = pick_table(lf, f.sheet, "tp")
    roles = roles or detect_tp_columns(table.headers)
    return build_tp_export(table, roles, f.name, lf.sha256), table


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


@dataclass
class RunOutput:
    run_id: str
    run_dir: str
    counts: dict
    checks: list
    warnings: list[str]
    limitations: list[str]
    files: dict[str, bytes]
    recon: ReconResult | None = None
    verify_rows: list[VerifyRow] = field(default_factory=list)
    detail: list[dict] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)
    source_summaries: dict = field(default_factory=dict)


def run_reconciliation(worklist: InputFile, export: InputFile, master: InputFile | None = None,
                       audit: InputFile | None = None, corrections: InputFile | None = None,
                       wl_roles: dict | None = None, tp_roles: dict | None = None, save: bool = True
                       ) -> RunOutput:
    limitations, warnings, source_reviews, summaries = [], [], [], {}
    wl_lf = worklist.load()
    wl_table = pick_table(wl_lf, worklist.sheet, "worklist")
    wl_roles = wl_roles or detect_worklist_columns(wl_table.headers)
    problems = validate_worklist_roles(wl_roles)
    if problems:
        raise ValueError(" ".join(problems))
    tp, tp_table = build_export(export, tp_roles)
    warnings += [f"Worklist: {w}" for w in wl_table.warnings]
    for kind in ("phone", "category", "note", "email"):
        ok, why = tp.field_available(kind)
        if not ok:
            limitations.append(f"{why} Items needing it are 'Not verifiable from export'.")

    prior = {}
    if corrections:
        prior, msg = prior_verified_ids(pick_table(corrections.load(), corrections.sheet, "worklist"), corrections.name)
        limitations.append(msg)
    else:
        limitations.append("No earlier corrections file supplied.")
    if master:
        summaries["Master workbook"], rv = analyze_master(master.load())
        source_reviews += rv
    else:
        limitations.append("No master workbook supplied; tab overlap and master duplicates were not analysed.")
    if audit:
        summaries["Audit workbook"], rv = analyze_audit(audit.load())
        source_reviews += rv
    else:
        limitations.append("No audit workbook supplied; audit identity findings are not listed.")
    limitations.append("Checks use only what the export contains. Cowork must still read the live record before "
                       "writing; 'missing in export' is not permission to write.")

    mapping = store.mapping_index(store.load_mapping())
    res = reconcile(wl_table, wl_roles, tp, mapping, confirmed=store.load_confirmed(), prior_ids=prior,
                    verified_before=store.verified_complete_ids(), holds=store.load_holds())
    warnings += res.warnings
    run_id, run_dir = ("preview", None)
    if save:
        run_id, run_dir = store.new_run("reconcile")
    run_at = store.utc_now()
    rem_headers, rem_rows = O.remaining_rows(res, run_id, export.name)
    reviews = O.review_rows(res, source_reviews)
    counts = res.counts()
    ph_contacts = {r.contact_key for r in res.rows if r.phone_count}
    counts["Contacts with phone items"] = len(ph_contacts)
    checks = O.count_checks(res, rem_rows, reviews)
    outputs = {
        "Remaining_Corrections.csv": O.csv_bytes(rem_headers, rem_rows),
        "Review_Required.csv": O.csv_bytes(O.REVIEW_COLUMNS, reviews),
        "All_Results.csv": O.csv_bytes(list(O.detail_rows(res)[0].keys()) if res.rows else ["Change_ID"],
                                       O.detail_rows(res)),
    }
    inputs = [_input_meta("Correction worklist", worklist, wl_table), _input_meta("Current TP export", export, tp_table)]
    for role, f in (("Master workbook", master), ("Audit workbook", audit), ("Earlier corrections", corrections)):
        if f:
            inputs.append(_input_meta(role, f, None))
    files = outputs
    if save:
        snaps = {f"{i}_{_safe_name(f.name)}": f.data for i, f in enumerate(
            [x for x in (worklist, export, corrections) if x])}
        files = O.save_run(run_dir, run_id, "reconcile", run_at, inputs, counts, checks, limitations, outputs, snaps)
        store.append_ledger(O.ledger_entries_recon(res, run_id, run_at, tp.sha256))
    return RunOutput(run_id, str(run_dir or ""), counts, checks, warnings, limitations, files, recon=res,
                     detail=O.detail_rows(res), review=reviews, source_summaries=summaries)


def run_verification(pre: InputFile, completed: InputFile, post: InputFile, wl_roles: dict | None = None,
                     tp_roles_pre: dict | None = None, tp_roles_post: dict | None = None, save: bool = True
                     ) -> RunOutput:
    wl_table = pick_table(completed.load(), completed.sheet, "worklist")
    wl_roles = wl_roles or detect_worklist_columns(wl_table.headers)
    problems = validate_worklist_roles(wl_roles)
    if not wl_roles.get("status"):
        problems.append("The completed worklist needs a Status column (Cowork's reported status).")
    if problems:
        raise ValueError(" ".join(problems))
    pre_x, pre_t = build_export(pre, tp_roles_pre)
    post_x, post_t = build_export(post, tp_roles_post)
    limitations = []
    for kind in ("phone", "category", "note", "email"):
        for label, x in (("Pre-edit", pre_x), ("Post-edit", post_x)):
            ok, why = x.field_available(kind)
            if not ok:
                limitations.append(f"{label} export: {why}")
    limitations.append("Differences between the two exports are reported, not attributed to Cowork; another "
                       "person may have edited the CRM between exports.")
    mapping = store.mapping_index(store.load_mapping())
    rows = verify(wl_table, wl_roles, pre_x, post_x, mapping, store.load_confirmed())
    counts = verification_counts(rows)
    checks = O.verify_checks(rows)
    run_id, run_dir = ("preview", None)
    if save:
        run_id, run_dir = store.new_run("verify")
    run_at = store.utc_now()
    vrows = O.verification_rows(rows)
    reviews = [{"Review_Category": cat, "Change_ID": v.change_id, "Contact": v.contact_label, "TP_IDs": v.tp_id,
                "Field": v.field, "Proposed_Addition": v.proposed, "Assessment": v.result, "Reason": reason}
               for v in rows for cat, reason in v.reviews]
    outputs = {"Verification_Results.csv": O.csv_bytes(O.VERIFY_COLUMNS, vrows),
               "Review_Required.csv": O.csv_bytes(O.REVIEW_COLUMNS, reviews)}
    inputs = [_input_meta("Pre-edit TP export", pre, pre_t), _input_meta("Completed Cowork worklist", completed, wl_table),
              _input_meta("Post-edit TP export", post, post_t)]
    files = outputs
    warnings = pre_x.warnings + post_x.warnings + wl_table.warnings
    if save:
        snaps = {f"{i}_{_safe_name(f.name)}": f.data for i, f in enumerate((pre, completed, post))}
        files = O.save_run(run_dir, run_id, "verify", run_at, inputs, counts, checks, limitations, outputs, snaps)
        store.append_ledger(O.ledger_entries_verify(rows, run_id, run_at, post_x.sha256))
    return RunOutput(run_id, str(run_dir or ""), counts, checks, warnings, limitations, files,
                     verify_rows=rows, detail=vrows, review=reviews)


def _input_meta(role: str, f: InputFile, table: Table | None) -> dict:
    lf = f.load()
    rows = len(table.rows) if table else sum(len(t.rows) for t in lf.tables)
    sheet = f" › {table.sheet}" if table is not None and table.sheet else ""
    return {"role": role, "file": f.name + sheet, "sha256": lf.sha256, "rows": rows}
