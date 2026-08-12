#!/usr/bin/env python3
"""Extract headings, tables, styles, and section geometry from a DOCX file."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


def qn(local: str) -> str:
    return f"{{{W_NS}}}{local}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract a DOCX structural summary without modifying the document."
    )
    parser.add_argument("docx", help="Input DOCX file.")
    parser.add_argument("--output", required=True, help="Output JSON file.")
    parser.add_argument(
        "--include-paragraphs",
        action="store_true",
        help="Include all non-empty paragraph records, not only headings.",
    )
    parser.add_argument(
        "--max-text",
        type=int,
        default=300,
        help="Maximum characters retained for a paragraph or table sample.",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def xml_from_zip(package: zipfile.ZipFile, part: str) -> ET.Element | None:
    try:
        return ET.fromstring(package.read(part))
    except KeyError:
        return None


def paragraph_text(paragraph: ET.Element) -> str:
    pieces: list[str] = []
    for node in paragraph.iter():
        if node.tag == qn("t") and node.text:
            pieces.append(node.text)
        elif node.tag == qn("tab"):
            pieces.append("\t")
        elif node.tag in {qn("br"), qn("cr")}:
            pieces.append("\n")
    return "".join(pieces).strip()


def cell_text(cell: ET.Element) -> str:
    paragraphs = [paragraph_text(node) for node in cell.findall(".//w:p", NS)]
    return " / ".join(text for text in paragraphs if text)


def load_styles(root: ET.Element | None) -> dict[str, dict[str, object]]:
    styles: dict[str, dict[str, object]] = {}
    if root is None:
        return styles
    for style in root.findall("w:style", NS):
        style_id = style.get(qn("styleId"), "")
        name_node = style.find("w:name", NS)
        based_node = style.find("w:basedOn", NS)
        outline_node = style.find("w:pPr/w:outlineLvl", NS)
        styles[style_id] = {
            "name": name_node.get(qn("val"), "") if name_node is not None else "",
            "based_on": based_node.get(qn("val"), "") if based_node is not None else "",
            "outline_level": (
                int(outline_node.get(qn("val"), ""))
                if outline_node is not None
                and outline_node.get(qn("val"), "").isdigit()
                else None
            ),
        }
    return styles


def infer_heading_level(
    style_id: str, styles: dict[str, dict[str, object]]
) -> int | None:
    style = styles.get(style_id, {})
    outline = style.get("outline_level")
    if isinstance(outline, int) and 0 <= outline <= 8:
        return outline + 1
    candidates = [style_id, str(style.get("name", ""))]
    for value in candidates:
        match = re.search(r"(?:heading|标题)\s*([1-9])", value, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def section_record(sect_pr: ET.Element, index: int) -> dict[str, object]:
    page_size = sect_pr.find("w:pgSz", NS)
    margins = sect_pr.find("w:pgMar", NS)
    width = page_size.get(qn("w"), "") if page_size is not None else ""
    height = page_size.get(qn("h"), "") if page_size is not None else ""
    orient = page_size.get(qn("orient"), "") if page_size is not None else ""
    if not orient and width.isdigit() and height.isdigit():
        orient = "landscape" if int(width) > int(height) else "portrait"
    return {
        "section_index": index,
        "orientation": orient or "portrait",
        "page_width_twips": width,
        "page_height_twips": height,
        "margins_twips": {
            key: margins.get(qn(key), "") if margins is not None else ""
            for key in ("top", "right", "bottom", "left", "header", "footer", "gutter")
        },
    }


def main() -> int:
    args = parse_args()
    input_path = Path(args.docx).resolve()
    output_path = Path(args.output).resolve()
    if not input_path.is_file():
        print(f"Input DOCX not found: {input_path}", file=sys.stderr)
        return 1
    if not zipfile.is_zipfile(input_path):
        print(f"Input is not a valid DOCX package: {input_path}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(input_path) as package:
        document = xml_from_zip(package, "word/document.xml")
        styles = load_styles(xml_from_zip(package, "word/styles.xml"))
        if document is None:
            print("DOCX package has no word/document.xml", file=sys.stderr)
            return 1

        body = document.find("w:body", NS)
        if body is None:
            print("DOCX document has no body", file=sys.stderr)
            return 1

        headings: list[dict[str, object]] = []
        paragraphs: list[dict[str, object]] = []
        tables: list[dict[str, object]] = []
        sections: list[dict[str, object]] = []
        style_usage: Counter[str] = Counter()
        paragraph_index = 0
        table_index = 0

        for child in body:
            if child.tag == qn("p"):
                paragraph_index += 1
                text = paragraph_text(child)
                style_node = child.find("w:pPr/w:pStyle", NS)
                style_id = (
                    style_node.get(qn("val"), "") if style_node is not None else ""
                )
                style_name = str(styles.get(style_id, {}).get("name", ""))
                if style_id:
                    style_usage[style_id] += 1
                heading_level = infer_heading_level(style_id, styles)
                record = {
                    "paragraph_index": paragraph_index,
                    "style_id": style_id,
                    "style_name": style_name,
                    "heading_level": heading_level,
                    "text": text[: args.max_text],
                }
                if text and heading_level is not None:
                    headings.append(record)
                if text and args.include_paragraphs:
                    paragraphs.append(record)

                sect_pr = child.find("w:pPr/w:sectPr", NS)
                if sect_pr is not None:
                    sections.append(section_record(sect_pr, len(sections) + 1))

            elif child.tag == qn("tbl"):
                table_index += 1
                rows = child.findall("w:tr", NS)
                row_values: list[list[str]] = []
                max_columns = 0
                for row in rows:
                    values = [cell_text(cell) for cell in row.findall("w:tc", NS)]
                    max_columns = max(max_columns, len(values))
                    if len(row_values) < 3:
                        row_values.append([value[: args.max_text] for value in values])
                tables.append(
                    {
                        "table_index": table_index,
                        "row_count": len(rows),
                        "column_count": max_columns,
                        "sample_rows": row_values,
                    }
                )
            elif child.tag == qn("sectPr"):
                sections.append(section_record(child, len(sections) + 1))

        payload = {
            "source": {
                "path": str(input_path),
                "size_bytes": input_path.stat().st_size,
                "sha256": sha256_file(input_path),
            },
            "summary": {
                "paragraph_count": paragraph_index,
                "heading_count": len(headings),
                "table_count": len(tables),
                "section_count": len(sections),
                "landscape_section_count": sum(
                    1 for section in sections if section["orientation"] == "landscape"
                ),
            },
            "headings": headings,
            "tables": tables,
            "sections": sections,
            "style_usage": [
                {
                    "style_id": style_id,
                    "style_name": styles.get(style_id, {}).get("name", ""),
                    "count": count,
                }
                for style_id, count in style_usage.most_common()
            ],
        }
        if args.include_paragraphs:
            payload["paragraphs"] = paragraphs

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Wrote {len(headings)} headings, {len(tables)} tables, "
        f"and {len(sections)} sections to {output_path}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
