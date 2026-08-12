#!/usr/bin/env python3
"""Build a clean A4 Chinese feasibility-report DOCX from assembled Markdown."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.shared import Cm, Pt, RGBColor


USABLE_WIDTH_DXA = 8960


def set_run_font(run, east_asia: str, size: float, *, bold: bool = False) -> None:
    run.font.name = "Arial"
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor(0, 0, 0)
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), east_asia)
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Arial")
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Arial")


def set_style_font(style, east_asia: str, size: float, *, bold: bool = False) -> None:
    style.font.name = "Arial"
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = RGBColor(0, 0, 0)
    style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), east_asia)
    style.element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Arial")
    style.element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Arial")


def configure_document(document: Document) -> None:
    section = document.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.8)
    section.bottom_margin = Cm(2.6)
    section.left_margin = Cm(2.6)
    section.right_margin = Cm(2.6)
    section.header_distance = Cm(1.5)
    section.footer_distance = Cm(1.5)

    normal = document.styles["Normal"]
    set_style_font(normal, "宋体", 12)
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    normal.paragraph_format.first_line_indent = Pt(24)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE

    heading_tokens = {
        "Heading 1": ("黑体", 16, 18, 12, WD_ALIGN_PARAGRAPH.CENTER),
        "Heading 2": ("黑体", 15, 14, 8, WD_ALIGN_PARAGRAPH.LEFT),
        "Heading 3": ("黑体", 14, 12, 6, WD_ALIGN_PARAGRAPH.LEFT),
    }
    for style_name, (font, size, before, after, alignment) in heading_tokens.items():
        style = document.styles[style_name]
        set_style_font(style, font, size, bold=True)
        style.paragraph_format.alignment = alignment
        style.paragraph_format.first_line_indent = Pt(0)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.line_spacing = 1.0
    document.styles["Heading 1"].paragraph_format.page_break_before = True

    for style_name in ("List Bullet", "List Number"):
        style = document.styles[style_name]
        set_style_font(style, "宋体", 12)
        style.paragraph_format.left_indent = Cm(0.74)
        style.paragraph_format.first_line_indent = Cm(-0.37)
        style.paragraph_format.space_after = Pt(0)
        style.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE


def add_field(paragraph, instruction: str, placeholder: str = "") -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = placeholder
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for element in (begin, instr, separate, text, end):
        run._r.append(element)


def configure_header_footer(document: Document, project_name: str) -> None:
    section = document.sections[0]
    header = section.header
    paragraph = header.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(project_name)
    set_run_font(run, "宋体", 9)
    footer = section.footer
    paragraph = footer.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_field(paragraph, "PAGE", "1")


def clear_container(element) -> None:
    for child in list(element):
        element.remove(child)
    element.append(OxmlElement("w:p"))


def scrub_template(document: Document) -> None:
    body = document._element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    removable_relationships = {
        RT.IMAGE,
        RT.HYPERLINK,
        RT.OLE_OBJECT,
        RT.PACKAGE,
        RT.COMMENTS,
        RT.CUSTOM_XML,
    }
    for part in [document.part, *[section.header.part for section in document.sections], *[section.footer.part for section in document.sections]]:
        for relationship_id, relationship in list(part.rels.items()):
            if relationship.reltype in removable_relationships:
                part.drop_rel(relationship_id)
    for section in document.sections:
        clear_container(section.header._element)
        clear_container(section.footer._element)


def set_cell_margins(cell, top=80, start=120, bottom=80, end=120) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    margins = tc_pr.first_child_found_in("w:tcMar")
    if margins is None:
        margins = OxmlElement("w:tcMar")
        tc_pr.append(margins)
    for tag, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = margins.find(qn(f"w:{tag}"))
        if node is None:
            node = OxmlElement(f"w:{tag}")
            margins.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_table_geometry(table, column_widths: list[int]) -> None:
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table_pr = table._tbl.tblPr
    table_width = table_pr.first_child_found_in("w:tblW")
    table_width.set(qn("w:w"), str(sum(column_widths)))
    table_width.set(qn("w:type"), "dxa")
    indent = OxmlElement("w:tblInd")
    indent.set(qn("w:w"), "120")
    indent.set(qn("w:type"), "dxa")
    table_pr.append(indent)
    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width in column_widths:
        column = OxmlElement("w:gridCol")
        column.set(qn("w:w"), str(width))
        grid.append(column)
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            cell.width = Cm(column_widths[index] / 567)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            tc_width = cell._tc.get_or_add_tcPr().first_child_found_in("w:tcW")
            tc_width.set(qn("w:w"), str(column_widths[index]))
            tc_width.set(qn("w:type"), "dxa")
            set_cell_margins(cell)


def add_markdown_table(document: Document, rows: list[list[str]]) -> None:
    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    if len(normalized) > 1 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in normalized[1]):
        normalized.pop(1)
    table = document.add_table(rows=len(normalized), cols=column_count)
    table.style = "Table Grid"
    base = USABLE_WIDTH_DXA // column_count
    widths = [base] * column_count
    widths[-1] += USABLE_WIDTH_DXA - sum(widths)
    for row_index, row in enumerate(normalized):
        for col_index, value in enumerate(row):
            paragraph = table.cell(row_index, col_index).paragraphs[0]
            paragraph.paragraph_format.first_line_indent = Pt(0)
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if row_index == 0 else WD_ALIGN_PARAGRAPH.LEFT
            run = paragraph.add_run(value)
            set_run_font(run, "黑体" if row_index == 0 else "宋体", 10.5, bold=row_index == 0)
    set_table_geometry(table, widths)


def parse_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def build_docx(
    markdown: str,
    output: Path,
    project_name: str,
    owner_name: str = "",
    template: Path | None = None,
) -> dict:
    markdown = re.sub(r"<!--[\s\S]*?-->", "", markdown)
    if template:
        template = template.resolve()
        if not template.is_file():
            raise FileNotFoundError(template)
        document = Document(template)
        scrub_template(document)
        preset = "confirmed-template styles and page geometry"
    else:
        document = Document()
        configure_document(document)
        preset = "narrative_proposal + A4 Chinese government feasibility override"
    configure_header_footer(document, project_name)
    document.core_properties.title = f"{project_name}可行性研究报告"
    document.core_properties.subject = "医疗信息化政府投资项目可行性研究报告"
    document.core_properties.author = ""
    document.core_properties.last_modified_by = ""
    document.core_properties.comments = (
        f"Layout source: {preset}. Template content, links, media relationships, headers, "
        "footers and personal metadata were scrubbed before report generation."
    )

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Cm(5.5)
    title.paragraph_format.space_after = Pt(24)
    run = title.add_run(project_name)
    set_run_font(run, "方正小标宋简体", 22, bold=False)
    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run("可行性研究报告")
    set_run_font(run, "方正小标宋简体", 22)
    if owner_name:
        owner = document.add_paragraph()
        owner.alignment = WD_ALIGN_PARAGRAPH.CENTER
        owner.paragraph_format.space_before = Cm(8)
        run = owner.add_run(owner_name)
        set_run_font(run, "宋体", 14)
    document.add_page_break()
    toc_title = document.add_paragraph("目录")
    toc_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_font(toc_title.runs[0], "黑体", 16, bold=True)
    toc = document.add_paragraph()
    add_field(toc, 'TOC \\o "1-3" \\h \\z \\u', "请在 Word 中更新目录")
    document.add_page_break()

    lines = markdown.splitlines()
    index = 0
    body_started = False
    paragraph_count = 0
    table_count = 0
    heading_count = 0
    while index < len(lines):
        line = lines[index].rstrip()
        if not line.strip():
            index += 1
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            text = heading.group(2).strip()
            if not body_started and not re.match(r"^第\d+章", text):
                index += 1
                continue
            body_started = True
            level = len(heading.group(1))
            document.add_paragraph(text, style=f"Heading {level}")
            heading_count += 1
            index += 1
            continue
        if not body_started:
            index += 1
            continue
        if line.strip().startswith("|") and "|" in line.strip()[1:]:
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(parse_table_row(lines[index]))
                index += 1
            add_markdown_table(document, table_lines)
            table_count += 1
            continue
        bullet = re.match(r"^[-*]\s+(.+)$", line)
        numbered = re.match(r"^\d+[.)]\s+(.+)$", line)
        if bullet or numbered:
            paragraph = document.add_paragraph(
                (bullet or numbered).group(1),
                style="List Bullet" if bullet else "List Number",
            )
            paragraph_count += 1
            index += 1
            continue
        paragraph = document.add_paragraph()
        paragraph.add_run(line.strip())
        paragraph_count += 1
        index += 1

    settings = document.settings._element
    update_fields = OxmlElement("w:updateFields")
    update_fields.set(qn("w:val"), "true")
    settings.append(update_fields)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    document.save(output)
    return {
        "output": str(output),
        "preset": preset,
        "template": str(template) if template else "",
        "headings": heading_count,
        "paragraphs": paragraph_count,
        "tables": table_count,
        "render_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--owner-name", default="")
    parser.add_argument("--template", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    result = build_docx(
        args.input.read_text(encoding="utf-8-sig"),
        args.output,
        args.project_name,
        args.owner_name,
        args.template,
    )
    text = __import__("json").dumps(result, ensure_ascii=False, indent=2)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
