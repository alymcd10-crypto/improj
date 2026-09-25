# LMT CRM Reconciliation Tool

A local tool for The Leslie McDonnell Team. It compares correction worklists with Top Producer exports, builds
precise worklists for Cowork, and verifies completed edits.

- **Runs only on your computer.** It never connects to Top Producer, AI services, analytics or any database.
  Streamlit's usage statistics are switched off and the app listens only on `localhost`.
- **Never edits Top Producer** and **never changes your original files.** Files are read into memory, and
  outputs are written to new, dated run folders.

## Start the tool

**One-time setup:** install Python 3.10 or newer from <https://www.python.org/downloads/>. On Windows, tick
**"Add python.exe to PATH"** during installation. The first launch then downloads the app's libraries, which
takes a few minutes and needs internet. After that, it starts in seconds.

| Computer | How to start |
|---|---|
| Windows | Double-click **`Start_Reconciliation_Tool.bat`** |
| Mac | Double-click **`Start_Reconciliation_Tool.command`**. If macOS blocks it, right-click, choose **Open**, then **Open** again. |
| Either (fallback) | Open Terminal in this folder and run `bash start_reconciliation_tool.sh` (Mac/Linux) |

Your browser opens at **http://localhost:8501**. To stop the tool, close the black terminal window.

**Try it first with fake data:** the `synthetic_demo/` folder contains fictional files. Load
`SYNTHETIC_Category_Mapping.csv` on the **Category mapping** page, then use the other files on pages 1 and 2.

## Using it

**Page 1: Check what still needs correction**

1. Select the correction worklist and the current Top Producer export. The master workbook, audit workbook
   and earlier corrections file are optional.
2. Open **Columns detected** and confirm each column is right. Leave a role blank if the file has no such
   column.
