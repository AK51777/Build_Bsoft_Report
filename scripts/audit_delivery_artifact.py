#!/usr/bin/env python3
"""Adversarially audit a DOCX candidate and its delivery authorization."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from knowledge_db import apply_migrations, connect, load_json, sha256_file


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS, "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


def document_metrics(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as package:
        root = ET.fromstring(package.read("word/document.xml"))
        styles_root = ET.fromstring(package.read("word/styles.xml"))
        media = [name for name in package.namelist() if name.startswith("word/media/")]
    style_names = {}
    for style in styles_root.findall("w:style", NS):
        style_id = style.get(f"{{{W_NS}}}styleId", "")
        name = style.find("w:name", NS)
        style_names[style_id] = name.get(f"{{{W_NS}}}val", style_id) if name is not None else style_id
    paragraphs = []
    heading_levels = {str(level): 0 for level in range(1, 8)}
    heading_titles = []
    for paragraph in root.findall(".//w:p", NS):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", NS)).strip()
        style = paragraph.find("./w:pPr/w:pStyle", NS)
        style_id = style.get(f"{{{W_NS}}}val", "") if style is not None else ""
        style_name = style_names.get(style_id, style_id)
        match = re.fullmatch(r"(?:Heading|标题)\s*([1-7])", style_name, flags=re.I)
        if match:
            heading_levels[match.group(1)] += 1
            heading_titles.append({"level": int(match.group(1)), "text": text})
        paragraphs.append({"text": text, "style": style_id, "style_name": style_name, "heading": bool(match)})
    body_texts = [
        item["text"]
        for item in paragraphs
        if item["text"] and not item["heading"]
    ]
    substantive = [text for text in body_texts if len(re.sub(r"\s+", "", text)) >= 80]
    visible_chars = sum(len(re.sub(r"\s+", "", text)) for text in body_texts)
    return {
        "paragraphs": len(body_texts),
        "substantive_paragraphs": len(substantive),
        "visible_body_chars": visible_chars,
        "average_substantive_chars": round(
            sum(len(re.sub(r"\s+", "", text)) for text in substantive) / max(1, len(substantive)), 1
        ),
        "heading_levels": heading_levels,
        "heading_titles": heading_titles,
        "tables": len(root.findall(".//w:tbl", NS)),
        "sections": len(root.findall(".//w:sectPr", NS)),
        "images": len(media),
        "markdown_residue": {
            "heading_markers": sum(bool(re.match(r"^#{1,7}\s+", item["text"])) for item in paragraphs),
            "bold_markers": sum(item["text"].count("**") for item in paragraphs),
            "code_markers": sum(item["text"].count("`") for item in paragraphs),
        },
        "working_watermark": any("未通过正式交付门禁" in item["text"] for item in paragraphs),
    }


def audit(
    docx: Path,
    *,
    build_summary: Path | None = None,
    database: Path | None = None,
    project_code: str = "",
    minimum_body_chars: int = 30000,
) -> dict[str, Any]:
    docx = docx.resolve()
    if not docx.is_file():
        raise FileNotFoundError(docx)
    metrics = document_metrics(docx)
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def add(target: list[dict[str, str]], code: str, description: str) -> None:
        target.append({"code": code, "description": description})

    if any(metrics["markdown_residue"].values()):
        add(blockers, "markdown_residue", f"DOCX 中仍有 Markdown 标记：{metrics['markdown_residue']}")
    if metrics["working_watermark"]:
        add(blockers, "working_draft_watermark", "该文档明确标记为未通过正式交付门禁的工作稿。")
    if not metrics["heading_levels"]["1"] or not metrics["heading_levels"]["2"]:
        add(blockers, "semantic_heading_hierarchy", "DOCX 缺少至少一级和二级语义标题样式。")

    required_chars = max(0, minimum_body_chars)
    validation_run_id = ""
    expected_outline_nodes: list[dict[str, Any]] = []
    if database and project_code:
        with connect(database.resolve()) as conn:
            apply_migrations(conn)
            project = conn.execute(
                "SELECT project_id FROM project WHERE project_code=?", (project_code,)
            ).fetchone()
            if project is None:
                add(blockers, "project_not_found", f"数据库中不存在项目：{project_code}")
            else:
                required_chars = conn.execute(
                    """
                    SELECT COALESCE(SUM(length_min),0) FROM section_composition_plan
                    WHERE project_id=? AND applicability_status<>'not_applicable'
                    """,
                    (project["project_id"],),
                ).fetchone()[0]
                expected_outline_nodes = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT n.heading_level,n.title,n.chapter_code
                        FROM section_outline_node n
                        JOIN section_composition_plan p ON p.plan_id=n.plan_id
                        WHERE p.project_id=? AND p.applicability_status<>'not_applicable'
                        ORDER BY p.chapter_code,n.ordinal
                        """,
                        (project["project_id"],),
                    )
                ]
                validation = conn.execute(
                    """
                    SELECT validation_run_id,status,blocking_count FROM validation_run
                    WHERE project_id=? AND validation_type='full_report_delivery'
                    ORDER BY completed_at DESC,validation_run_id DESC LIMIT 1
                    """,
                    (project["project_id"],),
                ).fetchone()
                if validation is None or validation["status"] != "passed" or validation["blocking_count"]:
                    add(blockers, "delivery_validation_not_passed", "数据库最新交付校验未通过。")
                else:
                    validation_run_id = validation["validation_run_id"]

    if metrics["visible_body_chars"] < required_chars:
        add(
            blockers,
            "body_depth_below_plan",
            f"DOCX 有效正文 {metrics['visible_body_chars']} 字，低于适用章节计划合计下限 {required_chars} 字。",
        )
    if required_chars >= 1000 and metrics["substantive_paragraphs"] < max(2, required_chars // 500):
        add(blockers, "thin_paragraph_structure", "实质论证段数量偏少，正文可能由套话、短句或清单代替论证。")
    if metrics["substantive_paragraphs"] and metrics["average_substantive_chars"] < 110:
        add(warnings, "short_substantive_paragraphs", "实质段平均长度偏短，需检查是否缺少机制、流程、数据和验收论证。")
    if expected_outline_nodes:
        actual = {
            (item["level"], re.sub(r"^\d+(?:\.\d+){3,6}\s+", "", item["text"]).strip())
            for item in metrics["heading_titles"]
        }
        missing_outline = [
            node for node in expected_outline_nodes
            if (int(node["heading_level"]), node["title"].strip()) not in actual
        ]
        if missing_outline:
            add(
                blockers,
                "dynamic_construction_outline_missing",
                "DOCX 未完整承载动态建设目录："
                + "、".join(f"{node['chapter_code']} {node['title']}" for node in missing_outline[:12]),
            )

    summary: dict[str, Any] = {}
    if build_summary is None or not build_summary.is_file():
        add(blockers, "build_summary_missing", "缺少 DOCX 构建摘要，无法核验模板、输入和授权链。")
    else:
        summary = load_json(build_summary)
        if summary.get("mode") != "delivery":
            add(blockers, "not_formal_build_mode", "构建摘要不是 delivery 模式。")
        if summary.get("output") and Path(summary["output"]).resolve() != docx:
            add(blockers, "summary_output_mismatch", "构建摘要绑定的输出文件不是当前 DOCX。")
        if summary.get("output_sha256") != sha256_file(docx):
            add(blockers, "summary_docx_hash_mismatch", "当前 DOCX 哈希与构建摘要不一致，文件可能已被替换或修改。")
        if not summary.get("template_sha256"):
            add(blockers, "confirmed_template_missing", "构建摘要没有已确认模板哈希。")
        authorization = summary.get("authorization") or {}
        if not authorization.get("validation_run_id"):
            add(blockers, "delivery_authorization_missing", "构建摘要没有数据库交付校验授权。")
        if validation_run_id and authorization.get("validation_run_id") != validation_run_id:
            add(blockers, "delivery_authorization_stale", "构建摘要绑定的交付校验不是数据库最新通过记录。")
        if summary.get("markdown_residue") != {
            "heading_markers": 0, "bold_markers": 0, "code_markers": 0
        }:
            add(blockers, "summary_reports_markdown_residue", "构建摘要未证明 Markdown 残留为零。")

    public_metrics = {
        key: value for key, value in metrics.items() if key != "heading_titles"
    }
    public_metrics["heading_title_count"] = len(metrics["heading_titles"])
    public_metrics["heading_title_sample"] = metrics["heading_titles"][:30]
    return {
        "schema_version": "1.0",
        "docx": str(docx),
        "docx_sha256": sha256_file(docx),
        "status": "failed" if blockers else "warning" if warnings else "passed",
        "required_body_chars": required_chars,
        "metrics": public_metrics,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "blockers": blockers,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", type=Path)
    parser.add_argument("--build-summary", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--project-code", default="")
    parser.add_argument("--minimum-body-chars", type=int, default=30000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(
        args.docx, build_summary=args.build_summary, database=args.database,
        project_code=args.project_code, minimum_body_chars=args.minimum_body_chars,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
