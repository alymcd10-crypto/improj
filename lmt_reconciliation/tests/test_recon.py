"""Tests on SYNTHETIC data only (see tests/synthetic.py)."""
from __future__ import annotations

import csv
import io

import pytest

from recon import normalize as N
from recon import store
from recon.columns import detect_tp_columns
from recon.contacts import build_tp_export, parse_household
from recon.loaders import load_file, read_csv_bytes
from recon.pipeline import InputFile, run_reconciliation, run_verification
from tests import synthetic as S


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LMT_RECON_DATA", str(tmp_path / "data"))
    store.save_mapping(S.mapping_rows())
    return tmp_path / "data"


def recon(worklist=None, export=None, **kw):
    return run_reconciliation(InputFile("worklist", "SYNTHETIC_worklist.csv", worklist or S.worklist_bytes()),
                              InputFile("export", "SYNTHETIC_tp.csv", export or S.export_bytes()), **kw)


def by_id(out):
    return {r.change_id: r for r in out.recon.rows}


def read_out(out, name):
    text = out.files[name].decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


# ------------------------------------------------------------------ matching
def test_same_name_resolved_only_with_corroboration():
    r = by_id(recon())
    assert r["C001"].match.tp_id == "T001"                 # email picks the right Avery Sample
    assert r["C002"].assessment == "MATCH_REVIEW"           # name alone never matches
    assert "T001" in " ".join(r["C002"].match.candidates) and "T002" in " ".join(r["C002"].match.candidates)


def test_couple_sharing_profile_is_not_split():
    r = by_id(recon())
    assert r["C003"].match.tp_id == "T003"
    assert r["C004"].match.tp_id == "T003"                  # spouse name + shared family email
    assert parse_household("Jordan & Casey Example")[1].last == "Example"
    assert [p.first for p in parse_household("Example, Jordan and Casey")] == ["Jordan", "Casey"]


def test_shared_family_email_alone_does_not_identify():
    rows = [S.wl("X1", "ADD_MISSING_PHONE", "206-555-0198", household="The Example Family",
                 email="example.family@example.com")]
    r = by_id(recon(S.worklist_bytes(rows)))
    assert r["X1"].assessment == "MATCH_REVIEW"             # two profiles share it; family name only


def test_business_without_first_name():
    r = by_id(recon())
    assert r["C005"].match.tp_id == "T005"
    assert r["C005"].assessment == "MISSING_IN_CURRENT_EXPORT"


def test_verified_id_pointing_to_other_identity_goes_to_review():
    r = by_id(recon())
    assert r["C009"].assessment == "MATCH_REVIEW"
    assert "does not match" in r["C009"].reason


def test_row_number_is_never_an_id():
    rows = [S.wl("X2", "ADD_MISSING_PHONE", "206-555-0197", "Jamie", "Missing", email="jamie@example.com",
                 tp_id="57")]
    r = by_id(recon(S.worklist_bytes(rows)))
    assert r["X2"].assessment == "NOT_FOUND"
    assert "row number" in r["X2"].reason


def test_accent_and_apostrophe_matching_preserves_raw():
    r = by_id(recon())
    assert r["C006"].match.tp_id == "T006"
    assert r["C006"].row["Last_Name"] == "O’Testly"   # original kept
    assert N.name_key("O'Testly") == N.name_key("O’Testly") == N.name_key("OTestly")
    assert N.name_key("Renée") == N.name_key("Renee")


def test_confirmed_match_is_reused_and_software_suggestion_is_not_confirmed(data_dir):
    out = recon()
    c2 = by_id(out)["C002"]
    assert store.load_confirmed() == {}                     # nothing auto-confirmed
    store.record_decision(c2.contact_key, c2.contact_label, "T002", "CONFIRMED", "phone call", "Tester", out.run_id)
    again = by_id(recon())["C002"]
    assert again.match.tp_id == "T002" and again.match.status == "MATCHED"
    assert "confirmed by Tester" in again.match.evidence


# ------------------------------------------------------------------ field checks
def test_phone_formats_and_extensions():
    assert N.parse_phone("(206) 555-0103").key == N.parse_phone("+1 206.555.0103").key
    assert N.parse_phone("206-555-0113 x46").ext == "46"
    assert N.parse_phone("206555011455").problem           # unmarked extra digits never guessed
    r = by_id(recon())
    assert r["C003"].assessment == "ALREADY_CORRECT"
    assert r["C013"].assessment == "NEEDS_REVIEW" and "extension" in r["C013"].reason
    assert r["C018"].assessment == "NEEDS_REVIEW"
    assert r["C014"].still_missing == ["+1 206 555 0144"]   # raw preserved, no country code invented


