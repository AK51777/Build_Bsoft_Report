#!/usr/bin/env python3
"""Assemble adopted section drafts into a deterministic report Markdown file."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

from knowledge_db import apply_migrations, connect
from report_outline import (
    CHAPTER_TITLES,
    GROUP_TITLES,
    build_outline_candidate,
    chapter_key,
)


def strip_duplicate_heading(content: str, title: str) -> str:
    lines = content.strip().splitlines()
    if lines and re.match(r"^#{1,7}\s+", lines[0]):
        heading = re.sub(r"^#{1,7}\s+", "", lines[0]).strip()
        if title in heading or heading in title:
            lines = lines[1:]
    return "\n".join(lines).strip()


def rewrite_confirmed_subheadings(content: str, nodes: list[dict]) -> str:
    value = content
    for node in nodes:
        level = int(node["heading_level"])
        pattern = re.compile(
            rf"^#{{{level}}}\s+{re.escape(node['node_code'])}(?:\s+.*)?$",
            flags=re.MULTILINE,
        )
        value = pattern.sub(
            f"{'#' * level} {node['node_code']} {node['title']}",
            value,
            count=1,
        )
    return value


def numbered_subheadings(content: str) -> set[tuple[int, str]]:
    return {
        (len(match.group(1)), match.group(2))
        for match in re.finditer(
            r"^(#{4,7})\s+(\d+(?:\.\d+){3,6})(?:\s+.*)?$",
            content,
            flags=re.MULTILINE,
        )
    }


def assemble(
    database: Path,
    project_code: str,
    mode: str = "working",
    *,
    allow_candidate_outline: bool = False,
) -> dict:
    if mode not in {"working", "delivery"}:
        raise ValueError("mode must be working or delivery")
    if allow_candidate_outline and mode != "working":
        raise ValueError("candidate outlines may only be previewed in working mode")
    outline = build_outline_candidate(database, project_code)
    if outline["status"] != "confirmed" and not allow_candidate_outline:
        raise RuntimeError(
            "report assembly blocked; current outline is not confirmed. "
            "Run confirm_report_outline.py after review."
        )
    section_nodes = [node for node in outline["nodes"] if node["node_kind"] == "section"]
    section_plan_ids = [node["source_object_id"] for node in section_nodes]
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT * FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        active_plans = conn.execute(
            """
            SELECT * FROM section_composition_plan WHERE project_id=?
            AND applicability_status<>'not_applicable'
            AND version_no=(
              SELECT MAX(p2.version_no) FROM section_composition_plan p2
              WHERE p2.project_id=section_composition_plan.project_id
                AND p2.chapter_code=section_composition_plan.chapter_code
                AND p2.applicability_status<>'not_applicable'
            )
            """,
            (project["project_id"],),
        ).fetchall()
        plan_by_id = {row["plan_id"]: row for row in active_plans}
        unknown_plan_ids = [plan_id for plan_id in section_plan_ids if plan_id not in plan_by_id]
        if unknown_plan_ids:
            raise RuntimeError(
                "confirmed outline references stale section plans: " + ", ".join(unknown_plan_ids)
            )
        plans = [plan_by_id[plan_id] for plan_id in section_plan_ids]
        adopted = {
            row["plan_id"]: row
            for row in conn.execute(
                "SELECT * FROM draft_section_version WHERE status='adopted'"
            )
        }
    missing = [plan["chapter_code"] for plan in plans if plan["plan_id"] not in adopted]
    if mode == "delivery" and missing:
        raise RuntimeError(f"delivery assembly blocked; missing adopted sections: {', '.join(missing)}")
    node_by_id = {node["report_outline_node_id"]: node for node in outline["nodes"]}
    plan_for_node: dict[str, str] = {}
    for node in outline["nodes"]:
        current = node
        while current and current["node_kind"] != "section":
            current = node_by_id.get(current["parent_node_id"])
        if current:
            plan_for_node[node["report_outline_node_id"]] = current["source_object_id"]
    descendants_by_plan: dict[str, list[dict]] = {}
    missing_outline_nodes = []
    for node in outline["nodes"]:
        if int(node["heading_level"]) <= 3:
            continue
        plan_id = plan_for_node.get(node["report_outline_node_id"], "")
        descendants_by_plan.setdefault(plan_id, []).append(node)
        draft = adopted.get(plan_id)
        if draft is None:
            continue
        expected_heading = re.compile(
            rf"^#{{{int(node['heading_level'])}}}\s+{re.escape(node['node_code'])}(?:\s+.*)?$",
            flags=re.MULTILINE,
        )
        if not expected_heading.search(draft["content"]):
            missing_outline_nodes.append(
                {"node_code": node["node_code"], "title": node["title"], "plan_id": plan_id}
            )
    unexpected_outline_nodes = []
    for plan_id, draft in adopted.items():
        if plan_id not in plan_by_id:
            continue
        expected = {
            (int(node["heading_level"]), node["node_code"])
            for node in descendants_by_plan.get(plan_id, [])
        }
        for level, code in sorted(numbered_subheadings(draft["content"]) - expected):
            unexpected_outline_nodes.append(
                {"node_code": code, "heading_level": level, "plan_id": plan_id}
            )
    if mode == "delivery" and missing_outline_nodes:
        raise RuntimeError(
            "delivery assembly blocked; adopted drafts do not carry the confirmed outline: "
            + ", ".join(item["node_code"] for item in missing_outline_nodes[:12])
        )
    if mode == "delivery" and unexpected_outline_nodes:
        raise RuntimeError(
            "delivery assembly blocked; adopted drafts contain headings outside the confirmed outline: "
            + ", ".join(item["node_code"] for item in unexpected_outline_nodes[:12])
        )
    lines = [
        f"# {project['official_name']}",
        "",
        f"## 可行性研究报告{'（工作稿）' if mode == 'working' else ''}",
        "",
        f"建设单位：{project['owner_name'] or '【待补充：建设单位】'}",
        "",
        f"编制日期：{date.today().isoformat()}",
        "",
    ]
    if mode == "working":
        lines.extend(
            [
                "> 【工作稿】章节“采纳”仅表示已选入本轮工作稿组装，不代表项目事实、候选能力映射、政策依据或正式交付已经确认。",
                "",
            ]
        )
        if outline["status"] != "confirmed":
            lines.extend(
                [
                    "> 【目录候选预览】本工作稿使用尚未确认的目录候选；正式组装前必须冻结确认版目录。",
                    "",
                ]
            )
    for node in outline["nodes"]:
        if node["node_kind"] == "chapter":
            lines.extend([f"# 第{node['node_code']}章 {node['title']}", ""])
        elif node["node_kind"] == "group":
            lines.extend([f"## {node['node_code']} {node['title']}", ""])
        elif node["node_kind"] == "section":
            plan = plan_by_id[node["source_object_id"]]
            lines.extend([f"### {node['node_code']} {node['title']}", ""])
            draft = adopted.get(plan["plan_id"])
            if draft:
                body = strip_duplicate_heading(draft["content"], node["title"])
                body = rewrite_confirmed_subheadings(
                    body, descendants_by_plan.get(plan["plan_id"], [])
                )
                lines.extend([body, ""])
            else:
                lines.extend([f"【待补充：{node['title']}尚无已采纳章节版本】", ""])
    return {
        "project_code": project_code,
        "mode": mode,
        "content": "\n".join(lines).rstrip() + "\n",
        "section_count": len(plans),
        "adopted_section_count": len(plans) - len(missing),
        "missing_adopted_sections": missing,
        "outline_version_id": outline["outline_version_id"],
        "outline_status": outline["status"],
        "outline_hash": outline["outline_hash"],
        "outline_source_signature": outline["source_signature"],
        "missing_confirmed_outline_nodes": missing_outline_nodes,
        "unexpected_draft_outline_nodes": unexpected_outline_nodes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--mode", choices=("working", "delivery"), default="working")
    parser.add_argument(
        "--allow-candidate-outline",
        action="store_true",
        help="Preview the current unconfirmed outline in working mode only.",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    result = assemble(
        args.database,
        args.project_code,
        args.mode,
        allow_candidate_outline=args.allow_candidate_outline,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.pop("content"), encoding="utf-8")
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
