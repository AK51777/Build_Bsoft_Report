#!/usr/bin/env python3
"""Export self-contained chapter task packages from composition plans."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from build_policy_section_material import policy_source_material
from knowledge_db import apply_migrations, connect


ROLE_ARGUMENT_OUTLINES = {
    "project_overview": ["项目定位与编制边界", "建设范围分类", "建设方式与依赖", "验收与决策边界"],
    "basis": ["国家政策法规", "行业标准与评价规范", "政策条款与本项目关系", "适用性及有效性边界"],
    "current_state": ["资料边界与核实口径", "业务运行现状", "应用系统现状", "数据接口与基础环境", "待核实事项"],
    "problem_need": ["证据支持的问题表现", "成因与影响链", "建设需求响应", "范围承载与优先关系"],
    "necessity_feasibility": ["政策必要性", "业务必要性", "技术可行性", "数据与安全可行性", "实施条件与约束"],
    "overall_design": ["建设原则", "总体目标", "业务与应用架构", "数据与集成架构", "技术与安全架构", "验收闭环"],
    "construction_content": ["客户清单范围", "能力映射", "功能流程", "数据接口", "安全控制", "验收取证"],
    "implementation_operation": ["项目治理与组织分工", "阶段计划与成果物", "配置和开发管理", "数据迁移", "测试与上线切换", "培训与知识转移", "运维服务与SLA", "质量安全和变更控制"],
    "investment_funding": ["估算范围与口径", "范围和费用映射", "计价依据与复核", "资金来源和年度安排", "概算变更控制"],
    "benefit_performance": ["医疗服务效益", "临床与质量效益", "运营管理效益", "数据与安全效益", "社会效益", "指标定义与取证", "基线目标和评价机制"],
    "risk": ["政策与合规风险", "范围与需求风险", "数据迁移风险", "接口与技术风险", "网络数据安全风险", "进度质量与运维风险"],
    "conclusion": ["研究判断", "推进条件", "待确认前置事项", "后续工作建议"],
}

CHAPTER_ARGUMENT_OUTLINES = {
    "1.1.1": ["项目定位与编制边界", "验收与决策边界"],
    "1.1.2": ["建设范围分类", "建设方式与依赖", "验收与决策边界"],
    "2.1.1": ["资料边界与核实口径", "业务运行现状", "待核实事项"],
    "2.1.2": ["资料边界与核实口径", "应用系统现状", "数据接口与基础环境", "待核实事项"],
    "2.2.1": ["证据支持的问题表现", "成因与影响链", "范围承载与优先关系"],
    "2.2.2": ["建设需求响应", "范围承载与优先关系", "验收与决策边界"],
    "3.1.1": ["政策必要性", "业务必要性", "实施条件与约束"],
    "3.2.1": ["技术可行性", "数据与安全可行性", "实施条件与约束"],
    "4.1.1": ["建设原则", "业务与应用架构", "数据与集成架构", "技术与安全架构"],
    "4.1.2": ["总体目标", "指标定义与取证", "基线目标和评价机制", "验收闭环"],
    "4.2.1": ["业务与应用架构", "数据与集成架构", "技术与安全架构", "验收闭环"],
    "4.2.2": ["数据与集成架构", "技术与安全架构", "网络数据安全风险", "验收闭环"],
    "6.1.1": ["项目治理与组织分工", "阶段计划与成果物", "进度与里程碑控制", "质量安全和变更控制"],
    "6.1.2": ["数据迁移", "测试与上线切换", "业务连续性与回退", "配置和开发管理"],
    "6.2.1": ["培训与知识转移", "运维服务与SLA", "运行监测与持续改进", "运维交接与服务评价"],
    "7.1.1": ["估算范围与口径", "计价依据与复核", "全生命周期成本"],
    "7.1.2": ["范围和费用映射", "软件与服务费用", "基础资源与其他费用", "概算变更控制"],
    "7.2.1": ["资金来源和年度安排", "支付条件与资金控制", "运行维护经费"],
    "8.1.1": ["医疗服务效益", "临床与质量效益", "运营管理效益", "数据与安全效益", "社会效益"],
    "8.2.1": ["指标体系设计", "指标定义与取证", "基线目标和评价机制", "验收闭环"],
    "9.1.1": ["政策与合规风险", "范围与需求风险", "数据迁移风险", "接口与技术风险", "网络数据安全风险", "进度质量与运维风险"],
    "10.1.1": ["研究判断", "推进条件", "待确认前置事项", "后续工作建议"],
}


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
        if policy and clause:
            result["assembly_material"] = policy_source_material(
                dict(policy), dict(clause), result
            )
        return result
    if source_type == "reference":
        row = conn.execute(
            "SELECT * FROM project_policy_catalog_match WHERE candidate_match_id=?",
            (object_id,),
        ).fetchone()
        if row is not None:
            result = dict(row)
            entry = conn.execute(
                "SELECT * FROM policy_catalog_entry WHERE catalog_entry_id=?",
                (row["catalog_entry_id"],),
            ).fetchone()
            result["policy_catalog_entry"] = dict(entry) if entry else None
            result["candidate_only"] = True
            result["prohibited_claim"] = (
                "目录标题只能用于核验清单，不得据此生成政策要求或进入正式编制依据。"
            )
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
    argument_outline = package.get("recommended_argument_outline") or []
    if argument_outline:
        lines.extend(["", "## 推荐论证层次", ""])
        lines.extend(f"- {item}" for item in argument_outline)
    lines.extend(["", "## 必备表格", ""])
    lines.extend(f"- {table}" for table in plan["required_tables"] or ["无强制表格"])
    lines.extend(["", "## 禁止内容", ""])
    lines.extend(f"- {item}" for item in plan["forbidden_content"])
    lines.extend(["", "## 完成条件", ""])
    lines.extend(f"- {item}" for item in plan["completion_rules"])
    if package.get("outline_nodes"):
        lines.extend(
            [
                "",
                "## 动态正文骨架",
                "",
                "以下标题树来自客户清单、能力映射和已审方案段落。正文必须按顺序完整承载，不得把多个系统压缩成一个泛化段落。",
                "",
            ]
        )
        for node in package["outline_nodes"]:
            marker = "#" * int(node["heading_level"])
            state = "可形成确定性正文" if node["status"] == "ready" else "仅形成可评审初稿，正式交付前须确认"
            lines.append(
                f"- `{marker} {node['chapter_code']} {node['title']}` — "
                f"`{node['node_kind']}` / `{node['usage_mode']}` / {state} / "
                f"{node['length_min']}—{node['length_max']} 字"
            )
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
            "7. 建设内容章节必须逐项输出动态正文骨架：四级写清单系统边界、建设方式、依赖和验收；五级写能力定位与业务闭环；六至七级写功能流程、数据输入输出、接口、安全和验收要点。",
            "8. `working_only` 节点只能写成基于客户清单与公司标准方案形成的待评审设计，不得写成医院现状、既定选型或已确认验收结论；同一四级系统仅在开头集中说明一次待确认边界，避免每段重复套话。",
            "9. 每个实质段落应形成‘对象/问题—建设机制—流程或数据—预期结果—验收取证’中的至少三项，不得只写背景口号或产品宣传。",
            "10. 经官方来源核验且现行有效、但尚未由用户确认的政策材料，只能用于带待确认边界的工作初稿；正式交付必须完成用户确认，不能因已核验而自动视为已批准采用。",
            "11. 政策依据不得只写政策名称：应列明名称、文号、发布单位、日期、有效性和与本项目的具体关系；实施运维、效益绩效章节必须按推荐论证层次展开，不能用少量通用套话代替。",
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
            SELECT p.*,b.section_role FROM section_composition_plan p
            LEFT JOIN section_blueprint b ON b.blueprint_id=p.blueprint_id
            WHERE project_id=? AND version_no=? AND applicability_status<>'not_applicable'
            ORDER BY chapter_code
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
            outline_rows = conn.execute(
                """
                SELECT * FROM section_outline_node WHERE plan_id=?
                ORDER BY ordinal,chapter_code
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
                "outline_nodes": [
                    {
                        **dict(node),
                        "metadata": json_value(node["metadata_json"]),
                    }
                    for node in outline_rows
                ],
                "recommended_argument_outline": CHAPTER_ARGUMENT_OUTLINES.get(
                    plan["chapter_code"],
                    ROLE_ARGUMENT_OUTLINES.get(plan.get("section_role", ""), []),
                ),
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
