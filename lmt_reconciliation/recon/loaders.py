"""Read CSV / XLSX files exactly once, as text, without changing the source.

Every loader works on bytes already in memory, so the original file on disk
is never opened for writing.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
from dataclasses import dataclass, field

from .normalize import clean, header_key

ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
DELIMITERS = [",", ";", "\t", "|"]


@dataclass
class Table:
    name: str                      # file name (and sheet, for workbooks)
    headers: list[str]
    rows: list[dict]               # each row: header -> raw text
    row_numbers: list[int]         # source line / worksheet row for each row
    warnings: list[str] = field(default_factory=list)
    encoding: str = ""
    delimiter: str = ""
    header_row: int = 1
    sheet: str = ""

    def column(self, header: str) -> list[str]:
        return [r.get(header, "") for r in self.rows]


@dataclass
class LoadedFile:
    filename: str
    sha256: str
    size: int
    tables: list[Table]            # one for CSV, one per sheet for XLSX
    warnings: list[str] = field(default_factory=list)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- CSV
def decode_bytes(data: bytes) -> tuple[str, str]:
    for enc in ENCODINGS:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not decode file")  # latin-1 never fails; defensive


def sniff_delimiter(text: str) -> str:
    sample = text[:50000]
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(DELIMITERS)).delimiter
    except csv.Error:
        first = sample.splitlines()[0] if sample else ""
        return max(DELIMITERS, key=first.count)


def dedupe_headers(headers: list[str]) -> tuple[list[str], list[str]]:
    seen: dict[str, int] = {}
    out, warnings = [], []
    for i, h in enumerate(headers):
        h = clean(h) or f"(blank column {i + 1})"
        if h in seen:
            seen[h] += 1
            new = f"{h} ({seen[h]})"
            warnings.append(f'Column name "{h}" appears more than once; the repeat is shown as "{new}".')
            h = new
        else:
            seen[h] = 1
        out.append(h)
    return out, warnings


def read_csv_bytes(data: bytes, name: str) -> Table:
    text, enc = decode_bytes(data)
    delim = sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)
    rows_out, numbers, warnings = [], [], []
    headers: list[str] = []
    header_line = 1
    try:
        for raw in reader:
            line_no = reader.line_num
            if not headers:
                if not any(clean(c) for c in raw):
                    continue
                headers, w = dedupe_headers(raw)
                warnings.extend(w)
                header_line = line_no
                continue
            if not any(clean(c) for c in raw):
                continue
            if len(raw) > len(headers):
                extra = [c for c in raw[len(headers):] if clean(c)]
                if extra:
                    warnings.append(
                        f"Line {line_no}: {len(raw)} values but {len(headers)} columns. "
                        "Extra values were kept in a column named '(overflow)'. "
                        "This usually means an unquoted comma; check that line in the source file.")
            elif len(raw) < len(headers):
                warnings.append(
                    f"Line {line_no}: only {len(raw)} of {len(headers)} values; missing values treated as blank.")
            row = {h: (raw[i] if i < len(raw) else "") for i, h in enumerate(headers)}
            if len(raw) > len(headers):
                row["(overflow)"] = delim.join(raw[len(headers):])
            rows_out.append(row)
            numbers.append(line_no)
    except csv.Error as exc:
        warnings.append(f"CSV parsing stopped early near line {reader.line_num}: {exc}. "
                        "Check for an unmatched quote mark in the source file.")
    if any("(overflow)" in r for r in rows_out) and "(overflow)" not in headers:
        headers.append("(overflow)")
    if enc != "utf-8-sig":
        warnings.append(f"File is not UTF-8; read as {enc}. Accented characters were decoded using {enc}.")
    return Table(name=name, headers=headers, rows=rows_out, row_numbers=numbers,
                 warnings=warnings, encoding=enc, delimiter=delim,
                 header_row=header_line)


# ---------------------------------------------------------------- XLSX
def _cell_text(cell) -> str:
    v = cell.value
    if v is None:
        return ""
    fmt = getattr(cell, "number_format", "") or ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, dt.datetime):
        if v.time() == dt.time(0, 0):
            return v.date().isoformat()
        return v.isoformat(sep=" ")
    if isinstance(v, (dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, (int, float)):
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        text = str(v)
        # A zero-padded number format (e.g. "00000" for ZIP) keeps its leading zeros.
        if isinstance(v, int) and fmt and set(fmt) == {"0"}:
            text = text.zfill(len(fmt))
        return text
    return str(v)


def guess_header_row(grid: list[list[str]], known_headers: set[str] | None = None) -> int:
    """Index (0-based) of the most header-like row in the first 15 rows."""
    best, best_score = 0, -1.0
    for i, row in enumerate(grid[:15]):
        cells = [clean(c) for c in row if clean(c)]
        if len(cells) < 2:
            continue
        texty = sum(1 for c in cells if not c.replace(".", "").isdigit())
        known = sum(1 for c in cells if known_headers and header_key(c) in known_headers)
        score = texty + 3 * known - 0.1 * i
        if score > best_score:
            best, best_score = i, score
    return best


def read_xlsx_bytes(data: bytes, name: str, known_headers: set[str] | None = None) -> list[Table]:
    import openpyxl  # imported lazily so CSV-only use needs no extra install

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    tables = []
    for ws in wb.worksheets:
        grid, numbers = [], []
        for row in ws.iter_rows():
            vals = [_cell_text(c) for c in row]
            if any(clean(v) for v in vals):
                grid.append(vals)
                numbers.append(row[0].row if row and hasattr(row[0], "row") else len(grid))
        if not grid:
            tables.append(Table(name=f"{name} › {ws.title}", headers=[], rows=[], row_numbers=[],
                                sheet=ws.title, warnings=["Sheet is empty."]))
            continue
        h_idx = guess_header_row(grid, known_headers)
        headers, warnings = dedupe_headers(grid[h_idx])
        rows, rnums = [], []
        for vals, rn in zip(grid[h_idx + 1:], numbers[h_idx + 1:]):
            row = {h: (vals[i] if i < len(vals) else "") for i, h in enumerate(headers)}
            if len(vals) > len(headers) and any(clean(v) for v in vals[len(headers):]):
                warnings.append(f"Row {rn}: values beyond the last headed column were ignored.")
            rows.append(row)
            rnums.append(rn)
        tables.append(Table(name=f"{name} › {ws.title}", headers=headers, rows=rows,
                            row_numbers=rnums, warnings=warnings, sheet=ws.title,
                            header_row=numbers[h_idx]))
    wb.close()
    return tables


def load_file(data: bytes, filename: str, known_headers: set[str] | None = None) -> LoadedFile:
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xlsm")):
        tables = read_xlsx_bytes(data, filename, known_headers)
    elif lower.endswith((".csv", ".txt", ".tsv")):
        tables = [read_csv_bytes(data, filename)]
    else:
        raise ValueError(f"{filename}: unsupported file type. Use .csv or .xlsx.")
    return LoadedFile(filename=filename, sha256=sha256_bytes(data), size=len(data), tables=tables)


def load_path(path: str, known_headers: set[str] | None = None) -> LoadedFile:
    with open(path, "rb") as fh:  # read-only
        data = fh.read()
    import os
    return load_file(data, os.path.basename(path), known_headers)
