#!/usr/bin/env python3
"""Select a bounded, review-only policy/standard candidate list for one project."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect, dump_json, now_iso, stable_id


MATCHER_VERSION = "policy-catalog-candidate-v6"
PROFILE_PATH = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "knowledge-base"
    / "seeds"
    / "smart_hospital_basis_profile_v1.json"
)
SECTION_ORDER = {
    "policy_basis": 0,
    "industry_standard": 1,
    "security_standard": 2,
    "investment_basis": 3,
}
USABLE_FACT_STATUSES = {"confirmed", "material_explicit"}
POLICY_CONTEXT_PREFIXES = (
    "project.jurisdiction.",
    "jurisdiction.",
    "organization.institution_type",
    "organization.hospital_grade",
    "organization.ownership",
    "project.report_type",
    "investment.funding_source",
    "investment.regime",
    "scope.project_type",
)


def load_basis_profile(project_type: str = "hospital_informationization") -> dict[str, Any]:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    applicable = set(profile.get("applicable_project_types", []))
    if project_type not in applicable:
        raise ValueError(f"no basis profile for project_type: {project_type}")
    return profile


def load_project_policy_context(
    connection: Any,
    project: Any,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = profile or load_basis_profile(str(project["project_type"]))
    facts = [
        dict(row)
        for row in connection.execute(
            """
            SELECT fact_id,fact_key,fact_content,normalized_value,fact_status
            FROM project_fact
            WHERE project_id=? AND fact_status IN ('confirmed','material_explicit')
            ORDER BY fact_key,fact_id
            """,
            (project["project_id"],),
        )
        if str(row["fact_key"]).startswith(POLICY_CONTEXT_PREFIXES)
    ]
    values = {
        str(row["fact_key"]): str(row["normalized_value"] or row["fact_content"] or "").strip()
        for row in facts
    }
    scope_bindings = [
        dict(row)
        for row in connection.execute(
            """
            SELECT scope_id,original_name,standard_name,domain,item_type,status
            FROM project_scope_item
            WHERE project_id=? AND customer_scope=1
              AND status='confirmed'
            ORDER BY scope_id
            """,
            (project["project_id"],),
        )
    ]
    scope_topics: set[str] = set()
    for scope in scope_bindings:
        searchable = " ".join(
            str(scope.get(key) or "")
            for key in ("original_name", "standard_name", "domain", "item_type")
        )
        for rule in profile.get("scope_topic_rules", []):
            if re.search(str(rule["pattern"]), searchable, flags=re.I):
                scope_topics.update(str(topic) for topic in rule.get("topics", []))

    def first_value(*keys: str) -> str:
        return next((values[key] for key in keys if values.get(key)), "")

    return {
        "project_type": str(project["project_type"]),
        "jurisdiction": {
            "code": str(project["jurisdiction_code"] or "")
            or first_value("project.jurisdiction.code", "jurisdiction.code"),
            "name": str(project["jurisdiction_name"] or "")
            or first_value("project.jurisdiction.name", "jurisdiction.name"),
            "province": first_value(
                "project.jurisdiction.province", "jurisdiction.province"
            ),
            "province_code": first_value(
                "project.jurisdiction.province_code", "jurisdiction.province_code"
            ),
            "prefecture": first_value(
                "project.jurisdiction.prefecture", "jurisdiction.prefecture"
            ),
            "prefecture_code": first_value(
                "project.jurisdiction.prefecture_code", "jurisdiction.prefecture_code"
            ),
        },
        "institution_type": first_value("organization.institution_type"),
        "hospital_grade": first_value("organization.hospital_grade"),
        "ownership": first_value("organization.ownership"),
        "report_type": first_value("project.report_type"),
        "funding_source": first_value("investment.funding_source"),
        "investment_regime": first_value("investment.regime"),
        "fact_bindings": facts,
        "scope_topics": sorted(scope_topics),
        "scope_bindings": scope_bindings,
        "missing_required_context": [],
    }


def section_storage_fields(basis_section: str, suggested_use: str) -> tuple[str, str]:
    if suggested_use == "historical_background":
        suggested_use = "background"
    if basis_section in {"industry_standard", "security_standard"}:
        return "standard", "security" if basis_section == "security_standard" else suggested_use
    if basis_section == "investment_basis":
        return "policy", "investment"
    return "policy", suggested_use


def section_from_storage(basis_group: str, suggested_use: str) -> str:
    if suggested_use == "security":
        return "security_standard"
    if suggested_use == "investment":
        return "investment_basis"
    if basis_group == "standard":
        return "industry_standard"
    return "policy_basis"


def classify_fallback(title: str, category: str) -> tuple[str, str, str]:
    searchable = title + category
    if any(term in searchable for term in ("网络安全", "数据安全", "个人信息", "密码", "等级保护")):
        return "standard", "security", "security_standard"
    if any(term in searchable for term in ("投资", "概算", "估算", "预算", "建设成本", "成本度量")):
        return "policy", "investment", "investment_basis"
    if any(term in searchable for term in ("标准", "规范", "测评", "评价", "数据集", "功能指引")):
        suggested_use = "performance" if any(term in title for term in ("测评", "评价")) else "technical"
        return "standard", suggested_use, "industry_standard"
    return "policy", "background", "policy_basis"


def score_entry(
    entry: dict[str, Any],
    topics: set[str],
    profile: dict[str, Any] | None = None,
    project_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    profile = profile or load_basis_profile()
    project_context = project_context or {}
    title = str(entry["title"])
    full_searchable_title = re.sub(r"\s+", "", title)
    searchable_title = re.sub(r"\s+", "", title.splitlines()[0])
    if any(
        re.search(pattern, full_searchable_title)
        for pattern in profile.get("exclusion_patterns", [])
    ):
        return None
    historical_patterns = profile.get("historical_period_patterns", [])
    for rule in profile.get("catalog_rules", []):
        pattern = str(rule["pattern"])
        if re.search(pattern, searchable_title):
            required_topics = set(rule.get("requires_topic_any", []))
            missing_topics = sorted(required_topics - topics) if required_topics and not required_topics.intersection(topics) else []
            if missing_topics:
                return None
            context_text = " ".join(
                str(project_context.get(key) or "")
                for key in ("institution_type", "hospital_grade", "ownership", "report_type", "investment_regime")
            )
            required_context = rule.get("requires_context_any", [])
            missing_context = (
                list(required_context)
                if required_context and not any(value in context_text for value in required_context)
                else []
            )
            basis_section = str(rule["basis_section"])
            profile_suggested_use = str(rule["suggested_use"])
            basis_group, suggested_use = section_storage_fields(basis_section, profile_suggested_use)
            historical = profile_suggested_use == "historical_background" or any(
                re.search(value, searchable_title) for value in historical_patterns
            )
            score = int(rule["importance"])
            limitations = (
                (["historical_period_reference_only"] if historical else [])
                + (["missing_required_topic:" + ",".join(missing_topics)] if missing_topics else [])
                + (["missing_required_context:" + ",".join(missing_context)] if missing_context else [])
            )
            return {
                "basis_group": basis_group,
                "suggested_use": suggested_use,
                "basis_section": basis_section,
                "score": score - (20 if historical else 0) - (15 if missing_topics else 0) - (15 if missing_context else 0),
                "relevance_level": "supplementary" if limitations else "core" if score >= 94 else "important",
                "counts_toward_minimum": not limitations,
                "match_reasons": [f"profile_rule:{pattern}"] + limitations,
            }
    if not profile.get("allow_topic_fallback", False):
        return None
    searchable = " ".join(
        str(entry.get(key, ""))
        for key in ("title", "category_name", "catalog_group_name", "keyword_text")
    ).casefold()
    topic_terms = {
        "hospital_informationization": ("信息化", "智慧医院"),
        "electronic_medical_record": ("电子病历",),
        "interoperability": ("互联互通", "信息平台"),
        "internet_health": ("互联网+医疗健康", "互联网诊疗"),
        "high_quality_hospital": ("高质量发展",),
        "cybersecurity": ("网络安全", "数据安全"),
        "standardization": ("标准化", "数据元", "共享文档"),
    }
    hits = [
        topic
        for topic in sorted(topics)
        if any(term.casefold() in searchable for term in topic_terms.get(topic, (topic,)))
    ]
    historical = any(re.search(value, searchable_title) for value in historical_patterns)
    if not hits or historical:
        return None
    basis_group, suggested_use, basis_section = classify_fallback(
        title, str(entry.get("category_name", ""))
    )
    return {
        "basis_group": basis_group,
        "suggested_use": suggested_use,
        "basis_section": basis_section,
        "score": 60 + min(15, 3 * len(hits)),
        "relevance_level": "supplementary",
        "counts_toward_minimum": True,
        "match_reasons": [f"topic:{topic}" for topic in hits],
    }


def normalize_fixed_groups(
    profile: dict[str, Any], topics: set[str] | None = None
) -> dict[str, list[dict[str, Any]]]:
    defaults = dict(profile.get("fixed_entry_defaults", {}))
    topic_requirements = profile.get("fixed_entry_topic_requirements", {})
    result: dict[str, list[dict[str, Any]]] = {}
    for section, raw_items in profile.get("fixed_basis_groups", {}).items():
        items = []
        for basis_id, title, document_no in raw_items:
            required_topics = set(topic_requirements.get(basis_id, []))
            if topics is not None and required_topics and not required_topics.intersection(topics):
                continue
            items.append(
                {
                    **defaults,
                    "basis_id": basis_id,
                    "basis_section": section,
                    "title": title,
                    "document_no": document_no,
                    "requires_topic_any": sorted(required_topics),
                    "counts_toward_minimum": True,
                }
            )
        result[section] = items
    return result


def candidate_identity(item: dict[str, Any]) -> str:
    title = re.sub(r"[《》〈〉\s]", "", str(item.get("title") or ""))
    document_no = re.sub(r"\s", "", str(item.get("document_no") or ""))
    return f"{title}|{document_no}"


def candidate_jurisdiction_applies(
    entry: dict[str, Any], project_context: dict[str, Any]
) -> bool:
    level = str(entry.get("jurisdiction_level") or "unclassified")
    if level == "national":
        return True
    if level == "unclassified":
        return False
    jurisdiction = project_context.get("jurisdiction", {})
    entry_code = str(entry.get("jurisdiction_code") or "")
    entry_name = str(entry.get("jurisdiction_name") or "")
    project_code = str(jurisdiction.get("code") or "")
    province_code = str(jurisdiction.get("province_code") or "")
    prefecture_code = str(jurisdiction.get("prefecture_code") or "")
    if level == "province":
        code_matches = bool(entry_code) and bool(province_code or project_code) and (
            (province_code and entry_code[:2] == province_code[:2])
            or (project_code and entry_code[:2] == project_code[:2])
        )
        name_matches = bool(entry_name) and entry_name == str(jurisdiction.get("province") or "")
        return code_matches or name_matches
    if level == "prefecture":
        code_matches = bool(entry_code) and bool(prefecture_code or project_code) and (
            (prefecture_code and entry_code[:4] == prefecture_code[:4])
            or (project_code and entry_code[:4] == project_code[:4])
        )
        name_matches = bool(entry_name) and entry_name == str(
            jurisdiction.get("prefecture") or jurisdiction.get("name") or ""
        )
        return code_matches or name_matches
    if level == "county":
        return bool(entry_code and project_code and entry_code[:6] == project_code[:6])
    return False


def select_project_catalog(connection: Any, project: Any) -> tuple[Any, str]:
    snapshots = connection.execute(
        """
        SELECT source_id FROM shared_knowledge_snapshot
        WHERE project_id=? AND source_type='policy_catalog' AND snapshot_status='current'
        ORDER BY fetched_at DESC,snapshot_id DESC
        """,
        (project["project_id"],),
    ).fetchall()
    if len(snapshots) > 1:
        raise ValueError("multiple current project policy catalog snapshots found")
    if snapshots:
        catalog = connection.execute(
            "SELECT * FROM policy_catalog WHERE catalog_id=? AND catalog_status='active'",
            (snapshots[0]["source_id"],),
        ).fetchone()
        if not catalog:
            raise ValueError("current project policy catalog snapshot is unavailable locally")
        return catalog, "project_snapshot"
    catalogs = connection.execute(
        """
        SELECT * FROM policy_catalog WHERE catalog_status='active'
        ORDER BY imported_at DESC,catalog_id DESC
        """
    ).fetchall()
    if not catalogs:
        raise ValueError("active policy catalog not found")
    if len(catalogs) > 1:
        raise ValueError(
            "multiple active policy catalogs found; select one through the project knowledge snapshot"
        )
    return catalogs[0], "single_active_fallback"


def select_bounded_group_candidates(
    entries: list[dict[str, Any]],
    basis_section: str,
    group_limit: int,
    background_rules: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    if basis_section != "policy_basis":
        return entries[:group_limit]
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for level in ("national", "province", "prefecture"):
        level_maximum = int(background_rules[level]["maximum"])
        level_rows = [
            item
            for item in entries
            if item.get("suggested_use") == "background"
            and item.get("jurisdiction_level") == level
        ]
        for item in level_rows[:level_maximum]:
            identity = candidate_identity(item)
            if identity in selected_ids or len(selected) >= group_limit:
                continue
            selected_ids.add(identity)
            selected.append(item)
    for item in entries:
        identity = candidate_identity(item)
        if identity in selected_ids:
            continue
        if len(selected) >= group_limit:
            break
        selected_ids.add(identity)
        selected.append(item)
    return selected


def basis_quality(
    selected: list[dict[str, Any]],
    fixed_groups: dict[str, list[dict[str, Any]]],
    quantity_rules: dict[str, dict[str, int]],
) -> dict[str, Any]:
    groups = {}
    for section in SECTION_ORDER:
        catalog_items = [
            item
            for item in selected
            if item["basis_section"] == section and item.get("counts_toward_minimum", True)
        ]
        fixed_items = fixed_groups.get(section, [])
        identities = {candidate_identity(item) for item in catalog_items + fixed_items}
        rule = quantity_rules[section]
        count = len(identities)
        groups[section] = {
            "candidate_count": count,
            "selection_capacity": min(count, int(rule["maximum"])),
            "catalog_candidate_count": len(catalog_items),
            "fixed_candidate_count": len(fixed_items),
            "minimum": int(rule["minimum"]),
            "target": int(rule["target"]),
            "maximum": int(rule["maximum"]),
            "gap": max(0, int(rule["minimum"]) - count),
            "overage": max(0, count - int(rule["maximum"])),
            "status": "candidate_ready" if count >= int(rule["minimum"]) else "insufficient_candidates",
        }
    blockers = [
        f"{section}:candidate_gap={item['gap']}"
        for section, item in groups.items()
        if item["gap"]
    ]
    blockers.append("catalog_candidates_are_unverified_and_cannot_support_delivery")
    if any(
        not item.get("delivery_eligible", False)
        for rows in fixed_groups.values()
        for item in rows
    ):
        blockers.append("fixed_basis_candidates_require_official_current_status_verification")
    return {
        "candidate_gate": "passed" if not any(item["gap"] for item in groups.values()) else "failed",
        "delivery_gate": "blocked",
        "groups": groups,
        "delivery_blockers": blockers,
    }


def match_candidates(
    database: Path,
    project_code: str,
    *,
    topics: set[str] | None = None,
    limit_per_group: int | None = None,
) -> dict[str, Any]:
    topics = set(topics or ())
    with connect(database.resolve()) as connection:
        apply_migrations(connection)
        project = connection.execute(
            "SELECT * FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if not project:
            raise ValueError(f"project not found: {project_code}")
        profile = load_basis_profile(str(project["project_type"]))
        project_context = load_project_policy_context(connection, project, profile)
        required_context_values = {
            "jurisdiction.province": project_context["jurisdiction"]["province"],
            "jurisdiction.prefecture": project_context["jurisdiction"]["prefecture"]
            or str(project["jurisdiction_name"] or ""),
            "organization.institution_type": project_context["institution_type"],
            "organization.ownership": project_context["ownership"],
            "project.report_type": project_context["report_type"],
            "investment.regime": project_context["investment_regime"],
        }
        project_context["missing_required_context"] = [
            key
            for key in profile.get("project_fact_contract", {}).get("required_for_delivery", [])
            if not required_context_values.get(key)
        ]
        topics.update(profile.get("default_topics", []))
        topics.update(project_context.get("scope_topics", []))
        catalog, catalog_selection_source = select_project_catalog(connection, project)
        entries = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM policy_catalog_entry
                WHERE catalog_id=? AND entry_status='active'
                ORDER BY source_row,catalog_entry_id
                """,
                (catalog["catalog_id"],),
            )
        ]
        ranked = []
        jurisdiction_excluded_count = 0
        for entry in entries:
            if not candidate_jurisdiction_applies(entry, project_context):
                jurisdiction_excluded_count += 1
                continue
            scored = score_entry(entry, topics, profile, project_context)
            if scored:
                ranked.append({**entry, **scored})
        ranked.sort(
            key=lambda item: (
                SECTION_ORDER[item["basis_section"]],
                -float(item["score"]),
                str(item.get("publish_date", "")),
                item["source_index_no"],
                item["source_row"],
            )
        )
        selected = []
        quantity_rules = profile["quantity_rules"]
        for basis_section in SECTION_ORDER:
            group_entries = [item for item in ranked if item["basis_section"] == basis_section]
            unique_entries = []
            seen = set()
            for item in group_entries:
                identity = candidate_identity(item)
                if identity in seen:
                    continue
                seen.add(identity)
                unique_entries.append(item)
            group_limit = limit_per_group or int(quantity_rules[basis_section]["maximum"])
            selected.extend(
                select_bounded_group_candidates(
                    unique_entries,
                    basis_section,
                    group_limit,
                    profile["policy_background_quantity_rules"],
                )
            )
        selected.sort(
            key=lambda item: (
                SECTION_ORDER[item["basis_section"]],
                -float(item["score"]),
                item["source_index_no"],
                item["source_row"],
            )
        )
        fixed_groups = normalize_fixed_groups(profile, topics)
        quality = basis_quality(selected, fixed_groups, quantity_rules)
        topic_json = dump_json(sorted(topics))
        match_input_signature = stable_id(
            "POLICYCATINPUT",
            dump_json(
                {
                    "project_type": project_context["project_type"],
                    "jurisdiction": project_context["jurisdiction"],
                    "fact_bindings": project_context["fact_bindings"],
                    "scope_topics": project_context["scope_topics"],
                    "scope_bindings": project_context["scope_bindings"],
                    "topics": sorted(topics),
                    "profile_version": profile["profile_version"],
                    "catalog_id": catalog["catalog_id"],
                    "catalog_content_hash": catalog["content_hash"],
                    "matcher_version": MATCHER_VERSION,
                    "limit_per_group": limit_per_group or "profile_limits",
                }
            ),
        )
        run_id = stable_id(
            "POLICYCATMATCHRUN",
            project["project_id"],
            match_input_signature,
        )
        timestamp = now_iso()
        connection.execute(
            """
            INSERT INTO policy_catalog_match_run (
              catalog_match_run_id,project_id,catalog_id,matcher_version,
              topic_tags_json,status,started_at,completed_at,summary_json
            ) VALUES (?,?,?,?,?,'completed',?,?,?)
            ON CONFLICT(catalog_match_run_id) DO UPDATE SET
              status='completed',completed_at=excluded.completed_at,
              summary_json=excluded.summary_json
            """,
            (
                run_id,
                project["project_id"],
                catalog["catalog_id"],
                MATCHER_VERSION,
                topic_json,
                timestamp,
                timestamp,
                dump_json(
                    {
                        "candidate_count": len(selected),
                        "match_input_signature": match_input_signature,
                        "policy_count": sum(item["basis_group"] == "policy" for item in selected),
                        "standard_count": sum(item["basis_group"] == "standard" for item in selected),
                        "basis_section_counts": {
                            section: sum(item["basis_section"] == section for item in selected)
                            for section in SECTION_ORDER
                        },
                        "quality": quality,
                    }
                ),
            ),
        )
        connection.execute(
            """
            DELETE FROM project_policy_catalog_match
            WHERE catalog_match_run_id=?
              AND decision_status NOT IN ('user_confirmed','user_excluded')
            """,
            (run_id,),
        )
        for item in selected:
            candidate_match_id = stable_id("POLICYCATMATCH", run_id, item["catalog_entry_id"])
            connection.execute(
                """
                INSERT INTO project_policy_catalog_match (
                  candidate_match_id,catalog_match_run_id,project_id,catalog_entry_id,
                  basis_group,suggested_use,relevance_level,score,match_reasons_json,
                  decision_status,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,'ai_recommended',?,?)
                ON CONFLICT(catalog_match_run_id,catalog_entry_id) DO UPDATE SET
                  basis_group=excluded.basis_group,
                  suggested_use=excluded.suggested_use,
                  relevance_level=excluded.relevance_level,
                  score=excluded.score,
                  match_reasons_json=excluded.match_reasons_json,
                  decision_status=CASE
                    WHEN project_policy_catalog_match.decision_status IN ('user_confirmed','user_excluded')
                    THEN project_policy_catalog_match.decision_status
                    ELSE excluded.decision_status
                  END,
                  updated_at=excluded.updated_at
                """,
                (
                    candidate_match_id,
                    run_id,
                    project["project_id"],
                    item["catalog_entry_id"],
                    item["basis_group"],
                    item["suggested_use"],
                    item["relevance_level"],
                    item["score"],
                    dump_json(item["match_reasons"]),
                    timestamp,
                    timestamp,
                ),
            )
        connection.commit()
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT m.*,e.source_row,e.source_index_no,e.index_occurrence,e.index_conflict,
                       e.title,e.document_no,e.issuer,
                       e.publish_date,e.external_url,e.verification_status,
                       e.category_name,e.catalog_group_name,
                       e.jurisdiction_level,e.jurisdiction_code,e.jurisdiction_name
                FROM project_policy_catalog_match m
                JOIN policy_catalog_entry e ON e.catalog_entry_id=m.catalog_entry_id
                WHERE m.catalog_match_run_id=? AND m.decision_status<>'user_excluded'
                ORDER BY CASE m.basis_group WHEN 'policy' THEN 0 ELSE 1 END,
                         m.score DESC,e.source_index_no,e.source_row
                """,
                (run_id,),
            )
        ]
        for row in rows:
            row["basis_section"] = section_from_storage(
                str(row["basis_group"]), str(row["suggested_use"])
            )
            row["candidate_only"] = True
            reasons = json.loads(row.get("match_reasons_json") or "[]")
            row["counts_toward_minimum"] = not any(
                reason == "historical_period_reference_only"
                or reason.startswith("missing_required_topic:")
                or reason.startswith("missing_required_context:")
                for reason in reasons
            )
        quality = basis_quality(rows, fixed_groups, quantity_rules)
    return {
        "schema_version": "1.0",
        "project_code": project_code,
        "project_type": project["project_type"],
        "project_context": project_context,
        "jurisdiction": project_context["jurisdiction"],
        "catalog_id": catalog["catalog_id"],
        "catalog_selection_source": catalog_selection_source,
        "jurisdiction_excluded_count": jurisdiction_excluded_count,
        "catalog_match_run_id": run_id,
        "match_input_signature": match_input_signature,
        "matcher_version": MATCHER_VERSION,
        "basis_profile_version": profile["profile_version"],
        "topics": sorted(topics),
        "candidate_count": len(rows),
        "candidates": rows,
        "fixed_basis_groups": fixed_groups,
        "quality": quality,
        "source_boundary": profile["source_boundary"],
    }


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 部门政策目录项目候选",
        "",
        "> 以下内容仅用于官方原文核验任务；未形成已核验政策文件和条款前，不得作为正式编制依据或扩写政策要求。",
        "",
    ]
    headings = (
        ("policy_basis", "政策类依据候选（来自政策目录）"),
        ("industry_standard", "行业标准依据候选（来自政策目录）"),
        ("security_standard", "安全类依据候选（来自政策目录）"),
        ("investment_basis", "投资估算依据候选（来自政策目录）"),
    )
    for basis_section, heading in headings:
        lines.extend([f"## {heading}", "", "| 序号 | 文件 | 文号 | 发布单位 | 日期 | 状态 |", "| --- | --- | --- | --- | --- | --- |"]) 
        rows = [item for item in payload["candidates"] if item["basis_section"] == basis_section]
        for index, item in enumerate(rows, 1):
            lines.append(
                "| " + " | ".join(
                    [
                        str(index),
                        item["title"].replace("|", "／"),
                        (item["document_no"] or "待核实").replace("|", "／"),
                        (item["issuer"] or "待核实").replace("|", "／"),
                        item["publish_date"] or "待核实",
                        "待核验",
                    ]
                ) + " |"
            )
        lines.append("")
    lines.extend(["## 固定智慧医院标准组", "", "> 固定组是受控召回基线；标记为候选的条目须在定稿前核验现行状态和官方来源。", ""])
    for basis_section, heading in headings[1:]:
        rows = payload.get("fixed_basis_groups", {}).get(basis_section, [])
        lines.extend([f"### {heading}", ""])
        for index, item in enumerate(rows, 1):
            suffix = f"（{item['document_no']}）" if item.get("document_no") else ""
            lines.append(f"{index}. 《{item['title']}》{suffix}；")
        lines.append("")
    quality = payload.get("quality", {})
    lines.extend(["## 数量与交付门禁", ""])
    for section, item in quality.get("groups", {}).items():
        lines.append(
            f"- {section}: 候选 {item['candidate_count']}，硬下限 {item['minimum']}，"
            f"状态 {item['status']}。"
        )
    lines.extend(["", "> 目录候选和未核验固定项均不得直接扩写为政策要求；交付门禁保持阻断，直至正式证据链完成。", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--topic", action="append", default=[])
    parser.add_argument("--limit-per-group", type=int)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    args = parser.parse_args()
    result = match_candidates(
        args.database,
        args.project_code,
        topics=set(args.topic),
        limit_per_group=args.limit_per_group,
    )
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(to_markdown(result), encoding="utf-8")
    print(json.dumps({"candidate_count": result["candidate_count"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
