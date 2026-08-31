#!/usr/bin/env python3
"""Run ten-category deterministic checks against adopted report sections."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from build_policy_section_material import build_material as build_policy_material
from knowledge_db import apply_migrations, connect, dump_json, now_iso, sha256_text, stable_id
from report_outline import build_outline_candidate
from validate_project_gates import policy_material_quality_checks
from validate_section_draft import (
    EVIDENCE_MARKER_PATTERN,
    HIGH_RISK_NUMBER_PATTERN,
    policy_content_quality_issues,
    visible_length,
)


LANGUAGE_TERMS = ("全面领先", "彻底解决", "国际一流", "必然实现", "完全满足", "确保达到")
PLACEHOLDER_PATTERN = re.compile(r"【(?:待补充|待确认|待核验|冲突|分析建议)[^】]*】")


def validate_report(
    database: Path,
    project_code: str,
    *,
    mode: str = "working",
    residual_terms: list[str] | None = None,
) -> dict:
    if mode not in {"working", "delivery"}:
        raise ValueError("mode must be working or delivery")
    residual_terms = [term.strip() for term in (residual_terms or []) if term.strip()]
    outline = build_outline_candidate(database, project_code)
    with connect(database.resolve()) as requirement_connection:
        project_requirement = requirement_connection.execute(
            "SELECT document_type FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
    requires_policy_chapters = bool(
        project_requirement
        and str(project_requirement["document_type"]) == "feasibility_study"
    )
    required_policy_chapter_codes = {"1.2.1", "2.1.1"}
    policy_chapter_codes = {
        str(node.get("node_code") or "")
        for node in outline.get("nodes", [])
        if node.get("node_kind") == "section"
    }
    policy_quality_checks = []
    policy_material = None
    policy_material_error = ""
    if mode == "delivery" and (
        requires_policy_chapters
        or policy_chapter_codes.intersection(required_policy_chapter_codes)
    ):
        try:
            policy_material = build_policy_material(database, project_code, mode="delivery")
        except ValueError as exc:
            policy_material_error = str(exc)
        policy_quality_checks = policy_material_quality_checks(
            policy_material,
            material_error=policy_material_error,
        )
    started_at = now_iso()
    issues: list[dict] = []

    def add(severity: str, issue_type: str, location: str, description: str, ids=(), action=""):
        issues.append(
            {
                "severity": severity,
                "issue_type": issue_type,
                "location": location,
                "description": description,
                "related_ids": list(ids),
                "suggested_action": action,
            }
        )

    missing_policy_chapters = sorted(
        required_policy_chapter_codes - policy_chapter_codes
        if requires_policy_chapters
        else set()
    )
    if missing_policy_chapters:
        add(
            "blocking" if mode == "delivery" else "high",
            "required_policy_chapters",
            "GATE-REQUIRED-POLICY-CHAPTERS",
            "可行性研究报告必须同时包含1.2.1可行性研究报告编制依据和2.1.1政策背景，且不得标记为不适用。缺失："
            + "、".join(missing_policy_chapters),
            missing_policy_chapters,
            "恢复两章的适用计划，按当前事实与已核验政策材料重新生成、校验并采纳。",
        )

    for check in policy_quality_checks:
        if check["result"] == "pass":
            continue
        add(
            "blocking" if check["result"] == "fail" else "high",
            "policy_quality_gate",
            check["gate_code"],
            check["message"],
            [check["gate_code"]],
            "补齐正式政策证据、属地事实和数量/顺序缺口后重新运行全文delivery校验。",
        )

    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT * FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        project_id = project["project_id"]
        plans = conn.execute(
            """
            SELECT * FROM section_composition_plan p WHERE project_id=?
              AND applicability_status<>'not_applicable'
              AND version_no=(
                SELECT MAX(p2.version_no) FROM section_composition_plan p2
                WHERE p2.project_id=p.project_id AND p2.chapter_code=p.chapter_code
                  AND p2.applicability_status<>'not_applicable'
              )
            """,
            (project_id,),
        ).fetchall()
        active_plan_ids = {row["plan_id"] for row in plans}
        adopted = {
            row["plan_id"]: row
            for row in conn.execute(
                "SELECT d.* FROM draft_section_version d JOIN section_composition_plan p ON p.plan_id=d.plan_id WHERE p.project_id=? AND d.status='adopted'",
                (project_id,),
            )
        }
        adopted = {
            plan_id: row for plan_id, row in adopted.items() if plan_id in active_plan_ids
        }
        full_text = "\n".join(row["content"] for row in adopted.values())
        adopted_manifest = [
            {
                "plan_id": plan_id,
                "draft_version_id": row["draft_version_id"],
                "version_no": row["version_no"],
                "content_sha256": sha256_text(row["content"]),
            }
            for plan_id, row in sorted(adopted.items())
        ]
        content_sha256 = sha256_text(
            json.dumps(adopted_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        if outline["status"] != "confirmed":
            add(
                "blocking" if mode == "delivery" else "high",
                "outline_confirmation",
                "report-outline",
                "当前目录候选尚未确认，不能作为正式报告的组装依据。",
                [outline["outline_version_id"]],
                "审阅 outline-candidate.json 后运行 confirm_report_outline.py。",
            )

        outline_node_by_id = {
            node["report_outline_node_id"]: node for node in outline["nodes"]
        }
        outline_plan_for_node: dict[str, str] = {}
        for node in outline["nodes"]:
            current = node
            while current and current["node_kind"] != "section":
                current = outline_node_by_id.get(current["parent_node_id"])
            if current:
                outline_plan_for_node[node["report_outline_node_id"]] = current[
                    "source_object_id"
                ]
        expected_headings_by_plan: dict[str, set[tuple[int, str]]] = {}
        for node in outline["nodes"]:
            if int(node["heading_level"]) <= 3:
                continue
            plan_id = outline_plan_for_node.get(node["report_outline_node_id"], "")
            expected_headings_by_plan.setdefault(plan_id, set()).add(
                (int(node["heading_level"]), node["node_code"])
            )
            draft = adopted.get(plan_id)
            if draft is None:
                continue
            expected_heading = re.compile(
                rf"^#{{{int(node['heading_level'])}}}\s+{re.escape(node['node_code'])}(?:\s+.*)?$",
                flags=re.MULTILINE,
            )
            if not expected_heading.search(draft["content"]):
                add(
                    "blocking" if mode == "delivery" else "high",
                    "outline_coverage",
                    node["node_code"],
                    "已采纳正文未承载当前确认目录节点。",
                    [node["report_outline_node_id"], plan_id],
                    "按确认版目录重新生成并校验对应章节。",
                )
        for plan_id, draft in adopted.items():
            actual = {
                (len(match.group(1)), match.group(2))
                for match in re.finditer(
                    r"^(#{4,7})\s+(\d+(?:\.\d+){3,6})(?:\s+.*)?$",
                    draft["content"],
                    flags=re.MULTILINE,
                )
            }
            for level, code in sorted(
                actual - expected_headings_by_plan.get(plan_id, set())
            ):
                add(
                    "blocking" if mode == "delivery" else "high",
                    "outline_extraneous_heading",
                    code,
                    "已采纳正文存在确认目录之外的编号标题。",
                    [plan_id],
                    "按确认版目录重新生成或调整对应章节。",
                )

        for plan in plans:
            if plan["plan_id"] not in adopted:
                add(
                    "blocking" if mode == "delivery" else "high",
                    "draft_completeness",
                    plan["chapter_code"],
                    "章节没有已采纳版本。",
                    [plan["plan_id"]],
                    "完成章节校验并采纳一个版本。",
                )
                continue
            draft = adopted[plan["plan_id"]]
            try:
                check = json.loads(draft["check_result_json"] or "{}")
            except json.JSONDecodeError:
                check = {}
            if check.get("status") != "passed" or check.get("content_sha256") != sha256_text(draft["content"]):
                add(
                    "blocking" if mode == "delivery" else "high",
                    "draft_validation",
                    plan["chapter_code"],
                    "已采纳章节缺少与当前内容哈希一致的章节校验通过记录。",
                    [draft["draft_version_id"]],
                    "重新运行章节校验；不得直接修改数据库状态绕过采纳门禁。",
                )
            elif mode == "delivery" and check.get("validation_mode") != "delivery":
                add(
                    "blocking",
                    "draft_validation_mode",
                    plan["chapter_code"],
                    "章节仅通过工作稿校验，尚未通过正式交付校验。",
                    [draft["draft_version_id"]],
                    "以 delivery 模式重新运行章节校验并处理全部占位与待确认项。",
                )
            if mode == "delivery" and plan["chapter_code"] in {"1.2.1", "2.1.1"}:
                for issue in policy_content_quality_issues(
                    draft["content"],
                    plan["chapter_code"],
                    policy_material,
                    mode=mode,
                    material_error=policy_material_error,
                    check_quality_gates=False,
                ):
                    add(
                        issue["severity"],
                        "policy_content_binding",
                        plan["chapter_code"],
                        issue["description"],
                        [draft["draft_version_id"], issue["code"]],
                        "按当前政策匹配运行和事实/范围基线重新生成、校验并采纳政策章节。",
                    )
            if plan["length_min"] and visible_length(draft["content"]) < plan["length_min"]:
                add(
                    "blocking" if mode == "delivery" else "high",
                    "content_depth",
                    plan["chapter_code"],
                    f"章节有效正文低于 {plan['length_min']} 字。",
                    [draft["draft_version_id"]],
                )
            if HIGH_RISK_NUMBER_PATTERN.search(draft["content"]) and not EVIDENCE_MARKER_PATTERN.search(draft["content"]):
                add(
                    "blocking" if mode == "delivery" else "high",
                    "quantitative_evidence",
                    plan["chapter_code"],
                    "高风险数字结论缺少来源标记。",
                    [draft["draft_version_id"]],
                )
        unsupported_facts = conn.execute(
            """
            SELECT DISTINCT f.fact_id FROM project_fact f
            JOIN section_plan_source s ON s.source_type='fact' AND s.source_object_id=f.fact_id
            JOIN section_composition_plan p ON p.plan_id=s.plan_id
            WHERE p.project_id=? AND s.usage_mode='direct'
              AND NOT EXISTS (SELECT 1 FROM fact_evidence e WHERE e.fact_id=f.fact_id)
            """,
            (project_id,),
        ).fetchall()
        for row in unsupported_facts:
            add("blocking" if mode == "delivery" else "high", "fact", "source-binding", "直接使用的事实没有证据绑定。", [row["fact_id"]])

        uncovered_scopes = conn.execute(
            """
            SELECT s.scope_id FROM project_scope_item s
            WHERE s.project_id=? AND s.customer_scope=1 AND s.status='confirmed'
              AND NOT EXISTS (
                SELECT 1 FROM section_plan_source ps
                JOIN section_composition_plan p ON p.plan_id=ps.plan_id
                JOIN draft_section_version d ON d.plan_id=p.plan_id AND d.status='adopted'
                WHERE ps.source_type='scope' AND ps.source_object_id=s.scope_id
                  AND ps.usage_mode='direct'
              )
            """,
            (project_id,),
        ).fetchall()
        for row in uncovered_scopes:
            add("blocking", "scope", "construction-content", "确认范围没有已采纳正文承载。", [row["scope_id"]])

        investment_plan = any(plan["chapter_code"].startswith("7.") for plan in plans)
        investment_facts = conn.execute(
            "SELECT COUNT(*) FROM project_fact WHERE project_id=? AND fact_key LIKE 'investment.%' AND fact_status IN ('confirmed','material_explicit')",
            (project_id,),
        ).fetchone()[0]
        if investment_plan and not investment_facts:
            add(
                "blocking" if mode == "delivery" else "high",
                "investment",
                "chapter-7",
                "投资章节缺少已确认或材料明确的投资事实。",
                action="保留明确占位并补充投资依据，不得由模型估价。",
            )
        indicator_facts = conn.execute(
            "SELECT COUNT(*) FROM project_fact WHERE project_id=? AND fact_key LIKE 'indicator.%' AND fact_status IN ('confirmed','material_explicit')",
            (project_id,),
        ).fetchone()[0]
        if any(plan["chapter_code"].startswith("8.") for plan in plans) and not indicator_facts:
            add(
                "blocking" if mode == "delivery" else "high",
                "indicator",
                "chapter-8",
                "绩效章节缺少已确认的指标定义、基线或目标事实。",
            )
        traceability_gaps = conn.execute(
            """
            SELECT t.traceability_id,t.scope_id,t.status,t.missing_items_json,b.status AS baseline_status
            FROM project_traceability_item t
            JOIN scope_baseline b ON b.baseline_id=t.baseline_id
            WHERE t.project_id=? AND b.status IN ('confirmed','pending_confirmation')
              AND t.status<>'complete'
            """,
            (project_id,),
        ).fetchall()
        for row in traceability_gaps:
            add(
                "blocking" if mode == "delivery" and row["baseline_status"] == "confirmed" else "high",
                "logic",
                "traceability",
                f"贯通链不完整：{row['missing_items_json']}",
                [row["traceability_id"], row["scope_id"]],
            )

        if project["official_name"] and adopted and project["official_name"] not in full_text:
            add("medium", "cross_chapter", "full-report", "已采纳正文未出现项目正式名称。")
        invalid_policy = conn.execute(
            """
            SELECT DISTINCT ps.source_object_id,m.decision_status
            FROM section_plan_source ps
            JOIN section_composition_plan p ON p.plan_id=ps.plan_id
            LEFT JOIN project_policy_match m ON m.match_id=ps.source_object_id
            WHERE p.project_id=? AND ps.source_type='policy' AND ps.usage_mode='evidence'
              AND (m.match_id IS NULL OR m.decision_status<>'user_confirmed')
            """,
            (project_id,),
        ).fetchall()
        for row in invalid_policy:
            missing_source = row["decision_status"] is None
            add(
                "blocking" if missing_source or mode == "delivery" else "medium",
                "policy",
                "source-binding",
                "政策证据来源不存在。" if missing_source else "政策证据已核验但尚未通过用户确认，仅可用于工作初稿。",
                [row["source_object_id"]],
            )
        for term in LANGUAGE_TERMS:
            if term in full_text:
                add("medium", "language", "full-report", f"存在绝对化或宣传性表述：{term}")
        for term in residual_terms:
            if term in full_text:
                add("blocking", "contamination", "full-report", f"命中参考残留词：{term}")
        placeholders = PLACEHOLDER_PATTERN.findall(full_text)
        if placeholders:
            add(
                "blocking" if mode == "delivery" else "medium",
                "placeholder",
                "full-report",
                f"正文包含 {len(placeholders)} 个待处理占位。",
                action="生成占位汇总并逐项确认、补充或保留风险说明。",
            )

        blocking_count = sum(issue["severity"] == "blocking" for issue in issues)
        high_count = sum(issue["severity"] == "high" for issue in issues)
        status = "failed" if blocking_count else "warning" if issues else "passed"
        completed_at = now_iso()
        validation_run_id = stable_id(
            "VALIDATION", project_id, "full_report", mode, started_at
        )
        conn.execute(
            """
            INSERT INTO validation_run (
              validation_run_id,project_id,validation_type,baseline_version,status,
              issue_count,blocking_count,started_at,completed_at,summary_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                validation_run_id,
                project_id,
                f"full_report_{mode}",
                project["baseline_version"],
                status,
                len(issues),
                blocking_count,
                started_at,
                completed_at,
                dump_json({
                    "high_count": high_count,
                    "content_sha256": content_sha256,
                    "adopted_manifest": adopted_manifest,
                    "outline_version_id": outline["outline_version_id"],
                    "outline_hash": outline["outline_hash"],
                    "outline_status": outline["status"],
                    "policy_match_run_id": (
                        policy_material.get("match_run_id", "") if policy_material else ""
                    ),
                    "policy_material_signature": (
                        policy_material.get("material_signature", "") if policy_material else ""
                    ),
                }),
            ),
        )
        for index, issue in enumerate(issues, start=1):
            conn.execute(
                """
                INSERT INTO validation_issue (
                  issue_id,validation_run_id,severity,issue_type,location,description,
                  related_ids_json,suggested_action,status,resolution
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    stable_id("ISSUE", validation_run_id, index, issue["issue_type"]),
                    validation_run_id,
                    issue["severity"],
                    issue["issue_type"],
                    issue["location"],
                    issue["description"],
                    dump_json(issue["related_ids"]),
                    issue["suggested_action"],
                    "open",
                    "",
                ),
            )
        conn.commit()
    return {
        "project_code": project_code,
        "mode": mode,
        "validation_run_id": validation_run_id,
        "status": status,
        "issue_count": len(issues),
        "blocking_count": blocking_count,
        "high_count": high_count,
        "content_sha256": content_sha256,
        "adopted_manifest": adopted_manifest,
        "outline_version_id": outline["outline_version_id"],
        "outline_hash": outline["outline_hash"],
        "outline_status": outline["status"],
        "policy_match_run_id": policy_material.get("match_run_id", "") if policy_material else "",
        "policy_material_signature": (
            policy_material.get("material_signature", "") if policy_material else ""
        ),
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--mode", choices=("working", "delivery"), default="working")
    parser.add_argument("--residual-terms", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    terms = (
        args.residual_terms.read_text(encoding="utf-8-sig").splitlines()
        if args.residual_terms
        else []
    )
    result = validate_report(
        args.database, args.project_code, mode=args.mode, residual_terms=terms
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
