#!/usr/bin/env python3
"""Seed section blueprints and build project-specific composition plans."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from build_dynamic_construction_outline import build_outline_nodes
from chapter_rules import resolve_chapter_rule
from knowledge_db import apply_migrations, connect, dump_json, load_json, now_iso, stable_id


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BLUEPRINTS = (
    SKILL_ROOT / "assets" / "knowledge-base" / "seeds" / "section_blueprints_v1.json"
)


def seed_blueprints(conn, payload: dict[str, Any]) -> dict[str, str]:
    document_type = payload.get("document_type", "feasibility_study")
    result: dict[str, str] = {}
    for blueprint in payload.get("blueprints", []):
        role = blueprint["section_role"]
        blueprint_id = stable_id("BLUEPRINT", document_type, role)
        conn.execute(
            """
            INSERT INTO section_blueprint (
              blueprint_id,document_type,section_role,purpose,
              required_questions_json,required_fact_categories_json,
              required_scope_types_json,required_policy_topics_json,
              required_tables_json,length_min,length_max,forbidden_content_json,
              completion_rules_json,review_status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(blueprint_id) DO UPDATE SET
              purpose=excluded.purpose,
              required_questions_json=excluded.required_questions_json,
              required_fact_categories_json=excluded.required_fact_categories_json,
              required_scope_types_json=excluded.required_scope_types_json,
              required_policy_topics_json=excluded.required_policy_topics_json,
              required_tables_json=excluded.required_tables_json,
              length_min=excluded.length_min,
              length_max=excluded.length_max,
              forbidden_content_json=excluded.forbidden_content_json,
              completion_rules_json=excluded.completion_rules_json,
              review_status=CASE WHEN section_blueprint.review_status='retired'
                THEN section_blueprint.review_status ELSE excluded.review_status END
            """,
            (
                blueprint_id,
                document_type,
                role,
                blueprint["purpose"],
                dump_json(blueprint.get("required_questions", [])),
                dump_json(blueprint.get("required_fact_categories", [])),
                dump_json(blueprint.get("required_scope_types", [])),
                dump_json(blueprint.get("required_policy_topics", [])),
                dump_json(blueprint.get("required_tables", [])),
                blueprint.get("length_min"),
                blueprint.get("length_max"),
                dump_json(blueprint.get("forbidden_content", [])),
                dump_json(blueprint.get("completion_rules", [])),
                blueprint.get("review_status", "approved"),
            ),
        )
        result[role] = blueprint_id
    return result


def fact_matches(fact_key: str, categories: list[str]) -> bool:
    return any(
        fact_key == category or fact_key.startswith(f"{category}.")
        for category in categories
    )


def scope_matches(item_type: str, investment_category: str, scope_types: list[str]) -> bool:
    return "all" in scope_types or item_type in scope_types or investment_category in scope_types


def construction_chapter(scope: Any) -> str:
    text = " ".join(
        str(scope[key] or "")
        for key in ("standard_name", "domain", "item_type", "investment_category")
    )
    if any(term in text for term in ("安全", "等保", "密码", "灾备", "审计", "防火墙")):
        return "5.2.2"
    if any(term in text for term in ("硬件", "服务器", "存储", "机房", "网络", "终端", "基础设施")):
        return "5.2.1"
    if any(term in text for term in ("接口", "集成", "数据", "主索引", "数据治理", "中台")):
        return "5.1.2"
    return "5.1.1"


def text_grams(value: str) -> set[str]:
    normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value).casefold()
    return {
        normalized[index : index + 2]
        for index in range(max(0, len(normalized) - 1))
    }


def rank_corpus_blocks(blocks: list[Any], query: str, limit: int = 10) -> list[Any]:
    del query
    return sorted(
        blocks,
        key=lambda block: (
            int(block["source_order"] or 0),
            str(block["content_slot"] or ""),
            str(block["block_id"]),
        ),
    )[:limit]


HOSPITAL_PROJECT_TYPES = {"smart_hospital", "hospital_informationization"}


def project_type_compatible(candidate_types: set[str], project_type: str) -> bool:
    if project_type in candidate_types:
        return True
    return project_type in HOSPITAL_PROJECT_TYPES and bool(
        candidate_types.intersection(HOSPITAL_PROJECT_TYPES)
    )


def narrative_block_eligible(block: Any, project_type: str) -> bool:
    source_type = block["source_corpus_type"] or "legacy_unspecified"
    content_type = block["content_type"] or "legacy_unspecified"
    applicable_project_types = set(
        json.loads(block["applicable_project_types_json"] or "[]")
    )
    project_type_matches = (
        project_type_compatible(applicable_project_types, project_type)
        if applicable_project_types
        else project_type_compatible(
            {str(block["corpus_project_type"] or "")}, project_type
        )
    )
    return source_type in {
        "reference_feasibility",
        "generic_reference",
    } and content_type in {
        "feasibility_narrative",
        "common_narrative",
        "structure_only",
    } and project_type_matches


def normalize_assessment_target(framework: str, value: str) -> str:
    compact = re.sub(r"\s+", "", str(value or "")).casefold()
    digit_map = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6"}
    for chinese, digit in digit_map.items():
        compact = compact.replace(chinese, digit)
    if framework == "interoperability":
        if re.search(r"4(?:级)?(?:甲等|甲|a)", compact):
            return "4a"
    match = re.search(r"([1-9])(?:级|level)?", compact)
    return match.group(1) if match else compact


def project_assessment_targets(project: Any, facts: list[Any]) -> dict[str, set[str]]:
    values: list[str] = []
    try:
        values.extend(json.loads(project["acceptance_targets_json"] or "[]"))
    except (KeyError, TypeError, json.JSONDecodeError):
        pass
    result: dict[str, set[str]] = {}
    for fact in facts:
        if fact["fact_status"] in {"conflict", "not_applicable", "reference_only"}:
            continue
        key = str(fact["fact_key"] or "")
        content = str(fact["fact_content"] or "")
        if key.startswith("acceptance."):
            values.append(content)
    for value in values:
        text = str(value)
        if "电子病历" in text or re.search(r"\bemr\b", text, re.IGNORECASE):
            normalized = normalize_assessment_target("emr", text)
            if normalized:
                result.setdefault("emr", set()).add(normalized)
        if "互联互通" in text or "interop" in text.casefold():
            normalized = normalize_assessment_target("interoperability", text)
            if normalized:
                result.setdefault("interoperability", set()).add(normalized)
    return result


def assessment_targets_compatible(
    block: Any, project_targets: dict[str, set[str]]
) -> bool:
    targets = json.loads(block["assessment_targets_json"] or "[]")
    for target in targets:
        framework = str(target.get("framework") or "")
        expected = normalize_assessment_target(framework, target.get("target", ""))
        if not expected or expected not in project_targets.get(framework, set()):
            return False
    return True


POLICY_TOPIC_GROUPS = {
    "technical": {
        "standardization", "hospital_platform", "hospital_informationization",
        "interoperability", "infrastructure", "smart_hospital",
        "electronic_medical_record", "health_informationization",
    },
    "security": {"data_security", "cybersecurity", "cryptography", "classified_protection"},
    "investment": {"government_investment", "investment", "budget", "funding"},
    "performance": {"evaluation", "performance", "benefit"},
}


def policy_matches_requirement(policy: Any, requirement: str) -> bool:
    if requirement == "basis":
        return bool(policy["basis_use"])
    if requirement == "background":
        return bool(policy["background_use"])
    clause_topics = set(json.loads(policy["topic_tags_json"] or "[]"))
    return bool(clause_topics & POLICY_TOPIC_GROUPS.get(requirement, {requirement}))


def build_composition_plan(
    database: Path,
    project_code: str,
    *,
    blueprint_payload: dict[str, Any] | None = None,
    version_no: int = 1,
    chapter_codes: list[str] | None = None,
) -> dict[str, Any]:
    blueprint_payload = blueprint_payload or load_json(DEFAULT_BLUEPRINTS)
    requested = list(dict.fromkeys(chapter_codes or []))
    outline_items = list(blueprint_payload.get("outline", []))
    if requested:
        requested_set = set(requested)
        available = {str(item["chapter_code"]) for item in outline_items}
        missing = [code for code in requested if code not in available]
        if missing:
            raise ValueError(
                "requested chapters are not present in the blueprint: " + ", ".join(missing)
            )
        outline_items = [
            item for item in outline_items if str(item["chapter_code"]) in requested_set
        ]
    timestamp = now_iso()
    with connect(database.resolve()) as conn:
        applied_migrations = apply_migrations(conn)
        project = conn.execute(
            "SELECT * FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        blueprint_ids = seed_blueprints(conn, blueprint_payload)
        blueprints = {
            row["section_role"]: row
            for row in conn.execute(
                "SELECT * FROM section_blueprint WHERE document_type=? AND review_status='approved'",
                (project["document_type"],),
            )
        }
        facts = conn.execute(
            "SELECT fact_id,fact_key,fact_content,normalized_value,fact_status FROM project_fact WHERE project_id=?",
            (project["project_id"],),
        ).fetchall()
        assessment_target_map = project_assessment_targets(project, facts)
        scopes = conn.execute(
            """
            SELECT scope_id,standard_name,domain,item_type,investment_category,status,
                   construction_mode,acceptance_target
            FROM project_scope_item WHERE project_id=? AND customer_scope=1
            """,
            (project["project_id"],),
        ).fetchall()
        latest_policy_run = conn.execute(
            """
            SELECT match_run_id FROM policy_match_run WHERE project_id=? AND status='completed'
            ORDER BY completed_at DESC,match_run_id DESC LIMIT 1
            """,
            (project["project_id"],),
        ).fetchone()
        policy_matches = []
        if latest_policy_run:
            policy_matches = conn.execute(
                """
                SELECT m.match_id,m.policy_id,m.basis_use,m.background_use,
                       m.decision_status,c.topic_tags_json,
                       d.validity_status,d.verification_status,
                       c.verification_status AS clause_verification_status
                FROM project_policy_match m
                JOIN policy_clause c ON c.clause_id=m.clause_id
                JOIN policy_document d ON d.policy_id=m.policy_id
                WHERE m.match_run_id=?
                """,
                (latest_policy_run["match_run_id"],),
            ).fetchall()
        latest_catalog_run = conn.execute(
            """
            SELECT catalog_match_run_id FROM policy_catalog_match_run
            WHERE project_id=? AND status='completed'
            ORDER BY completed_at DESC,catalog_match_run_id DESC LIMIT 1
            """,
            (project["project_id"],),
        ).fetchone()
        catalog_candidates = []
        if latest_catalog_run:
            catalog_candidates = conn.execute(
                """
                SELECT candidate_match_id,basis_group,suggested_use,relevance_level,
                       score,decision_status,catalog_entry_id
                FROM project_policy_catalog_match
                WHERE catalog_match_run_id=? AND decision_status<>'user_excluded'
                """,
                (latest_catalog_run["catalog_match_run_id"],),
            ).fetchall()
        corpus_blocks = conn.execute(
            """
            SELECT b.block_id,b.section_role,b.module_code,b.clean_text,b.source_location,
                   b.heading_path_json,
                   b.reuse_class,b.review_status,s.source_scope,
                   d.source_corpus_type,b.content_type,b.semantic_section,b.content_slot,
                   b.source_order,b.adaptation_mode,b.assessment_targets_json,
                   b.construction_scope_tags_json,b.applicable_project_types_json,
                   d.project_type AS corpus_project_type
            FROM corpus_block b JOIN corpus_document d ON d.corpus_document_id=b.corpus_document_id
            JOIN source_document s ON s.source_id=d.source_id
            WHERE (s.project_id=? OR s.source_scope='shared')
              AND b.review_status IN ('approved','pending')
              AND d.document_type=?
            """,
            (project["project_id"], project["document_type"]),
        ).fetchall()
        capability_maps = conn.execute(
            """
            SELECT m.map_id,m.scope_id,m.capability_id,m.status,m.confidence,
                    c.product_name,c.capability_name,c.module_name,c.selection_rules_json,
                    c.standard_block_ids_json,c.block_match_scope
            FROM scope_product_map m
            JOIN product_capability c ON c.capability_id=m.capability_id
            WHERE m.project_id=?
            """,
            (project["project_id"],),
        ).fetchall()

        plans = []
        for outline_item in outline_items:
            role = outline_item["section_role"]
            generation_contract = resolve_chapter_rule(chapter_code := str(outline_item["chapter_code"]), role)
            blueprint = blueprints.get(role)
            if blueprint is None:
                raise RuntimeError(f"approved blueprint not found for role: {role}")
            plan_id = stable_id(
                "SECTIONPLAN", project["project_id"], chapter_code, version_no
            )
            required_questions = json.loads(blueprint["required_questions_json"])
            required_fact_categories = json.loads(
                blueprint["required_fact_categories_json"]
            )
            required_scope_types = json.loads(blueprint["required_scope_types_json"])
            required_policy_topics = json.loads(
                blueprint["required_policy_topics_json"]
            )
            matched_facts = [
                fact for fact in facts if fact_matches(fact["fact_key"], required_fact_categories)
            ]
            matched_scopes = [
                scope
                for scope in scopes
                if scope_matches(
                    scope["item_type"], scope["investment_category"], required_scope_types
                )
            ]
            if role == "construction_content":
                matched_scopes = [
                    scope
                    for scope in matched_scopes
                    if construction_chapter(scope) == chapter_code
                ]
            applicability_status = "applicable"
            applicability_reason = ""
            if role == "construction_content" and scopes and not matched_scopes:
                applicability_status = "not_applicable"
                applicability_reason = "已登记客户范围没有归入本建设类别；保留计划记录但不进入正文。"
            elif role == "construction_content" and not scopes:
                applicability_status = "pending_confirmation"
                applicability_reason = "尚未登记客户建设范围，无法判断本建设类别是否适用。"
            matched_policies = [
                policy
                for policy in policy_matches
                if any(
                    policy_matches_requirement(policy, requirement)
                    for requirement in required_policy_topics
                )
            ]
            usable_facts = [
                fact
                for fact in matched_facts
                if fact["fact_status"] in {"confirmed", "material_explicit"}
            ]
            usable_scopes = [
                scope for scope in matched_scopes if scope["status"] == "confirmed"
            ]
            usable_policies = [
                policy
                for policy in matched_policies
                if policy["decision_status"] == "user_confirmed"
            ]
            missing = [
                f"fact:{category}"
                for category in required_fact_categories
                if not any(fact_matches(fact["fact_key"], [category]) for fact in usable_facts)
            ]
            if required_scope_types and not usable_scopes and applicability_status != "not_applicable":
                missing.append("scope")
            for requirement in required_policy_topics:
                if (
                    applicability_status != "not_applicable"
                    and not any(
                        policy["decision_status"] == "user_confirmed"
                        and policy_matches_requirement(policy, requirement)
                        for policy in matched_policies
                    )
                ):
                    missing.append(f"policy:{requirement}")
            chapter_scope_ids = {
                scope["scope_id"]
                for scope in matched_scopes
                if scope["status"] not in {"rejected", "not_applicable"}
            }
            chapter_maps = [
                mapping
                for mapping in capability_maps
                if mapping["scope_id"] in chapter_scope_ids
            ]
            draftable_scopes = [
                scope for scope in matched_scopes
                if scope["status"] not in {"rejected", "not_applicable"}
            ]
            draftable_maps = [
                mapping for mapping in chapter_maps
                if mapping["status"] in {"confirmed", "candidate"}
            ]
            if role == "construction_content" and any(
                mapping["status"] == "candidate" for mapping in chapter_maps
            ):
                missing.append("capability_mapping_review")
            if role == "construction_content" and any(
                mapping["status"] == "confirmed"
                and mapping["block_match_scope"] == "product_heading_fallback"
                for mapping in chapter_maps
            ):
                missing.append("capability_block_scope_review")
            generated_status = "blocked" if missing else "ready"
            conclusion_boundary = (
                "Only confirmed/material-explicit facts and confirmed scope may be stated as certain; "
                "all other items must remain explicit placeholders or analysis recommendations."
            )
            existing = conn.execute(
                "SELECT status FROM section_composition_plan WHERE plan_id=?", (plan_id,)
            ).fetchone()
            conn.execute(
                """
                INSERT INTO section_composition_plan (
                  plan_id,project_id,chapter_code,section_title,blueprint_id,purpose,
                  conclusion_boundary,required_questions_json,length_min,length_max,
                  required_tables_json,forbidden_content_json,completion_rules_json,
                  status,version_no,created_at,updated_at,applicability_status,applicability_reason
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(plan_id) DO UPDATE SET
                  section_title=excluded.section_title,
                  blueprint_id=excluded.blueprint_id,
                  purpose=excluded.purpose,
                  conclusion_boundary=excluded.conclusion_boundary,
                  required_questions_json=excluded.required_questions_json,
                  length_min=excluded.length_min,
                  length_max=excluded.length_max,
                  required_tables_json=excluded.required_tables_json,
                  forbidden_content_json=excluded.forbidden_content_json,
                  completion_rules_json=excluded.completion_rules_json,
                  status=CASE WHEN section_composition_plan.status IN ('completed','draft')
                    THEN section_composition_plan.status ELSE excluded.status END,
                  applicability_status=excluded.applicability_status,
                  applicability_reason=excluded.applicability_reason,
                  updated_at=excluded.updated_at
                """,
                (
                    plan_id,
                    project["project_id"],
                    chapter_code,
                    outline_item["section_title"],
                    blueprint_ids[role],
                    blueprint["purpose"],
                    conclusion_boundary,
                    dump_json(required_questions),
                    max(
                        outline_item.get("length_min", blueprint["length_min"]) or 0,
                        1200 + 700 * len(draftable_scopes) + 320 * len(draftable_maps),
                    ) if role == "construction_content" else outline_item.get("length_min", blueprint["length_min"]),
                    max(
                        outline_item.get("length_max", blueprint["length_max"]) or 0,
                        int((1200 + 700 * len(draftable_scopes) + 320 * len(draftable_maps)) * 1.8),
                    ) if role == "construction_content" else outline_item.get("length_max", blueprint["length_max"]),
                    blueprint["required_tables_json"],
                    blueprint["forbidden_content_json"],
                    blueprint["completion_rules_json"],
                    generated_status,
                    version_no,
                    timestamp,
                    timestamp,
                    applicability_status,
                    applicability_reason,
                ),
            )

            sources = []
            conn.execute("DELETE FROM section_plan_source WHERE plan_id=?", (plan_id,))
            for fact in matched_facts:
                sources.append(
                    (
                        "fact",
                        fact["fact_id"],
                        "direct"
                        if fact["fact_status"] in {"confirmed", "material_explicit"}
                        else "prohibited",
                        fact["fact_status"],
                    )
                )
            for scope in matched_scopes:
                sources.append(
                    (
                        "scope",
                        scope["scope_id"],
                        "direct" if scope["status"] == "confirmed" else "parameterized",
                        scope["status"],
                    )
                )
            for policy in matched_policies:
                verified_working_evidence = (
                    policy["validity_status"] == "current"
                    and policy["verification_status"] == "verified"
                    and policy["clause_verification_status"] == "verified"
                )
                sources.append(
                    (
                        "policy",
                        policy["match_id"],
                        "evidence"
                        if policy["decision_status"] == "user_confirmed" or verified_working_evidence
                        else "prohibited",
                        (
                            f"decision_status={policy['decision_status']}; delivery_eligible=true"
                            if policy["decision_status"] == "user_confirmed"
                            else f"decision_status={policy['decision_status']}; working_draft_only=true"
                        ),
                    )
                )
            if chapter_code in {"1.2.1", "1.2.2"}:
                expected_group = "policy" if chapter_code == "1.2.1" else "standard"
                for candidate in catalog_candidates:
                    if candidate["basis_group"] != expected_group:
                        continue
                    sources.append(
                        (
                            "reference",
                            candidate["candidate_match_id"],
                            "structure_only",
                            (
                                "department_policy_catalog_candidate; unverified_title_only; "
                                f"suggested_use={candidate['suggested_use']}; "
                                f"decision_status={candidate['decision_status']}"
                            ),
                        )
                    )
            if role != "construction_content":
                semantic_sections = set(
                    generation_contract.get("canonical_semantic_sections")
                    or generation_contract.get("semantic_sections")
                    or []
                )
                content_slots = set(generation_contract.get("content_slots") or [])
                content_slot_prefixes = tuple(
                    generation_contract.get("content_slot_prefixes") or []
                )
                role_blocks = [
                    block
                    for block in corpus_blocks
                    if semantic_sections
                    and block["semantic_section"] in semantic_sections
                    and (
                        not content_slots
                        or block["content_slot"] in content_slots
                    )
                    and (
                        not content_slot_prefixes
                        or str(block["content_slot"] or "").startswith(
                            content_slot_prefixes
                        )
                    )
                    and block["review_status"] == "approved"
                    and narrative_block_eligible(block, project["project_type"])
                    and assessment_targets_compatible(block, assessment_target_map)
                ]
                for block in rank_corpus_blocks(
                    role_blocks,
                    f"{outline_item['section_title']} {blueprint['purpose']}",
                    limit=generation_contract.get("max_corpus_blocks", 16),
                ):
                    usage_mode = (
                        "direct" if block["review_status"] == "approved" and block["reuse_class"] == "A"
                        else "parameterized" if block["review_status"] == "approved" and block["reuse_class"] == "B"
                        else "structure_only" if block["review_status"] == "approved" and block["reuse_class"] == "C"
                        else "prohibited"
                    )
                    sources.append((
                        "corpus", block["block_id"], usage_mode,
                        (
                            f"role={block['section_role']}; semantic_section={block['semantic_section']}; "
                            f"content_slot={block['content_slot']}; content_type={block['content_type']}; "
                            f"source_corpus_type={block['source_corpus_type']}; "
                            f"assembly_mode={generation_contract['assembly_mode']}; "
                            f"canonical_chapter={generation_contract.get('canonical_chapter_code', '')}; "
                            f"location={block['source_location']}"
                        ),
                    ))
            if role == "construction_content":
                for mapping in chapter_maps:
                    sources.append(
                        (
                            "capability",
                            mapping["capability_id"],
                            "parameterized" if mapping["status"] == "confirmed" else "structure_only",
                            f"scope_id={mapping['scope_id']}; map_status={mapping['status']}",
                        )
                    )
            outline_nodes = []
            if role == "construction_content" and applicability_status != "not_applicable":
                outline_nodes = build_outline_nodes(
                    conn,
                    plan_id=plan_id,
                    chapter_code=chapter_code,
                    scopes=draftable_scopes,
                    capability_maps=draftable_maps,
                    corpus_blocks=corpus_blocks,
                    timestamp=timestamp,
                )
                blocks_by_id = {block["block_id"]: block for block in corpus_blocks}
                selected_block_modes: dict[str, str] = {}
                for node in outline_nodes:
                    if node["source_type"] != "corpus":
                        continue
                    block_ids = node.get("metadata", {}).get("block_ids") or [
                        node["source_object_id"]
                    ]
                    for block_id in block_ids:
                        current_mode = selected_block_modes.get(block_id, "")
                        if node["usage_mode"] == "parameterized" or not current_mode:
                            selected_block_modes[block_id] = node["usage_mode"]
                for block_id in sorted(selected_block_modes):
                    block = blocks_by_id.get(block_id)
                    if block is None:
                        continue
                    sources.append(
                        (
                            "corpus",
                            block_id,
                            selected_block_modes[block_id],
                            (
                                "construction block selected by the confirmed-capability outline; "
                                f"location={block['source_location']}"
                            ),
                        )
                    )
            for source_type, object_id, usage_mode, notes in sources:
                plan_source_id = stable_id("PLANSOURCE", plan_id, source_type, object_id)
                conn.execute(
                    """
                    INSERT INTO section_plan_source (
                      plan_source_id,plan_id,source_type,source_object_id,usage_mode,notes
                    ) VALUES (?,?,?,?,?,?)
                    ON CONFLICT(plan_source_id) DO UPDATE SET
                      usage_mode=excluded.usage_mode,notes=excluded.notes
                    """,
                    (plan_source_id, plan_id, source_type, object_id, usage_mode, notes),
                )
            plans.append(
                {
                    "plan_id": plan_id,
                    "chapter_code": chapter_code,
                    "section_title": outline_item["section_title"],
                    "section_role": role,
                    "status": existing["status"] if existing and existing["status"] in {"completed", "draft"} else generated_status,
                    "applicability_status": applicability_status,
                    "applicability_reason": applicability_reason,
                    "missing_source_types": missing,
                    "source_count": len(sources),
                    "outline_node_count": len(outline_nodes),
                    "outline_heading_levels": {
                        str(level): sum(node["heading_level"] == level for node in outline_nodes)
                        for level in range(4, 8)
                    },
                    "full_standard_block_count": len(
                        {
                            block_id
                            for node in outline_nodes
                            if node["source_type"] == "corpus"
                            and node["usage_mode"] == "parameterized"
                            for block_id in (
                                node.get("metadata", {}).get("block_ids")
                                or [node["source_object_id"]]
                            )
                        }
                    ),
                }
            )
        conn.commit()
    return {
        "database": str(database.resolve()),
        "project_code": project_code,
        "version_no": version_no,
        "selection_mode": "chapter" if requested else "all_blueprint_chapters",
        "requested_chapters": requested,
        "plan_count": len(plans),
        "ready_count": sum(plan["status"] == "ready" for plan in plans),
        "blocked_count": sum(
            plan["status"] == "blocked" and plan["applicability_status"] != "not_applicable"
            for plan in plans
        ),
        "not_applicable_count": sum(
            plan["applicability_status"] == "not_applicable" for plan in plans
        ),
        "plans": plans,
        "applied_migrations": applied_migrations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--blueprints", type=Path, default=DEFAULT_BLUEPRINTS)
    parser.add_argument("--version-no", type=int, default=1)
    parser.add_argument(
        "--chapter",
        action="append",
        dest="chapters",
        help="仅重建指定章节计划；可重复使用，例如 --chapter 5.1.1",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = build_composition_plan(
        args.database,
        args.project_code,
        blueprint_payload=load_json(args.blueprints),
        version_no=args.version_no,
        chapter_codes=args.chapters,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
