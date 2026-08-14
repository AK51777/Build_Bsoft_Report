#!/usr/bin/env python3
"""Build traceable policy basis and paragraph materials from one project match run."""

from __future__ import annotations

import argparse
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect, sha256_text


REQUIREMENT_VERBS = {
    "mandatory": "明确要求",
    "guiding": "提出",
    "target": "明确目标",
    "encouraging": "鼓励",
    "evaluation": "建立评价要求",
    "background": "说明",
}

TOPIC_LABELS = {
    "cybersecurity": "网络安全",
    "data_security": "数据安全",
    "electronic_medical_record": "电子病历",
    "evaluation": "行业评价",
    "high_quality_hospital": "公立医院高质量发展",
    "hospital_informationization": "医院信息化",
    "hospital_management": "医院管理",
    "hospital_platform": "医院信息平台",
    "internet_health": "互联网医疗健康",
    "interoperability": "互联互通",
    "smart_hospital": "智慧医院",
    "standardization": "信息标准化",
}


def json_list(value: str | list[Any] | None) -> list[Any]:
    if isinstance(value, list):
        return value
    return json.loads(value or "[]")


def policy_source_material(policy: dict[str, Any], clause: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    requirement_type = clause.get("requirement_type", "guiding")
    forbidden_claims = json_list(clause.get("forbidden_claims_json"))
    if requirement_type != "mandatory":
        forbidden_claims = list(
            dict.fromkeys(
                forbidden_claims
                + ["不得把指导、评价、鼓励或背景性表述改写为强制要求", "不得写成项目已经达标或通过验收"]
            )
        )
    return {
        "basis_entry": {
            "title": policy["title"],
            "document_no": policy.get("document_no", ""),
            "issuer": policy["issuer"],
            "publish_date": policy.get("publish_date", ""),
            "official_url": policy["official_url"],
            "policy_type": policy.get("policy_type", ""),
        },
        "clause_id": clause["clause_id"],
        "article_path": clause["article_path"],
        "requirement_type": requirement_type,
        "requirement_verb": REQUIREMENT_VERBS.get(requirement_type, "提出"),
        "safe_summary": clause["normalized_summary"],
        "project_relation": match["project_relation"],
        "applicability_notes": clause.get("applicability_notes", ""),
        "permitted_sections": json_list(clause.get("permitted_sections_json")),
        "forbidden_claims": forbidden_claims,
        "decision_status": match["decision_status"],
    }


def action_sentence(topic_tags: set[str]) -> str:
    if topic_tags.intersection({"data_security", "cybersecurity", "cryptography", "classified_protection"}):
        return "编制时应在安全体系、数据保护和运维管理设计中落实对应防护要求，并以项目实际边界确定具体措施。"
    if topic_tags.intersection({"evaluation", "electronic_medical_record", "interoperability"}):
        return "编制时应把评价对象、适用范围和测评边界转化为可核验的建设任务与指标，但不得提前表述为已经达标。"
    if topic_tags.intersection({"hospital_platform", "hospital_informationization", "infrastructure", "standardization"}):
        return "编制时应在总体架构、平台整合、标准体系和建设内容之间建立对应关系，并保持与已确认建设范围一致。"
    return "编制时应将该政策作为必要性和建设方向依据，并结合项目事实说明其适用边界。"


def policy_paragraph(policy: dict[str, Any], materials: list[dict[str, Any]], topic_tags: set[str]) -> str:
    document_no = f"（{policy['document_no']}）" if policy.get("document_no") else ""
    summaries = []
    boundaries = []
    verbs = []
    for material in materials:
        summary = material["safe_summary"].rstrip("。；; ")
        if summary:
            summaries.append(summary)
        boundary = material["applicability_notes"].rstrip("。；; ")
        if boundary:
            boundaries.append(boundary)
        verbs.append(material["requirement_verb"])
    summary_text = "；".join(dict.fromkeys(summaries))
    boundary_text = "；".join(dict.fromkeys(boundaries))
    lead_verb = verbs[0] if verbs else "提出"
    action_text = {
        "明确要求": "提出明确要求",
        "建立评价要求": "建立评价要求",
        "明确目标": "明确建设目标",
    }.get(lead_verb, f"{lead_verb}相关要求")
    sentences = [
        f"《{policy['title']}》{document_no}由{policy['issuer']}发布，围绕相关建设任务{action_text}。"
    ]
    if summary_text:
        sentences.append(f"经核验的相关条款主要包括：{summary_text}。")
    topic_names = [TOPIC_LABELS[tag] for tag in sorted(topic_tags) if tag in TOPIC_LABELS]
    if topic_names:
        sentences.append(f"该文件与本项目的{'、'.join(topic_names[:5])}建设直接相关。")
    sentences.append(action_sentence(topic_tags))
    if boundary_text:
        sentences.append(f"适用时还应注意：{boundary_text}。")
    return "".join(sentences)


def build_material(database: Path, project_code: str, *, mode: str = "working") -> dict[str, Any]:
    if mode not in {"working", "delivery"}:
        raise ValueError("mode must be working or delivery")
    with connect(database.resolve()) as connection:
        apply_migrations(connection)
        project = connection.execute(
            "SELECT * FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if not project:
            raise ValueError(f"project not found: {project_code}")
        run = connection.execute(
            """
            SELECT * FROM policy_match_run
            WHERE project_id=? AND status='completed'
            ORDER BY completed_at DESC,started_at DESC,match_run_id DESC LIMIT 1
            """,
            (project["project_id"],),
        ).fetchone()
        if not run:
            raise ValueError("completed policy match run not found")
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT m.*,p.title,p.document_no,p.issuer,p.publish_date,p.official_url,p.policy_type,
                       p.validity_status,p.verification_status,
                       c.article_path,c.normalized_summary,c.topic_tags_json,
                       c.requirement_type,c.applicability_notes,
                       c.permitted_sections_json,c.forbidden_claims_json,
                       c.verification_status AS clause_verification_status
                FROM project_policy_match m
                JOIN policy_document p ON p.policy_id=m.policy_id
                JOIN policy_clause c ON c.clause_id=m.clause_id
                WHERE m.match_run_id=? AND m.decision_status<>'user_excluded'
                ORDER BY COALESCE(m.basis_order,999999),m.sort_key,m.policy_id,m.clause_id
                """,
                (run["match_run_id"],),
            )
        ]
        catalog_run = connection.execute(
            """
            SELECT * FROM policy_catalog_match_run
            WHERE project_id=? AND status='completed'
            ORDER BY completed_at DESC,started_at DESC,catalog_match_run_id DESC LIMIT 1
            """,
            (project["project_id"],),
        ).fetchone()
        catalog_candidates = []
        if mode == "working" and catalog_run:
            catalog_candidates = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT m.*,e.source_row,e.source_index_no,e.index_occurrence,e.index_conflict,
                           e.title,e.document_no,e.issuer,
                           e.publish_date,e.external_url,e.verification_status,
                           e.category_name,e.catalog_group_name
                    FROM project_policy_catalog_match m
                    JOIN policy_catalog_entry e ON e.catalog_entry_id=m.catalog_entry_id
                    WHERE m.catalog_match_run_id=? AND m.decision_status<>'user_excluded'
                    ORDER BY CASE m.basis_group WHEN 'policy' THEN 0 ELSE 1 END,
                             m.score DESC,e.source_index_no,e.source_row
                    """,
                    (catalog_run["catalog_match_run_id"],),
                )
            ]
    eligible_rows = [
        row
        for row in rows
        if row["validity_status"] == "current"
        and row["verification_status"] == "verified"
        and row["clause_verification_status"] == "verified"
    ]
    unconfirmed = [row["match_id"] for row in eligible_rows if row["decision_status"] != "user_confirmed"]
    if mode == "delivery" and unconfirmed:
        raise ValueError(f"delivery policy material contains unconfirmed matches: {unconfirmed[:10]}")
    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for row in eligible_rows:
        grouped.setdefault(row["policy_id"], []).append(row)
    basis_entries = []
    paragraphs = []
    for policy_id, policy_rows in grouped.items():
        first = policy_rows[0]
        policy = {
            "policy_id": policy_id,
            "title": first["title"],
            "document_no": first["document_no"],
            "issuer": first["issuer"],
            "publish_date": first["publish_date"],
            "official_url": first["official_url"],
            "policy_type": first["policy_type"],
        }
        materials = []
        topics: set[str] = set()
        for row in policy_rows:
            clause = {
                "clause_id": row["clause_id"],
                "article_path": row["article_path"],
                "normalized_summary": row["normalized_summary"],
                "requirement_type": row["requirement_type"],
                "applicability_notes": row["applicability_notes"],
                "permitted_sections_json": row["permitted_sections_json"],
                "forbidden_claims_json": row["forbidden_claims_json"],
            }
            materials.append(policy_source_material(policy, clause, row))
            topics.update(json_list(row["topic_tags_json"]))
        basis_rows = [row for row in policy_rows if row["basis_use"]]
        if basis_rows:
            basis_entries.append(
                {
                    **materials[0]["basis_entry"],
                    "policy_id": policy_id,
                    "basis_order": min(row["basis_order"] or 999999 for row in basis_rows),
                    "decision_status": first["decision_status"],
                    "policy_type": first["policy_type"],
                    "clause_ids": [row["clause_id"] for row in basis_rows],
                }
            )
        background_rows = [row for row in policy_rows if row["background_use"]]
        if background_rows:
            paragraph = policy_paragraph(policy, materials, topics)
            paragraphs.append(
                {
                    "policy_id": policy_id,
                    "title": policy["title"],
                    "text": paragraph,
                    "text_hash": sha256_text(paragraph),
                    "clause_ids": [row["clause_id"] for row in background_rows],
                    "topic_tags": sorted(topics),
                    "decision_status": first["decision_status"],
                    "delivery_eligible": first["decision_status"] == "user_confirmed",
                    "policy_type": first["policy_type"],
                    "materials": materials,
                }
            )
    basis_entries.sort(key=lambda item: (item["basis_order"], item["title"]))
    return {
        "schema_version": "1.0",
        "project_code": project_code,
        "project_name": project["official_name"],
        "match_run_id": run["match_run_id"],
        "mode": mode,
        "basis_entries": basis_entries,
        "background_paragraphs": paragraphs,
        "catalog_match_run_id": catalog_run["catalog_match_run_id"] if catalog_run else "",
        "catalog_candidates": catalog_candidates,
        "unconfirmed_match_ids": unconfirmed,
        "delivery_eligible": not unconfirmed,
    }


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['project_name']}政策章节组装材料",
        "",
        f"> 匹配运行：`{payload['match_run_id']}`；模式：`{payload['mode']}`。正文只能按条款边界审慎改写。",
        "",
        "## 编制依据建议清单",
        "",
    ]
    for index, item in enumerate(payload["basis_entries"], 1):
        document_no = f"（{item['document_no']}）" if item["document_no"] else ""
        lines.append(
            f"{index}. 《{item['title']}》{document_no}，{item['issuer']}，{item['publish_date']}。"
        )
    lines.extend(["", "## 政策背景段落材料", ""])
    for item in payload["background_paragraphs"]:
        lines.extend(
            [
                f"### {item['title']}",
                "",
                item["text"],
                "",
                f"- 条款ID：{', '.join(item['clause_ids'])}",
                f"- 状态：{item['decision_status']}；正式交付可用：{item['delivery_eligible']}",
                "",
            ]
        )
    if payload["unconfirmed_match_ids"]:
        lines.extend(
            [
                "## 待确认项",
                "",
                *[f"- `{match_id}`" for match_id in payload["unconfirmed_match_ids"]],
                "",
            ]
        )
    if payload.get("catalog_candidates"):
        lines.extend(
            [
                "## 部门目录待核验候选",
                "",
                "> 以下候选不得直接生成政策要求；需完成官方原文、有效性和条款核验后转入正式政策证据表。",
                "",
            ]
        )
        for basis_group, heading in (("policy", "政策法规"), ("standard", "标准规范与评价")):
            lines.extend([f"### {heading}", ""])
            for item in [candidate for candidate in payload["catalog_candidates"] if candidate["basis_group"] == basis_group]:
                document_no = f"（{item['document_no']}）" if item["document_no"] else ""
                lines.append(
                    f"- {item['title']}{document_no}；{item['issuer'] or '发布单位待核实'}；"
                    f"{item['publish_date'] or '日期待核实'}；状态：待核验。"
                )
            lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--mode", choices=("working", "delivery"), default="working")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    args = parser.parse_args()
    result = build_material(args.database, args.project_code, mode=args.mode)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(to_markdown(result), encoding="utf-8")
    print(
        json.dumps(
            {
                "basis_count": len(result["basis_entries"]),
                "background_paragraph_count": len(result["background_paragraphs"]),
                "catalog_candidate_count": len(result.get("catalog_candidates", [])),
                "delivery_eligible": result["delivery_eligible"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
