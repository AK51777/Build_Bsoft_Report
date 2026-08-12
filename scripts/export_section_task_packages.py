#!/usr/bin/env python3
"""Export self-contained chapter task packages from composition plans."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect


def json_value(value: str) -> Any:
    return json.loads(value or "[]")


def resolve_source(conn, source_type: str, object_id: str) -> dict[str, Any]:
    queries = {
        "fact": ("SELECT * FROM project_fact WHERE fact_id=?", object_id),
        "scope": ("SELECT * FROM project_scope_item WHERE scope_id=?", object_id),
        "corpus": ("SELECT * FROM corpus_block WHERE block_id=?", object_id),
        "capability": (
            "SELECT * FROM product_capability WHERE capability_id=?",
            object_id,
        ),
    }
    if source_type == "policy":
        row = conn.execute(
            "SELECT * FROM project_policy_match WHERE match_id=?", (object_id,)
        ).fetchone()
        if row is None:
            return {"missing": True}
        result = dict(row)
        policy = conn.execute(
            "SELECT * FROM policy_document WHERE policy_id=?", (row["policy_id"],)
        ).fetchone()
        clause = conn.execute(
            "SELECT * FROM policy_clause WHERE clause_id=?", (row["clause_id"],)
        ).fetchone()
        result["policy_document"] = dict(policy) if policy else None
        result["policy_clause"] = dict(clause) if clause else None
        return result
    query = queries.get(source_type)
    if query is None:
        return {"source_object_id": object_id}
    row = conn.execute(query[0], (query[1],)).fetchone()
    return dict(row) if row else {"missing": True}


def safe_filename(value: str) -> str:
    return re.sub(r"[<>:\"/\\|?*]+", "_", value).strip(" .")


def render_markdown(package: dict[str, Any]) -> str:
    plan = package["plan"]
    project = package["project"]
    lines = [
        f"# {plan['chapter_code']} {plan['section_title']} 章节任务包",
        "",
        "## 项目口径",
        "",
        f"- 项目编号：`{project['project_code']}`",
        f"- 项目名称：{project['official_name']}",
        f"- 建设单位：{project['owner_name'] or '【待补充】'}",
        f"- 基线版本：`{project['baseline_version']}`",
        f"- 计划状态：`{plan['status']}`",
        "",
        "## 章节边界",
        "",
        f"- 章节目的：{plan['purpose']}",
        f"- 结论边界：{plan['conclusion_boundary']}",
        f"- 建议篇幅：{plan['length_min'] or '不限'}—{plan['length_max'] or '不限'} 字",
        "",
        "## 必须回答",
        "",
    ]
    lines.extend(f"- {question}" for question in plan["required_questions"])
    lines.extend(["", "## 必备表格", ""])
    lines.extend(f"- {table}" for table in plan["required_tables"] or ["无强制表格"])
    lines.extend(["", "## 禁止内容", ""])
    lines.extend(f"- {item}" for item in plan["forbidden_content"])
    lines.extend(["", "## 完成条件", ""])
    lines.extend(f"- {item}" for item in plan["completion_rules"])
    lines.extend(["", "## 来源绑定", ""])
    for source in package["sources"]:
        lines.append(
            f"### `{source['source_type']}` / `{source['source_object_id']}` / `{source['usage_mode']}`"
        )
        lines.append("")
        lines.append(f"- 说明：{source['notes'] or '无'}")
        lines.append("- 数据：")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(source["data"], ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    lines.extend(
        [
            "## 执行要求",
            "",
            "1. 仅以本任务包和项目数据库中的同版本对象为输入边界。",
            "2. `direct` 可作为确定性内容；`evidence` 仅按条款边界引用；`parameterized` 必须结合项目事实改写。",
            "3. `structure_only` 只复用结构；`prohibited` 只用于识别缺口或禁止继承，不得写成确定性正文。",
            "4. 先写证据骨架，再补论证；缺失内容使用明确的 `【待补充：…】` 或 `【待确认：…】`。",
            "5. 不得从产品能力、参考项目或行业常识反向扩大客户范围。",
            "6. 输出正文时保留来源追踪清单，但正式交付正文不显示内部 ID。",
            "",
        ]
    )
    return "\n".join(lines)


def export_packages(database: Path, project_code: str, output_dir: Path, version_no: int = 1) -> dict:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT * FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        plans = conn.execute(
            """
            SELECT * FROM section_composition_plan
            WHERE project_id=? AND version_no=? ORDER BY chapter_code
            """,
            (project["project_id"], version_no),
        ).fetchall()
        if not plans:
            raise RuntimeError("no section composition plans found; build plans first")
        written = []
        blocked = 0
        for plan_row in plans:
            plan = dict(plan_row)
            for field in (
                "required_questions_json",
                "required_tables_json",
                "forbidden_content_json",
                "completion_rules_json",
            ):
                plan[field.removesuffix("_json")] = json_value(plan.pop(field))
            source_rows = conn.execute(
                """
                SELECT * FROM section_plan_source WHERE plan_id=?
                ORDER BY source_type,source_object_id
                """,
                (plan["plan_id"],),
            ).fetchall()
            sources = [
                {
                    **dict(source),
                    "data": resolve_source(
                        conn, source["source_type"], source["source_object_id"]
                    ),
                }
                for source in source_rows
            ]
            package = {
                "schema_version": "1.0",
                "project": dict(project),
                "plan": plan,
                "sources": sources,
                "source_summary": {
                    mode: sum(source["usage_mode"] == mode for source in sources)
                    for mode in (
                        "direct",
                        "evidence",
                        "parameterized",
                        "structure_only",
                        "prohibited",
                    )
                },
            }
            stem = safe_filename(f"CH{plan['chapter_code']}-{plan['section_title']}")
            json_path = output_dir / f"{stem}.json"
            markdown_path = output_dir / f"{stem}.md"
            json_path.write_text(
                json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            markdown_path.write_text(render_markdown(package), encoding="utf-8")
            written.extend([str(json_path), str(markdown_path)])
            blocked += int(plan["status"] == "blocked")
    return {
        "project_code": project_code,
        "version_no": version_no,
        "package_count": len(plans),
        "blocked_package_count": blocked,
        "files_written": written,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--version-no", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = export_packages(
        args.database, args.project_code, args.output_dir, args.version_no
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
