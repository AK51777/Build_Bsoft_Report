"""Construction DOCX with source-bound visible-content and heading audit."""
from __future__ import annotations

import re
from pathlib import Path
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from construction_model import fingerprint, render_tree
from knowledge_db import sha256_file

PRESET_ID = "construction-default-v1"


def construction_markdown(manifest):
    from construction_alignment import render_scope_fragment, render_solution_fragment
    return render_scope_fragment(manifest, 1, "第1章 建设清单") + "\n\n" + render_solution_fragment(manifest, 1, "第2章 建设内容")


def fidelity(manifest):
    fragments = [f for i in manifest["application_software_solution"]["items"] for f in i["fragments"]]
    assets = [a for f in fragments for a in f.get("asset_manifest", [])]
    rich = any(f.get("content_format", "plain_text") not in {"plain_text", "markdown"} for f in fragments)
    dangling = sum(bool(re.search(r"如下图|见.{0,6}(?:示意图|流程图)", f["clean_text"])) for f in fragments)
    return {"text_fidelity": "verbatim_visible_text", "table_fidelity": "basic_markdown_tables",
            "image_fidelity": "unsupported_assets" if assets or rich else "not_available_in_source_pack",
            "asset_count": len(assets), "dangling_image_reference_blocks": dangling,
            "requires_rich_renderer": bool(assets or rich)}


def expected_tokens(markdown):
    """Independent ordered visible stream for body, heading levels and table cells."""
    from build_report_docx import plain_inline_text, parse_table_row
    lines = re.sub(r"<!--[\s\S]*?-->", "", markdown).splitlines()
    result, index, fence = [], 0, False
    while index < len(lines):
        line = lines[index].rstrip()
        index += 1
        if line.strip().startswith("```"):
            fence = not fence
            continue
        if not line.strip():
            continue
        heading = re.match(r"^(#{1,7})\s+(.+)$", line) if not fence else None
        if heading:
            level = len(heading[1])
            text = plain_inline_text(heading[2])
            text = re.sub(r"^第(?:\d+|[一二三四五六七八九十百]+)章\s*", "", text) if level == 1 else re.sub(r"^\d+(?:\.\d+){0,6}[.、．]?\s*", "", text)
            result.append(["heading", level, text])
        elif not fence and line.strip().startswith("|"):
            rows = [parse_table_row(line)]
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(parse_table_row(lines[index])); index += 1
            if len(rows) > 1 and all(re.fullmatch(r":?-{3,}:?", c) for c in rows[1]):
                rows.pop(1)
            width = max(map(len, rows))
            result.append(["table", [[plain_inline_text(c).replace("<br>", "\n") for c in r + [""] * (width-len(r))] for r in rows]])
        elif re.fullmatch(r"\s*(?:---+|___+|\*\*\*+)\s*", line):
            continue
        else:
            text = line if fence else re.sub(r"^\s*(?:[-+*]|\d+[.)])\s+", "", line).lstrip("> ")
            result.append(["paragraph", text if fence else plain_inline_text(text).replace("<br>", "\n")])
    return result


def audit_word(docx, manifest, semantic_styles=None):
    document = Document(docx)
    tokens, started, heading_errors = [], False, []
    heading_names = {(semantic_styles or {}).get(f"heading_{level}", f"Heading {level}"): level for level in range(1, 8)}
    for element in document.element.body:
        if element.tag == qn("w:p"):
            paragraph = Paragraph(element, document)
            text = paragraph.text
            level = heading_names.get(paragraph.style.name)
            if level == 1:
                started = True
            if not started:
                continue
            if level:
                tokens.append(["heading", level, text])
                properties = [paragraph._p.pPr]
                style = paragraph.style
                while style is not None:
                    properties.append(style.element.find(qn("w:pPr")))
                    style = style.base_style
                effective = {}
                for props in properties:
                    num = props.find(qn("w:numPr")) if props is not None else None
                    if num is not None:
                        for key in ("numId", "ilvl"):
                            value = num.find(qn("w:" + key))
                            if value is not None and key not in effective:
                                effective[key] = value.get(qn("w:val"))
                number_id = effective.get("numId", "0")
                numbering = document.part.numbering_part.element
                valid_num = next((n for n in numbering.findall(qn("w:num")) if n.get(qn("w:numId")) == number_id), None)
                if number_id == "0" or valid_num is None or effective.get("ilvl", "0") != str(level - 1):
                    heading_errors.append(text)
            elif text.strip():
                tokens.append(["paragraph", text])
        elif element.tag == qn("w:tbl") and started:
            tokens.append(["table", [[c.text for c in row.cells] for row in Table(element, document).rows]])
    expected = expected_document_tokens(manifest)
    mismatch = next((n for n, pair in enumerate(zip(expected, tokens)) if pair[0] != pair[1]), None)
    if mismatch is None and len(expected) != len(tokens):
        mismatch = min(len(expected), len(tokens))
    return {"valid": mismatch is None and not heading_errors,
            "docx_sha256": sha256_file(Path(docx)), "manifest_hash": manifest["manifest_hash"],
            "expected_count": len(expected), "actual_count": len(tokens),
            "first_mismatch": mismatch, "numbering_errors": heading_errors,
            "expected_visible_hash": fingerprint(expected), "actual_visible_hash": fingerprint(tokens),
            "heading_tree_hash": manifest.get("heading_tree", {}).get("tree_hash", ""), "fidelity": fidelity(manifest)}


def expected_document_tokens(manifest):
    """Audit original table cells against the snapshot, not the renderer's parser."""
    from construction_alignment import render_solution_fragment
    scope = manifest["construction_list_import"]
    payload = scope.get("original_display_payload", scope["display_payload"])
    expected = [["heading", 1, "建设清单"]]

    def display(value):
        return str(value if value is not None else "").replace("\r\n", "\n")

    for sheet in payload.get("sheets", []):
        headers = [str(value) for value in sheet.get("headers", [])]
        if not headers:
            continue
        expected.extend(expected_tokens(f"## {sheet.get('name', '')}"))
        rows = [[display(header) for header in headers]]
        rows.extend([[display(row.get(header, "")) for header in headers]
                     for row in sheet.get("rows", [])])
        expected.append(["table", rows])
    expected.extend(expected_tokens(render_solution_fragment(manifest, 1, "第2章 建设内容")))
    return expected


def generate_word(database, manifest, output, project_name):
    from build_construction_docx import build_construction_docx
    return build_construction_docx(Path(database), manifest, Path(output), project_name)
