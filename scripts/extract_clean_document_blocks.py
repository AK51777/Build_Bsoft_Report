#!/usr/bin/env python3
"""Extract clean, source-traceable text blocks from DOCX, Markdown, or TXT."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


def qn(local: str) -> str:
    return f"{{{W_NS}}}{local}"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}-{sha256_text('|'.join(str(part) for part in parts))[:16]}"


def clean_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).replace("\u00a0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def paragraph_text(paragraph: ET.Element) -> str:
    pieces: list[str] = []
    for node in paragraph.iter():
        if node.tag == qn("t") and node.text:
            pieces.append(node.text)
        elif node.tag == qn("tab"):
            pieces.append("\t")
        elif node.tag in {qn("br"), qn("cr")}:
            pieces.append("\n")
    return clean_text("".join(pieces))


def heading_level(paragraph: ET.Element) -> int | None:
    style = paragraph.find("w:pPr/w:pStyle", NS)
    style_id = style.get(qn("val"), "") if style is not None else ""
    match = re.search(r"(?:heading|标题)\s*([1-9])", style_id, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def table_text(table: ET.Element) -> str:
    rows: list[str] = []
    for row in table.findall("w:tr", NS):
        cells = [paragraph_text(cell) for cell in row.findall("w:tc", NS)]
        rows.append(" | ".join(cells))
    return clean_text("\n".join(row for row in rows if row))


def docx_blocks(path: Path) -> list[dict[str, object]]:
    with zipfile.ZipFile(path) as package:
        document = ET.fromstring(package.read("word/document.xml"))
    body = document.find("w:body", NS)
    if body is None:
        raise ValueError("DOCX does not contain a document body")

    blocks: list[dict[str, object]] = []
    headings: list[str] = []
    paragraph_index = 0
    table_index = 0
    for child in body:
        if child.tag == qn("p"):
            paragraph_index += 1
            text = paragraph_text(child)
            if not text:
                continue
            level = heading_level(child)
            if level is not None:
                headings = headings[: level - 1]
                headings.append(text)
                block_type = "heading"
            else:
                block_type = "paragraph"
            blocks.append(
                {
                    "block_type": block_type,
                    "heading_path": headings.copy(),
                    "source_location": f"paragraph:{paragraph_index}",
                    "clean_text": text,
                }
            )
        elif child.tag == qn("tbl"):
            table_index += 1
            text = table_text(child)
            if text:
                blocks.append(
                    {
                        "block_type": "table",
                        "heading_path": headings.copy(),
                        "source_location": f"table:{table_index}",
                        "clean_text": text,
                    }
                )
    return blocks


def text_blocks(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8-sig")
    blocks: list[dict[str, object]] = []
    headings: list[str] = []
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        cleaned = clean_text("\n".join(paragraph_lines))
        if cleaned:
            blocks.append(
                {
                    "block_type": "paragraph",
                    "heading_path": headings.copy(),
                    "source_location": f"paragraph:{len(blocks) + 1}",
                    "clean_text": cleaned,
                }
            )
        paragraph_lines.clear()

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", raw_line)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            title = clean_text(heading.group(2))
            headings = headings[: level - 1]
            headings.append(title)
            blocks.append(
                {
                    "block_type": "heading",
                    "heading_path": headings.copy(),
                    "source_location": f"line:{line_number}",
                    "clean_text": title,
                }
            )
        elif raw_line.strip():
            paragraph_lines.append(raw_line)
        else:
            flush_paragraph()
    flush_paragraph()
    return blocks


def build_payload(path: Path, project_code: str, title: str, contains_personal_data: bool) -> dict[str, object]:
    raw = path.read_bytes()
    source_hash = sha256_bytes(raw)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        blocks = docx_blocks(path)
        source_type = "DOCX"
    elif suffix in {".md", ".txt"}:
        blocks = text_blocks(path)
        source_type = suffix.removeprefix(".").upper()
    else:
        raise ValueError("Only DOCX, Markdown, and TXT files are supported")

    document_id = stable_id("DOC", project_code, source_hash)
    for index, block in enumerate(blocks, start=1):
        block["block_id"] = stable_id("BLOCK", document_id, index, block["clean_text"])
        block["block_index"] = index
        block["clean_text_sha256"] = sha256_text(str(block["clean_text"]))

    return {
        "document": {
            "document_id": document_id,
            "project_code": project_code,
            "title": title or path.stem,
            "source_path": str(path.resolve()),
            "source_sha256": source_hash,
            "source_type": source_type,
            "cleaning_version": "v1",
            "cleaning_method": "unicode-normalization + whitespace-normalization + source-block-extraction",
            "contains_personal_data": contains_personal_data,
            "metadata": {"block_count": len(blocks)},
        },
        "blocks": blocks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--project-code", required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--contains-personal-data", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    payload = build_payload(
        args.input.resolve(), args.project_code, args.title, args.contains_personal_data
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"document_id": payload["document"]["document_id"], "blocks": len(payload["blocks"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
