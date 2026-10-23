"""Read CSV or XLSX into (columns, rows) with everything as strings. Capped so a stray 200k-row file can't stall a request."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime

MAX_ROWS = 5000


class ParseError(Exception):
    pass


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d") if (v.hour, v.minute, v.second) == (0, 0, 0) else v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def parse(filename: str, data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        return _parse_xlsx(data)
    if name.endswith((".csv", ".txt")):
        return _parse_csv(data)
    raise ParseError("Upload a .csv or .xlsx file.")


def _parse_csv(data: bytes):
    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ParseError("Could not read the file's text encoding.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = next(reader)
    except StopIteration:
        raise ParseError("The file is empty.")
    columns = [h.strip() for h in header]
    if not any(columns):
        raise ParseError("The first row must contain column headings.")
    rows = []
    for i, rec in enumerate(reader):
        if not any(c.strip() for c in rec):
            continue
        rows.append({columns[j]: _cell(rec[j]) if j < len(rec) else "" for j in range(len(columns)) if columns[j]})
        if len(rows) >= MAX_ROWS:
            raise ParseError(f"Files are limited to {MAX_ROWS:,} rows per import. Split the file and import in parts.")
    return [c for c in columns if c], rows


def _parse_xlsx(data: bytes):
    try:
        import openpyxl
    except ImportError:  # pragma: no cover
        raise ParseError("XLSX support is not installed.")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ParseError("Could not open the workbook. Save it as .xlsx and try again.")
    ws = wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    try:
        header = next(it)
    except StopIteration:
        raise ParseError("The first sheet is empty.")
    columns = [_cell(h) for h in header]
    if not any(columns):
        raise ParseError("The first row must contain column headings.")
    rows = []
    for rec in it:
        if rec is None or not any(_cell(c) for c in rec):
            continue
        rows.append({columns[j]: _cell(rec[j]) if j < len(rec) else "" for j in range(len(columns)) if columns[j]})
        if len(rows) >= MAX_ROWS:
            raise ParseError(f"Files are limited to {MAX_ROWS:,} rows per import. Split the file and import in parts.")
    return [c for c in columns if c], rows
