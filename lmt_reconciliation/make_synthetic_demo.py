"""Write SYNTHETIC demo files (fictional people only) to synthetic_demo/ for trying the app."""
from pathlib import Path

from tests import synthetic as S
from tests.test_recon import completed_worklist, post_contacts

out = Path(__file__).parent / "synthetic_demo"
out.mkdir(exist_ok=True)
(out / "SYNTHETIC_Correction_Worklist.csv").write_bytes(S.worklist_bytes())
(out / "SYNTHETIC_TP_Export_Before.csv").write_bytes(S.export_bytes())
(out / "SYNTHETIC_Completed_Cowork_Worklist.csv").write_bytes(S.worklist_bytes(completed_worklist()))
(out / "SYNTHETIC_TP_Export_After.csv").write_bytes(S.export_bytes(post_contacts()))
(out / "SYNTHETIC_Master.xlsx").write_bytes(S.master_xlsx_bytes())
(out / "SYNTHETIC_Category_Mapping.csv").write_bytes(
    S.to_csv(["Source_Category", "TP_Contact_Type", "Status", "Conflicts_With", "Notes"], S.mapping_rows()))
print(f"Synthetic demo files written to {out}")
