#!/usr/bin/env python3
"""Extract a DOCX/WPS-compatible style profile and semantic style contract candidate."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from knowledge_db import (
    apply_migrations,
    connect,
    dump_json,
    now_iso,
    sha256_file,
    sha256_text,
    stable_id,
)


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"w": W, "r": R}


def qn(local: str) -> str:
    return f"{{{W}}}{local}"


def attr(node: ET.Element | None, name: str, default: str = "") -> str:
    return node.attrib.get(qn(name), default) if node is not None else default


def text_of(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == qn("t"):
            parts.append(node.text or "")
        elif node.tag == qn("tab"):
            parts.append("\t")
        elif node.tag == qn("br"):
            parts.append("\n")
    return "".join(parts)


def parse_styles(root: ET.Element) -> dict[str, dict[str, Any]]:
    styles: dict[str, dict[str, Any]] = {}
    for node in root.findall("w:style", NS):
        style_id = attr(node, "styleId")
        if not style_id:
            continue
        name_node = node.find("w:name", NS)
        based_on = node.find("w:basedOn", NS)
        next_style = node.find("w:next", NS)
        ppr = node.find("w:pPr", NS)
        rpr = node.find("w:rPr", NS)
        outline = ppr.find("w:outlineLvl", NS) if ppr is not None else None
        ind = ppr.find("w:ind", NS) if ppr is not None else None
        spacing = ppr.find("w:spacing", NS) if ppr is not None else None
        rfonts = rpr.find("w:rFonts", NS) if rpr is not None else None
        size = rpr.find("w:sz", NS) if rpr is not None else None
        num_id = ppr.find("w:numPr/w:numId", NS) if ppr is not None else None
        ilvl = ppr.find("w:numPr/w:ilvl", NS) if ppr is not None else None
        style = {
            "style_id": style_id,
            "style_name": attr(name_node, "val", style_id),
            "style_type": attr(node, "type"),
            "is_default": attr(node, "default") == "1",
            "based_on_style_id": attr(based_on, "val"),
            "next_style_id": attr(next_style, "val"),
            "outline_level": int(attr(outline, "val")) if attr(outline, "val").isdigit() else None,
            "numbering_level": int(attr(ilvl, "val")) if attr(ilvl, "val").isdigit() else None,
            "num_id": attr(num_id, "val"),
            "alignment": attr(ppr.find("w:jc", NS) if ppr is not None else None, "val"),
            "keep_next": ppr is not None and ppr.find("w:keepNext", NS) is not None,
            "indent": {
                "first_line": attr(ind, "firstLine"),
                "first_line_chars": attr(ind, "firstLineChars"),
                "hanging": attr(ind, "hanging"),
                "left": attr(ind, "left"),
                "left_chars": attr(ind, "leftChars"),
                "right": attr(ind, "right"),
                "right_chars": attr(ind, "rightChars"),
            },
            "spacing": {
                "before": attr(spacing, "before"),
                "after": attr(spacing, "after"),
                "line": attr(spacing, "line"),
                "line_rule": attr(spacing, "lineRule"),
            },
            "font_east_asia": attr(rfonts, "eastAsia"),
            "font_ascii": attr(rfonts, "ascii"),
            "font_hansi": attr(rfonts, "hAnsi"),
            "font_size_half_points": attr(size, "val"),
            "bold": rpr is not None and rpr.find("w:b", NS) is not None,
        }
        styles[style_id] = style
    return styles


def semantic_role(style: dict[str, Any]) -> str:
    name = f"{style.get('style_id','')} {style.get('style_name','')}".lower().replace(" ", "")
    outline = style.get("outline_level")
    if outline is not None and 0 <= outline <= 8:
        return f"heading_{outline + 1}"
    match = re.search(r"(?:heading|标题)([1-9])", name)
    if match:
        return f"heading_{match.group(1)}"
    if any(token in name for token in ("caption", "题注", "表题", "图题")):
        return "caption"
    if any(token in name for token in ("list", "列表")):
        return "list"
    if any(token in name for token in ("table", "表格", "表内")):
        return "table_body"
    if style.get("is_default") or any(token in name for token in ("normal", "正文", "bodytext")):
        return "body"
    return "other"


def extract_profile(docx: Path) -> dict[str, Any]:
    with zipfile.ZipFile(docx) as archive:
        document_root = ET.fromstring(archive.read("word/document.xml"))
        styles_root = ET.fromstring(archive.read("word/styles.xml"))
        settings_root = (
            ET.fromstring(archive.read("word/settings.xml"))
            if "word/settings.xml" in archive.namelist()
            else None
        )
        numbering_xml = archive.read("word/numbering.xml") if "word/numbering.xml" in archive.namelist() else b""

    styles = parse_styles(styles_root)
    usage = Counter()
    paragraph_count = 0
    table_paragraph_count = 0
    direct_ppr_count = 0
    direct_run_count = 0
    leading_whitespace_count = 0
    paragraphs: list[dict[str, Any]] = []
    table_paragraph_ids = {id(node) for node in document_root.findall(".//w:tbl//w:p", NS)}
    for index, paragraph in enumerate(document_root.findall(".//w:body//w:p", NS), 1):
        paragraph_count += 1
        ppr = paragraph.find("w:pPr", NS)
        style_node = ppr.find("w:pStyle", NS) if ppr is not None else None
        style_id = attr(style_node, "val")
        usage[style_id or "(none)"] += 1
        text = text_of(paragraph)
        in_table = id(paragraph) in table_paragraph_ids
        table_paragraph_count += int(in_table)
        has_direct_ppr = bool(
            ppr is not None
            and any(ppr.find(path, NS) is not None for path in ("w:ind", "w:spacing", "w:tabs", "w:jc", "w:numPr"))
        )
        direct_ppr_count += int(has_direct_ppr)
        run_direct = sum(1 for run in paragraph.findall("w:r", NS) if run.find("w:rPr", NS) is not None)
        direct_run_count += run_direct
        leading = bool(re.match(r"^[\t \u3000]+", text))
        leading_whitespace_count += int(leading)
        if text.strip():
            paragraphs.append(
                {
                    "paragraph_index": index,
                    "text": text[:160],
                    "style_id": style_id,
                    "style_name": styles.get(style_id, {}).get("style_name", style_id),
                    "semantic_role": semantic_role(styles.get(style_id, {})) if style_id else "body_unstyled",
                    "in_table": in_table,
                    "has_direct_paragraph_format": has_direct_ppr,
                    "direct_run_count": run_direct,
                    "has_leading_whitespace": leading,
                }
            )

    sections: list[dict[str, Any]] = []
    for index, sect in enumerate(document_root.findall(".//w:sectPr", NS), 1):
        pg_sz = sect.find("w:pgSz", NS)
        pg_mar = sect.find("w:pgMar", NS)
        sections.append(
            {
                "section_index": index,
                "orientation": attr(pg_sz, "orient", "portrait"),
                "page_width_twips": attr(pg_sz, "w"),
                "page_height_twips": attr(pg_sz, "h"),
                "margins_twips": {
                    "top": attr(pg_mar, "top"),
                    "right": attr(pg_mar, "right"),
                    "bottom": attr(pg_mar, "bottom"),
                    "left": attr(pg_mar, "left"),
                    "header": attr(pg_mar, "header"),
                    "footer": attr(pg_mar, "footer"),
                    "gutter": attr(pg_mar, "gutter"),
                },
                "section_type": attr(sect.find("w:type", NS), "val", "nextPage"),
                "columns": attr(sect.find("w:cols", NS), "num", "1"),
            }
        )

    style_contract = []
    preferred_roles: dict[str, tuple[int, dict[str, Any]]] = {}
    for style_id, style in styles.items():
        role = semantic_role(style)
        if role == "other" or style.get("style_type") != "paragraph":
            continue
        score = usage.get(style_id, 0) + (1000 if style.get("outline_level") is not None else 0)
        if role not in preferred_roles or score > preferred_roles[role][0]:
            preferred_roles[role] = (score, style)
    for role in sorted(preferred_roles, key=lambda value: (not value.startswith("heading_"), value)):
        style = preferred_roles[role][1]
        style_contract.append(
            {
                "semantic_role": role,
                "style_id": style["style_id"],
                "style_name": style["style_name"],
                "outline_level": style["outline_level"],
                "numbering_level": style["numbering_level"],
                "based_on_style_id": style["based_on_style_id"],
                "font_east_asia": style["font_east_asia"],
                "font_latin": style["font_ascii"] or style["font_hansi"],
                "font_size_pt": (
                    float(style["font_size_half_points"]) / 2
                    if str(style["font_size_half_points"]).isdigit()
                    else None
                ),
                "bold": style["bold"],
                "alignment": style["alignment"],
                "spacing_before_twips": style["spacing"]["before"],
                "spacing_after_twips": style["spacing"]["after"],
                "line_spacing": style["spacing"]["line"],
                "first_line_indent_chars": style["indent"]["first_line_chars"],
                "left_indent_chars": style["indent"]["left_chars"],
                "allow_direct_formatting": role in {"body", "table_body", "list"},
                "review_status": "candidate",
            }
        )

    profile_core = {
        "file_name": docx.name,
        "file_path": str(docx.resolve()),
        "file_hash": sha256_file(docx),
        "sections": sections,
        "style_count": len(styles),
        "style_usage": dict(usage.most_common()),
        "numbering_hash": sha256_text(numbering_xml.decode("utf-8", errors="ignore")),
        "settings": {
            "update_fields": bool(settings_root is not None and settings_root.find("w:updateFields", NS) is not None),
            "track_revisions": bool(settings_root is not None and settings_root.find("w:trackRevisions", NS) is not None),
        },
        "paragraph_summary": {
            "paragraph_count": paragraph_count,
            "table_paragraph_count": table_paragraph_count,
            "direct_paragraph_format_count": direct_ppr_count,
            "direct_run_format_count": direct_run_count,
            "leading_whitespace_count": leading_whitespace_count,
        },
        "styles": sorted(styles.values(), key=lambda value: (value["style_type"], value["style_id"])),
        "paragraph_sample": paragraphs[:100],
    }
    return {
        "profile": profile_core,
        "style_contract": {
            "source_file": str(docx.resolve()),
            "profile_hash": sha256_text(json.dumps(profile_core, ensure_ascii=False, sort_keys=True)),
            "rules": style_contract,
            "status": "candidate_requires_user_confirmation",
        },
    }


def persist_profile(
    database: Path,
    project_code: str,
    extracted: dict[str, Any],
    profile_name: str,
    standard_id: str | None = None,
) -> dict[str, str]:
    with connect(database) as conn:
        apply_migrations(conn)
        project = conn.execute("SELECT * FROM project WHERE project_code=?", (project_code,)).fetchone()
        if not project:
            raise SystemExit(f"未找到项目：{project_code}")
        profile = extracted["profile"]
        profile_hash = extracted["style_contract"]["profile_hash"]
        source_id = stable_id("SRC", project["project_id"], profile["file_path"], profile["file_hash"])
        conn.execute(
            """
            INSERT INTO source_document (
              source_id,project_id,source_scope,source_class,file_name,file_type,
              source_path,sha256,usage_scope,verification_status,imported_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_id) DO NOTHING
            """,
            (
                source_id,
                project["project_id"],
                "project",
                "format_reference",
                profile["file_name"],
                "DOCX",
                profile["file_path"],
                profile["file_hash"],
                "格式画像和样式契约候选",
                "registered",
                now_iso(),
            ),
        )
        profile_id = stable_id("PROFILE", source_id, profile_hash)
        timestamp = now_iso()
        conn.execute(
            """
            INSERT INTO format_profile (
              profile_id,standard_id,profile_name,source_document_id,page_setup_json,
              cover_rules_json,toc_rules_json,numbering_rules_json,
              header_footer_rules_json,section_rules_json,profile_hash,
              confidence,review_status,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(profile_id) DO UPDATE SET
              standard_id=excluded.standard_id,
              profile_name=excluded.profile_name,
              page_setup_json=excluded.page_setup_json,
              numbering_rules_json=excluded.numbering_rules_json,
              section_rules_json=excluded.section_rules_json,
              profile_hash=excluded.profile_hash,
              updated_at=excluded.updated_at
            """,
            (
                profile_id,
                standard_id,
                profile_name,
                source_id,
                dump_json(profile["sections"]),
                "{}",
                "{}",
                dump_json({"numbering_hash": profile["numbering_hash"]}),
                "{}",
                dump_json({"section_count": len(profile["sections"])}),
                profile_hash,
                0.7,
                "candidate",
                timestamp,
                timestamp,
            ),
        )
        for rule in extracted["style_contract"]["rules"]:
            conn.execute(
                """
                INSERT INTO style_rule (
                  style_rule_id,profile_id,semantic_role,style_id,style_name,
                  outline_level,numbering_level,based_on_style_id,font_east_asia,
                  font_latin,font_size_pt,bold,alignment,spacing_before_pt,
                  spacing_after_pt,line_spacing,first_line_indent_chars,
                  left_indent_chars,allow_direct_formatting,raw_style_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(style_rule_id) DO UPDATE SET
                  style_id=excluded.style_id,
                  style_name=excluded.style_name,
                  outline_level=excluded.outline_level,
                  numbering_level=excluded.numbering_level,
                  based_on_style_id=excluded.based_on_style_id,
                  font_east_asia=excluded.font_east_asia,
                  font_latin=excluded.font_latin,
                  font_size_pt=excluded.font_size_pt,
                  bold=excluded.bold,
                  alignment=excluded.alignment,
                  line_spacing=excluded.line_spacing,
                  first_line_indent_chars=excluded.first_line_indent_chars,
                  left_indent_chars=excluded.left_indent_chars,
                  allow_direct_formatting=excluded.allow_direct_formatting,
                  raw_style_json=excluded.raw_style_json
                """,
                (
                    stable_id("STYLE", profile_id, rule["semantic_role"]),
                    profile_id,
                    rule["semantic_role"],
                    rule["style_id"],
                    rule["style_name"],
                    rule["outline_level"],
                    rule["numbering_level"],
                    rule["based_on_style_id"],
                    rule["font_east_asia"],
                    rule["font_latin"],
                    rule["font_size_pt"],
                    int(bool(rule["bold"])),
                    rule["alignment"],
                    None,
                    None,
                    rule["line_spacing"],
                    float(rule["first_line_indent_chars"]) / 100 if str(rule["first_line_indent_chars"]).isdigit() else None,
                    float(rule["left_indent_chars"]) / 100 if str(rule["left_indent_chars"]).isdigit() else None,
                    int(bool(rule["allow_direct_formatting"])),
                    dump_json(rule),
                ),
            )
        project_profile_id = stable_id("PPROFILE", project["project_id"], profile_id)
        conn.execute(
            """
            INSERT INTO project_document_profile (
              project_profile_id,project_id,profile_id,match_score,match_reason,decision_status
            ) VALUES (?,?,?,?,?,?)
            ON CONFLICT(project_profile_id) DO UPDATE SET
              match_score=excluded.match_score,
              match_reason=excluded.match_reason,
              decision_status=excluded.decision_status
            """,
            (
                project_profile_id,
                project["project_id"],
                profile_id,
                0.7,
                "从用户指定的项目 Word 参考稿提取，作为候选格式画像；需人工确认后冻结。",
                "ai_recommended",
            ),
        )
        conn.commit()
    return {"source_id": source_id, "profile_id": profile_id, "project_profile_id": project_profile_id}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", type=Path)
    parser.add_argument("--profile-output", type=Path, required=True)
    parser.add_argument("--style-output", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--project-code")
    parser.add_argument("--profile-name", default="DOCX候选格式画像")
    parser.add_argument("--standard-id", help="Optional matched document_standard ID")
    args = parser.parse_args()

    extracted = extract_profile(args.docx)
    args.profile_output.parent.mkdir(parents=True, exist_ok=True)
    args.style_output.parent.mkdir(parents=True, exist_ok=True)
    args.profile_output.write_text(
        json.dumps(extracted["profile"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.style_output.write_text(
        json.dumps(extracted["style_contract"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    persisted = None
    if args.database:
        if not args.project_code:
            parser.error("--database requires --project-code")
        persisted = persist_profile(
            args.database,
            args.project_code,
            extracted,
            args.profile_name,
            args.standard_id,
        )
    print(
        json.dumps(
            {
                "profile_output": str(args.profile_output),
                "style_output": str(args.style_output),
                "style_rules": len(extracted["style_contract"]["rules"]),
                "persisted": persisted,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