def test_every_phone_field_checked():
    contacts = S.base_contacts()
    contacts[13]["Home Phone"] = "206 555 0144"             # Jamie's number in a secondary field
    r = by_id(recon(export=S.export_bytes(contacts)))
    assert r["C014"].assessment == "ALREADY_CORRECT"


def test_categories_as_sets_and_unmapped():
    r = by_id(recon())
    assert r["C001"].assessment == "ALREADY_CORRECT"
    assert r["C010"].assessment == "ALREADY_CORRECT"
    assert r["C011"].still_missing == ["Sphere"] and r["C011"].eligible
    assert any(cat == "Unresolved category mapping" for cat, _ in r["C011"].reviews)


def test_opt_out_holds_category():
    assert by_id(recon())["C008"].assessment == "CONFLICT_REVIEW"


def test_newer_conflicting_phone_info_holds():
    r = by_id(recon())["C007"]
    assert r.assessment == "CONFLICT_REVIEW" and not r.eligible


def test_note_detail_with_different_formatting_is_not_duplicated():
    r = by_id(recon())
    assert r["C015"].assessment == "ALREADY_CORRECT"
    assert r["C006"].still_missing == ["Dog named Biscuit"]
    assert r["C019"].assessment == "CONFLICT_REVIEW"        # whole-note replacement -> review


def test_missing_column_vs_blank_values():
    no_types = by_id(recon(export=S.export_bytes(drop=("Contact Type",))))
    assert no_types["C004"].assessment == "NOT_VERIFIABLE_FROM_EXPORT"
    blank_notes = by_id(recon(export=S.export_bytes(blank=("Notes",))))
    assert blank_notes["C006"].assessment == "NOT_VERIFIABLE_FROM_EXPORT"
    contacts = S.base_contacts()
    contacts[5]["Notes"] = ""                               # one blank value: a real empty field
    one_blank = by_id(recon(export=S.export_bytes(contacts)))
    assert one_blank["C006"].assessment == "MISSING_IN_CURRENT_EXPORT"


def test_placeholder_birth_year():
    assert N.birthday_parts("1800-04-02")["year_known"] is False
    assert N.birthday_parts("04/02/1975")["year"] == "1975"
    contacts = S.base_contacts()
    for c in contacts:
        c["Birthday"] = ""
    contacts[13]["Birthday"] = "1800-04-02"
    headers = S.TP_HEADERS + ["Birthday"]
    rows = [S.wl("B1", "ADD_MISSING_BIRTHDAY", "1975-04-02", "Jamie", "Missing", email="jamie@example.com")]
    out = recon(S.worklist_bytes(rows), S.to_csv(headers, contacts))
    r = by_id(out)["B1"]
    assert r.assessment == "MISSING_IN_CURRENT_EXPORT" and r.still_missing == ["1975"]


# ------------------------------------------------------------------ worklist rules
def test_review_only_and_done_never_queued():
    out = recon()
    r = by_id(out)
    assert not r["C016"].eligible and r["C016"].assessment == "NEEDS_REVIEW"
    assert not r["C021"].eligible
    remaining = {x["Change_ID"] for x in read_out(out, "Remaining_Corrections.csv")}
    assert remaining == {"C004", "C005", "C006", "C011", "C012", "C014"}


def test_outputs_preserve_columns_and_status():
    out = recon()
    rows = read_out(out, "Remaining_Corrections.csv")
    assert all(x["Worklist_Type"].startswith("COWORK WORKLIST - NOT A TOP PRODUCER IMPORT") for x in rows)
    headers = list(rows[0].keys())
    assert headers[1:1 + len(S.WL_HEADERS)] == S.WL_HEADERS   # original order preserved
    assert all(x["Status"] == "PENDING_CURRENT_CHECK" for x in rows)  # execution status untouched


def test_formula_injection_neutralised_in_downloads():
    out = recon()
    text = out.files["Remaining_Corrections.csv"].decode("utf-8-sig")
    assert "'=cmd|" in text and ",=cmd" not in text
    assert N.spreadsheet_safe("+1 206 555 0144") == "+1 206 555 0144"
    assert N.spreadsheet_safe("@SUM(A1)") == "'@SUM(A1)"
    assert by_id(out)["C012"].proposed == "=cmd|' /C calc'!A0"  # original kept internally


