#!/usr/bin/env python3
"""Seed section blueprints and build project-specific composition plans."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

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


def build_composition_plan(
    database: Path,
    project_code: str,
    *,
    blueprint_payload: dict[str, Any] | None = None,
    version_no: int = 1,
) -> dict[str, Any]:
    blueprint_payload = blueprint_payload or load_json(DEFAULT_BLUEPRINTS)
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
            "SELECT fact_id,fact_key,fact_status FROM project_fact WHERE project_id=?",
            (project["project_id"],),
        ).fetchall()
        scopes = conn.execute(
            """
            SELECT scope_id,item_type,investment_category,status
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
                SELECT match_id,policy_id,basis_use,background_use,decision_status
                FROM project_policy_match WHERE match_run_id=?
                """,
                (latest_policy_run["match_run_id"],),
            ).fetchall()
        corpus_blocks = conn.execute(
            """
            SELECT b.block_id,b.section_role,b.reuse_class,b.review_status
            FROM corpus_block b JOIN corpus_document d ON d.corpus_document_id=b.corpus_document_id
            JOIN source_document s ON s.source_id=d.source_id
            WHERE s.project_id=? AND b.review_status IN ('approved','pending')
            """,
            (project["project_id"],),
        ).fetchall()
        capability_maps = conn.execute(
            """
            SELECT m.map_id,m.scope_id,m.capability_id,m.status
            FROM scope_product_map m WHERE m.project_id=?
            """,
            (project["project_id"],),
        ).fetchall()

        plans = []
        for outline_item in blueprint_payload.get("outline", []):
            role = outline_item["section_role"]
            blueprint = blueprints.get(role)
            if blueprint is None:
                raise RuntimeError(f"approved blueprint not found for role: {role}")
            chapter_code = str(outline_item["chapter_code"])
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
            matched_policies = [
                policy
                for policy in policy_matches
                if ("basis" in required_policy_topics and policy["basis_use"])
                or ("background" in required_policy_topics and policy["background_use"])
                or any(
                    topic not in {"basis", "background"}
                    for topic in required_policy_topics
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
            if required_scope_types and not usable_scopes:
                missing.append("scope")
            if required_policy_topics and not usable_policies:
                missing.append("policy")
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
                  status,version_no,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    blueprint["length_min"],
                    blueprint["length_max"],
                    blueprint["required_tables_json"],
                    blueprint["forbidden_content_json"],
                    blueprint["completion_rules_json"],
                    generated_status,
                    version_no,
                    timestamp,
                    timestamp,
                ),
            )

            sources = []
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
                        "direct" if scope["status"] == "confirmed" else "prohibited",
                        scope["status"],
                    )
                )
            for policy in matched_policies:
                sources.append(
                    (
                        "policy",
                        policy["match_id"],
                        "evidence"
                        if policy["decision_status"] == "user_confirmed"
                        else "prohibited",
                        policy["decision_status"],
                    )
                )
            if role in {"current_state", "problem_need", "necessity_feasibility", "construction_content"}:
                for block in corpus_blocks:
                    usage_mode = (
                        "direct" if block["review_status"] == "approved" and block["reuse_class"] == "A"
                        else "parameterized" if block["review_status"] == "approved" and block["reuse_class"] == "B"
                        else "structure_only" if block["review_status"] == "approved" and block["reuse_class"] == "C"
                        else "prohibited"
                    )
                    sources.append(("corpus", block["block_id"], usage_mode, block["section_role"]))
            if role == "construction_content":
                for mapping in capability_maps:
                    sources.append(
                        (
                            "capability",
                            mapping["capability_id"],
                            "parameterized" if mapping["status"] == "confirmed" else "prohibited",
                            f"scope_id={mapping['scope_id']}; map_status={mapping['status']}",
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
                    "missing_source_types": missing,
                    "source_count": len(sources),
                }
            )
        conn.commit()
    return {
        "database": str(database.resolve()),
        "project_code": project_code,
        "version_no": version_no,
        "plan_count": len(plans),
        "ready_count": sum(plan["status"] == "ready" for plan in plans),
        "blocked_count": sum(plan["status"] == "blocked" for plan in plans),
        "plans": plans,
        "applied_migrations": applied_migrations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--blueprints", type=Path, default=DEFAULT_BLUEPRINTS)
    parser.add_argument("--version-no", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = build_composition_plan(
        args.database,
        args.project_code,
        blueprint_payload=load_json(args.blueprints),
        version_no=args.version_no,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
