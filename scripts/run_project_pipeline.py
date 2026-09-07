#!/usr/bin/env python3
"""Run the local-first feasibility-report pipeline from one project directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from assemble_report_markdown import CHAPTER_TITLES, GROUP_TITLES, assemble, chapter_key
from audit_delivery_artifact import audit as audit_delivery_artifact
from build_candidate_fact_workpack import build_workpack
from build_evidence_bound_initial_drafts import build_initial_drafts
from build_policy_section_material import build_material as build_policy_material
from build_policy_section_material import to_markdown as policy_material_markdown
from build_reference_reuse_workpack import build_workpack as build_reference_workpack
from build_reference_reuse_workpack import to_markdown as reference_workpack_markdown
from build_scope_baseline import build_scope_baseline
from build_section_composition_plan import build_composition_plan
from build_traceability_matrix import build_traceability_matrix, write_csv
from check_dependencies import check_dependencies
from classify_source_roles import classify_inventory
from export_section_task_packages import export_packages
from extract_clean_document_blocks import build_payload as build_clean_payload
from extract_xlsx_scope import build_payload as build_scope_payload
from export_policy_selection_data import export_selection
from ingest_clean_documents_sqlite import ingest_payload as ingest_clean_payload
from ingest_document_standards import ingest as ingest_document_standards
from ingest_policies import ingest as ingest_policies
from ingest_scope_items_sqlite import ingest_scope_payload
from import_policy_catalog_sqlite import import_catalog as import_policy_catalog
from import_standard_knowledge_pack import import_pack as import_standard_pack
from init_project_workbench import initialize_project, load_json, write_json
from inventory_sources import build_inventory
from knowledge_doctor import diagnose_settings
from knowledge_db import connect, now_iso, sha256_file, sha256_text
from knowledge_profile import (
    KnowledgeConfigurationError,
    public_settings,
    redact_text,
    resolve_knowledge_settings,
)
from knowledge_snapshot import KnowledgeSnapshotError, validate_snapshots
from local_knowledge_bootstrap import discover_local_config, prepare_project_knowledge
from map_scope_capabilities import map_capabilities
from match_document_standards import (
    MATCHER_VERSION as DOCUMENT_STANDARD_MATCHER_VERSION,
    export_latest_match,
    match as match_document_standards,
    to_markdown as document_standard_markdown,
)
from match_project_policies import (
    MATCHER_VERSION as POLICY_MATCHER_VERSION,
    match as match_project_policies,
    policy_match_input_signature,
    to_markdown as policy_markdown,
)
from match_policy_catalog_candidates import (
    load_basis_profile,
    load_project_policy_context,
    match_candidates as match_policy_catalog_candidates,
    to_markdown as policy_catalog_markdown,
)
from postgres_knowledge_db import canonical_json
from postgres_knowledge_db import connect as connect_postgres
from report_outline import build_outline_candidate, write_outline_outputs
from sync_postgres_knowledge_snapshot import KnowledgeSelectionError
from sync_postgres_knowledge_snapshot import sync as sync_postgres_knowledge
from validate_full_report import validate_report
from validate_project_gates import validate as validate_gates


SUPPORTED_TEXT = {".docx", ".md", ".txt"}
SUPPORTED_SCOPE = {".xlsx"}
EXPLICITLY_BLOCKED = {".pdf", ".doc", ".xls", ".xlsm", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
SKILL_ROOT = Path(__file__).resolve().parent.parent
POLICY_SEED = SKILL_ROOT / "assets" / "knowledge-base" / "seeds" / "core_policy_seed_20260804.json"


def project_policy_topics(
    database: Path,
    project_code: str,
    configured_topics: set[str],
) -> set[str]:
    with connect(database) as connection:
        project = connection.execute(
            "SELECT * FROM project WHERE project_code=?",
            (project_code,),
        ).fetchone()
        if project is None:
            raise ValueError(f"project not found: {project_code}")
        profile = load_basis_profile(str(project["project_type"]))
        context = load_project_policy_context(connection, project, profile)
    return (
        set(configured_topics)
        .union(profile.get("default_topics", []))
        .union(context.get("scope_topics", []))
    )


def reusable_policy_run(
    connection: Any,
    project_id: str,
    topic_json: str,
    input_signature: str,
) -> Any | None:
    rows = connection.execute(
        """
        SELECT match_run_id,summary_json FROM policy_match_run
        WHERE project_id=? AND matcher_version=? AND topic_tags_json=? AND status='completed'
        ORDER BY completed_at DESC,started_at DESC,match_run_id DESC
        """,
        (project_id, POLICY_MATCHER_VERSION, topic_json),
    ).fetchall()
    for row in rows:
        summary = json.loads(row["summary_json"] or "{}")
        if summary.get("match_input_signature") == input_signature:
            return row
    return None
DOCUMENT_STANDARD_SEED = SKILL_ROOT / "assets" / "knowledge-base" / "seeds" / "document_standard_seed_20260804.json"


def read_residual_terms(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def gate_status(gates: dict, *stage_codes: str) -> str:
    checks = [
        item for item in gates["checks"] if item["stage_code"] in set(stage_codes)
    ]
    if any(item["result"] == "fail" for item in checks):
        return "blocked"
    if any(item["result"] == "warning" for item in checks):
        return "pending_confirmation"
    return "completed"


def postgres_args(server_config: dict) -> argparse.Namespace:
    required = [key for key in ("database", "user") if not str(server_config.get(key, "")).strip()]
    if required:
        raise ValueError(f"knowledge.server is missing required fields: {required}")
    return argparse.Namespace(
        host=server_config.get("host", "127.0.0.1"),
        port=int(server_config.get("port", 15432)),
        database=server_config["database"],
        user=server_config["user"],
        password_env=server_config.get("password_env", "MEDICAL_FEASIBILITY_DB_PASSWORD"),
        schema=server_config.get("schema", "medical_report_kb"),
        connect_timeout=int(server_config.get("connect_timeout", 10)),
    )


def render_review_is_current(review: dict, candidate_docx: Path) -> bool:
    try:
        pages = review["pages"]
        return bool(
            review.get("schema_version") == "1.0"
            and review.get("review_type") == "word_render_human_review"
            and review.get("result") == "pass"
            and str(review.get("reviewed_by", "")).strip()
            and review.get("checked_all_pages") is True
            and review.get("page_count", 0) == len(pages)
            and pages
            and candidate_docx.is_file()
            and review.get("docx_sha256") == sha256_file(candidate_docx)
            and all(
                Path(page["path"]).is_file()
                and sha256_file(Path(page["path"])) == page["sha256"]
                for page in pages
            )
        )
    except (KeyError, TypeError, OSError, ValueError):
        return False


def retrospective_markdown(project_code: str, delivery: dict, stage_results: list[dict]) -> str:
    lines = [
        "# 项目复盘与通用沉淀候选",
        "",
        f"- 项目编号：{project_code}",
        f"- 当前状态：{delivery['status']}",
        "- 本文件只记录流程级候选，不自动写回 Skill。",
        "",
        "## 十阶段状态",
        "",
    ]
    lines.extend(
        f"- {item['stage_code']} {item['name']}：{item['status']}"
        for item in stage_results
    )
    lines.extend(
        [
            "",
            "## 可复用候选",
            "",
            "- 来源角色必须先于候选事实提取，参考材料不得进入项目事实池。",
            "- Word 渲染复核记录必须绑定当前 DOCX 与全部页面 PNG 的 SHA-256。",
            "- 未确认范围、断裂贯通链和未采纳章节必须形成显式阻断项。",
            "",
            "## 禁止沉淀",
            "",
            "- 客户名称、人员信息、未公开材料、范围、金额、指标和项目结论不得写回 Skill。",
        ]
    )
    return "\n".join(lines) + "\n"


def s0_blocked_result(
    *,
    workbench: Path,
    database: Path,
    project_code: str,
    blockers: list[dict],
    dependencies: dict,
    knowledge: dict,
) -> dict:
    stage_results = [
        {
            "stage_code": "S0",
            "name": "任务定义、环境与共享知识门禁",
            "status": "blocked",
            "artifacts": [
                "00-项目任务书.md",
                "project-config.json",
                "运行记录/dependency-check.json",
                "运行记录/knowledge-doctor.json",
            ],
        }
    ] + [
        {
            "stage_code": f"S{stage}",
            "name": name,
            "status": "not_started",
            "artifacts": [],
        }
        for stage, name in (
            (1, "资料、事实与政策证据"),
            (2, "清单、映射与范围基线"),
            (3, "参考方案受限复用"),
            (4, "贯通矩阵与目录"),
            (5, "章节任务与组成计划"),
            (6, "正文生成与版本采纳"),
            (7, "多维校验"),
            (8, "Word 候选稿与渲染复核"),
            (9, "复盘与通用沉淀候选"),
        )
    ]
    return {
        "project_code": project_code,
        "workbench": str(workbench),
        "database": str(database),
        "status": "blocked",
        "exit_code": 2,
        "blockers": blockers,
        "dependencies": {
            "core_ready": dependencies["core"]["ready"],
            "knowledge_ready": dependencies["knowledge"]["ready"],
            "word_candidate_ready": dependencies["word"]["candidate_generation_ready"],
            "word_visual_delivery_ready": dependencies["word"]["visual_delivery_ready"],
        },
        "standard_knowledge": knowledge,
        "stage_results": stage_results,
        "next_action": "Resolve the S0 knowledge or environment blocker, then rerun the same command.",
    }


def construction_knowledge_coverage(database: Path, project_code: str) -> dict:
    with connect(database) as conn:
        project = conn.execute(
            "SELECT project_id FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if not project:
            return {"scope_count": 0, "scope_node_count": 0, "coverage_ratio": 0.0, "block_ids": []}
        project_id = project["project_id"]
        scope_count = conn.execute(
            """
            SELECT COUNT(*) FROM project_scope_item
            WHERE project_id=? AND customer_scope=1 AND status<>'not_applicable'
            """,
            (project_id,),
        ).fetchone()[0]
        scope_node_count = conn.execute(
            """
            SELECT COUNT(DISTINCT n.source_object_id)
            FROM section_outline_node n
            JOIN section_composition_plan p ON p.plan_id=n.plan_id
            WHERE p.project_id=? AND n.node_kind='scope'
            """,
            (project_id,),
        ).fetchone()[0]
        capability_count = conn.execute(
            """
            SELECT COUNT(DISTINCT n.source_object_id)
            FROM section_outline_node n
            JOIN section_composition_plan p ON p.plan_id=n.plan_id
            WHERE p.project_id=? AND n.node_kind='capability'
            """,
            (project_id,),
        ).fetchone()[0]
        mapped_scope_ids = set()
        confirmed_capability_ids: set[str] = set()
        confirmed_available_block_ids: set[str] = set()
        imprecise_confirmed_capability_ids: set[str] = set()
        for row in conn.execute(
            """
            SELECT m.scope_id,m.capability_id,m.status,c.standard_block_ids_json,
                   c.block_match_scope
            FROM scope_product_map m
            JOIN product_capability c ON c.capability_id=m.capability_id
            WHERE m.project_id=? AND m.status IN ('candidate','confirmed')
            """,
            (project_id,),
        ):
            if json.loads(row["standard_block_ids_json"] or "[]"):
                mapped_scope_ids.add(row["scope_id"])
            if row["status"] == "confirmed":
                confirmed_capability_ids.add(row["capability_id"])
                if row["block_match_scope"] == "product_heading_fallback":
                    imprecise_confirmed_capability_ids.add(row["capability_id"])
                else:
                    confirmed_available_block_ids.update(
                        json.loads(row["standard_block_ids_json"] or "[]")
                    )
        block_ids = [
            row[0]
            for row in conn.execute(
                """
                SELECT DISTINCT s.source_object_id
                FROM section_plan_source s
                JOIN section_composition_plan p ON p.plan_id=s.plan_id
                JOIN section_blueprint b ON b.blueprint_id=p.blueprint_id
                WHERE p.project_id=? AND b.section_role='construction_content'
                  AND s.source_type='corpus' AND s.usage_mode IN ('direct','parameterized','structure_only')
                ORDER BY s.source_object_id
                """,
                (project_id,),
            )
        ]
        parameterized_block_ids = {
            row[0]
            for row in conn.execute(
                """
                SELECT DISTINCT s.source_object_id
                FROM section_plan_source s
                JOIN section_composition_plan p ON p.plan_id=s.plan_id
                JOIN section_blueprint b ON b.blueprint_id=p.blueprint_id
                WHERE p.project_id=? AND b.section_role='construction_content'
                  AND s.source_type='corpus' AND s.usage_mode='parameterized'
                """,
                (project_id,),
            )
        }
        selected_confirmed_block_ids = confirmed_available_block_ids & parameterized_block_ids
    return {
        "scope_count": scope_count,
        "scope_node_count": scope_node_count,
        "scope_coverage_count": min(scope_count, scope_node_count),
        "coverage_ratio": round(scope_node_count / scope_count, 6) if scope_count else 1.0,
        "capability_node_count": capability_count,
        "mapped_scope_count": len(mapped_scope_ids),
        "mapping_coverage_ratio": round(len(mapped_scope_ids) / scope_count, 6) if scope_count else 1.0,
        "standard_block_count": len(block_ids),
        "block_ids": block_ids,
        "confirmed_capability_count": len(confirmed_capability_ids),
        "imprecise_confirmed_capability_count": len(imprecise_confirmed_capability_ids),
        "confirmed_available_block_count": len(confirmed_available_block_ids),
        "confirmed_selected_block_count": len(selected_confirmed_block_ids),
        "full_block_coverage_ratio": (
            round(len(selected_confirmed_block_ids) / len(confirmed_available_block_ids), 6)
            if confirmed_available_block_ids
            else 1.0
        ),
        "missing_confirmed_block_ids": sorted(
            confirmed_available_block_ids - selected_confirmed_block_ids
        ),
    }


def run_pipeline(
    workbench: Path,
    *,
    project_code: str,
    official_name: str | None = None,
    owner_name: str | None = None,
    jurisdiction_code: str = "",
    jurisdiction_name: str = "",
    policy_topics: set[str] | None = None,
    word_template: Path | None = None,
    standard_knowledge_packs: list[Path] | None = None,
    policy_catalogs: list[Path] | None = None,
    knowledge_config_path: Path | None = None,
    knowledge_profile: str | None = None,
    knowledge_mode: str | None = None,
    local_knowledge_config_path: Path | None = None,
    generate_working_drafts: bool = True,
) -> dict:
    initialized = initialize_project(
        workbench,
        project_code=project_code,
        official_name=official_name,
        owner_name=owner_name,
        jurisdiction_code=jurisdiction_code,
        jurisdiction_name=jurisdiction_name,
    )
    workbench = Path(initialized["workbench"])
    database = Path(initialized["database"])
    config = load_json(workbench / "project-config.json")
    source_dir = workbench / config["paths"]["source_materials"]
    clean_dir = workbench / config["paths"]["cleaned_text"]
    structured_dir = workbench / config["paths"]["structured_data"]
    logs_dir = workbench / config["paths"]["run_logs"]
    task_dir = workbench / "10-章节任务包"
    draft_dir = workbench / config["paths"]["drafts"]
    delivery_dir = workbench / config["paths"]["delivery"]
    blockers = []
    processed = {"clean_documents": [], "scope_workbooks": [], "restricted_references": []}
    knowledge_results = []
    policy_catalog_results = []

    local_preparation = {}
    explicit_remote_or_mode = bool(knowledge_config_path or knowledge_profile or knowledge_mode)
    # server_required is the initializer default. Preserve deliberately configured
    # offline/disabled/snapshot modes and an explicit opt-out from local discovery.
    project_knowledge = config.get("knowledge", {})
    auto_local_allowed = (
        project_knowledge.get("mode", "server_required") == "server_required"
        and project_knowledge.get("prefer_local_packages", True)
    )
    local_path = (
        discover_local_config(local_knowledge_config_path)
        if local_knowledge_config_path is not None or
        (not explicit_remote_or_mode and auto_local_allowed) else None
    )
    if local_path is not None:
        if knowledge_config_path or knowledge_profile or knowledge_mode not in (None, "snapshot_required"):
            raise ValueError("local knowledge config conflicts with explicit remote/offline/disabled settings")
        knowledge_mode = "snapshot_required"
        try:
            local_preparation = prepare_project_knowledge(
                local_path, database, project_code, selection=config.get("knowledge", {}),
            )
            write_json(logs_dir / "local-knowledge-preparation.json", local_preparation)
        except Exception as exc:
            blockers.append({
                "stage": "S0_KNOWLEDGE", "reason": "local_knowledge_preparation_failed",
                "required_action": str(exc),
            })

    configured_mode = str(
        knowledge_mode or config.get("knowledge", {}).get("mode") or "server_required"
    )
    dependencies = check_dependencies(configured_mode)
    write_json(logs_dir / "dependency-check.json", dependencies)
    if not dependencies["core"]["ready"]:
        blockers.append(
            {
                "stage": "S0_ENVIRONMENT",
                "reason": "core_dependency_check_failed",
                "required_action": dependencies["core"]["migration_error"]
                or "Use Python 3.10+ with working SQLite migrations.",
            }
        )
    if not dependencies["knowledge"]["ready"]:
        blockers.append(
            {
                "stage": "S0_ENVIRONMENT",
                "reason": "knowledge_dependency_check_failed",
                "required_action": dependencies["knowledge"]["blocking_reason"],
            }
        )

    knowledge_config = config.get("knowledge", {})
    policy_seed = load_json(POLICY_SEED)
    knowledge_settings: dict = {}
    knowledge_manifest: dict = {}
    server_sync_result: dict = {"enabled": False, "status": "not_requested"}
    doctor_result: dict = {}
    try:
        knowledge_settings = ({
            "mode": "snapshot_required", "profile_name": "local-split-packages",
            "config_path": str(local_path), "config_source": "local_split_packages",
            "package_ids": local_preparation.get("manifest", {}).get("package_ids", []),
            "catalog_ids": local_preparation.get("manifest", {}).get("catalog_ids", []),
            "permission_scopes": config.get("knowledge", {}).get("permission_scopes", {}),
            "allow_stale_cache": False,
        } if local_path is not None else resolve_knowledge_settings(
            config,
            explicit_config_path=knowledge_config_path,
            explicit_profile=knowledge_profile,
            explicit_mode=knowledge_mode,
        ))
        write_json(logs_dir / "knowledge-profile.json", public_settings(knowledge_settings))
    except KnowledgeConfigurationError as exc:
        doctor_result = {
            "schema_version": "1.0",
            "status": "failed",
            "failure_class": "configuration",
            "reason": exc.reason,
            "message": str(exc),
            "details": exc.details,
        }
        write_json(logs_dir / "knowledge-doctor.json", doctor_result)
        blockers.append(
            {
                "stage": "S0_KNOWLEDGE",
                "reason": exc.reason,
                "required_action": (
                    "Create the user-level medical-report-kb.json profile or select an explicit "
                    "snapshot/offline/disabled mode."
                ),
                "details": exc.details,
            }
        )

    configured_topics = (
        set(knowledge_settings.get("policy_topics", []))
        if policy_topics is None
        else set(policy_topics)
    )
    policy_topics = project_policy_topics(database, project_code, configured_topics)

    if knowledge_settings and knowledge_settings.get("mode") != "server_required":
        doctor_result = diagnose_settings(knowledge_settings)
        write_json(logs_dir / "knowledge-doctor.json", doctor_result)

    if knowledge_settings.get("mode") == "server_required":
        doctor_result = diagnose_settings(knowledge_settings)
        write_json(logs_dir / "knowledge-doctor.json", doctor_result)
        if doctor_result["status"] != "passed":
            blockers.append(
                {
                    "stage": "S0_KNOWLEDGE",
                    "reason": f"knowledge_doctor_{doctor_result.get('failure_class') or 'failed'}",
                    "required_action": next(
                        (
                            item.get("required_action")
                            for item in doctor_result.get("checks", [])
                            if item.get("status") == "fail" and item.get("required_action")
                        ),
                        "Resolve every failed knowledge doctor check.",
                    ),
                    "details": doctor_result.get("checks", []),
                }
            )
        else:
            try:
                connection_args = postgres_args(knowledge_settings)
                with connect_postgres(connection_args) as postgres_connection:
                    server_sync_result = {
                        "enabled": True,
                        "status": "live_sync",
                        "profile_name": knowledge_settings["profile_name"],
                        **sync_postgres_knowledge(
                            database,
                            project_code,
                            postgres_connection,
                            schema=connection_args.schema,
                            package_ids=list(knowledge_settings.get("package_ids", [])),
                            catalog_ids=list(knowledge_settings.get("catalog_ids", [])),
                            topics=set(policy_topics),
                            criteria=knowledge_settings.get("selection", {}),
                            permission_scopes=knowledge_settings.get("permission_scopes", {}),
                            profile_name=knowledge_settings.get("profile_name", ""),
                        ),
                    }
                knowledge_results.extend(server_sync_result.get("knowledge_packages", []))
                knowledge_manifest = server_sync_result["snapshot_validation"]
                knowledge_manifest["source"] = "live_postgres_sync"
                knowledge_manifest["connection_status"] = "connected"
                config["knowledge"]["profile"] = knowledge_settings["profile_name"]
                config["knowledge"]["mode"] = "server_required"
                config["knowledge"]["package_ids"] = server_sync_result["selected_package_ids"]
                config["knowledge"]["catalog_ids"] = server_sync_result["selected_catalog_ids"]
                write_json(workbench / "project-config.json", config)
            except KnowledgeSelectionError as exc:
                server_sync_result = {
                    "enabled": True,
                    "status": "failed",
                    "reason": exc.reason,
                    "error": str(exc),
                    "candidates": exc.candidates,
                }
                blockers.append(
                    {
                        "stage": "S0_KNOWLEDGE",
                        "reason": exc.reason,
                        "required_action": "Select one of the listed published candidates explicitly in project-config.json.",
                        "candidates": exc.candidates,
                    }
                )
            except Exception as exc:
                safe_error = redact_text(exc, knowledge_settings)
                server_sync_result = {
                    "enabled": True,
                    "status": "failed",
                    "reason": "postgres_knowledge_sync_failed",
                    "error": safe_error,
                }
                blockers.append(
                    {
                        "stage": "S0_KNOWLEDGE",
                        "reason": "postgres_knowledge_sync_failed",
                        "required_action": "Restore the configured read-only PostgreSQL connection and rerun; server_required never falls back to a stale cache.",
                        "details": safe_error,
                    }
                )
    elif knowledge_settings.get("mode") == "snapshot_required":
        try:
            knowledge_manifest = validate_snapshots(
                database,
                project_code,
                package_ids=knowledge_settings.get("package_ids", []),
                catalog_ids=knowledge_settings.get("catalog_ids", []),
                permission_scopes=knowledge_settings.get("permission_scopes", {}),
                allow_stale=bool(knowledge_settings.get("allow_stale_cache")),
            )
            knowledge_manifest["source"] = "local_sqlite_snapshot"
            knowledge_manifest["connection_status"] = "not_required"
            if local_preparation:
                knowledge_manifest["local_preparation"] = local_preparation
            server_sync_result = {"enabled": False, "status": "local_snapshot"}
        except KnowledgeSnapshotError as exc:
            blockers.append(
                {
                    "stage": "S0_KNOWLEDGE",
                    "reason": exc.reason,
                    "required_action": "Run a successful server sync first, or repair the selected local snapshot.",
                    "details": exc.details,
                }
            )
    elif knowledge_settings.get("mode") == "disabled":
        knowledge_manifest = {
            "status": "disabled",
            "source": "none",
            "declaration": "Shared company knowledge was explicitly disabled and was not used.",
            "counts": {"corpus_blocks": 0, "capabilities": 0, "catalog_records": 0, "policy_clauses": 0},
            "package_ids": [],
            "catalog_ids": [],
            "content_hashes": {},
            "synced_at": {},
            "permission_scopes": {},
            "connection_status": "not_attempted",
        }
    configured_packs = [Path(value) for value in knowledge_config.get("standard_packs", [])]
    selected_packs = list(standard_knowledge_packs or configured_packs)
    if selected_packs and knowledge_settings.get("mode") != "offline_pack":
        blockers.append(
            {
                "stage": "S0_KNOWLEDGE",
                "reason": "offline_pack_mode_required",
                "required_action": "Select knowledge.mode=offline_pack before importing reviewed local knowledge packs.",
            }
        )
        selected_packs = []
    for pack_path in selected_packs:
        if not pack_path.is_absolute():
            pack_path = workbench / pack_path
        pack_path = pack_path.resolve()
        if not pack_path.is_file():
            blockers.append(
                {
                    "stage": "S0_ENVIRONMENT",
                    "reason": "standard_knowledge_pack_not_found",
                    "required_action": f"Provide a readable reviewed knowledge pack: {pack_path}",
                }
            )
            continue
        pack_payload = load_json(pack_path)
        imported_at = now_iso()
        logical_content_hash = sha256_text(canonical_json(pack_payload))
        knowledge_results.append(
            {
                **import_standard_pack(database, pack_payload),
                # PostgreSQL publication hashes canonical JSON content. Keep the
                # physical file hash separately so offline and server manifests
                # can be compared without conflating serialization differences.
                "content_hash": logical_content_hash,
                "file_sha256": sha256_file(pack_path),
                "pack_path": str(pack_path),
                "permission_scope": pack_payload.get("permission_scope", ""),
                "synced_at": imported_at,
            }
        )

    configured_catalogs = [Path(value) for value in knowledge_config.get("policy_catalogs", [])]
    selected_catalogs = list(policy_catalogs or configured_catalogs)
    if selected_catalogs and knowledge_settings.get("mode") != "offline_pack":
        blockers.append(
            {
                "stage": "S0_KNOWLEDGE",
                "reason": "offline_catalog_mode_required",
                "required_action": "Select knowledge.mode=offline_pack before importing a reviewed local policy catalog.",
            }
        )
        selected_catalogs = []
    for catalog_path in selected_catalogs:
        if not catalog_path.is_absolute():
            catalog_path = workbench / catalog_path
        catalog_path = catalog_path.resolve()
        if not catalog_path.is_file():
            blockers.append(
                {
                    "stage": "S0_ENVIRONMENT",
                    "reason": "policy_catalog_not_found",
                    "required_action": f"Provide a readable reviewed policy catalog: {catalog_path}",
                }
            )
            continue
        catalog_payload = load_json(catalog_path)
        policy_catalog_results.append(
            {
                **import_policy_catalog(database, catalog_payload),
                "permission_scope": catalog_payload.get("permission_scope", ""),
                "synced_at": now_iso(),
            }
        )

    if knowledge_settings.get("mode") == "offline_pack":
        imported_package_ids = sorted(item["package_id"] for item in knowledge_results)
        with connect(database) as conn:
            if imported_package_ids:
                placeholders = ",".join("?" for _ in imported_package_ids)
                corpus_block_count = conn.execute(
                    f"""
                    SELECT COUNT(*) FROM corpus_block AS block
                    JOIN corpus_document AS document
                      ON document.corpus_document_id=block.corpus_document_id
                    WHERE block.review_status='approved'
                      AND document.review_status='approved'
                      AND document.version IN ({placeholders})
                    """,
                    imported_package_ids,
                ).fetchone()[0]
            else:
                corpus_block_count = 0
            offline_counts = {
                "corpus_blocks": corpus_block_count,
                "capabilities": sum(
                    int(item.get("capabilities_imported", 0)) for item in knowledge_results
                ),
                "catalog_records": sum(
                    int(item.get("records_available", item.get("records_imported", 0)))
                    for item in policy_catalog_results
                ),
                # Bundled/project policy seeds are not part of an offline
                # standard-knowledge package or policy catalog snapshot.
                "policy_clauses": 0,
            }
        if not knowledge_results or offline_counts["corpus_blocks"] < 1 or offline_counts["capabilities"] < 1:
            blockers.append(
                {
                    "stage": "S0_KNOWLEDGE",
                    "reason": "offline_pack_empty_or_missing",
                    "required_action": "Configure at least one reviewed non-empty standard knowledge pack for offline_pack mode.",
                    "details": offline_counts,
                }
            )
        else:
            knowledge_manifest = {
                "status": "valid",
                "source": "reviewed_offline_pack",
                "package_ids": sorted(item["package_id"] for item in knowledge_results),
                "catalog_ids": sorted(
                    item.get("catalog_id", "") for item in policy_catalog_results if item.get("catalog_id")
                ),
                "content_hashes": {
                    **{item["package_id"]: item["content_hash"] for item in knowledge_results},
                    **{item["catalog_id"]: item["content_hash"] for item in policy_catalog_results},
                },
                "synced_at": {
                    **{item["package_id"]: item["synced_at"] for item in knowledge_results},
                    **{item["catalog_id"]: item["synced_at"] for item in policy_catalog_results},
                },
                "permission_scopes": {
                    **{item["package_id"]: item["permission_scope"] for item in knowledge_results},
                    **{item["catalog_id"]: item["permission_scope"] for item in policy_catalog_results},
                },
                "connection_status": "not_required",
                "counts": offline_counts,
            }

    s0_blockers = [item for item in blockers if item.get("stage") in {"S0_ENVIRONMENT", "S0_KNOWLEDGE"}]
    if s0_blockers:
        early = s0_blocked_result(
            workbench=workbench,
            database=database,
            project_code=project_code,
            blockers=blockers,
            dependencies=dependencies,
            knowledge={
                "settings": public_settings(knowledge_settings) if knowledge_settings else {},
                "doctor": doctor_result,
                "server_sync": server_sync_result,
                "manifest": knowledge_manifest,
            },
        )
        write_json(logs_dir / "pipeline-result.json", early)
        return early

    inventory_path = structured_dir / "source-inventory.json"
    inventory = build_inventory(
        [source_dir],
        existing_path=inventory_path if inventory_path.exists() else None,
        output_path=inventory_path,
    )
    write_json(inventory_path, inventory)
    source_role_config = config.get("source_roles", {})
    source_roles = classify_inventory(
        inventory,
        overrides=source_role_config.get("overrides", {}),
        default_role=source_role_config.get("default_role", "project_material"),
    )
    write_json(structured_dir / "source-role-register.json", source_roles)
    roles_by_relative_path = {
        item["relative_path"]: item["role"] for item in source_roles["sources"]
    }

    for source in sorted(path for path in source_dir.rglob("*") if path.is_file()):
        suffix = source.suffix.lower()
        relative_path = source.relative_to(source_dir).as_posix()
        source_role = roles_by_relative_path.get(relative_path, "project_material")
        if suffix in SUPPORTED_TEXT:
            payload = build_clean_payload(source, project_code, source.stem, False)
            output = clean_dir / f"{source.stem}-{payload['document']['document_id']}.json"
            write_json(output, payload)
            ingest_result = ingest_clean_payload(
                database,
                payload,
                source_class=source_role,
                reuse_class="D" if source_role == "format_reference" else "C",
                review_status="prohibited" if source_role == "format_reference" else "pending",
            )
            processed["clean_documents"].append(
                {"source": str(source), "output": str(output), "ingest": ingest_result}
            )
        elif suffix in SUPPORTED_SCOPE:
            if source_role == "project_material":
                payload = build_scope_payload(source)
                output = structured_dir / f"{source.stem}-scope.json"
                write_json(output, payload)
                ingest_result = ingest_scope_payload(
                    database, payload, project_code=project_code
                )
                processed["scope_workbooks"].append(
                    {"source": str(source), "output": str(output), "ingest": ingest_result}
                )
            else:
                processed["restricted_references"].append(
                    {"source": str(source), "source_role": source_role, "action": "not_ingested_as_scope"}
                )
        elif suffix in EXPLICITLY_BLOCKED:
            blockers.append(
                {
                    "source": str(source),
                    "reason": "unsupported_or_ocr_required",
                    "required_action": "Convert to DOCX/XLSX/TXT or run verified PDF/OCR extraction.",
                }
            )
        else:
            blockers.append(
                {
                    "source": str(source),
                    "reason": "unsupported_file_type",
                    "required_action": "Register a verified extractor before continuing.",
                }
            )

    standard_seed = load_json(DOCUMENT_STANDARD_SEED)
    synced_policy = server_sync_result.get("policy", {})
    if synced_policy.get("policies", 0):
        policy_ingest = {
            "policies": synced_policy.get("policies", 0),
            "clauses": synced_policy.get("clauses", 0),
            "source": "postgres_snapshot",
        }
    elif knowledge_manifest.get("source") == "local_sqlite_snapshot" and knowledge_manifest.get("counts", {}).get("policy_clauses", 0):
        policy_ingest = {
            "policies": 0,
            "clauses": knowledge_manifest["counts"].get("policy_clauses", 0),
            "source": "local_postgres_snapshot",
        }
    elif knowledge_settings.get("mode") in {"server_required", "snapshot_required"}:
        policy_ingest = {"policies": 0, "clauses": 0, "source": "no_verified_policy_snapshot"}
    else:
        policy_ingest = {**ingest_policies(database, policy_seed), "source": "bundled_seed"}
    standard_ingest = ingest_document_standards(database, standard_seed)
    policy_topics = project_policy_topics(database, project_code, set(policy_topics))
    sorted_topics = sorted(policy_topics)
    with connect(database) as conn:
        project = conn.execute(
            "SELECT * FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        topic_json = json.dumps(
            sorted_topics, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        current_policy_input_signature = policy_match_input_signature(
            conn, project, set(sorted_topics)
        )
        existing_policy_run = reusable_policy_run(
            conn,
            project["project_id"],
            topic_json,
            current_policy_input_signature,
        )
        existing_standard_run = conn.execute(
            """
            SELECT standard_match_run_id FROM document_standard_match_run
            WHERE project_id=? AND matcher_version=? AND status='completed'
            ORDER BY completed_at DESC,started_at DESC,standard_match_run_id DESC LIMIT 1
            """,
            (project["project_id"], DOCUMENT_STANDARD_MATCHER_VERSION),
        ).fetchone()
    policy_result = (
        export_selection(database, project_code, existing_policy_run["match_run_id"])
        if existing_policy_run
        else match_project_policies(database, project_code, set(sorted_topics), None, None)
    )
    with connect(database) as conn:
        active_catalog = conn.execute(
            "SELECT catalog_id FROM policy_catalog WHERE catalog_status='active' LIMIT 1"
        ).fetchone()
    policy_catalog_result = (
        match_policy_catalog_candidates(database, project_code, topics=set(sorted_topics))
        if active_catalog
        else {"candidate_count": 0, "candidates": [], "catalog_match_run_id": ""}
    )
    standard_result = (
        export_latest_match(database, project_code)
        if existing_standard_run
        else match_document_standards(database, project_code)
    )
    write_json(structured_dir / "policy-selection.json", policy_result)
    (structured_dir / "policy-matrix.md").write_text(
        policy_markdown(policy_result), encoding="utf-8"
    )
    write_json(structured_dir / "policy-catalog-candidates.json", policy_catalog_result)
    (structured_dir / "policy-catalog-candidates.md").write_text(
        policy_catalog_markdown(policy_catalog_result)
        if policy_catalog_result.get("candidate_count")
        else "# 部门政策目录项目候选\n\n> 当前未导入部门政策目录。\n",
        encoding="utf-8",
    )
    policy_material = build_policy_material(database, project_code, mode="working")
    write_json(structured_dir / "policy-section-material.json", policy_material)
    (structured_dir / "policy-section-material.md").write_text(
        policy_material_markdown(policy_material), encoding="utf-8"
    )
    write_json(structured_dir / "document-standard-selection.json", standard_result)
    (structured_dir / "document-standard-match.md").write_text(
        document_standard_markdown(standard_result), encoding="utf-8"
    )

    reference_workpack = build_reference_workpack(database, project_code)
    write_json(structured_dir / "reference-reuse-workpack.json", reference_workpack)
    (workbench / "07-参考方案复用地图.md").write_text(
        reference_workpack_markdown(reference_workpack), encoding="utf-8"
    )
    fact_workpack = build_workpack(database, project_code)
    write_json(structured_dir / "candidate-fact-workpack.json", fact_workpack)
    scope_baseline = build_scope_baseline(database, project_code)
    write_json(structured_dir / "scope-baseline.json", scope_baseline)
    capability_mapping = map_capabilities(
        database,
        project_code,
        threshold=float(knowledge_config.get("mapping_threshold", 0.55)),
        max_candidates=int(knowledge_config.get("mapping_max_candidates", 3)),
    )
    write_json(structured_dir / "scope-capability-candidates.json", capability_mapping)
    traceability_links_path = structured_dir / "traceability-links.json"
    traceability = build_traceability_matrix(
        database,
        project_code,
        links_payload=(
            load_json(traceability_links_path)
            if traceability_links_path.exists()
            else None
        ),
    )
    write_json(structured_dir / "traceability-matrix.json", traceability)
    write_csv(structured_dir / "traceability-matrix.csv", traceability["rows"])
    if scope_baseline["status"] != "confirmed":
        blockers.append(
            {
                "stage": "S2_SCOPE_BASELINE",
                "reason": "scope_baseline_not_confirmed",
                "required_action": (
                    "Complete scope ingestion, resolve pending items, and confirm the "
                    f"baseline {scope_baseline['baseline_id']}."
                ),
            }
        )
    if traceability["incomplete_count"] or traceability["conflict_count"]:
        blockers.append(
            {
                "stage": "S4_TRACEABILITY_OUTLINE",
                "reason": "traceability_chain_incomplete",
                "required_action": (
                    "Complete traceability-links.json without guessing facts, investment, "
                    "indicators, or benefits."
                ),
            }
        )
    plan_result = build_composition_plan(database, project_code)
    write_json(structured_dir / "section-composition-plan.json", plan_result)
    outline_result = build_outline_candidate(database, project_code)
    write_outline_outputs(
        outline_result,
        json_path=structured_dir / "outline-candidate.json",
        markdown_path=structured_dir / "outline-candidate.md",
    )
    if outline_result["status"] == "confirmed":
        write_outline_outputs(
            outline_result,
            json_path=structured_dir / "outline-confirmed.json",
            markdown_path=workbench / "09-确认版目录.md",
        )
    else:
        blockers.append(
            {
                "stage": "S4_TRACEABILITY_OUTLINE",
                "reason": "report_outline_not_confirmed",
                "required_action": (
                    "Review 数据包/结构化数据/outline-candidate.json, then run "
                    "confirm_report_outline.py to freeze the current source signature."
                ),
                "outline_version_id": outline_result["outline_version_id"],
                "source_signature": outline_result["source_signature"],
            }
        )
    construction_coverage = construction_knowledge_coverage(database, project_code)
    write_json(structured_dir / "construction-knowledge-coverage.json", construction_coverage)
    if construction_coverage["scope_count"] and construction_coverage["coverage_ratio"] < 1.0:
        blockers.append(
            {
                "stage": "S5_SECTION_PACKAGES",
                "reason": "construction_scope_not_fully_carried",
                "required_action": "Ensure every customer scope item has a level-4 construction outline node.",
                "details": construction_coverage,
            }
        )
    if (
        knowledge_settings.get("mode") != "disabled"
        and construction_coverage["scope_count"]
        and construction_coverage["mapping_coverage_ratio"] < 1.0
    ):
        blockers.append(
            {
                "stage": "S5_SECTION_PACKAGES",
                "reason": "construction_scope_missing_standard_knowledge",
                "required_action": "Review scope-capability candidates or record an explicit company-knowledge gap for every unmapped scope item.",
                "details": construction_coverage,
            }
        )
    if construction_coverage["imprecise_confirmed_capability_count"]:
        blockers.append(
            {
                "stage": "S5_SECTION_PACKAGES",
                "reason": "confirmed_capability_uses_product_level_block_fallback",
                "required_action": (
                    "Refine capability-to-standard-block relations to module/capability headings; "
                    "product-level fallback remains a bounded working preview."
                ),
                "details": construction_coverage,
            }
        )
    if construction_coverage["full_block_coverage_ratio"] < 1.0:
        blockers.append(
            {
                "stage": "S5_SECTION_PACKAGES",
                "reason": "confirmed_capability_standard_blocks_not_fully_carried",
                "required_action": (
                    "Carry every approved block bound to each confirmed capability in stored source order."
                ),
                "details": construction_coverage,
            }
        )
    package_result = export_packages(database, project_code, task_dir)
    draft_generation = {
        "status": "disabled",
        "section_count": 0,
        "adopted_count": 0,
        "failed_count": 0,
        "total_visible_length": 0,
        "sections": [],
    }
    if generate_working_drafts:
        draft_generation = build_initial_drafts(
            database,
            project_code,
            task_dir,
            draft_dir,
            adopt=True,
        )
        draft_generation["status"] = (
            "completed" if draft_generation["failed_count"] == 0 else "needs_review"
        )
    write_json(logs_dir / "evidence-bound-drafts.json", draft_generation)
    gates = validate_gates(database, project_code)
    write_json(logs_dir / "stage-gates.json", gates)
    residual_terms = read_residual_terms(workbench / "参考残留词表.txt")
    report_validation = validate_report(
        database, project_code, mode="working", residual_terms=residual_terms
    )
    write_json(logs_dir / "working-validation.json", report_validation)
    assembly = assemble(
        database,
        project_code,
        mode="working",
        allow_candidate_outline=True,
    )
    report_content = assembly.pop("content")
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "report-working.md").write_text(report_content, encoding="utf-8")
    write_json(logs_dir / "working-assembly.json", assembly)

    if plan_result["blocked_count"]:
        blockers.append(
            {
                "stage": "S5_SECTION_PACKAGES",
                "reason": "section_composition_plans_blocked",
                "required_action": "Resolve the missing confirmed facts, scope, and policy sources listed in section packages.",
            }
        )
    if assembly["missing_adopted_sections"]:
        blockers.append(
            {
                "stage": "S6_CONTENT_GENERATION",
                "reason": "missing_adopted_sections",
                "required_action": "Generate, validate, and adopt every section version before delivery assembly.",
                "chapter_codes": assembly["missing_adopted_sections"],
            }
        )

    delivery_validation = validate_report(
        database, project_code, mode="delivery", residual_terms=residual_terms
    )
    write_json(logs_dir / "delivery-validation.json", delivery_validation)
    delivery = {
        "status": "blocked",
        "validation_status": delivery_validation["status"],
        "markdown": "",
        "docx": "",
        "docx_sha256": "",
        "render_review": "missing",
    }
    if delivery_validation["status"] != "passed":
        blockers.append(
            {
                "stage": "S7_VALIDATION",
                "reason": "delivery_validation_failed",
                "required_action": "Resolve every blocking delivery validation issue before Word generation.",
                "blocking_count": delivery_validation["blocking_count"],
            }
        )
    else:
        delivery_assembly = assemble(database, project_code, mode="delivery")
        delivery_content = delivery_assembly.pop("content")
        delivery_dir.mkdir(parents=True, exist_ok=True)
        delivery_markdown = delivery_dir / "report-delivery.md"
        delivery_markdown.write_text(delivery_content, encoding="utf-8")
        write_json(logs_dir / "delivery-assembly.json", delivery_assembly)
        delivery["markdown"] = str(delivery_markdown)

        if not dependencies["word"]["candidate_generation_ready"]:
            blockers.append(
                {
                    "stage": "S8_WORD_DELIVERY",
                    "reason": "python_docx_unavailable",
                    "required_action": "Install python-docx in the active local Python runtime.",
                }
            )
        else:
            delivery_config = config.get("delivery", {})
            configured_template = str(delivery_config.get("word_template", "")).strip()
            configured_format = str(delivery_config.get("word_format_config", "")).strip()
            format_config_path = None
            format_authority = {}
            if configured_format:
                format_config_path = Path(configured_format)
                if not format_config_path.is_absolute():
                    format_config_path = workbench / format_config_path
            selected_template = word_template
            if selected_template is None and configured_template:
                selected_template = Path(configured_template)
                if not selected_template.is_absolute():
                    selected_template = workbench / selected_template
            if selected_template is not None:
                selected_template = selected_template.resolve()
                if not selected_template.is_file():
                    blockers.append(
                        {
                            "stage": "S8_WORD_DELIVERY",
                            "reason": "word_template_not_found",
                            "required_action": f"Provide a readable confirmed Word template: {selected_template}",
                        }
                    )
                    selected_template = None
            if format_config_path is not None:
                try:
                    from build_report_docx import resolve_format_authority

                    selected_template, format_authority = resolve_format_authority(
                        selected_template,
                        format_config_path,
                    )
                except (FileNotFoundError, ValueError) as exc:
                    blockers.append(
                        {
                            "stage": "S8_WORD_DELIVERY",
                            "reason": "word_format_authority_invalid",
                            "required_action": str(exc),
                        }
                    )
                    selected_template = None
            if selected_template is None:
                blockers.append(
                    {
                        "stage": "S8_WORD_DELIVERY",
                        "reason": "confirmed_word_template_required",
                        "required_action": "Provide a confirmed DOCX format template; the generic working preset cannot produce a formal delivery.",
                    }
                )

            if selected_template is not None:
                candidate_docx = delivery_dir / "report-candidate.docx"
                build_summary_path = logs_dir / "docx-build-summary.json"
                build_input = {
                    "markdown_sha256": sha256_text(delivery_content),
                    "project_name": config["project"].get("official_name", ""),
                    "owner_name": config["project"].get("owner_name", ""),
                    "template_sha256": sha256_file(selected_template) if selected_template else "",
                    "format_config_sha256": format_authority.get("_config_sha256", ""),
                    "delivery_validation_run_id": delivery_validation["validation_run_id"],
                    "validated_content_sha256": delivery_validation["content_sha256"],
                    "outline_version_id": delivery_assembly["outline_version_id"],
                    "outline_hash": delivery_assembly["outline_hash"],
                    "delivery_gate_version": "2.0",
                }
                build_input_sha256 = sha256_text(
                    json.dumps(build_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                )
                previous_summary = (
                    load_json(build_summary_path) if build_summary_path.is_file() else {}
                )
                if (
                    candidate_docx.is_file()
                    and previous_summary.get("build_input_sha256") == build_input_sha256
                    and previous_summary.get("output_sha256") == sha256_file(candidate_docx)
                ):
                    docx_summary = previous_summary
                    docx_summary["reused_existing_candidate"] = True
                else:
                    from build_report_docx import build_docx

                    docx_summary = build_docx(
                        delivery_content,
                        candidate_docx,
                        config["project"].get("official_name", ""),
                        config["project"].get("owner_name", ""),
                        selected_template,
                        mode="delivery",
                        database=database,
                        project_code=project_code,
                        format_config=format_config_path,
                    )
                    docx_summary.update(
                        {
                            "build_input": build_input,
                            "build_input_sha256": build_input_sha256,
                            "reused_existing_candidate": False,
                        }
                    )
                    write_json(build_summary_path, docx_summary)
                delivery.update(
                    {
                        "status": "candidate_generated",
                        "docx": str(candidate_docx),
                        "docx_sha256": sha256_file(candidate_docx),
                    }
                )

                artifact_audit = audit_delivery_artifact(
                    candidate_docx,
                    build_summary=build_summary_path,
                    database=database,
                    project_code=project_code,
                )
                write_json(logs_dir / "delivery-artifact-audit.json", artifact_audit)
                if artifact_audit["status"] == "failed":
                    delivery["status"] = "blocked_by_artifact_audit"
                    blockers.append(
                        {
                            "stage": "S8_WORD_DELIVERY",
                            "reason": "delivery_artifact_audit_failed",
                            "required_action": "Resolve every blocker in 运行记录/delivery-artifact-audit.json and rebuild the DOCX.",
                            "blocking_count": artifact_audit["blocker_count"],
                        }
                    )

                review_path = logs_dir / "word-render-review.json"
                review = load_json(review_path) if review_path.is_file() else {}
                review_valid = render_review_is_current(review, candidate_docx)
                if review_valid and artifact_audit["status"] != "failed":
                    delivery["status"] = "delivery_ready"
                    delivery["render_review"] = "pass"
                else:
                    delivery["render_review"] = "missing_or_stale"
                    blockers.append(
                        {
                            "stage": "S8_WORD_DELIVERY",
                            "reason": "word_render_review_missing_or_stale",
                            "required_action": (
                                "Render the current candidate DOCX to page PNGs, inspect every page, "
                                "then record a hash-bound review with record_word_render_review.py."
                            ),
                        }
                    )

    fact_gate_status = gate_status(gates, "S1_FACT")
    policy_gate_status = gate_status(gates, "S1P_POLICY")
    source_fact_policy_status = (
        "blocked"
        if "blocked" in {fact_gate_status, policy_gate_status}
        else "pending_confirmation"
        if "pending_confirmation" in {fact_gate_status, policy_gate_status}
        else "completed"
    )
    delivery_ready = delivery["status"] == "delivery_ready"
    stage_results = [
        {
            "stage_code": "S0",
            "name": "任务定义、环境与格式标准",
            "status": "blocked" if not dependencies["core"]["ready"] else gate_status(gates, "S0_FORMAT"),
            "artifacts": [
                "00-项目任务书.md", "project-config.json", "运行记录/dependency-check.json",
                "运行记录/knowledge-profile.json", "运行记录/knowledge-doctor.json",
            ],
        },
        {
            "stage_code": "S1",
            "name": "资料、事实与政策证据",
            "status": source_fact_policy_status,
            "artifacts": [
                "数据包/结构化数据/source-inventory.json",
                "数据包/结构化数据/source-role-register.json",
                "数据包/结构化数据/candidate-fact-workpack.json",
                "数据包/结构化数据/policy-selection.json",
            ],
        },
        {
            "stage_code": "S2",
            "name": "清单、映射与范围基线",
            "status": "completed" if scope_baseline["status"] == "confirmed" else "blocked",
            "artifacts": ["数据包/结构化数据/scope-baseline.json"],
        },
        {
            "stage_code": "S3",
            "name": "参考方案受限复用",
            "status": reference_workpack["status"],
            "artifacts": ["07-参考方案复用地图.md", "数据包/结构化数据/reference-reuse-workpack.json"],
        },
        {
            "stage_code": "S4",
            "name": "贯通矩阵与三级目录",
            "status": (
                "completed"
                if scope_baseline["status"] == "confirmed"
                and traceability["incomplete_count"] == 0
                and traceability["conflict_count"] == 0
                and outline_result["status"] == "confirmed"
                else "blocked"
            ),
            "artifacts": [
                "数据包/结构化数据/traceability-matrix.csv",
                "数据包/结构化数据/outline-candidate.json",
                "数据包/结构化数据/outline-candidate.md",
                "09-确认版目录.md",
            ],
        },
        {
            "stage_code": "S5",
            "name": "章节任务与组成计划",
            "status": "completed" if plan_result["blocked_count"] == 0 else "blocked",
            "artifacts": ["数据包/结构化数据/section-composition-plan.json", "10-章节任务包"],
        },
        {
            "stage_code": "S6",
            "name": "正文生成与版本采纳",
            "status": (
                "completed"
                if not assembly["missing_adopted_sections"] and draft_generation["failed_count"] == 0
                else "blocked"
            ),
            "artifacts": ["11-正文工作稿/report-working.md", "运行记录/evidence-bound-drafts.json"],
        },
        {
            "stage_code": "S7",
            "name": "多维校验",
            "status": "completed" if delivery_validation["status"] == "passed" else "blocked",
            "artifacts": ["运行记录/working-validation.json", "运行记录/delivery-validation.json"],
        },
        {
            "stage_code": "S8",
            "name": "Word 候选稿与渲染复核",
            "status": "completed" if delivery_ready else "blocked",
            "artifacts": ["14-交付稿/report-candidate.docx", "运行记录/word-render-review.json"],
        },
        {
            "stage_code": "S9",
            "name": "复盘与通用沉淀候选",
            "status": "completed",
            "artifacts": ["15-项目复盘.md", "运行记录/retrospective-candidates.json"],
        },
    ]
    retrospective = {
        "schema_version": "1.0",
        "project_code": project_code,
        "delivery_status": delivery["status"],
        "stage_results": stage_results,
        "skill_writeback": "not_performed",
        "privacy_rule": "No customer facts, personal data, unpublished content, scope, investment, indicators, or conclusions may be written back to the Skill.",
    }
    write_json(logs_dir / "retrospective-candidates.json", retrospective)
    (workbench / "15-项目复盘.md").write_text(
        retrospective_markdown(project_code, delivery, stage_results), encoding="utf-8"
    )

    blocked = bool(blockers) or gates["overall"] == "fail"
    result = {
        "project_code": project_code,
        "workbench": str(workbench),
        "database": str(database),
        "status": "blocked" if blocked else delivery["status"],
        "exit_code": 2 if blocked else 0,
        "blockers": blockers,
        "processed": processed,
        "source_inventory": {
            "file_count": inventory["file_count"],
            "error_count": inventory["error_count"],
        },
        "source_roles": source_roles["counts"],
        "dependencies": {
            "core_ready": dependencies["core"]["ready"],
            "knowledge_ready": dependencies["knowledge"]["ready"],
            "word_candidate_ready": dependencies["word"]["candidate_generation_ready"],
            "word_visual_delivery_ready": dependencies["word"]["visual_delivery_ready"],
        },
        "policy": {
            "seeded_policies": policy_ingest["policies"],
            "source": policy_ingest.get("source", ""),
            "match_run_id": policy_result["match_run_id"],
            "match_count": policy_result["clause_match_count"],
            "basis_count": len(policy_material["basis_entries"]),
            "background_paragraph_count": len(policy_material["background_paragraphs"]),
            "catalog_imports": policy_catalog_results,
            "catalog_candidate_count": policy_catalog_result.get("candidate_count", 0),
            "catalog_match_run_id": policy_catalog_result.get("catalog_match_run_id", ""),
            "delivery_eligible": policy_material["delivery_eligible"],
        },
        "document_standard": {
            "seeded_standards": standard_ingest["standards"],
            "match_run_id": standard_result["standard_match_run_id"],
            "match_count": len(standard_result["matches"]),
        },
        "standard_knowledge": {
            "mode": knowledge_settings.get("mode", ""),
            "profile_name": knowledge_settings.get("profile_name", ""),
            "doctor": doctor_result,
            "server_sync": server_sync_result,
            "manifest": knowledge_manifest,
            "packs_imported": len(knowledge_results),
            "imports": knowledge_results,
            "mapping_candidates": len(capability_mapping["candidates"]),
            "unmapped_scope_items": len(capability_mapping["unmapped_scope_items"]),
        },
        "candidate_fact_counts": fact_workpack["counts"],
        "reference_reuse": {
            "status": reference_workpack["status"],
            "reference_source_count": reference_workpack["reference_source_count"],
            "review_item_count": reference_workpack["review_item_count"],
        },
        "scope_baseline": {
            "baseline_id": scope_baseline["baseline_id"],
            "status": scope_baseline["status"],
            "counts": scope_baseline["counts"],
        },
        "traceability": {
            "row_count": traceability["row_count"],
            "complete_count": traceability["complete_count"],
            "incomplete_count": traceability["incomplete_count"],
            "conflict_count": traceability["conflict_count"],
        },
        "composition_plan": {
            "plan_count": plan_result["plan_count"],
            "ready_count": plan_result["ready_count"],
            "blocked_count": plan_result["blocked_count"],
            "not_applicable_count": plan_result["not_applicable_count"],
        },
        "report_outline": {
            "outline_version_id": outline_result["outline_version_id"],
            "version_no": outline_result["version_no"],
            "status": outline_result["status"],
            "source_signature": outline_result["source_signature"],
            "outline_hash": outline_result["outline_hash"],
            "node_count": len(outline_result["nodes"]),
        },
        "construction_knowledge_coverage": construction_coverage,
        "task_packages": package_result["package_count"],
        "draft_generation": draft_generation,
        "stage_gate_overall": gates["overall"],
        "working_validation_status": report_validation["status"],
        "delivery": delivery,
        "stage_results": stage_results,
        "next_action": (
            "Resolve blockers and confirmation questions, then rerun the same command."
            if blocked
            else "Archive the accepted delivery and review stage-9 reuse candidates."
        ),
    }
    write_json(logs_dir / "pipeline-result.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbench", type=Path)
    parser.add_argument("--project-code", required=True)
    parser.add_argument("--official-name")
    parser.add_argument("--owner-name")
    parser.add_argument("--jurisdiction-code", default="")
    parser.add_argument("--jurisdiction-name", default="")
    parser.add_argument("--policy-topic", action="append", default=[])
    parser.add_argument("--word-template", type=Path)
    parser.add_argument("--standard-knowledge-pack", type=Path, action="append", default=[])
    parser.add_argument("--policy-catalog", type=Path, action="append", default=[])
    parser.add_argument("--knowledge-config", type=Path)
    parser.add_argument("--knowledge-profile")
    parser.add_argument("--local-knowledge-config", type=Path)
    parser.add_argument(
        "--knowledge-mode",
        choices=("server_required", "snapshot_required", "offline_pack", "disabled"),
    )
    parser.add_argument("--no-generate-working-drafts", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--print-full-result",
        action="store_true",
        help="Print the complete result JSON even when --output is used.",
    )
    args = parser.parse_args()
    result = run_pipeline(
        args.workbench,
        project_code=args.project_code,
        official_name=args.official_name,
        owner_name=args.owner_name,
        jurisdiction_code=args.jurisdiction_code,
        jurisdiction_name=args.jurisdiction_name,
        policy_topics=set(args.policy_topic) or None,
        word_template=args.word_template,
        standard_knowledge_packs=args.standard_knowledge_pack or None,
        policy_catalogs=args.policy_catalog or None,
        knowledge_config_path=args.knowledge_config,
        knowledge_profile=args.knowledge_profile,
        knowledge_mode=args.knowledge_mode,
        local_knowledge_config_path=args.local_knowledge_config,
        generate_working_drafts=not args.no_generate_working_drafts,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        write_json(args.output, result)
    if args.output and not args.print_full_result:
        manifest = result.get("standard_knowledge", {}).get("manifest", {})
        summary = {
            "project_code": result.get("project_code", ""),
            "status": result.get("status", ""),
            "exit_code": result.get("exit_code", 0),
            "workbench": result.get("workbench", ""),
            "result_path": str(args.output.resolve()),
            "blocker_count": len(result.get("blockers", [])),
            "knowledge": {
                "mode": result.get("standard_knowledge", {}).get("mode", ""),
                "source": manifest.get("source", ""),
                "package_ids": manifest.get("package_ids", []),
                "catalog_ids": manifest.get("catalog_ids", []),
                "counts": manifest.get("counts", {}),
            },
            "next_action": result.get("next_action", ""),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(text)
    return int(result.get("exit_code", 0))


if __name__ == "__main__":
    raise SystemExit(main())
