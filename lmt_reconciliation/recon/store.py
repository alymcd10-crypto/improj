"""Local, append-only persistence: run folders, history ledger, confirmed
matches, category mapping and hold list. Everything lives in DATA_DIR, which
is excluded from Git. Nothing is ever sent anywhere.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import secrets
import shutil
from pathlib import Path

from . import normalize as N

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("LMT_RECON_DATA", APP_DIR / "data"))

MAPPING_COLUMNS = ["Source_Category", "TP_Contact_Type", "Status", "Conflicts_With", "Notes",
                   "Updated_By", "Updated_At_UTC"]
MAPPING_STATUSES = ("APPROVED", "NO_TP_EQUIVALENT", "UNRESOLVED")
CONFIRM_COLUMNS = ["Contact_Key", "Contact_Label", "TP_ID", "Decision", "Evidence",
                   "Decided_By", "Decided_At_UTC", "Run_ID"]
LEDGER_COLUMNS = ["Run_ID", "Run_Type", "Run_At_UTC", "Change_ID", "Result", "Cowork_Status",
                  "Matched_TP_ID", "Export_SHA256"]
HOLD_COLUMNS = ["Change_ID", "Reason", "Added_By", "Added_At_UTC"]


def data_dir() -> Path:
    d = Path(os.environ.get("LMT_RECON_DATA", DATA_DIR))
    for sub in ("runs", "history", "config"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run(kind: str) -> tuple[str, Path]:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = f"{stamp}-{kind}-{secrets.token_hex(2)}"
    path = data_dir() / "runs" / run_id
    path.mkdir(parents=True, exist_ok=False)  # never reuse / overwrite a run folder
    return run_id, path


# ---------------------------------------------------------------- CSV helpers
def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def append_rows(path: Path, columns: list[str], rows: list[dict]) -> None:
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        if new:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def replace_with_backup(path: Path, columns: list[str], rows: list[dict]) -> None:
    """Rewrite a small config file, keeping a timestamped copy of the old one."""
    if path.exists():
        backups = path.parent / "backups"
        backups.mkdir(exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, backups / f"{path.stem}.{stamp}{path.suffix}")
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


# ---------------------------------------------------------------- category mapping
def mapping_path() -> Path:
    return data_dir() / "config" / "category_mapping.csv"


def load_mapping() -> list[dict]:
    return read_rows(mapping_path())


def save_mapping(rows: list[dict]) -> None:
    clean_rows = []
    for r in rows:
        if not N.clean(r.get("Source_Category")):
            continue
        status = N.clean(r.get("Status")).upper() or "UNRESOLVED"
        if status not in MAPPING_STATUSES:
            status = "UNRESOLVED"
        if status == "APPROVED" and not N.clean(r.get("TP_Contact_Type")):
            status = "UNRESOLVED"
        clean_rows.append({**{c: N.clean(r.get(c, "")) for c in MAPPING_COLUMNS}, "Status": status})
    replace_with_backup(mapping_path(), MAPPING_COLUMNS, clean_rows)


def mapping_index(rows: list[dict]) -> dict[str, dict]:
    return {N.category_key(r["Source_Category"]): r for r in rows if N.clean(r.get("Source_Category"))}


# ---------------------------------------------------------------- confirmed matches
def confirm_path() -> Path:
    return data_dir() / "history" / "confirmed_matches.csv"


def load_confirmed() -> dict[str, dict]:
    """Latest human decision per contact key (append-only file; last entry wins)."""
    latest = {}
    for r in read_rows(confirm_path()):
        latest[r["Contact_Key"]] = r
    return latest


def record_decision(contact_key: str, label: str, tp_id: str, decision: str, evidence: str,
                    decided_by: str, run_id: str) -> None:
    if decision not in ("CONFIRMED", "REJECTED"):
        raise ValueError("decision must be CONFIRMED or REJECTED")
    if not N.clean(decided_by):
        raise ValueError("Enter your name so the decision has a record of who made it.")
    append_rows(confirm_path(), CONFIRM_COLUMNS, [{
        "Contact_Key": contact_key, "Contact_Label": label, "TP_ID": tp_id, "Decision": decision,
        "Evidence": evidence, "Decided_By": decided_by, "Decided_At_UTC": utc_now(), "Run_ID": run_id}])


# ---------------------------------------------------------------- ledger
def ledger_path() -> Path:
    return data_dir() / "history" / "ledger.csv"


def append_ledger(rows: list[dict]) -> None:
    append_rows(ledger_path(), LEDGER_COLUMNS, rows)


def verified_complete_ids() -> dict[str, str]:
    """Change_IDs independently verified complete in any earlier run -> run id."""
    out = {}
    for r in read_rows(ledger_path()):
        if r.get("Run_Type") == "verify" and r.get("Result") == "VERIFIED_COMPLETE":
            out[r["Change_ID"]] = r["Run_ID"]
    return out


# ---------------------------------------------------------------- hold list
def hold_path() -> Path:
    return data_dir() / "config" / "hold_list.csv"


def load_holds() -> dict[str, str]:
    return {N.clean(r["Change_ID"]): r.get("Reason", "") for r in read_rows(hold_path())
            if N.clean(r.get("Change_ID"))}


def add_hold(change_id: str, reason: str, added_by: str) -> None:
    append_rows(hold_path(), HOLD_COLUMNS, [{"Change_ID": change_id, "Reason": reason,
                                             "Added_By": added_by, "Added_At_UTC": utc_now()}])


def list_runs() -> list[dict]:
    runs = []
    for p in sorted((data_dir() / "runs").iterdir(), reverse=True):
        if p.is_dir():
            runs.append({"Run_ID": p.name, "Files": ", ".join(sorted(f.name for f in p.iterdir()))})
    return runs
