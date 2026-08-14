#!/usr/bin/env python3
"""Assemble adopted section drafts into a deterministic report Markdown file."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

from knowledge_db import apply_migrations, connect


CHAPTER_TITLES = {
    "1": "总论",
    "2": "现状与需求分析",
    "3": "建设必要性与可行性",
    "4": "总体建设方案",
    "5": "建设内容",
    "6": "项目实施与运维",
    "7": "投资估算与资金筹措",
    "8": "效益与绩效评价",
    "9": "风险分析",
    "10": "研究结论与建议",
}
GROUP_TITLES = {
    "1.1": "项目概况", "1.2": "编制依据",
    "2.1": "建设单位与现状", "2.2": "问题与需求",
    "3.1": "建设必要性", "3.2": "建设可行性",
    "4.1": "建设原则与目标", "4.2": "总体架构",
    "5.1": "应用、集成与数据建设", "5.2": "基础设施与安全建设",
    "6.1": "项目实施", "6.2": "运行维护",
    "7.1": "投资估算", "7.2": "资金筹措",
    "8.1": "预期效益", "8.2": "绩效评价",
    "9.1": "风险与对策", "10.1": "结论与建议",
}


def chapter_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def strip_duplicate_heading(content: str, title: str) -> str:
    lines = content.strip().splitlines()
    if lines and re.match(r"^#{1,7}\s+", lines[0]):
        heading = re.sub(r"^#{1,7}\s+", "", lines[0]).strip()
        if title in heading or heading in title:
            lines = lines[1:]
    return "\n".join(lines).strip()


def assemble(database: Path, project_code: str, mode: str = "working") -> dict:
    if mode not in {"working", "delivery"}:
        raise ValueError("mode must be working or delivery")
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT * FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        plans = conn.execute(
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
        adopted = {
            row["plan_id"]: row
            for row in conn.execute(
                "SELECT * FROM draft_section_version WHERE status='adopted'"
            )
        }
    plans = sorted(plans, key=lambda row: chapter_key(row["chapter_code"]))
    missing = [plan["chapter_code"] for plan in plans if plan["plan_id"] not in adopted]
    if mode == "delivery" and missing:
        raise RuntimeError(f"delivery assembly blocked; missing adopted sections: {', '.join(missing)}")
    lines = [
        f"# {project['official_name']}",
        "",
        "## 可行性研究报告",
        "",
        f"建设单位：{project['owner_name'] or '【待补充：建设单位】'}",
        "",
        f"编制日期：{date.today().isoformat()}",
        "",
    ]
    current_chapter = ""
    current_group = ""
    for plan in plans:
        parts = plan["chapter_code"].split(".")
        chapter = parts[0]
        group = ".".join(parts[:2])
        if chapter != current_chapter:
            lines.extend([f"# 第{chapter}章 {CHAPTER_TITLES.get(chapter, '')}", ""])
            current_chapter = chapter
            current_group = ""
        if group != current_group:
            lines.extend([f"## {group} {GROUP_TITLES.get(group, '')}", ""])
            current_group = group
        lines.extend([f"### {plan['chapter_code']} {plan['section_title']}", ""])
        draft = adopted.get(plan["plan_id"])
        if draft:
            lines.extend([strip_duplicate_heading(draft["content"], plan["section_title"]), ""])
        else:
            lines.extend([f"【待补充：{plan['section_title']}尚无已采纳章节版本】", ""])
    return {
        "project_code": project_code,
        "mode": mode,
        "content": "\n".join(lines).rstrip() + "\n",
        "section_count": len(plans),
        "adopted_section_count": len(plans) - len(missing),
        "missing_adopted_sections": missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--mode", choices=("working", "delivery"), default="working")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    result = assemble(args.database, args.project_code, args.mode)
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
