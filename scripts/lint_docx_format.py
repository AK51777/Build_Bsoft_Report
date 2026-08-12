#!/usr/bin/env python3
"""Lint DOCX indentation and style drift without changing the source document."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from extract_document_profile import NS, W, attr, parse_styles, qn, semantic_role, text_of
from knowledge_db import apply_migrations, connect, dump_json, now_iso, sha256_file, stable_id


HEADING_LIKE = re.compile(
    r"^(?:第[一二三四五六七八九十百]+[章节篇]|[一二三四五六七八九十]+、|\d+(?:\.\d+){0,3}[\.、 ]+)"
)


def lint_docx(docx: Path, style_contract_path: Path | None = None) -> dict[str, Any]:
    with zipfile.ZipFile(docx) as archive:
        document_root = ET.fromstring(archive.read("word/document.xml"))
        styles_root = ET.fromstring(archive.read("word/styles.xml"))
    styles = parse_styles(styles_root)
    default_paragraph_style_id = next(
        (
            style_id
            for style_id, style in styles.items()
            if style.get("style_type") == "paragraph" and style.get("is_default")
        ),
        "",
    )
    contract: dict[str, Any] = {}
    if style_contract_path:
        contract_payload = json.loads(style_contract_path.read_text(encoding="utf-8-sig"))
        contract = {rule["style_id"]: rule for rule in contract_payload.get("rules", [])}

    issues: list[dict[str, Any]] = []
    paragraph_nodes = document_root.findall(".//w:body//w:p", NS)
    table_map: dict[int, int] = {}
    for table_index, table in enumerate(document_root.findall(".//w:tbl", NS), 1):
        for paragraph in table.findall(".//w:p", NS):
            table_map[id(paragraph)] = table_index

    def add_issue(
        paragraph_index: int,
        table_index: int | None,
        issue_type: str,
        severity: str,
        style_id: str,
        text: str,
        actual: Any,
        expected: Any,
        auto_fixable: bool,
    ) -> None:
        issues.append(
            {
                "issue_id": stable_id("FMTISSUE", str(docx.resolve()), paragraph_index, issue_type, text[:80]),
                "paragraph_index": paragraph_index,
                "table_index": table_index,
                "issue_type": issue_type,
                "severity": severity,
                "style_id": style_id,
                "text_excerpt": text.strip()[:140],
                "actual": actual,
                "expected": expected,
                "auto_fixable": auto_fixable,
            }
        )

    nonempty = 0
    for paragraph_index, paragraph in enumerate(paragraph_nodes, 1):
        text = text_of(paragraph)
        if not text.strip():
            continue
        nonempty += 1
        ppr = paragraph.find("w:pPr", NS)
        style_node = ppr.find("w:pStyle", NS) if ppr is not None else None
        explicit_style_id = attr(style_node, "val")
        style_id = explicit_style_id or default_paragraph_style_id
        style = styles.get(style_id, {})
        role = semantic_role(style) if style else ("body_unstyled" if not style_id else "unknown")
        table_index = table_map.get(id(paragraph))
        leading = re.match(r"^([\t \u3000]+)", text)
        if leading:
            chars = leading.group(1)
            types = []
            if "\t" in chars:
                types.append("tab")
            if " " in chars:
                types.append("space")
            if "\u3000" in chars:
                types.append("fullwidth_space")
            add_issue(
                paragraph_index,
                table_index,
                "leading_whitespace",
                "high" if "tab" in types or role.startswith("heading_") else "medium",
                style_id,
                text,
                {"characters": repr(chars), "types": types, "count": len(chars)},
                {"rule": "禁止用空格或Tab模拟缩进"},
                True,
            )
        if style_id and style_id not in styles:
            add_issue(
                paragraph_index,
                table_index,
                "unknown_style_id",
                "high",
                style_id,
                text,
                {"style_id": style_id},
                {"rule": "样式ID必须存在于styles.xml"},
                False,
            )
        if not explicit_style_id and not default_paragraph_style_id:
            add_issue(
                paragraph_index,
                table_index,
                "unstyled_paragraph",
                "medium",
                style_id,
                text,
                {},
                {"rule": "非空段落应绑定语义样式"},
                True,
            )
        if table_index is None and HEADING_LIKE.match(text.strip()) and not role.startswith("heading_"):
            add_issue(
                paragraph_index,
                table_index,
                "heading_like_without_heading_style",
                "high",
                style_id,
                text,
                {"semantic_role": role},
                {"semantic_role": "heading_n"},
                False,
            )
        if ppr is not None:
            ind = ppr.find("w:ind", NS)
            spacing = ppr.find("w:spacing", NS)
            tabs = ppr.find("w:tabs", NS)
            num_pr = ppr.find("w:numPr", NS)
            if ind is not None:
                indent_values = {key.split("}")[-1]: value for key, value in ind.attrib.items()}
                table_first_line = table_index is not None and any(
                    indent_values.get(key) not in {None, "", "0"}
                    for key in ("firstLine", "firstLineChars", "hanging", "hangingChars")
                )
                allow = role in {"body", "list"} and not table_first_line
                add_issue(
                    paragraph_index,
                    table_index,
                    "direct_paragraph_indent",
                    "low" if allow else ("high" if table_first_line else "medium"),
                    style_id,
                    text,
                    indent_values,
                    {"rule": "缩进优先由样式契约控制；表格、标题、题注不得继承正文首行缩进"},
                    True,
                )
            if tabs is not None:
                add_issue(
                    paragraph_index,
                    table_index,
                    "direct_tab_stops",
                    "high",
                    style_id,
                    text,
                    {"count": len(list(tabs))},
                    {"rule": "非白名单段落不得写入w:tabs"},
                    True,
                )
            if spacing is not None:
                add_issue(
                    paragraph_index,
                    table_index,
                    "direct_paragraph_spacing",
                    "low",
                    style_id,
                    text,
                    {key.split("}")[-1]: value for key, value in spacing.attrib.items()},
                    {"rule": "段前段后及行距优先由样式控制"},
                    True,
                )
            if num_pr is not None and role.startswith("heading_") and style_id in contract:
                expected_num_level = contract[style_id].get("numbering_level")
                ilvl = num_pr.find("w:ilvl", NS)
                actual_level = attr(ilvl, "val")
                if expected_num_level is not None and actual_level and int(actual_level) != int(expected_num_level):
                    add_issue(
                        paragraph_index,
                        table_index,
                        "numbering_level_mismatch",
                        "high",
                        style_id,
                        text,
                        {"numbering_level": actual_level},
                        {"numbering_level": expected_num_level},
                        False,
                    )
        direct_runs = [run for run in paragraph.findall("w:r", NS) if run.find("w:rPr", NS) is not None]
        if direct_runs and role.startswith("heading_"):
            add_issue(
                paragraph_index,
                table_index,
                "heading_run_direct_formatting",
                "medium",
                style_id,
                text,
                {"run_count": len(direct_runs)},
                {"rule": "标题字体字号应由标题样式控制"},
                True,
            )

    severity_counts = Counter(issue["severity"] for issue in issues)
    type_counts = Counter(issue["issue_type"] for issue in issues)
    return {
        "file_path": str(docx.resolve()),
        "file_hash": sha256_file(docx),
        "checked_at": now_iso(),
        "summary": {
            "paragraph_count": len(paragraph_nodes),
            "nonempty_paragraph_count": nonempty,
            "issue_count": len(issues),
            "blocking_count": severity_counts.get("blocking", 0),
            "severity_counts": dict(severity_counts),
            "type_counts": dict(type_counts),
            "normalization_policy": "本轮仅检测，不修改源文件；自动修复前必须复制候选稿并渲染复核。",
        },
        "issues": issues,
    }


def persist_lint(database: Path, project_code: str, report: dict[str, Any], profile_id: str | None) -> str:
    with connect(database) as conn:
        apply_migrations(conn)
        project = conn.execute("SELECT * FROM project WHERE project_code=?", (project_code,)).fetchone()
        if not project:
            raise SystemExit(f"未找到项目：{project_code}")
        lint_run_id = stable_id("FMTRUN", project["project_id"], report["file_hash"], report["checked_at"])
        conn.execute(
            """
            INSERT INTO format_lint_run (
              lint_run_id,project_id,profile_id,file_path,file_hash,issue_count,
              blocking_count,checked_at,summary_json
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                lint_run_id,
                project["project_id"],
                profile_id,
                report["file_path"],
                report["file_hash"],
                report["summary"]["issue_count"],
                report["summary"]["blocking_count"],
                report["checked_at"],
                dump_json(report["summary"]),
            ),
        )
        for issue in report["issues"]:
            conn.execute(
                """
                INSERT INTO format_lint_issue (
                  lint_issue_id,lint_run_id,paragraph_index,table_index,issue_type,
                  severity,style_id,text_excerpt,actual_json,expected_json,
                  auto_fixable,resolution_status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    stable_id("FMTISSUE", lint_run_id, issue["issue_id"]),
                    lint_run_id,
                    issue["paragraph_index"],
                    issue["table_index"],
                    issue["issue_type"],
                    issue["severity"],
                    issue["style_id"],
                    issue["text_excerpt"],
                    dump_json(issue["actual"]),
                    dump_json(issue["expected"]),
                    int(bool(issue["auto_fixable"])),
                    "open",
                ),
            )
        conn.commit()
    return lint_run_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", type=Path)
    parser.add_argument("--style-contract", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--project-code")
    parser.add_argument("--profile-id")
    args = parser.parse_args()

    report = lint_docx(args.docx, args.style_contract)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lint_run_id = None
    if args.database:
        if not args.project_code:
            parser.error("--database requires --project-code")
        lint_run_id = persist_lint(args.database, args.project_code, report, args.profile_id)
    print(
        json.dumps(
            {"output": str(args.output), "summary": report["summary"], "lint_run_id": lint_run_id},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