3. Click **Run comparison**.
4. Read the totals, search the table, and download:
   - **Remaining_Corrections.csv**: the items that are still missing and eligible. This is a **Cowork
     worklist, not a Top Producer import file.** It keeps every original column (including `Change_ID`,
     evidence and Cowork's `Status`), plus `Recon_*` columns that explain the finding.
   - **Review_Required.csv**: everything a person must decide, each with a specific reason.
   - **All_Results.csv** and **Run_Summary.md**: the full detail, plus file names, file hashes, counts,
     count checks and limitations.

**Page 2: Verify completed work.** Select the pre-edit export, the completed Cowork worklist and the post-edit
export. **Verification_Results.csv** shows Cowork's reported status and this tool's independent result side by
side. Two separate columns show whether the **requested addition is present** and whether the **earlier
information in that field was retained**. An item is only `VERIFIED_COMPLETE` when both are confirmed. Other
differences on the contact are reported, not blamed on Cowork, because someone else may have edited the CRM.

**Page 3: Review exceptions.** Filter the review items by type. When you have checked a contact yourself, you
can **confirm or reject a match** here. Your name and reason are saved, and later runs reuse the decision.
Software suggestions are never saved as confirmed.

**Category mapping.** This is the reviewable list of how source categories map to Top Producer contact types.
A category is proposed only if it has an `APPROVED` row, or if Top Producer already has a type with the
identical label. Use `Conflicts_With` to list existing types that should block an addition. Every save keeps a
backup copy.

**History & help.** Past runs and their files, plus the meaning of each result.

## What the results mean

| Result | Meaning |
|---|---|
| ALREADY_CORRECT | The export already shows the proposed information. Nothing to add. |
| MISSING_IN_CURRENT_EXPORT | Matched contact; the information is not in the export. Cowork must still check the live record before writing. |
| CONFLICT_REVIEW | Something conflicts: a newer change, an opt-out, a partly present note, or a different value. |
| MATCH_REVIEW | The contact is not certain: several candidates, a name-only match, or an ID with a different name. |
| NOT_FOUND | No matching contact, or a stored ID is not in this export. |
| NOT_VERIFIABLE_FROM_EXPORT | The export has no column for this field, or the column is blank for every contact. This is never treated as "blank". |
| NEEDS_REVIEW | A review-only item, an unmapped category, unclear phone digits or extension, an unsupported change type, or a missing source detail. |
| VERIFIED_COMPLETE | The post-edit export shows the requested addition on the right contact **and** everything already in that field is still there. |
| ADDED_BUT_EARLIER_INFO_MISSING | The addition is there, but something already in that field is gone (for example a contact type or the primary phone). Needs review; the exports cannot show who removed it. |
| ADDED_RETENTION_NOT_VERIFIABLE | The addition is there, but an export lacks the field needed to check that earlier information was kept. Not counted as verified. |
| EXPECTED_CHANGE_NOT_FOUND | Cowork reported it done, but the post-edit export does not show it. |

Only items that meet **all** of these conditions go to `Remaining_Corrections.csv`:

- queue is `CORRECTION_CANDIDATE`
- Cowork status is pending or in progress
- the contact matched
- the information is missing
- nothing needs review
- the item was not already verified complete in an earlier run

`REVIEW_ONLY` rows are never promoted.

## How contacts are matched

1. A previously verified ID is used first, from the worklist `TP_ID` column, a match you confirmed, or the
   earlier corrections file. The name is still checked against it.
2. Without an ID, the contact needs a name (person, spouse, household or business) **plus** a matching email,
   phone or street address. A name alone is never enough.
3. Shared family emails, phones and addresses only count together with the right name. If several profiles
   qualify, the item goes to review.
4. Similar names and details that match a differently named profile are shown as suggestions for review.
   They are never matched automatically.
5. A master worksheet row number is a source reference, never a contact ID.
6. If one contact ID appears on more than one row of a Top Producer export, the tool never picks a row. Every
   change that would touch that ID goes to review ("Duplicate Top Producer ID"), whether it was found by ID or
   by name and details. This is separate from the master workbook, where the same person appearing on
   All Contacts and on agent/category tabs is expected.

## What each check does

- **Phones:** every exported phone column (and the notes) is checked. Formatting is ignored, but no country
  code is invented. Extensions count only when they are marked (`x`, `ext`, `#`). Extra digits, differing
  extensions, or a number that appears on another profile go to review.
- **Categories:** compared as sets, so order and case don't matter, while the original labels are kept. Only
  missing types are proposed. An opt-out note or a "Do Not Contact"-type label holds the item.
- **Notes:** only the missing detail is proposed. A detail counts as already present only when its words appear
  together, in the same order; capitalisation, spacing, line breaks, punctuation and `&`/`and` don't matter.
  The same words scattered through unrelated sentences do not count. The same words reordered within one
  sentence ("John Pinto was the agent") or a different person or role ("Agent: John Smith") go to review, so
  nothing is marked done or added twice by guesswork. Whole-note replacements always go to review.
- **Birthdays:** placeholder years such as 1800 are never treated as real years.
- **Downloads:** any cell that a spreadsheet could run as a formula gets a leading `'`. The stored values are
  unchanged.

## Where things are saved

Everything goes in `data/` inside this folder. That folder is excluded from Git, so keep it private.

- `data/runs/<run id>/`: the outputs, the summary, and exact copies of the input files for that run. Runs are
  never overwritten.
- `data/history/ledger.csv`: an append-only record of every result per `Change_ID`. It is how reruns avoid
  repeating completed work.
- `data/history/confirmed_matches.csv`: your match decisions, with name, reason and time.
- `data/config/category_mapping.csv` (with backups) and `data/config/hold_list.csv`. To hold a `Change_ID`,
  add a row with `Change_ID,Reason`.

## For whoever maintains this

```
pip install -r requirements.txt
python -m pytest -q            # synthetic tests only; no client data in the repo
python make_synthetic_demo.py  # regenerate synthetic_demo/
python cli.py reconcile --worklist W.csv --export TP.csv   # optional command-line run
```

The code is split into three layers:

- **Loading and normalizing:** `recon/loaders.py`, `normalize.py`, `columns.py`, `contacts.py`
- **Matching:** `recon/matching.py`
- **Checks and workflows:** `recon/checks.py`, `reconcile.py`, `verify.py`, `sources.py`, `outputs.py`,
  `store.py`

`recon/pipeline.py` orchestrates a run, `app.py` is only the interface, and `launch.py` starts it for the launchers. Never commit client files. The
`.gitignore` blocks spreadsheets and CSVs except the synthetic demo.
