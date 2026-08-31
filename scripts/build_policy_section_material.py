#!/usr/bin/env python3
"""Build traceable policy basis and paragraph materials from one project match run."""

from __future__ import annotations

import argparse
import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect, sha256_text
from match_policy_catalog_candidates import (
    SECTION_ORDER,
    basis_quality,
    candidate_identity,
    candidate_jurisdiction_applies,
    load_basis_profile,
    load_project_policy_context,
    normalize_fixed_groups,
    section_from_storage,
)
from match_project_policies import jurisdiction_applies, permits_section


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

SECURITY_TOPICS = {
    "cybersecurity",
    "data_security",
    "cryptography",
    "classified_protection",
    "personal_information",
}
INVESTMENT_TOPICS = {
    "investment",
    "cost_estimation",
    "government_investment",
    "budget",
    "procurement",
}
INDUSTRY_POLICY_TYPES = {
    "evaluation_rule",
    "standard",
    "technical_standard",
    "specification",
    "standard_guidance",
}
JURISDICTION_ORDER = {"national": 0, "province": 1, "prefecture": 2, "county": 3, "other": 4}


def classify_formal_basis(policy_type: str, title: str, topic_tags: set[str]) -> str:
    if topic_tags.intersection(SECURITY_TOPICS) and re.search(
        r"网络安全|数据安全|个人信息|密码|等级保护|安全管理", title
    ):
        return "security_standard"
    if topic_tags.intersection(INVESTMENT_TOPICS) and re.search(
        r"投资|概算|估算|预算|建设成本|成本度量|采购", title
    ):
        return "investment_basis"
    if policy_type.casefold() in INDUSTRY_POLICY_TYPES or re.search(
        r"标准|规范|评价|测评|指南", title
    ):
        return "industry_standard"
    return "policy_basis"


def exact_ordered_subsequence(parent_ids: list[str], child_ids: list[str]) -> bool:
    if len(child_ids) != len(set(child_ids)):
        return False
    selected = set(child_ids)
    return child_ids == [policy_id for policy_id in parent_ids if policy_id in selected]


def background_level(item: dict[str, Any], project_context: dict[str, Any]) -> str:
    level = str(item.get("jurisdiction_level") or "")
    if level in {"national", "province", "prefecture"}:
        return level
    searchable = " ".join(
        str(item.get(key) or "") for key in ("title", "issuer", "authority_level_label")
    )
    jurisdiction = project_context.get("jurisdiction", {})
    if jurisdiction.get("prefecture") and jurisdiction["prefecture"] in searchable:
        return "prefecture"
    if jurisdiction.get("province") and jurisdiction["province"] in searchable:
        return "province"
    if any(value in searchable for value in ("国家", "国务院", "部", "委", "局")):
        return "national"
    return "other"


def group_quality(count: int, rule: dict[str, Any]) -> dict[str, Any]:
    minimum = int(rule["minimum"])
    maximum = int(rule["maximum"])
    return {
        "count": count,
        "minimum": minimum,
        "target": int(rule["target"]),
        "maximum": maximum,
        "gap": max(0, minimum - count),
        "overage": max(0, count - maximum),
        "status": "passed" if count >= minimum else "failed",
    }


def formal_document_identity(item: dict[str, Any]) -> str:
    document_no = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", str(item.get("document_no") or "").casefold())
    official_url = re.sub(
        r"[?#].*$|/+$", "", str(item.get("official_url") or "").strip().casefold()
    )
    title = re.sub(r"[《》〈〉\s]", "", str(item.get("title") or "").casefold())
    if document_no:
        return f"document_no:{document_no}"
    if official_url:
        return f"official_url:{official_url}"
    return f"title:{title}"