def test_summary_totals_reconcile_with_detail():
    out = recon()
    c = out.counts
    assert all(ok for _, ok in out.checks)
    assert sum(c["by_assessment"].values()) == c["Change items checked"] == len(out.detail)
    assert c["Ready for Cowork"] == len(read_out(out, "Remaining_Corrections.csv"))
    assert c["Phone numbers proposed"] == 11 and c["Contacts with phone items"] == 8
    assert c["Unique contacts"] == len({r.contact_key for r in out.recon.rows})


def test_runs_are_preserved_not_overwritten(data_dir):
    a, b = recon(), recon()
    assert a.run_id != b.run_id
    runs = store.list_runs()
    assert len(runs) == 2
    assert (data_dir / "runs" / a.run_id / "Run_Summary.md").exists()
    assert (data_dir / "runs" / a.run_id / "input_snapshots").is_dir()
    assert len(store.read_rows(store.ledger_path())) == 2 * len(S.base_worklist())


# ------------------------------------------------------------------ verification
def completed_worklist(rows=None):
    rows = rows or S.base_worklist()
    for r in rows:
        if r["Change_ID"] in ("C004", "C005", "C006", "C014"):
            r["Status"] = "DONE"
    return rows


def post_contacts():
    c = S.base_contacts()
    c[2]["Contact Type"] = "Sphere; Past Client"                # C004 done
    c[13]["Work Phone"] = "206-555-0144"                        # C014 done
    c[5]["Contact Type"] = "Sphere"                             # T006 lost "Buyer" (retention problem)
    c[5]["Notes"] += "\nDog named Biscuit (from Agent Office)"  # C006 done
    c[0]["Address"] = "77 New Road"                             # unrelated change on another contact
    return c                                                    # C005 claimed DONE but not present


def test_verification_distinguishes_claims_and_evidence():
    out = run_verification(InputFile("pre", "SYNTHETIC_pre.csv", S.export_bytes()),
                           InputFile("done", "SYNTHETIC_done.csv", S.worklist_bytes(completed_worklist())),
                           InputFile("post", "SYNTHETIC_post.csv", S.export_bytes(post_contacts())))
    v = {x.change_id: x for x in out.verify_rows}
    assert v["C004"].result == "VERIFIED_COMPLETE" and v["C004"].cowork_status == "DONE"
    assert v["C014"].result == "VERIFIED_COMPLETE"
    assert v["C005"].result == "EXPECTED_CHANGE_NOT_FOUND"
    assert v["C006"].result == "VERIFIED_COMPLETE"
    assert v["C001"].result == "NO_COMPLETION_CLAIMED"
    assert v["C021"].result == "EXPECTED_CHANGE_NOT_FOUND"
    assert all(ok for _, ok in out.checks)


def test_retention_and_unexpected_differences_reported_not_attributed():
    rows = completed_worklist()
    rows.append(S.wl("V1", "ADD_MISSING_CATEGORIES", "Sphere", "Renée", "O'Testly", email="renee@example.com",
                     status="DONE"))
    rows.append(S.wl("V2", "ADD_MISSING_CATEGORIES", "Past Client", "Avery", "Sample", email="avery@example.com",
                     status="DONE"))
    out = run_verification(InputFile("pre", "pre.csv", S.export_bytes()),
                           InputFile("done", "done.csv", S.worklist_bytes(rows)),
                           InputFile("post", "post.csv", S.export_bytes(post_contacts())))
    v = {x.change_id: x for x in out.verify_rows}
    assert v["V1"].result == "VERIFIED_COMPLETE" and "Buyer" in v["V1"].retained
    assert "Address" in v["V2"].other_changes and "another user" in v["V2"].other_changes


def test_missing_field_in_post_export_is_not_verifiable():
    out = run_verification(InputFile("pre", "pre.csv", S.export_bytes()),
                           InputFile("done", "done.csv", S.worklist_bytes(completed_worklist())),
                           InputFile("post", "post.csv", S.export_bytes(post_contacts(), drop=("Notes",))))
    v = {x.change_id: x for x in out.verify_rows}
    assert v["C006"].result == "NOT_VERIFIABLE_FROM_EXPORT"


