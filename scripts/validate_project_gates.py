#!/usr/bin/env python3
"""Evaluate P0/P1 correctness gates without silently resolving unknowns."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_policy_section_material import build_material
from knowledge_db import apply_migrations, connect, dump_json, now_iso, stable_id


BASIS_GATE_LABELS = {
    "policy_basis": "政策类依据",
    "industry_standard": "行业标准依据",
    "security_standard": "安全类标准依据",
    "investment_basis": "投资估算编制依据",
}
BACKGROUND_GATE_LABELS = {
    "national": "国家政策背景",
    "province": "省/自治区政策背景",
    "prefecture": "市/项目建设地区政策背景",
}
BASIS_HARD_MINIMUMS = {
    "policy_basis": 16,
    "industry_standard": 20,
    "security_standard": 16,
    "investment_basis": 5,
}
BACKGROUND_HARD_MINIMUMS = {"national": 8, "province": 2, "prefecture": 1}


def policy_material_quality_checks(
    material: dict | None,
    *,
    material_error: str = "",
) -> list[dict]:
    if material is None:
        reason = material_error or "政策章节材料不可用"
        return [
            {
                "stage_code": "S1P_POLICY",
                "gate_code": "GATE-BASIS-QUANTITY",
                "result": "fail",
                "blocking_count": 1,
                "message": f"无法核验四类编制依据数量：{reason}。",
            },
            {
                "stage_code": "S1P_POLICY",
                "gate_code": "GATE-POLICY-BACKGROUND-CHAIN",
                "result": "fail",
                "blocking_count": 1,
                "message": f"无法核验政策背景来源、属地层级与顺序：{reason}。",
            },
            {
                "stage_code": "S1P_POLICY",
                "gate_code": "GATE-POLICY-MATERIAL-ELIGIBILITY",
                "result": "fail",
                "blocking_count": 1,
                "message": f"无法核验政策材料完整性与交付资格：{reason}。",
            },
        ]

    quality = material.get("quality", {})
    basis_quality = quality.get("formal_basis", {})
    basis_groups = material.get("basis_groups", {})
    basis_actual_counts = {
        section: len(basis_groups.get(section, [])) for section in BASIS_GATE_LABELS
    }
    basis_minimums = {
        section: max(
            BASIS_HARD_MINIMUMS[section],
            int(basis_quality.get(section, {}).get("minimum", 0)),
        )
        for section in BASIS_GATE_LABELS
    }
    basis_gaps = {
        section: max(0, basis_minimums[section] - basis_actual_counts[section])
        for section in BASIS_GATE_LABELS
    }
    basis_overages = {
        section: int(basis_quality.get(section, {}).get("overage", 0))
        for section in BASIS_GATE_LABELS
    }
    total_basis_gap = sum(basis_gaps.values())
    total_basis_overage = sum(basis_overages.values())
    basis_counts = "、".join(
        f"{label}{basis_actual_counts[section]}/{basis_minimums[section]}"
        for section, label in BASIS_GATE_LABELS.items()
    )
    basis_result = "fail" if total_basis_gap else ("warning" if total_basis_overage else "pass")

    background_quality = quality.get("formal_background", {})
    background_groups = material.get("policy_background_groups", {})
    background_gaps = {
        level: max(
            0,
            max(
                BACKGROUND_HARD_MINIMUMS.get(level, 0),
                int(item.get("minimum", 0)),
            )
            - len(background_groups.get(level, [])),
        )
        for level, item in background_quality.items()
    }
    background_gap = sum(background_gaps.values())
    order_ok = bool(material.get("policy_background_is_ordered_subsequence"))
    working_order_ok = bool(material.get("working_policy_background_is_ordered_subsequence"))
    orphan_count = len(material.get("policy_background_orphan_ids", []))
    missing_context = list(material.get("project_context", {}).get("missing_required_context", []))
    background_blocking = background_gap + orphan_count + len(missing_context)
    if not order_ok:
        background_blocking += 1
    if not working_order_ok:
        background_blocking += 1
    background_counts = "、".join(
        f"{BACKGROUND_GATE_LABELS.get(level, level)}{len(background_groups.get(level, []))}/"
        f"{max(BACKGROUND_HARD_MINIMUMS.get(level, 0), int(item.get('minimum', 0)))}"
        for level, item in background_quality.items()
    ) or "无可核验层级"

    delivery_blockers = list(quality.get("delivery_blockers", []))
    delivery_eligible = material.get("delivery_eligible") is True
    material_blocking = len(delivery_blockers) or (0 if delivery_eligible else 1)

    return [
        {
            "stage_code": "S1P_POLICY",
            "gate_code": "GATE-BASIS-QUANTITY",
            "result": basis_result,
            "blocking_count": total_basis_gap,
            "message": (
                "四类正式编制依据必须达到项目画像规定的硬下限；超过建议上限时转人工复核。"
                f" 当前：{basis_counts}。"
            ),
        },
        {
            "stage_code": "S1P_POLICY",
            "gate_code": "GATE-POLICY-BACKGROUND-CHAIN",
            "result": "fail" if background_blocking else "pass",
            "blocking_count": background_blocking,
            "message": (
                "政策背景只能使用政策类依据，须按同一顺序形成国家—省/自治区—市/地区层级，"
                "且项目属地与组织事实必须齐备。"
                f" 当前：{background_counts}；正式同序={order_ok}；工作态同序={working_order_ok}。"
            ),
        },
        {
            "stage_code": "S1P_POLICY",
            "gate_code": "GATE-POLICY-MATERIAL-ELIGIBILITY",
            "result": "fail" if material_blocking else "pass",
            "blocking_count": material_blocking,
            "message": (
                "政策材料必须通过唯一文件去重、条款章节许可、背景摘要、禁用主张、当前属地/范围适用性及交付资格检查。"
                + (
                    " 当前阻断：" + "、".join(delivery_blockers[:10]) + "。"
                    if delivery_blockers
                    else f" 当前交付资格={delivery_eligible}。"
                )
            ),
        },
    ]


def validate(database: Path, project_code: str) -> dict:
    policy_material = None
    policy_material_error = ""
    try:
        policy_material = build_material(database, project_code, mode="working")
    except ValueError as exc:
        policy_material_error = str(exc)
    with connect(database) as conn:
        apply_migrations(conn)
        project = conn.execute("SELECT * FROM project WHERE project_code=?", (project_code,)).fetchone()
        if not project:
            raise SystemExit(f"未找到项目：{project_code}")
        project_id = project["project_id"]
        checks = []

        def count(sql: str, params=()) -> int:
            return int(conn.execute(sql, params).fetchone()[0])

        unresolved_conflicts = count(
            """
            SELECT COUNT(*) FROM project_fact
            WHERE project_id=? AND materiality IN ('A','B') AND fact_status='conflict'
            """,
            (project_id,),
        )
        checks.append(
            {
                "stage_code": "S1_FACT",
                "gate_code": "GATE-FACT-CONFLICT",
                "result": "fail" if unresolved_conflicts else "pass",
                "blocking_count": unresolved_conflicts,
                "message": "A/B级冲突事实必须解决；不得自行选取口径。",
            }
        )
        required_keys = {
            "project.official_name",
            "scope.final_boundary",
            "acceptance.emr_level",
            "acceptance.interop_level",
        }
        rows = conn.execute(
            "SELECT fact_key,fact_status FROM project_fact WHERE project_id=?",
            (project_id,),
        ).fetchall()
        confirmed_keys = {
            row["fact_key"]
            for row in rows
            if row["fact_status"] in {"confirmed", "material_explicit"}
        }
        missing_core = sorted(required_keys - confirmed_keys)
        checks.append(
            {
                "stage_code": "S1_FACT",
                "gate_code": "GATE-FACT-CORE",
                "result": "fail" if missing_core else "pass",
                "blocking_count": len(missing_core),
                "message": "项目正式名称、本期边界和核心验收目标必须形成已确认基线。"
                + (f" 缺失：{', '.join(missing_core)}" if missing_core else ""),
            }
        )
        pending_ab = count(
            """
            SELECT COUNT(*) FROM project_fact
            WHERE project_id=? AND materiality IN ('A','B')
              AND fact_status IN ('pending_confirmation','pending_supplement')
            """,
            (project_id,),
        )
        checks.append(
            {
                "stage_code": "S1_FACT",
                "gate_code": "GATE-FACT-PENDING",
                "result": "warning" if pending_ab else "pass",
                "blocking_count": 0,
                "message": "A/B级未知项允许保留显式占位，但不得写成确定事实。",
            }
        )
        open_questions = count(
            "SELECT COUNT(*) FROM confirmation_question WHERE project_id=? AND status='open'",
            (project_id,),
        )
        checks.append(
            {
                "stage_code": "S1_FACT",
                "gate_code": "GATE-QUESTION-BATCH",
                "result": "pass" if open_questions <= 15 else "fail",
                "blocking_count": max(open_questions - 15, 0),
                "message": "单轮实质性确认问题不得超过15项。",
            }
        )
        latest_policy_run = conn.execute(
            """
            SELECT match_run_id FROM policy_match_run
            WHERE project_id=? AND status='completed'
            ORDER BY completed_at DESC, started_at DESC, match_run_id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        latest_policy_run_id = latest_policy_run["match_run_id"] if latest_policy_run else ""
        basis_total = count(
            "SELECT COUNT(DISTINCT policy_id) FROM project_policy_match WHERE match_run_id=? AND basis_use=1",
            (latest_policy_run_id,),
        ) if latest_policy_run_id else 0
        invalid_basis = count(
            """
            SELECT COUNT(DISTINCT m.policy_id)
            FROM project_policy_match m
            JOIN policy_document p ON p.policy_id=m.policy_id
            JOIN policy_clause c ON c.clause_id=m.clause_id
            WHERE m.match_run_id=? AND m.basis_use=1
              AND (
                p.validity_status<>'current' OR p.verification_status<>'verified'
                OR p.official_url='' OR c.verification_status<>'verified'
              )
            """,
            (latest_policy_run_id,),
        ) if latest_policy_run_id else 0
        policy_evidence_ok = basis_total > 0 and invalid_basis == 0
        checks.append(
            {
                "stage_code": "S1P_POLICY",
                "gate_code": "GATE-POLICY-EVIDENCE",
                "result": "pass" if policy_evidence_ok else "fail",
                "blocking_count": invalid_basis or (0 if basis_total else 1),
                "message": "最新一次政策匹配中的每项正式依据都必须有现行有效、条款已核验的官方原文。",
            }
        )
        unconfirmed_policy = count(
            """
            SELECT COUNT(DISTINCT policy_id) FROM project_policy_match
            WHERE match_run_id=? AND basis_use=1 AND decision_status<>'user_confirmed'
            """,
            (latest_policy_run_id,),
        ) if latest_policy_run_id else 0
        checks.append(
            {
                "stage_code": "S1P_POLICY",
                "gate_code": "GATE-POLICY-CONFIRMATION",
                "result": "warning" if unconfirmed_policy else "pass",
                "blocking_count": 0,
                "message": "AI推荐政策须由用户确认后才能冻结为正式编制依据。",
            }
        )
        missing_basis_citations = count(
            """
            SELECT COUNT(*) FROM project_policy_match m
            WHERE m.match_run_id=? AND m.basis_use=1
              AND NOT EXISTS (
                SELECT 1 FROM policy_citation c
                WHERE c.match_id=m.match_id AND c.citation_purpose='basis'
              )
            """,
            (latest_policy_run_id,),
        ) if latest_policy_run_id else 0
        missing_background_citations = count(
            """
            SELECT COUNT(*) FROM project_policy_match m
            WHERE m.match_run_id=? AND m.background_use=1
              AND NOT EXISTS (
                SELECT 1 FROM policy_citation c
                WHERE c.match_id=m.match_id AND c.citation_purpose='background'
              )
            """,
            (latest_policy_run_id,),
        ) if latest_policy_run_id else 0
        citation_gap = missing_basis_citations + missing_background_citations
        checks.append(
            {
                "stage_code": "S1P_POLICY",
                "gate_code": "GATE-POLICY-CITATION-MATRIX",
                "result": "pass" if latest_policy_run_id and citation_gap == 0 else "fail",
                "blocking_count": citation_gap or (0 if latest_policy_run_id else 1),
                "message": "编制依据与政策背景必须共用最新匹配运行、相同政策ID和条款引用矩阵。",
            }
        )
        checks.extend(
            policy_material_quality_checks(
                policy_material,
                material_error=policy_material_error,
            )
        )
        latest_standard_run = conn.execute(
            """
            SELECT standard_match_run_id FROM document_standard_match_run
            WHERE project_id=? AND status='completed'
            ORDER BY completed_at DESC, started_at DESC, standard_match_run_id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        confirmed_standard = count(
            """
            SELECT COUNT(*) FROM project_document_standard_match
            WHERE standard_match_run_id=? AND decision_status='user_confirmed'
            """,
            (latest_standard_run["standard_match_run_id"],),
        ) if latest_standard_run else 0
        checks.append(
            {
                "stage_code": "S0_FORMAT",
                "gate_code": "GATE-DOCUMENT-STANDARD",
                "result": "pass" if confirmed_standard else "warning",
                "blocking_count": 0,
                "message": "地方或国家编制标准须形成候选并由用户确认；工作稿阶段允许保留告警。",
            }
        )
        selected_profile = count(
            """
            SELECT COUNT(*) FROM project_document_profile
            WHERE project_id=? AND decision_status='user_confirmed'
            """,
            (project_id,),
        )
        checks.append(
            {
                "stage_code": "S0_FORMAT",
                "gate_code": "GATE-FORMAT-PROFILE",
                "result": "pass" if selected_profile else "warning",
                "blocking_count": 0,
                "message": "Word正式交付前必须确认文档标准和格式画像；正文工作阶段允许告警。",
            }
        )

        scope_total = count(
            "SELECT COUNT(*) FROM project_scope_item WHERE project_id=? AND customer_scope=1",
            (project_id,),
        )
        checks.append(
            {
                "stage_code": "S2_SCOPE",
                "gate_code": "GATE-SCOPE-REGISTERED",
                "result": "pass" if scope_total else "fail",
                "blocking_count": 0 if scope_total else 1,
                "message": "客户本期建设范围必须形成至少一个可追溯的稳定范围项。",
            }
        )
        pending_scope = count(
            """
            SELECT COUNT(*) FROM project_scope_item
            WHERE project_id=? AND customer_scope=1
              AND status IN ('pending_confirmation','pending_supplement','conflict')
            """,
            (project_id,),
        )
        checks.append(
            {
                "stage_code": "S2_SCOPE",
                "gate_code": "GATE-SCOPE-BOUNDARY",
                "result": "warning" if pending_scope else "pass",
                "blocking_count": 0,
                "message": "范围边界未确认项可保留占位，但不得写成已确定建设内容。",
            }
        )
        confirmed_unmapped = count(
            """
            SELECT COUNT(*) FROM project_scope_item s
            WHERE s.project_id=? AND s.customer_scope=1 AND s.status='confirmed'
              AND NOT EXISTS (
                SELECT 1 FROM scope_product_map m
                WHERE m.scope_id=s.scope_id AND m.status IN ('candidate','confirmed','conflict','missing')
              )
            """,
            (project_id,),
        )
        checks.append(
            {
                "stage_code": "S2_SCOPE",
                "gate_code": "GATE-SCOPE-MAPPING",
                "result": "warning" if confirmed_unmapped else "pass",
                "blocking_count": 0,
                "message": "已确认客户范围应有能力映射、缺口或冲突记录；能力不得反向扩大范围。",
            }
        )
        latest_scope_baseline = conn.execute(
            """
            SELECT baseline_id,status FROM scope_baseline WHERE project_id=?
            ORDER BY CASE status WHEN 'confirmed' THEN 0 WHEN 'pending_confirmation' THEN 1 ELSE 2 END,
                     version_no DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        checks.append(
            {
                "stage_code": "S2_SCOPE",
                "gate_code": "GATE-SCOPE-BASELINE",
                "result": (
                    "fail" if latest_scope_baseline is None
                    else "pass" if latest_scope_baseline["status"] == "confirmed"
                    else "warning"
                ),
                "blocking_count": 1 if latest_scope_baseline is None else 0,
                "message": "本期范围必须形成追加式基线；正式正文前应由用户确认且不得含待定项。",
            }
        )
        traceability_incomplete = 0
        traceability_conflict = 0
        if latest_scope_baseline:
            traceability_incomplete = count(
                "SELECT COUNT(*) FROM project_traceability_item WHERE baseline_id=? AND status='incomplete'",
                (latest_scope_baseline["baseline_id"],),
            )
            traceability_conflict = count(
                "SELECT COUNT(*) FROM project_traceability_item WHERE baseline_id=? AND status='conflict'",
                (latest_scope_baseline["baseline_id"],),
            )
        traceability_gap = traceability_incomplete + traceability_conflict
        baseline_confirmed = bool(
            latest_scope_baseline and latest_scope_baseline["status"] == "confirmed"
        )
        checks.append(
            {
                "stage_code": "S4_TRACEABILITY",
                "gate_code": "GATE-TRACEABILITY-MATRIX",
                "result": (
                    "fail" if baseline_confirmed and traceability_gap
                    else "warning" if traceability_gap or latest_scope_baseline is None
                    else "pass"
                ),
                "blocking_count": traceability_gap if baseline_confirmed else 0,
                "message": "问题—需求—建设—投资—指标—效益链不得断裂；无依据关系必须登记为缺口。",
            }
        )
        active_outline_candidate = conn.execute(
            """
            SELECT outline_version_id FROM report_outline_version
            WHERE project_id=? AND status='candidate'
            """,
            (project_id,),
        ).fetchone()
        confirmed_outline = conn.execute(
            """
            SELECT outline_version_id FROM report_outline_version
            WHERE project_id=? AND status='confirmed'
            """,
            (project_id,),
        ).fetchone()
        outline_current = confirmed_outline is not None and active_outline_candidate is None
        checks.append(
            {
                "stage_code": "S4_TRACEABILITY",
                "gate_code": "GATE-REPORT-OUTLINE-CONFIRMATION",
                "result": "pass" if outline_current else "fail",
                "blocking_count": 0 if outline_current else 1,
                "message": (
                    "目录候选必须显式确认并与当前章节计划、动态建设目录的来源签名绑定；"
                    "存在新的候选版本时，旧确认版不得继续组装。"
                ),
            }
        )

        checked_at = now_iso()
        for item in checks:
            conn.execute(
                """
                INSERT INTO stage_gate_result (
                  gate_result_id,project_id,stage_code,gate_code,result,
                  issue_count,blocking_count,details_json,checked_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    stable_id("GATE", project_id, item["gate_code"], checked_at),
                    project_id,
                    item["stage_code"],
                    item["gate_code"],
                    item["result"],
                    item["blocking_count"],
                    item["blocking_count"],
                    dump_json({"message": item["message"]}),
                    checked_at,
                ),
            )
        conn.commit()

    overall = "fail" if any(item["result"] == "fail" for item in checks) else (
        "warning" if any(item["result"] == "warning" for item in checks) else "pass"
    )
    return {
        "project_code": project_code,
        "checked_at": checked_at,
        "overall": overall,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.database, args.project_code)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