def deduplicate_formal_basis(
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    unique: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    first_by_identity: dict[str, dict[str, Any]] = {}
    for item in entries:
        identity = formal_document_identity(item)
        first = first_by_identity.get(identity)
        if first is None:
            first_by_identity[identity] = item
            unique.append(item)
            continue
        duplicates.append(
            {
                "identity": identity,
                "kept_policy_id": first.get("policy_id", ""),
                "duplicate_policy_id": item.get("policy_id", ""),
                "kept_title": first.get("title", ""),
                "duplicate_title": item.get("title", ""),
            }
        )
    return unique, duplicates


def policy_material_signature(payload: dict[str, Any]) -> str:
    context = payload.get("project_context", {})
    signature_payload = {
        "schema_version": "2.0",
        "project_code": payload.get("project_code", ""),
        "basis_profile_version": payload.get("basis_profile_version", ""),
        "match_run_id": payload.get("match_run_id", ""),
        "jurisdiction": context.get("jurisdiction", {}),
        "institution_type": context.get("institution_type", ""),
        "hospital_grade": context.get("hospital_grade", ""),
        "ownership": context.get("ownership", ""),
        "report_type": context.get("report_type", ""),
        "funding_source": context.get("funding_source", ""),
        "investment_regime": context.get("investment_regime", ""),
        "fact_bindings": context.get("fact_bindings", []),
        "scope_topics": context.get("scope_topics", []),
        "scope_bindings": context.get("scope_bindings", []),
        "basis_groups": {
            section: [
                {
                    "policy_id": item.get("policy_id", ""),
                    "title": item.get("title", ""),
                    "document_no": item.get("document_no", ""),
                    "official_url": item.get("official_url", ""),
                    "clause_ids": item.get("clause_ids", []),
                }
                for item in payload.get("basis_groups", {}).get(section, [])
            ]
            for section in SECTION_ORDER
        },
        "policy_background": [
            {
                "policy_id": item.get("policy_id", ""),
                "clause_ids": item.get("clause_ids", []),
                "background_level": item.get("background_level", ""),
                "text_hash": item.get("text_hash", ""),
            }
            for item in payload.get("background_paragraphs", [])
        ],
        "delivery_eligible": payload.get("delivery_eligible") is True,
        "delivery_blockers": payload.get("quality", {}).get("delivery_blockers", []),
        "duplicate_formal_basis": payload.get("duplicate_formal_basis", []),
        "section_permission_violations": payload.get(
            "section_permission_violations", []
        ),
        "missing_background_summaries": payload.get(
            "missing_background_summaries", []
        ),
        "forbidden_claim_violations": payload.get("forbidden_claim_violations", []),
        "stale_policy_match_violations": payload.get(
            "stale_policy_match_violations", []
        ),
    }
    canonical = json.dumps(
        signature_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256_text(canonical)


def bounded_background_candidate_groups(
    items: list[dict[str, Any]],
    project_context: dict[str, Any],
    background_rules: dict[str, dict[str, int]],
) -> dict[str, list[dict[str, Any]]]:
    result = {level: [] for level in ("national", "province", "prefecture")}
    for item in items:
        if (
            item.get("basis_section") != "policy_basis"
            or item.get("suggested_use") != "background"
            or not item.get("counts_toward_minimum")
        ):
            continue
        level = background_level(item, project_context)
        if level not in result:
            continue
        if len(result[level]) >= int(background_rules[level]["maximum"]):
            continue
        result[level].append(item)
    return result


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
        return "本项目应在安全体系、数据保护和运维管理设计中落实对应防护要求，并以实际建设边界确定具体措施。"
    if topic_tags.intersection({"evaluation", "electronic_medical_record", "interoperability"}):
        return "本项目应把评价对象、适用范围和测评边界转化为可核验的建设任务与指标，评价目标不代表医院当前已经达标。"
    if topic_tags.intersection({"hospital_platform", "hospital_informationization", "infrastructure", "standardization"}):
        return "本项目应在总体架构、平台整合、标准体系和建设内容之间建立对应关系，并保持与已确认建设范围一致。"
    return "本项目将该政策作为必要性和建设方向依据，并结合项目实际说明其适用边界。"


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
    if not summary_text:
        raise ValueError(f"verified policy background summary is missing: {policy['title']}")
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
        sentences.append(f"文件中与本项目相关的要求包括：{summary_text}。")
    topic_names = [TOPIC_LABELS[tag] for tag in sorted(topic_tags) if tag in TOPIC_LABELS]
    if topic_names:
        sentences.append(f"该文件与本项目的{'、'.join(topic_names[:5])}建设直接相关。")
    if boundary_text:
        sentences.append(f"适用时还应注意：{boundary_text}。")
    return "".join(sentences)


def build_material(database: Path, project_code: str, *, mode: str = "working") -> dict[str, Any]:
    if mode not in {"working", "delivery"}:
        raise ValueError("mode must be working or delivery")
    with connect(database.resolve()) as connection:
        apply_migrations(connection)
        project_row = connection.execute(
            "SELECT * FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if not project_row:
            raise ValueError(f"project not found: {project_code}")
        project = dict(project_row)
        profile = load_basis_profile(str(project["project_type"]))
        project_context = load_project_policy_context(connection, project_row)
        applicable_topics = set(profile.get("default_topics", [])).union(
            project_context.get("scope_topics", [])
        )
        national_project = (
            str(project.get("jurisdiction_code") or "") == "100000"
            or str(project.get("jurisdiction_name") or "") in {"全国", "国家"}
        )
        required_context_values = {
            "jurisdiction.province": project_context["jurisdiction"]["province"],
            "jurisdiction.prefecture": project_context["jurisdiction"]["prefecture"]
            or str(project.get("jurisdiction_name") or ""),
            "organization.institution_type": project_context["institution_type"],
            "organization.ownership": project_context["ownership"],
            "project.report_type": project_context["report_type"],
            "investment.regime": project_context["investment_regime"],
        }
        project_context["missing_required_context"] = [
            key
            for key in profile.get("project_fact_contract", {}).get("required_for_delivery", [])
            if not required_context_values.get(key)
            and not (national_project and key in {"jurisdiction.province", "jurisdiction.prefecture"})
        ]
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
                       p.authority_group,p.authority_rank,p.jurisdiction_level,p.jurisdiction_code,
                       p.jurisdiction_name,p.validity_status,p.verification_status,
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
                           e.title,e.document_no,e.issuer,e.authority_level_label,
                           e.publish_date,e.external_url,e.verification_status,
                           e.category_name,e.catalog_group_name,
                           e.jurisdiction_level,e.jurisdiction_code,e.jurisdiction_name
                    FROM project_policy_catalog_match m
                    JOIN policy_catalog_entry e ON e.catalog_entry_id=m.catalog_entry_id
                    WHERE m.catalog_match_run_id=? AND m.decision_status<>'user_excluded'
                    ORDER BY CASE m.basis_group WHEN 'policy' THEN 0 ELSE 1 END,
                             m.score DESC,e.source_index_no,e.source_row
                    """,
                    (catalog_run["catalog_match_run_id"],),
                )
            ]

    stale_catalog_candidate_ids = []
    current_catalog_candidates = []
    for item in catalog_candidates:
        if not candidate_jurisdiction_applies(item, project_context):
            stale_catalog_candidate_ids.append(item["catalog_entry_id"])
            continue
        item["basis_section"] = section_from_storage(
            str(item["basis_group"]), str(item["suggested_use"])
        )
        reasons = json_list(item.get("match_reasons_json"))
        item["candidate_only"] = True
        item["counts_toward_minimum"] = not any(
            reason == "historical_period_reference_only"
            or str(reason).startswith("missing_required_topic:")
            or str(reason).startswith("missing_required_context:")
            for reason in reasons
        )
        current_catalog_candidates.append(item)
    catalog_candidates = current_catalog_candidates

    eligible_rows = []
    stale_policy_match_violations: list[dict[str, Any]] = []
    for row in rows:
        if not (
            row["validity_status"] == "current"
            and row["verification_status"] == "verified"
            and row["clause_verification_status"] == "verified"
        ):
            continue
        stale_reasons = []
        if not jurisdiction_applies(
            str(project.get("jurisdiction_code") or ""),
            str(row.get("jurisdiction_level") or ""),
            str(row.get("jurisdiction_code") or ""),
        ):
            stale_reasons.append("jurisdiction_no_longer_applies")
        clause_topics = set(json_list(row.get("topic_tags_json")))
        if not applicable_topics.intersection(clause_topics):
            stale_reasons.append("project_topics_no_longer_apply")
        if stale_reasons and (row["basis_use"] or row["background_use"]):
            stale_policy_match_violations.append(
                {
                    "match_id": row["match_id"],
                    "policy_id": row["policy_id"],
                    "clause_id": row["clause_id"],
                    "reasons": stale_reasons,
                }
            )
            continue
        eligible_rows.append(row)
    unconfirmed = [
        row["match_id"]
        for row in eligible_rows
        if row["decision_status"] != "user_confirmed"
    ]
    if mode == "delivery" and unconfirmed:
        raise ValueError(f"delivery policy material contains unconfirmed matches: {unconfirmed[:10]}")

    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for row in eligible_rows:
        grouped.setdefault(row["policy_id"], []).append(row)

    basis_entries = []
    section_permission_violations: list[dict[str, str]] = []
    missing_background_summaries: list[dict[str, str]] = []
    forbidden_claim_violations: list[dict[str, str]] = []
    policy_records: dict[str, dict[str, Any]] = {}
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
        materials_by_clause = {}
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
            material = policy_source_material(policy, clause, row)
            materials.append(material)
            materials_by_clause[row["clause_id"]] = material
            topics.update(json_list(row["topic_tags_json"]))
        basis_rows = [
            row
            for row in policy_rows
            if row["basis_use"]
            and permits_section(row["permitted_sections_json"], "basis", "1.2.1")
        ]
        section_permission_violations.extend(
            {
                "policy_id": policy_id,
                "clause_id": row["clause_id"],
                "section": "basis",
            }
            for row in policy_rows
            if row["basis_use"]
            and not permits_section(row["permitted_sections_json"], "basis", "1.2.1")
        )
        basis_section = classify_formal_basis(first["policy_type"], first["title"], topics)
        if basis_rows:
            basis_entries.append(
                {
                    **materials_by_clause[basis_rows[0]["clause_id"]]["basis_entry"],
                    "policy_id": policy_id,
                    "basis_section": basis_section,
                    "basis_order": min(row["basis_order"] or 999999 for row in basis_rows),
                    "authority_group": first["authority_group"],
                    "authority_rank": first["authority_rank"],
                    "jurisdiction_level": first["jurisdiction_level"],
                    "jurisdiction_code": first["jurisdiction_code"],
                    "jurisdiction_name": first["jurisdiction_name"],
                    "decision_status": first["decision_status"],
                    "delivery_eligible": all(
                        row["decision_status"] == "user_confirmed" for row in basis_rows
                    ),
                    "clause_ids": [row["clause_id"] for row in basis_rows],
                }
            )
        policy_records[policy_id] = {
            "policy": policy,
            "rows": policy_rows,
            "materials": materials,
            "materials_by_clause": materials_by_clause,
            "topics": topics,
            "basis_section": basis_section,
        }

    basis_entries.sort(
        key=lambda item: (
            SECTION_ORDER[item["basis_section"]],
            JURISDICTION_ORDER.get(item["jurisdiction_level"], 9),
            item["basis_order"],
            item["authority_group"],
            item["authority_rank"],
            item["title"],
        )
    )
    basis_entries, duplicate_formal_basis = deduplicate_formal_basis(basis_entries)
    basis_groups = {
        section: [item for item in basis_entries if item["basis_section"] == section]
        for section in SECTION_ORDER
    }
    policy_basis_ids = [item["policy_id"] for item in basis_groups["policy_basis"]]
    policy_basis_by_id = {
        item["policy_id"]: item for item in basis_groups["policy_basis"]
    }

    paragraphs = []
    for policy_id in policy_basis_ids:
        record = policy_records[policy_id]
        background_rows = []
        for row in record["rows"]:
            if not row["background_use"]:
                continue
            if not permits_section(
                row["permitted_sections_json"], "policy_background", "2.1.1"
            ):
                section_permission_violations.append(
                    {
                        "policy_id": policy_id,
                        "clause_id": row["clause_id"],
                        "section": "policy_background",
                    }
                )
                continue
            if not str(row.get("normalized_summary") or "").strip():
                missing_background_summaries.append(
                    {"policy_id": policy_id, "clause_id": row["clause_id"]}
                )
                continue
            background_rows.append(row)
        if not background_rows:
            continue
        background_materials = [
            record["materials_by_clause"][row["clause_id"]] for row in background_rows
        ]
        paragraph = policy_paragraph(
            record["policy"], background_materials, record["topics"]
        )
        for material in background_materials:
            for claim in material.get("forbidden_claims", []):
                if claim and claim in paragraph:
                    forbidden_claim_violations.append(
                        {
                            "policy_id": policy_id,
                            "clause_id": material["clause_id"],
                            "claim": claim,
                        }
                    )
        basis_item = policy_basis_by_id[policy_id]
        paragraph_item = {
            "policy_id": policy_id,
            "title": record["policy"]["title"],
            "text": paragraph,
            "text_hash": sha256_text(paragraph),
            "clause_ids": [row["clause_id"] for row in background_rows],
            "topic_tags": sorted(record["topics"]),
            "decision_status": basis_item["decision_status"],
            "delivery_eligible": all(
                row["decision_status"] == "user_confirmed" for row in background_rows
            ),
            "policy_type": basis_item["policy_type"],
            "basis_order": basis_item["basis_order"],
            "jurisdiction_level": basis_item["jurisdiction_level"],
            "jurisdiction_code": basis_item["jurisdiction_code"],
            "jurisdiction_name": basis_item["jurisdiction_name"],
            "materials": background_materials,
        }
        paragraph_item["background_level"] = background_level(
            paragraph_item, project_context
        )
        paragraphs.append(paragraph_item)

    background_orphans = sorted(
        policy_id
        for policy_id, record in policy_records.items()
        if any(row["background_use"] for row in record["rows"])
        and record["basis_section"] == "policy_basis"
        and policy_id not in policy_basis_by_id
    )
    background_ids = [item["policy_id"] for item in paragraphs]
    order_ok = exact_ordered_subsequence(policy_basis_ids, background_ids)
    policy_background_groups = {
        level: [item for item in paragraphs if item["background_level"] == level]
        for level in ("national", "province", "prefecture")
    }

    fixed_groups = normalize_fixed_groups(profile, applicable_topics) if mode == "working" else {
        section: [] for section in SECTION_ORDER
    }
    working_basis_groups = {section: [] for section in SECTION_ORDER}
    if mode == "working":
        for section in SECTION_ORDER:
            maximum = int(profile["quantity_rules"][section]["maximum"])
            seen = {candidate_identity(item) for item in basis_groups[section]}
            for item in [
                *fixed_groups.get(section, []),
                *[candidate for candidate in catalog_candidates if candidate["basis_section"] == section],
            ]:
                identity = candidate_identity(item)
                if identity in seen:
                    continue
                seen.add(identity)
                working_basis_groups[section].append(item)
                if len(basis_groups[section]) + len(working_basis_groups[section]) >= maximum:
                    break
        working_basis_groups["policy_basis"].sort(
            key=lambda item: (
                JURISDICTION_ORDER.get(background_level(item, project_context), 9),
                -float(item.get("score") or 0),
                str(item.get("source_index_no") or item.get("basis_id") or ""),
            )
        )
    candidate_quality = basis_quality(
        catalog_candidates,
        fixed_groups,
        profile["quantity_rules"],
    )
    formal_basis_quality = {
        section: group_quality(len(basis_groups[section]), profile["quantity_rules"][section])
        for section in SECTION_ORDER
    }
    local_project = not national_project
    required_background_levels = ["national"] + (
        ["province", "prefecture"] if local_project else []
    )
    background_rules = profile["policy_background_quantity_rules"]
    formal_background_quality = {
        level: group_quality(len(policy_background_groups[level]), background_rules[level])
        for level in required_background_levels
    }
    background_candidate_groups = bounded_background_candidate_groups(
        working_basis_groups["policy_basis"], project_context, background_rules
    )
    candidate_background_quality = {
        level: group_quality(len(background_candidate_groups[level]), background_rules[level])
        for level in required_background_levels
    }
    working_policy_basis_order = [
        *policy_basis_ids,
        *[
            str(item.get("catalog_entry_id") or item.get("basis_id") or candidate_identity(item))
            for item in working_basis_groups["policy_basis"]
        ],
    ]
    working_policy_background_order = [
        *background_ids,
        *[
            str(item.get("catalog_entry_id") or item.get("basis_id") or candidate_identity(item))
            for level in ("national", "province", "prefecture")
            for item in background_candidate_groups[level]
        ],
    ]
    working_order_ok = exact_ordered_subsequence(
        working_policy_basis_order, working_policy_background_order
    )

    delivery_blockers = []
    if unconfirmed:
        delivery_blockers.append(f"unconfirmed_policy_matches:{len(unconfirmed)}")
    for section, item in formal_basis_quality.items():
        if item["gap"]:
            delivery_blockers.append(f"formal_{section}_gap:{item['gap']}")
    for level, item in formal_background_quality.items():
        if item["gap"]:
            delivery_blockers.append(f"formal_policy_background_{level}_gap:{item['gap']}")
    if not order_ok:
        delivery_blockers.append("policy_background_not_ordered_subsequence")
    if background_orphans:
        delivery_blockers.append(f"policy_background_not_in_policy_basis:{len(background_orphans)}")
    if project_context.get("missing_required_context"):
        delivery_blockers.append(
            "missing_project_policy_context:"
            + ",".join(project_context["missing_required_context"])
        )
    if duplicate_formal_basis:
        delivery_blockers.append(
            f"duplicate_formal_basis_documents:{len(duplicate_formal_basis)}"
        )
    if section_permission_violations:
        delivery_blockers.append(
            f"policy_clause_section_permission_violations:{len(section_permission_violations)}"
        )
    if missing_background_summaries:
        delivery_blockers.append(
            f"policy_background_summary_missing:{len(missing_background_summaries)}"
        )
    if forbidden_claim_violations:
        delivery_blockers.append(
            f"policy_forbidden_claim_violations:{len(forbidden_claim_violations)}"
        )
    if stale_policy_match_violations:
        delivery_blockers.append(
            f"stale_policy_match_violations:{len(stale_policy_match_violations)}"
        )

    payload = {
        "schema_version": "2.0",
        "project_code": project_code,
        "project_name": project["official_name"],
        "project_context": project_context,
        "basis_profile_version": profile["profile_version"],
        "match_run_id": run["match_run_id"],
        "mode": mode,
        "basis_entries": basis_entries,
        "basis_groups": basis_groups,
        "duplicate_formal_basis": duplicate_formal_basis,
        "section_permission_violations": section_permission_violations,
        "missing_background_summaries": missing_background_summaries,
        "forbidden_claim_violations": forbidden_claim_violations,
        "stale_policy_match_violations": stale_policy_match_violations,
        "background_paragraphs": paragraphs,
        "policy_background_groups": policy_background_groups,
        "policy_basis_order": policy_basis_ids,
        "policy_background_order": background_ids,
        "policy_background_is_ordered_subsequence": order_ok,
        "working_policy_basis_order": working_policy_basis_order,
        "working_policy_background_order": working_policy_background_order,
        "working_policy_background_is_ordered_subsequence": working_order_ok,
        "policy_background_orphan_ids": background_orphans,
        "catalog_match_run_id": catalog_run["catalog_match_run_id"] if catalog_run else "",
        "catalog_candidates": catalog_candidates,
        "stale_catalog_candidate_ids": stale_catalog_candidate_ids,
        "fixed_basis_groups": fixed_groups,
        "working_basis_groups": working_basis_groups,
        "policy_background_candidate_groups": background_candidate_groups,
        "quality": {
            "candidate_basis": candidate_quality,
            "candidate_background": candidate_background_quality,
            "formal_basis": formal_basis_quality,
            "formal_background": formal_background_quality,
            "delivery_blockers": delivery_blockers,
        },
        "unconfirmed_match_ids": unconfirmed,
        "delivery_eligible": not delivery_blockers,
    }
    payload["material_signature"] = policy_material_signature(payload)
    return payload


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['project_name']}政策章节组装材料",
        "",
        f"> 匹配运行：`{payload['match_run_id']}`；模式：`{payload['mode']}`。正文只能按条款边界审慎改写。",
        "",
        "## 可行性研究报告编制依据",
        "",
    ]
    section_headings = (
        ("policy_basis", "政策类依据"),
        ("industry_standard", "行业标准依据"),
        ("security_standard", "安全类标准依据"),
        ("investment_basis", "投资估算编制依据"),
    )
    for section, heading in section_headings:
        lines.extend([f"### {heading}", ""])
        formal = payload.get("basis_groups", {}).get(section, [])
        for index, item in enumerate(formal, 1):
            document_no = f"（{item['document_no']}）" if item["document_no"] else ""
            lines.append(
                f"{index}. 《{item['title']}》{document_no}，{item['issuer']}，{item['publish_date']}。"
            )
        if payload["mode"] == "working":
            candidates = [
                item
                for item in payload.get("catalog_candidates", [])
                if item.get("basis_section") == section
            ]
            fixed = payload.get("fixed_basis_groups", {}).get(section, [])
            if candidates or fixed:
                lines.extend(
                    [
                        "",
                        "> 以下为待核验候选或固定召回基线，不得直接生成政策要求，不得据标题扩写政策要求，也不得冒充正式编制依据。",
                        "",
                    ]
                )
            display_index = len(formal)
            for item in payload.get("working_basis_groups", {}).get(section, [*fixed, *candidates]):
                display_index += 1
                document_no = f"（{item.get('document_no')}）" if item.get("document_no") else ""
                title = str(item["title"]).strip().strip("《》〈〉")
                lines.append(f"{display_index}. 《{title}》{document_no}【待核验】；")
        if not formal and payload["mode"] == "delivery":
            lines.append("【阻断：本类尚无可交付的已核验依据】")
        lines.append("")

    lines.extend(["## 项目背景及需求分析", "", "### 政策背景", ""])
    background_headings = (
        ("national", "国家政策背景"),
        ("province", "省/自治区政策背景"),
        ("prefecture", "市/项目建设地区政策背景"),
    )
    for level, heading in background_headings:
        lines.extend([f"#### {heading}", ""])
        paragraphs = payload.get("policy_background_groups", {}).get(level, [])
        for item in paragraphs:
            lines.extend(
                [
                    item["text"],
                    "",
                    f"- 政策ID：`{item['policy_id']}`；条款ID：{', '.join(item['clause_ids'])}",
                    f"- 状态：{item['decision_status']}；正式交付可用：{item['delivery_eligible']}",
                    "",
                ]
            )
        if payload["mode"] == "working":
            candidates = payload.get("policy_background_candidate_groups", {}).get(level, [])
            if candidates:
                lines.append("> 下列目录项未取得核验条款，故不生成对应政策背景正文。待核验文件：")
                lines.append("")
                lines.extend(f"- {item['title']}" for item in candidates)
                lines.append("")
            elif not paragraphs:
                lines.extend(["> 当前无可用的已核验政策条款，保留证据缺口。", ""])
    if payload["unconfirmed_match_ids"]:
        lines.extend(
            [
                "## 待确认项",
                "",
                *[f"- `{match_id}`" for match_id in payload["unconfirmed_match_ids"]],
                "",
            ]
        )
    lines.extend(["## 质量门禁", ""])
    for section, item in payload.get("quality", {}).get("formal_basis", {}).items():
        lines.append(
            f"- 正式{section}：{item['count']}条，硬下限{item['minimum']}条，状态{item['status']}。"
        )
    for level, item in payload.get("quality", {}).get("formal_background", {}).items():
        lines.append(
            f"- 正式政策背景{level}：{item['count']}条，硬下限{item['minimum']}条，状态{item['status']}。"
        )
    lines.extend(
        [
            f"- 政策背景为政策类依据同序子序列：{payload.get('policy_background_is_ordered_subsequence', False)}。",
            f"- 工作态政策背景为工作态政策类依据同序子序列：{payload.get('working_policy_background_is_ordered_subsequence', False)}。",
            f"- 正式交付可用：{payload['delivery_eligible']}。",
            "",
        ]
    )
    for blocker in payload.get("quality", {}).get("delivery_blockers", []):
        lines.append(f"- 阻断：`{blocker}`")
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
