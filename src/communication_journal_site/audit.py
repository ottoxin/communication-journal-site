from __future__ import annotations

import csv
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .models import JournalConfig
from .normalize import normalize_whitespace


def audit_journal_registry(
    journals: list[JournalConfig],
    output_path: str | Path,
    source_list_path: str | Path | None = None,
) -> Path:
    rows: list[dict[str, str]] = []
    seen_issns: dict[str, str] = {}
    normalized_registry_titles = {_normalize_title(journal.title): journal for journal in journals}
    for journal in journals:
        status = "ok"
        detail = ""
        if not journal.active:
            status = "disabled"
        elif not journal.collect:
            status = "not_collecting"
        elif not journal.issns:
            status = "missing_issn"
        for issn in journal.issns:
            if issn in seen_issns:
                status = "duplicate_issn"
                detail = f"Also used by {seen_issns[issn]}"
            else:
                seen_issns[issn] = journal.id
        rows.append(
            {
                "source": "registry",
                "status": status,
                "journal_id": journal.id,
                "title": journal.title,
                "issns": "; ".join(journal.issns),
                "publisher": journal.publisher,
                "primary_subarea": journal.primary_subarea,
                "detail": detail,
            }
        )
    if source_list_path:
        for source_row in read_source_list(source_list_path):
            title = _source_title(source_row)
            if not title:
                continue
            if _normalize_title(title) in normalized_registry_titles:
                continue
            rows.append(
                {
                    "source": Path(source_list_path).name,
                    "status": "missing_from_registry",
                    "journal_id": "",
                    "title": title,
                    "issns": _source_issn(source_row),
                    "publisher": _source_publisher(source_row),
                    "primary_subarea": "",
                    "detail": "Present in source list but not configured locally.",
                }
            )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source",
                "status",
                "journal_id",
                "title",
                "issns",
                "publisher",
                "primary_subarea",
                "detail",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    return output


def read_source_list(path: str | Path) -> list[dict[str, str]]:
    source_path = Path(path)
    if source_path.suffix.lower() == ".csv":
        with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if source_path.suffix.lower() == ".xlsx":
        return _read_xlsx(source_path)
    raise ValueError("Source list must be a .csv or .xlsx file.")


def _read_xlsx(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _xlsx_shared_strings(archive)
        sheet_name = "xl/worksheets/sheet1.xml"
        root = ET.fromstring(archive.read(sheet_name))
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[str]] = []
    for row in root.findall(".//x:sheetData/x:row", ns):
        values: list[str] = []
        for cell in row.findall("x:c", ns):
            values.append(_xlsx_cell_text(cell, shared_strings, ns))
        rows.append(values)
    if not rows:
        return []
    headers = [normalize_whitespace(value) for value in rows[0]]
    output: list[dict[str, str]] = []
    for row in rows[1:]:
        output.append({header: row[index] if index < len(row) else "" for index, header in enumerate(headers)})
    return output


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        raw = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    strings: list[str] = []
    for item in root.findall(".//x:si", ns):
        strings.append(normalize_whitespace(" ".join(text.text or "" for text in item.findall(".//x:t", ns))))
    return strings


def _xlsx_cell_text(cell: ET.Element, shared_strings: list[str], ns: dict[str, str]) -> str:
    value = cell.find("x:v", ns)
    if value is None or value.text is None:
        inline = cell.find(".//x:t", ns)
        return normalize_whitespace(inline.text if inline is not None else "")
    if cell.attrib.get("t") == "s":
        index = int(value.text)
        return shared_strings[index] if index < len(shared_strings) else ""
    return normalize_whitespace(value.text)


def _source_title(row: dict[str, str]) -> str:
    return _first(row, "Title", "Source Title", "Source title", "Journal", "Journal Title", "journal")


def _source_issn(row: dict[str, str]) -> str:
    return _first(row, "ISSN", "Issn", "Print ISSN", "E-ISSN", "EISSN", "issn")


def _source_publisher(row: dict[str, str]) -> str:
    return _first(row, "Publisher", "publisher")


def _first(row: dict[str, str], *keys: str) -> str:
    lowered = {key.lower(): value for key, value in row.items()}
    for key in keys:
        value = row.get(key) or lowered.get(key.lower())
        if value:
            return normalize_whitespace(value)
    return ""


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())

