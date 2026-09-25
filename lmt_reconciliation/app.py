"""LMT CRM Reconciliation Tool — local Streamlit interface.

Run with the launcher (see README). Everything stays on this computer.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from recon import store
from recon.columns import (ALL_TP_ROLES, ROLE_LABELS, TP_MULTI_ROLES, WORKLIST_ROLES, detect_tp_columns,
                           detect_worklist_columns)
from recon.pipeline import InputFile, pick_table, run_reconciliation, run_verification
from recon.reconcile import ASSESSMENTS

st.set_page_config(page_title="LMT CRM Reconciliation", layout="wide")

PAGES = ["1. Check what still needs correction", "2. Verify completed work", "3. Review exceptions",
         "Category mapping", "History & help"]
TRUNC = 120

WL_ROLE_LABELS = {
    "change_id": "Change ID", "queue": "Queue", "status": "Cowork status", "field": "Field", "action": "Action",
    "proposed": "Proposed addition", "tp_id": "TP contact ID", "first": "First name", "last": "Last name",
    "spouse_first": "Spouse first name", "spouse_last": "Spouse last name", "household": "Household / contact name",
    "company": "Company", "match_email": "Match email", "match_other_email": "Match other email",
    "match_phones": "Match phones", "match_address": "Match address", "match_city": "Match city",
    "match_zip": "Match ZIP", "source_value": "Historical / source value", "source_ref": "Source reference",
    "reason": "Audit reason", "execution_rule": "Execution rule", "identity_rule": "Identity rule",
    "current_tp_value": "Current TP value (Cowork)", "actual_change": "Actual change (Cowork)",
    "verified_after": "Verified after value (Cowork)", "verified_at": "Verified at (Cowork)",
    "operator": "Operator", "run_id": "Run ID (Cowork)", "result_note": "Result note (Cowork)",
    "contact_key": "Contact key",
}


def short(text, n=TRUNC):
    text = "" if text is None else str(text)
    return text if len(text) <= n else text[:n].rstrip() + " …"


def uploader(label: str, key: str, help_text: str, types=("csv", "xlsx")) -> InputFile | None:
    f = st.file_uploader(label, type=list(types), key=key, help=help_text)
    if f is None:
        return None
    cache = st.session_state.setdefault("_files", {})
    ident = (key, f.file_id)
    if ident not in cache:  # parse each upload once, not on every screen refresh
        cache[ident] = InputFile(role=label, name=f.name, data=f.getvalue())
    return cache[ident]


def sheet_picker(f: InputFile, key: str, prefer: str, choose: bool = True) -> None:
    try:
        lf = f.load()
    except Exception as exc:  # noqa: BLE001 - show the user a readable message
        st.error(f"{f.name} could not be read: {exc}")
        st.stop()
    sheets = [t.sheet for t in lf.tables if t.headers]
    if choose and len(sheets) > 1:
        best = pick_table(lf, "", prefer).sheet
        f.sheet = st.selectbox(f"Sheet to use from {f.name}", sheets, index=sheets.index(best), key=key)
    for t in lf.tables:
        if t.warnings:
            with st.expander(f"⚠️ {len(t.warnings)} reading note(s) for {t.name}"):
                for w in t.warnings[:200]:
                    st.write("•", w)


def mapping_editor(f: InputFile, kind: str, key: str) -> dict:
    """Show detected columns; let the user correct them."""
    table = pick_table(f.load(), f.sheet, "worklist" if kind == "worklist" else "tp")
    headers = table.headers
    detected = detect_worklist_columns(headers) if kind == "worklist" else detect_tp_columns(headers)
    roles = dict(detected)
    with st.expander(f"Columns detected in {f.name} ({len(table.rows)} rows, header on row {table.header_row})"):
        if kind == "worklist":
            cols = st.columns(3)
            for i, role in enumerate(WORKLIST_ROLES):
                opts = [""] + headers
                cur = detected.get(role, "")
                roles[role] = cols[i % 3].selectbox(WL_ROLE_LABELS.get(role, role), opts,
                                                    index=opts.index(cur) if cur in opts else 0, key=f"{key}_{role}")
        else:
            cols = st.columns(3)
            for i, role in enumerate(ALL_TP_ROLES):
                if role in TP_MULTI_ROLES:
                    roles[role] = cols[i % 3].multiselect(ROLE_LABELS[role], headers, default=detected.get(role, []),
                                                          key=f"{key}_{role}")
                else:
                    opts = [""] + headers
                    cur = detected.get(role, "")
                    roles[role] = cols[i % 3].selectbox(ROLE_LABELS[role], opts,
                                                        index=opts.index(cur) if cur in opts else 0,
                                                        key=f"{key}_{role}")
        st.caption("Blank means the file has no such column. A missing column is reported as "
                   "'Not verifiable from export', never as an empty value.")
    return roles


def totals(items: dict, keys: list[str]) -> None:
    cols = st.columns(len(keys))
    for c, k in zip(cols, keys):
        c.metric(k, items.get(k, 0))


def downloads(out, names: list[str]) -> None:
    cols = st.columns(len(names))
    for c, name in zip(cols, names):
        if name in out.files:
            c.download_button(f"⬇️ {name}", out.files[name], file_name=name,
                              mime="text/csv" if name.endswith(".csv") else "text/markdown", key=f"dl_{out.run_id}_{name}")
    if out.run_dir:
        st.caption(f"Saved permanently in: {out.run_dir}")


def results_table(rows: list[dict], key: str, status_col: str) -> None:
    if not rows:
        st.info("No rows.")
        return
    df = pd.DataFrame(rows)
    c1, c2 = st.columns([2, 1])
    q = c1.text_input("Search (name, Change ID, value, reason)", key=f"{key}_q")
    choices = sorted(df[status_col].unique())
    pick = c2.multiselect("Show only", choices, key=f"{key}_f")
    if q:
        mask = df.apply(lambda r: r.astype(str).str.contains(q, case=False, regex=False).any(), axis=1)
        df = df[mask]
    if pick:
        df = df[df[status_col].isin(pick)]
    view = df.copy()
    for col in view.columns:
        view[col] = view[col].map(short)
    st.dataframe(view, use_container_width=True, hide_index=True)
    st.caption(f"{len(df)} of {len(rows)} rows shown. Long text is shortened here; open it below.")
    if "Change_ID" in df.columns and len(df):
        with st.expander("Show full text for one item"):
            cid = st.selectbox("Change ID", df["Change_ID"].tolist(), key=f"{key}_full")
            for k, v in df[df["Change_ID"] == cid].iloc[0].items():
                st.markdown(f"**{k}**")
                st.text(v if v else "(blank)")


def show_warnings(out) -> None:
    for name, ok in out.checks:
        if not ok:
            st.error(f"Count check failed: {name}. Please report this; do not use the outputs.")
    if out.warnings or out.limitations:
        with st.expander(f"Notes and limitations ({len(out.warnings) + len(out.limitations)})"):
            for w in out.limitations:
                st.write("•", w)
            for w in out.warnings[:300]:
                st.write("•", w)


# ------------------------------------------------------------------ page 1
def page_reconcile() -> None:
    st.header("Check what still needs correction")
    st.write("Compares the correction worklist with a current Top Producer export. Nothing in Top Producer "
             "is changed, and your files are not modified.")
    st.subheader("Step 1 · Select files")
    c1, c2 = st.columns(2)
    with c1:
        wl = uploader("Correction worklist (required)", "a_wl", "LMT_Audit_Correction_Worklist.csv or a later copy")
        master = uploader("Master workbook (optional)", "a_master", "LMT_Contact_Master_v7.5.xlsx", ("xlsx",))
        corrections = uploader("Earlier verified corrections (optional)", "a_corr",
                               "Profiles_With_Verified_Corrections.csv")
    with c2:
        tp = uploader("Current Top Producer export (required)", "a_tp", "CSV or XLSX exported from Top Producer")
        audit = uploader("Audit workbook (optional)", "a_audit", "LMT_Contact_Audit.xlsx", ("xlsx",))
    if not (wl and tp):
        st.info("Select the correction worklist and a current Top Producer export to continue.")
        return
    st.subheader("Step 2 · Check sheets and columns")
    sheet_picker(wl, "a_wl_sheet", "worklist")
    sheet_picker(tp, "a_tp_sheet", "tp")
    for f, k in ((master, "a_m_sheet"), (audit, "a_au_sheet")):
        if f:  # every sheet of these workbooks is analysed
            sheet_picker(f, k, "tp", choose=False)
    if corrections:
        sheet_picker(corrections, "a_c_sheet", "worklist")
    wl_roles = mapping_editor(wl, "worklist", "a_wlmap")
    tp_roles = mapping_editor(tp, "tp", "a_tpmap")
    st.subheader("Step 3 · Run")
    if st.button("Run comparison", type="primary"):
        with st.spinner("Comparing…"):
            try:
                st.session_state["recon_out"] = run_reconciliation(wl, tp, master, audit, corrections,
                                                                   wl_roles, tp_roles)
            except ValueError as exc:
                st.error(str(exc))
                return
    out = st.session_state.get("recon_out")
    if not out:
        return
    st.subheader("Step 4 · Results")
    st.caption(f"Run {out.run_id}")
    show_warnings(out)
    totals(out.counts, ["Unique contacts", "Change items checked", "Already correct", "Missing proposed additions",
                        "Ready for Cowork", "Review needed", "Not verifiable"])
    st.caption(f"Phone items: {out.counts.get('Phone numbers proposed', 0)} numbers across "
               f"{out.counts.get('Contacts with phone items', 0)} contacts.")
    downloads(out, ["Remaining_Corrections.csv", "Review_Required.csv", "All_Results.csv", "Run_Summary.md"])
    st.caption("Remaining_Corrections.csv is a worklist for Cowork, not a Top Producer import file. "
               "Cowork must still check each live record before writing.")
    cols = ["Change_ID", "Contact", "Match evidence", "Field", "Historical/source value", "Current TP value",
            "Proposed addition", "Assessment", "Reason", "Ready for Cowork"]
    results_table([{k: d[k] for k in cols} for d in out.detail], "a_tbl", "Assessment")
    for name, summ in out.source_summaries.items():
        with st.expander(f"{name}: sheets and overlap"):
            for k, v in summ.items():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    st.write(k)
                    st.dataframe(pd.DataFrame(v), hide_index=True)
                else:
                    st.write(f"**{k}:** {v}")


# ------------------------------------------------------------------ page 2
def page_verify() -> None:
    st.header("Verify completed work")
    st.write("Checks each item Cowork reported as done against a post-edit export. Cowork's report and this "
             "independent check are shown separately.")
    st.subheader("Step 1 · Select files")
    c1, c2, c3 = st.columns(3)
    with c1:
        pre = uploader("Pre-edit Top Producer export", "b_pre", "Export taken before Cowork's edits")
    with c2:
        done = uploader("Completed Cowork worklist", "b_done", "The worklist file Cowork returned")
    with c3:
        post = uploader("Post-edit Top Producer export", "b_post", "Export taken after Cowork's edits")
    if not (pre and done and post):
        st.info("All three files are needed. Without them, completion cannot be independently verified.")
        return
    st.subheader("Step 2 · Check sheets and columns")
    for f, k, p in ((pre, "b_pre_s", "tp"), (done, "b_done_s", "worklist"), (post, "b_post_s", "tp")):
        sheet_picker(f, k, p)
    wl_roles = mapping_editor(done, "worklist", "b_wlmap")
    pre_roles = mapping_editor(pre, "tp", "b_premap")
    post_roles = mapping_editor(post, "tp", "b_postmap")
    st.subheader("Step 3 · Run")
    if st.button("Verify completed work", type="primary"):
        with st.spinner("Verifying…"):
            try:
                st.session_state["verify_out"] = run_verification(pre, done, post, wl_roles, pre_roles, post_roles)
            except ValueError as exc:
                st.error(str(exc))
                return
    out = st.session_state.get("verify_out")
    if not out:
        return
    st.subheader("Step 4 · Results")
    st.caption(f"Run {out.run_id}")
    show_warnings(out)
    totals(out.counts, ["Completion claims checked", "Verified completed", "Expected change not found",
                        "Not verifiable", "Match problems", "Retention problems"])
    downloads(out, ["Verification_Results.csv", "Review_Required.csv", "Run_Summary.md"])
    cols = ["Change_ID", "Contact", "Field", "Proposed_Addition", "Cowork_Reported_Status", "Independent_Result",
            "Reason", "Earlier_Values_Retained", "Other_Differences_On_Contact"]
    results_table([{k: d[k] for k in cols} for d in out.detail], "b_tbl", "Independent_Result")


# ------------------------------------------------------------------ page 3
def page_exceptions() -> None:
    st.header("Review exceptions")
    sources = []
    if st.session_state.get("recon_out"):
        sources.append("Latest comparison")
    if st.session_state.get("verify_out"):
        sources.append("Latest verification")
    if not sources:
        st.info("Run a comparison or verification first.")
        return
    src = st.radio("Show exceptions from", sources, horizontal=True)
    out = st.session_state["recon_out" if src == "Latest comparison" else "verify_out"]
    if not out.review:
        st.success("No exceptions.")
        return
    df = pd.DataFrame(out.review).fillna("")
    counts = df["Review_Category"].value_counts()
    st.write("  ·  ".join(f"**{k}**: {v}" for k, v in counts.items()))
    cat = st.selectbox("Category", ["All"] + list(counts.index))
    view = df if cat == "All" else df[df["Review_Category"] == cat]
    q = st.text_input("Search", key="ex_q")
    if q:
        view = view[view.apply(lambda r: r.astype(str).str.contains(q, case=False, regex=False).any(), axis=1)]
    shown = view.copy()
    for col in shown.columns:
        shown[col] = shown[col].map(short)
    st.dataframe(shown, use_container_width=True, hide_index=True)
    st.caption("Nothing here becomes a correction automatically.")

    if src == "Latest comparison" and out.recon:
        st.subheader("Confirm a contact match")
        st.write("For an ambiguous or unconfirmed match, choose the correct Top Producer contact after checking "
                 "it yourself. Your decision is saved with your name and reused in later runs.")
        pending = [r for r in out.recon.rows if r.match.status != "MATCHED" or r.match.basis.startswith("Name")]
        opts = {}
        for r in pending:
            opts.setdefault(r.contact_key, r)
        if not opts:
            st.write("No matches need confirmation.")
            return
        key = st.selectbox("Contact", list(opts), format_func=lambda k: f"{opts[k].contact_label} "
                           f"({', '.join(x.change_id for x in out.recon.rows if x.contact_key == k)})")
        r = opts[key]
        st.write(r.match.reason or r.match.evidence)
        cands = [c.split(" ")[0] for c in r.match.candidates if not c.startswith("row")]
        if r.match.tp_id and r.match.tp_id not in cands:
            cands.insert(0, r.match.tp_id)
        c1, c2, c3 = st.columns(3)
        tp_id = c1.selectbox("Top Producer contact ID", cands + ["(type another ID)"]) if cands else "(type another ID)"
        if tp_id == "(type another ID)":
            tp_id = c1.text_input("Contact ID")
        who = c2.text_input("Your name")
        why = c3.text_input("How you confirmed it (e.g. checked phone in TP)")
        b1, b2 = st.columns(2)
        for btn, decision in ((b1, "CONFIRMED"), (b2, "REJECTED")):
            if btn.button("Confirm this match" if decision == "CONFIRMED" else "Reject this match", key=decision):
                try:
                    if not tp_id:
                        raise ValueError("Choose or type a contact ID.")
                    store.record_decision(key, r.contact_label, tp_id, decision, why, who, out.run_id)
                    st.success(f"Saved: {decision.lower()} {tp_id}. Re-run the comparison to use it.")
                except ValueError as exc:
                    st.error(str(exc))


# ------------------------------------------------------------------ page 4
def page_mapping() -> None:
    st.header("Category mapping")
    st.write("Source categories are only proposed as Top Producer contact types through an APPROVED row here, "
             "or when the label already exists in Top Producer with identical spelling. Nothing is guessed.")
    rows = store.load_mapping()
    out = st.session_state.get("recon_out")
    known = {r["Source_Category"].casefold() for r in rows}
    if out and out.recon:
        from recon.checks import split_source_categories
        seen = sorted({c for r in out.recon.rows if "CATEGOR" in r.action.upper()
                       for c in split_source_categories(r.proposed)})
        new = [c for c in seen if c.casefold() not in known]
        if new:
            st.warning(f"{len(new)} category label(s) from the last comparison are not in the mapping yet.")
            if st.button("Add them as UNRESOLVED rows"):
                rows += [{"Source_Category": c, "Status": "UNRESOLVED"} for c in new]
                store.save_mapping(rows)
                st.rerun()
    with st.expander("Import mapping rows from a CSV file"):
        up = st.file_uploader("Mapping CSV with Source_Category, TP_Contact_Type, Status columns", type=["csv"],
                              key="map_up")
        if up is not None and st.button("Add rows from this file"):
            from recon.loaders import read_csv_bytes
            t = read_csv_bytes(up.getvalue(), up.name)
            if "Source_Category" not in t.headers:
                st.error("The file needs a Source_Category column.")
            else:
                existing = {r["Source_Category"].casefold() for r in rows}
                added = [r for r in t.rows if r.get("Source_Category", "").strip()
                         and r["Source_Category"].casefold() not in existing]
                store.save_mapping(rows + added)
                st.success(f"Added {len(added)} row(s); existing rows were left unchanged.")
                st.rerun()
    df = pd.DataFrame(rows, columns=store.MAPPING_COLUMNS).fillna("")
    edited = st.data_editor(df, num_rows="dynamic", use_container_width=True, hide_index=True, column_config={
        "Status": st.column_config.SelectboxColumn(options=list(store.MAPPING_STATUSES), required=True),
        "Conflicts_With": st.column_config.TextColumn(help="Existing TP types that should block this addition, "
                                                           "separated by semicolons"),
    }, disabled=["Updated_At_UTC"])
    who = st.text_input("Your name (recorded on changed rows)")
    if st.button("Save mapping", type="primary"):
        if not who:
            st.error("Enter your name first.")
            return
        new_rows = edited.to_dict("records")
        old = {r.get("Source_Category"): r for r in rows}
        for r in new_rows:
            prev = old.get(r.get("Source_Category"), {})
            if any(str(prev.get(k, "")) != str(r.get(k, "")) for k in ("TP_Contact_Type", "Status", "Conflicts_With")):
                r["Updated_By"], r["Updated_At_UTC"] = who, store.utc_now()
        store.save_mapping(new_rows)
        st.success("Saved. The previous version was kept in the backups folder. Re-run the comparison to apply it.")
    st.caption(f"Stored at {store.mapping_path()}")


# ------------------------------------------------------------------ page 5
def page_history() -> None:
    st.header("History & help")
    st.subheader("What each result means")
    st.table(pd.DataFrame([{"Result": k, "Meaning": v} for k, v in ASSESSMENTS.items()] +
                          [{"Result": "NO_COMPLETION_CLAIMED", "Meaning": "Cowork has not reported this item as "
                                                                          "done, so it is not verified."}]))
    st.subheader("Previous runs")
    runs = store.list_runs()
    if not runs:
        st.write("No runs yet.")
    else:
        st.dataframe(pd.DataFrame(runs), hide_index=True, use_container_width=True)
        pick = st.selectbox("Open a run summary", [r["Run_ID"] for r in runs])
        p = store.data_dir() / "runs" / pick / "Run_Summary.md"
        if p.exists():
            with st.expander("Run summary", expanded=True):
                st.markdown(p.read_text(encoding="utf-8"))
            for f in sorted((p.parent).glob("*.csv")):
                st.download_button(f"⬇️ {f.name}", f.read_bytes(), file_name=f"{pick}_{f.name}", key=f"h_{pick}_{f.name}")
    st.caption(f"All history is stored locally in {store.data_dir()}. Runs are never overwritten.")


def main() -> None:
    st.sidebar.title("LMT CRM Reconciliation")
    page = st.sidebar.radio("Go to", PAGES, label_visibility="collapsed")
    st.sidebar.caption("Local only. Does not connect to Top Producer or any online service.")
    {PAGES[0]: page_reconcile, PAGES[1]: page_verify, PAGES[2]: page_exceptions, PAGES[3]: page_mapping,
     PAGES[4]: page_history}[page]()


main()