def test_rerun_after_completion_creates_no_duplicate_actions():
    run_verification(InputFile("pre", "pre.csv", S.export_bytes()),
                     InputFile("done", "done.csv", S.worklist_bytes(completed_worklist())),
                     InputFile("post", "post.csv", S.export_bytes(post_contacts())))
    # Original (un-updated) worklist against the post-edit export: completed items are already correct.
    r = by_id(recon(export=S.export_bytes(post_contacts())))
    for cid in ("C004", "C006", "C014"):
        assert r[cid].assessment == "ALREADY_CORRECT" and not r[cid].eligible
    # Original worklist against the OLD export: verified items are still not re-queued.
    old = by_id(recon())
    for cid in ("C004", "C006", "C014"):
        assert not old[cid].eligible
        assert any("Verified complete in run" in reason for _, reason in old[cid].reviews)
    assert old["C005"].eligible                               # not verified -> still pending


# ------------------------------------------------------------------ loading
def test_csv_encodings_delimiters_and_multiline_cells():
    contacts = S.base_contacts()
    contacts[15]["Notes"] = "Line one, with comma\nLine \"two\"\n" + "x" * 20000
    data = S.export_bytes(contacts, delimiter=";", encoding="cp1252")
    t = read_csv_bytes(data, "cp.csv")
    assert t.delimiter == ";" and t.encoding == "cp1252"
    row = next(r for r in t.rows if r["Contact ID"] == "T016")
    assert row["Notes"].endswith("x" * 20000) and "\n" in row["Notes"]  # no truncation
    assert next(r for r in t.rows if r["Contact ID"] == "T006")["First Name"] == "Renée"


def test_leading_zero_zip_csv_and_xlsx():
    t = read_csv_bytes(S.export_bytes(), "z.csv")
    assert t.rows[0]["Zip"] == "02134"
    lf = load_file(S.master_xlsx_bytes(), "SYNTHETIC_master.xlsx")
    allc = lf.tables[0]
    assert allc.header_row == 2 and allc.rows[0]["Zip"] == "02134"
    assert allc.rows[0]["Birthday"] == "1800-04-02"             # date text kept as written


def test_malformed_rows_reported():
    data = b"Contact ID,First Name,Last Name\nT1,Ann,Test\nT2,Bob,Test,EXTRA\nT3,Cy\n"
    t = read_csv_bytes(data, "bad.csv")
    assert len(t.rows) == 3
    assert any("Line 3" in w and "unquoted comma" in w for w in t.warnings)
    assert any("Line 4" in w for w in t.warnings)


def test_master_tabs_overlap_vs_duplicates():
    out = recon(master=InputFile("m", "SYNTHETIC_master.xlsx", S.master_xlsx_bytes()),
                audit=InputFile("a", "SYNTHETIC_audit.xlsx", S.master_xlsx_bytes()))
    m = out.source_summaries["Master workbook"]
    assert m["Tab rows that repeat an All Contacts row (not duplicates)"] == 1
    assert m["Tab rows not on All Contacts"] == 1
    cats = [r["Review_Category"] for r in out.review]
    assert "Suspected duplicate" in cats
    ident = [r for r in out.review if "Unresolved identity finding" in r.get("Reason", "")]
    assert len(ident) == 1                                     # resolved finding not repeated


def test_tp_suspected_duplicates_flagged_for_review():
    contacts = S.base_contacts() + [S.tp("T099", "Jamie", "Missing", email="jamie@example.com")]
    out = recon(export=S.export_bytes(contacts))
    assert any(r["Review_Category"] == "Suspected duplicate" for r in out.review)
    assert by_id(out)["C014"].assessment == "MATCH_REVIEW"     # two plausible profiles -> review


def test_column_detection():
    roles = detect_tp_columns(S.TP_HEADERS)
    assert roles["tp_id"] == "Contact ID" and roles["phones"] == ["Home Phone", "Mobile Phone", "Work Phone"]
    assert roles["phone_exts"] == ["Work Phone Ext"] and roles["categories"] == ["Contact Type"]
    x = build_tp_export(read_csv_bytes(S.export_bytes(), "t.csv"), roles, "t.csv", "0")
    assert x.by_id["T005"].phones[0][1].ext == "12"


def test_source_files_are_never_modified(tmp_path):
    import hashlib
    import cli
    wl = tmp_path / "SYNTHETIC_wl.csv"
    tpf = tmp_path / "SYNTHETIC_tp.csv"
    wl.write_bytes(S.worklist_bytes())
    tpf.write_bytes(S.export_bytes())
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (wl, tpf)}
    assert cli.main(["reconcile", "--worklist", str(wl), "--export", str(tpf)]) == 0
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (wl, tpf)}
    assert sorted(x.name for x in tmp_path.iterdir() if x.is_file()) == ["SYNTHETIC_tp.csv", "SYNTHETIC_wl.csv"]
