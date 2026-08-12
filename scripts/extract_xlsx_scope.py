#!/usr/bin/env python3
"""Extract workbook sheets and candidate construction-list rows from XLSX."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"x": MAIN_NS, "r": REL_NS}

HEADER_ALIASES = {
    "sequence": {"序号", "编号", "序列号"},
    "original_name": {"系统名称", "建设项", "建设内容", "项目名称", "产品名称", "模块名称"},
    "domain": {"分类", "业务域", "所属系统", "系统分类"},
    "item_type": {"类型", "建设类型", "费用类型"},
    "construction_mode": {"建设方式", "建设性质", "新建/升级"},
    "quantity": {"数量", "数目"},
    "unit": {"单位", "计量单位"},
    "unit_price": {"单价", "含税单价"},
    "amount": {"合价", "总价", "金额", "含税总价"},
    "description": {"功能描述", "建设说明", "规格参数", "技术要求", "主要功能"},
    "investment_category": {"投资分类", "费用分类", "费用类别", "投资类别"},
    "acceptance_target": {"验收目标", "验收指标", "建设目标"},
    "notes": {"备注", "说明"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract raw construction-list rows from an XLSX without changing it."
    )
    parser.add_argument("xlsx", help="Input XLSX file.")
    parser.add_argument("--output", required=True, help="Output JSON file.")
    parser.add_argument(
        "--sheet",
        action="append",
        default=[],
        help="Sheet name to extract. Repeat for multiple sheets. Default: all visible sheets.",
    )
    parser.add_argument(
        "--header-row",
        type=int,
        help="One-based header row. If omitted, detect a candidate in the first 20 rows.",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=5000,
        help="Maximum data rows extracted per sheet.",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def column_index(reference: str) -> int:
    letters = re.match(r"([A-Z]+)", reference.upper())
    if not letters:
        return 0
    value = 0
    for char in letters.group(1):
        value = value * 26 + ord(char) - ord("A") + 1
    return value


def read_shared_strings(package: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(package.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values: list[str] = []
    for item in root.findall("x:si", NS):
        values.append("".join(node.text or "" for node in item.findall(".//x:t", NS)))
    return values


def workbook_sheets(package: zipfile.ZipFile) -> list[dict[str, str]]:
    workbook = ET.fromstring(package.read("xl/workbook.xml"))
    rels = ET.fromstring(package.read("xl/_rels/workbook.xml.rels"))
    relation_map = {
        rel.get("Id", ""): rel.get("Target", "") for rel in rels.findall(
            f"{{{PKG_REL_NS}}}Relationship"
        )
    }
    sheets: list[dict[str, str]] = []
    for sheet in workbook.findall("x:sheets/x:sheet", NS):
        relation_id = sheet.get(f"{{{REL_NS}}}id", "")
        target = relation_map.get(relation_id, "")
        if target.startswith("/"):
            part = target.lstrip("/")
        else:
            part = str(Path("xl") / target).replace("\\", "/")
        sheets.append(
            {
                "name": sheet.get("name", ""),
                "state": sheet.get("state", "visible"),
                "part": part,
            }
        )
    return sheets


def cell_value(cell: ET.Element, shared_strings: list[str]) -> tuple[str, str]:
    cell_type = cell.get("t", "")
    formula_node = cell.find("x:f", NS)
    formula = formula_node.text or "" if formula_node is not None else ""
    if cell_type == "inlineStr":
        value = "".join(node.text or "" for node in cell.findall(".//x:t", NS))
        return value, formula
    value_node = cell.find("x:v", NS)
    raw = value_node.text or "" if value_node is not None else ""
    if cell_type == "s" and raw.isdigit():
        index = int(raw)
        return (
            shared_strings[index] if 0 <= index < len(shared_strings) else raw,
            formula,
        )
    if cell_type == "b":
        return ("TRUE" if raw == "1" else "FALSE"), formula
    return raw, formula


def read_rows(
    package: zipfile.ZipFile, part: str, shared_strings: list[str]
) -> tuple[list[dict[int, str]], dict[str, str], list[str]]:
    root = ET.fromstring(package.read(part))
    rows: list[dict[int, str]] = []
    formulas: dict[str, str] = {}
    merged = [
        node.get("ref", "") for node in root.findall("x:mergeCells/x:mergeCell", NS)
    ]
    for row in root.findall("x:sheetData/x:row", NS):
        values: dict[int, str] = {}
        for cell in row.findall("x:c", NS):
            reference = cell.get("r", "")
            index = column_index(reference)
            value, formula = cell_value(cell, shared_strings)
            if index:
                values[index] = value.strip() if isinstance(value, str) else str(value)
            if formula:
                formulas[reference] = formula
        rows.append(values)
    return rows, formulas, merged


def normalized_header(value: str, column: int, used: set[str]) -> str:
    base = re.sub(r"\s+", " ", value).strip() or f"column_{column}"
    name = base
    suffix = 2
    while name in used:
        name = f"{base}_{suffix}"
        suffix += 1
    used.add(name)
    return name


def header_score(values: dict[int, str]) -> tuple[int, int]:
    non_empty = [value.strip() for value in values.values() if value.strip()]
    alias_values = {alias for group in HEADER_ALIASES.values() for alias in group}
    exact_hits = sum(1 for value in non_empty if value in alias_values)
    partial_hits = sum(
        1
        for value in non_empty
        if any(alias in value for alias in alias_values if len(alias) >= 2)
    )
    return exact_hits * 10 + partial_hits * 2 + len(non_empty), len(non_empty)


def detect_header_row(rows: list[dict[int, str]]) -> int:
    candidates = rows[:20]
    if not candidates:
        return 1
    scored = [(header_score(row), index + 1) for index, row in enumerate(candidates)]
    scored.sort(key=lambda item: (item[0][0], item[0][1], -item[1]), reverse=True)
    return scored[0][1]


def detect_columns(headers: list[str]) -> dict[str, list[str]]:
    detected: dict[str, list[str]] = {}
    for field, aliases in HEADER_ALIASES.items():
        matches = [
            header
            for header in headers
            if header in aliases or any(alias in header for alias in aliases)
        ]
        if matches:
            detected[field] = matches
    return detected


def extract_sheet(
    package: zipfile.ZipFile,
    sheet: dict[str, str],
    shared_strings: list[str],
    requested_header_row: int | None,
    max_rows: int,
) -> dict[str, object]:
    raw_rows, formulas, merged = read_rows(package, sheet["part"], shared_strings)
    header_row = requested_header_row or detect_header_row(raw_rows)
    header_values = raw_rows[header_row - 1] if 0 < header_row <= len(raw_rows) else {}
    max_column = max(
        [0]
        + list(header_values.keys())
        + [column for row in raw_rows for column in row.keys()]
    )
    used: set[str] = set()
    headers = [
        normalized_header(header_values.get(column, ""), column, used)
        for column in range(1, max_column + 1)
    ]

    records: list[dict[str, object]] = []
    for source_row_number, raw_row in enumerate(
        raw_rows[header_row : header_row + max_rows], start=header_row + 1
    ):
        if not any(value.strip() for value in raw_row.values()):
            continue
        record: dict[str, object] = {"_source_row": source_row_number}
        for column, header in enumerate(headers, start=1):
            record[header] = raw_row.get(column, "")
        records.append(record)

    return {
        "name": sheet["name"],
        "state": sheet["state"],
        "part": sheet["part"],
        "source_row_count": len(raw_rows),
        "header_row": header_row,
        "headers": headers,
        "detected_columns": detect_columns(headers),
        "merged_ranges": merged,
        "formula_count": len(formulas),
        "formulas": formulas,
        "extracted_row_count": len(records),
        "rows": records,
    }


def build_payload(
    input_path: Path,
    requested_sheets: set[str] | None = None,
    header_row: int | None = None,
    max_rows: int = 5000,
) -> dict[str, object]:
    input_path = input_path.resolve()
    requested = requested_sheets or set()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    if not zipfile.is_zipfile(input_path):
        raise ValueError(f"Input is not a valid XLSX package: {input_path}")
    with zipfile.ZipFile(input_path) as package:
        shared_strings = read_shared_strings(package)
        sheets = workbook_sheets(package)
        selected = [
            sheet
            for sheet in sheets
            if (not requested and sheet["state"] == "visible")
            or sheet["name"] in requested
        ]
        missing = sorted(requested - {sheet["name"] for sheet in sheets})
        if missing:
            raise ValueError(f"Requested sheets not found: {', '.join(missing)}")
        extracted = [
            extract_sheet(package, sheet, shared_strings, header_row, max_rows)
            for sheet in selected
        ]
    return {
        "source": {
            "path": str(input_path),
            "size_bytes": input_path.stat().st_size,
            "sha256": sha256_file(input_path),
        },
        "sheet_count": len(extracted),
        "sheets": extracted,
        "notes": [
            "Rows are mechanically extracted and have not been confirmed as project scope.",
            "Detected column mappings are suggestions and require semantic review.",
            "Numeric and date cells are retained as their stored values.",
        ],
    }


def main() -> int:
    args = parse_args()
    input_path = Path(args.xlsx).resolve()
    output_path = Path(args.output).resolve()
    if not input_path.is_file():
        print(f"Input XLSX not found: {input_path}", file=sys.stderr)
        return 1
    if not zipfile.is_zipfile(input_path):
        print(f"Input is not a valid XLSX package: {input_path}", file=sys.stderr)
        return 1

    try:
        payload = build_payload(
            input_path, set(args.sheet), args.header_row, args.max_rows
        )
    except (KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
        print(f"Failed to parse XLSX: {exc}", file=sys.stderr)
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Wrote {payload['sheet_count']} sheets and "
        f"{sum(sheet['extracted_row_count'] for sheet in payload['sheets'])} rows "
        f"to {output_path}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
