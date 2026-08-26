#!/usr/bin/env python3
"""Sync published PostgreSQL knowledge into a project SQLite snapshot."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from import_standard_knowledge_pack import import_pack as import_local_pack
from import_policy_catalog_sqlite import import_catalog as import_local_catalog
from ingest_policies import ingest as ingest_local_policies
from knowledge_db import apply_migrations, connect as connect_sqlite, dump_json, now_iso, sha256_text, stable_id
from knowledge_snapshot import snapshot_payload_hash, validate_snapshots
from postgres_knowledge_db import add_connection_arguments, canonical_json, connect as connect_postgres, validate_schema
from standard_solution_coverage import (
    COVERAGE_KEY,
    audit_standard_solution_coverage,
    require_complete_standard_solution_coverage,
)


def iso_value(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value or "")


def fetch_dicts(cursor) -> list[dict[str, Any]]:
    columns = [column.name for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


class KnowledgeSelectionError(ValueError):
    def __init__(self, reason: str, message: str, *, candidates: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.reason = reason
        self.candidates = candidates or []


def _candidate_summary(package: dict[str, Any], document: dict[str, Any] | None = None) -> dict[str, Any]:
    document = document or {}
    return {
        "package_id": package.get("package_id", ""),
        "title": package.get("title", ""),
        "version": document.get("version", package.get("schema_version", "")),
        "published_at": iso_value(package.get("published_at")),
        "document_type": document.get("document_type", ""),
        "project_type": document.get("project_type", ""),
        "jurisdiction_code": document.get("jurisdiction_code", ""),
        "permission_scope": package.get("permission_scope", ""),
        "content_hash": package.get("content_hash", ""),
    }


def _package_document(connection, schema: str, package_id: str) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT * FROM {schema}.runtime_corpus_document WHERE package_id=%s ORDER BY corpus_document_id",
            (package_id,),
        )
        rows = fetch_dicts(cursor)
    if len(rows) != 1:
        return None
    return rows[0]


def _package_facets(connection, schema: str, package_id: str) -> dict[str, set[str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT section_role,module_code FROM {schema}.runtime_corpus_block WHERE package_id=%s ORDER BY block_id",
            (package_id,),
        )
        rows = cursor.fetchall()
    return {
        "section_roles": {str(row[0]) for row in rows if row[0]},
        "module_codes": {str(row[1]) for row in rows if row[1]},
    }


def _package_matches(
    package: dict[str, Any],
    document: dict[str, Any],
    criteria: dict[str, Any],
    allowed_permissions: set[str],
    facets: dict[str, set[str]],
) -> bool:
    if allowed_permissions and package.get("permission_scope") not in allowed_permissions:
        return False
    for key in ("document_type", "project_type"):
        expected = str(criteria.get(key) or "").strip()
        if expected and str(document.get(key) or "").strip() != expected:
            return False
    jurisdiction = str(criteria.get("jurisdiction_code") or "").strip()
    candidate_jurisdiction = str(document.get("jurisdiction_code") or "").strip()
    if jurisdiction and candidate_jurisdiction not in {"", jurisdiction}:
        return False
    version = str(criteria.get("applicable_version") or "").strip()
    if version and str(document.get("version") or "").strip() != version:
        return False
    requested_roles = criteria.get("section_roles") or (
        [criteria["section_role"]] if criteria.get("section_role") else []
    )
    if requested_roles and not set(requested_roles).issubset(facets["section_roles"]):
        return False
    requested_modules = criteria.get("module_codes") or (
        [criteria["module_code"]] if criteria.get("module_code") else []
    )
    requested_topics = criteria.get("topics") or (
        [criteria["topic"]] if criteria.get("topic") else []
    )
    if (requested_modules or requested_topics) and not set(requested_modules + requested_topics).issubset(
        facets["module_codes"]
    ):
        return False
    return True


def select_packages(
    connection,
    schema: str,
    package_ids: list[str],
    *,
    criteria: dict[str, Any] | None = None,
    permission_scopes: list[str] | None = None,
) -> list[dict[str, Any]]:
    criteria = criteria or {}
    allowed_permissions = set(permission_scopes or [])
    with connection.cursor() as cursor:
        if package_ids:
            cursor.execute(
                f"""
                SELECT * FROM {schema}.runtime_knowledge_package
                WHERE package_id = ANY(%s)
                ORDER BY published_at,package_id
                """,
                (package_ids,),
            )
        else:
            cursor.execute(
                f"""
                SELECT * FROM {schema}.runtime_knowledge_package
                ORDER BY published_at DESC NULLS LAST,package_id DESC
                """
            )
        packages = fetch_dicts(cursor)
    if package_ids:
        missing = sorted(set(package_ids).difference(package["package_id"] for package in packages))
        if missing:
            raise KnowledgeSelectionError(
                "knowledge_package_not_found",
                f"published knowledge packages not found: {missing}",
            )
    if not packages:
        raise KnowledgeSelectionError(
            "knowledge_package_not_found", "published knowledge package not found"
        )
    candidates = []
    selected = []
    for package in packages:
        document = _package_document(connection, schema, package["package_id"])
        summary = _candidate_summary(package, document)
        facets = _package_facets(connection, schema, package["package_id"])
        summary.update({key: sorted(values) for key, values in facets.items()})
        candidates.append(summary)
        if document and _package_matches(package, document, criteria, allowed_permissions, facets):
            selected.append(package)
    if package_ids:
        rejected = sorted(set(package_ids).difference(item["package_id"] for item in selected))
        if rejected:
            raise KnowledgeSelectionError(
                "knowledge_package_not_applicable",
                f"configured knowledge packages fail permission or applicability checks: {rejected}",
                candidates=candidates,
            )
        return selected
    if not selected:
        raise KnowledgeSelectionError(
            "knowledge_package_candidate_missing",
            "no published knowledge package matches the project and permission filters",
            candidates=candidates,
        )
    if len(selected) > 1:
        raise KnowledgeSelectionError(
            "knowledge_package_ambiguous",
            "multiple published knowledge packages match; configure package_ids explicitly",
            candidates=[
                item for item in candidates if item["package_id"] in {row["package_id"] for row in selected}
            ],
        )
    return selected


def build_pack_snapshot(connection, schema: str, package: dict[str, Any]) -> dict[str, Any]:
    package_id = package["package_id"]
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT * FROM {schema}.runtime_package_source WHERE package_id=%s ORDER BY source_role",
            (package_id,),
        )
        sources = fetch_dicts(cursor)
        cursor.execute(
            f"SELECT * FROM {schema}.runtime_corpus_document WHERE package_id=%s ORDER BY corpus_document_id",
            (package_id,),
        )
        documents = fetch_dicts(cursor)
        if len(documents) != 1:
            raise ValueError(
                f"knowledge package {package_id} must contain exactly one runtime corpus document"
            )
        document = documents[0]
        cursor.execute(
            f"""
            SELECT * FROM {schema}.runtime_corpus_block
            WHERE package_id=%s ORDER BY block_index
            """,
            (package_id,),
        )
        blocks = fetch_dicts(cursor)
        cursor.execute(
            f"""
            SELECT * FROM {schema}.runtime_product_capability
            WHERE package_id=%s ORDER BY product_name,capability_name
            """,
            (package_id,),
        )
        capabilities = fetch_dicts(cursor)
        capability_ids = [capability["capability_id"] for capability in capabilities]
        relations: list[dict[str, Any]] = []
        if capability_ids:
            cursor.execute(
                f"""
                SELECT * FROM {schema}.runtime_capability_block
                WHERE capability_id = ANY(%s)
                ORDER BY capability_id,priority,block_id
                """,
                (capability_ids,),
            )
            relations = fetch_dicts(cursor)
    relation_map: dict[str, list[dict[str, Any]]] = {}
    for relation in relations:
        relation_map.setdefault(relation["capability_id"], []).append(
            {
                "block_id": relation["block_id"],
                "relation_type": relation.get("relation_type", "standard_description"),
                "priority": int(relation.get("priority") or 0),
                "review_status": relation.get("review_status", "approved"),
                "root_heading_path": relation.get("root_heading_path", []),
                "relation_order": int(relation.get("relation_order") or 0),
                "verbatim_eligible": bool(relation.get("verbatim_eligible", True)),
            }
        )
    for capability_relations in relation_map.values():
        capability_relations.sort(
            key=lambda item: (
                item["relation_order"],
                item["priority"],
                item["block_id"],
            )
        )
    source_files = [
        {
            "role": source["source_role"],
            "file_name": source["file_name"],
            "sha256": source["source_sha256"],
        }
        for source in sources
    ]
    source_corpus_type = str(document.get("source_corpus_type") or "")
    if source_corpus_type in {"", "legacy_unspecified"} and any(
        source.get("source_role") == "standard_solution" for source in sources
    ):
        source_corpus_type = "standard_solution"
    block_payload = [
        {
            "block_id": block["block_id"],
            "source_section_id": block.get("source_section_id", ""),
            "chunk_index": int(block.get("chunk_index") or 0),
            "source_is_heading": bool(block.get("source_is_heading", False)),
            "source_location": block["source_location"],
            "heading_path": block["heading_path"],
            "section_role": block["section_role"],
            "module_code": block["module_code"],
            "clean_text": block["clean_text"],
            "reuse_class": block["reuse_class"],
            "quality_level": block["quality_level"],
            "review_status": "approved",
            "prerequisites": block["prerequisites"],
            "variable_slots": block["variable_slots"],
            "forbidden_terms": block["forbidden_terms"],
            "length_band": block["length_band"],
            "text_hash": block["text_hash"],
            "content_type": block.get("content_type", "legacy_unspecified"),
            "semantic_section": block.get("semantic_section", ""),
            "content_slot": block.get("content_slot", ""),
            "source_order": int(block.get("source_order") or block.get("block_index") or 0),
            "adaptation_mode": block.get("adaptation_mode", "structure_only"),
            "assessment_targets": block.get("assessment_targets", []),
            "construction_scope_tags": block.get("construction_scope_tags", []),
            "content_format": block.get("content_format", "plain_text"),
            "content_payload": block.get("content_payload", {}),
            "asset_manifest": block.get("asset_manifest", []),
            "visible_text_hash": block.get("visible_text_hash", block["text_hash"]),
        }
        for block in blocks
    ]
    capability_payload = [
        {
            "capability_id": capability["capability_id"],
            "product_code": capability["product_code"],
            "product_name": capability["product_name"],
            "capability_name": capability["capability_name"],
            "capability_description": capability["capability_description"],
            "category": capability["category"],
            "module_name": capability["module_name"],
            "selection_rules": capability["selection_rules"],
            "prerequisites": capability["prerequisites"],
            "interface_dependencies": capability["interface_dependencies"],
            "exclusions": capability["exclusions"],
            "applicable_versions": capability["applicable_versions"],
            "standard_block_ids": [
                relation["block_id"]
                for relation in relation_map.get(capability["capability_id"], [])
            ],
            "standard_block_relations": relation_map.get(capability["capability_id"], []),
            "block_match_scope": capability.get("block_match_scope", ""),
            "review_status": "approved",
            "source_location": capability["source_location"],
        }
        for capability in capabilities
    ]
    review_summary = dict(package.get("review_summary") or {})
    runtime_source_sections: list[dict[str, Any]] = []
    if source_corpus_type == "standard_solution":
        require_complete_standard_solution_coverage(
            review_summary, context=f"服务器发布包 {package_id}"
        )
        all_source_sections = (document.get("metadata") or {}).get("source_sections", [])
        runtime_block_ids = {block["block_id"] for block in block_payload}
        runtime_source_sections = [
            section
            for section in all_source_sections
            if section.get("expected_block_ids")
            and set(section.get("expected_block_ids", [])).issubset(runtime_block_ids)
        ]
        runtime_coverage = audit_standard_solution_coverage(
            runtime_source_sections, block_payload
        )
        review_summary = {
            **review_summary,
            "source_package_standard_solution_coverage": review_summary.get(
                COVERAGE_KEY, {}
            ),
            COVERAGE_KEY: runtime_coverage,
        }
    return {
        "schema_version": package["schema_version"],
        "package_id": package_id,
        "package_kind": (
            "standard_solution"
            if source_corpus_type == "standard_solution"
            else "reference_corpus"
        ),
        "title": package["title"],
        "permission_scope": package["permission_scope"],
        "source_files": source_files,
        "corpus": {
            "document_id": document["corpus_document_id"],
            "document_type": document["document_type"],
            "project_type": document["project_type"],
            "source_corpus_type": source_corpus_type or "legacy_unspecified",
            "version": document.get("version", ""),
            "quality_level": document["quality_level"],
            "review_status": "approved",
            "source_sections": runtime_source_sections,
            "blocks": block_payload,
        },
        "capabilities": capability_payload,
        "review_summary": review_summary,
        "server_content_hash": package["content_hash"],
    }


def _catalog_summary(catalog: dict[str, Any]) -> dict[str, Any]:
    metadata = catalog.get("metadata") or {}
    return {
        "catalog_id": catalog.get("catalog_id", ""),
        "title": catalog.get("title", ""),
        "catalog_scope": catalog.get("catalog_scope", ""),
        "version": metadata.get("version", catalog.get("schema_version", "")),
        "published_at": iso_value(catalog.get("published_at")),
        "jurisdiction_code": metadata.get("jurisdiction_code", ""),
        "project_type": metadata.get("project_type", ""),
        "permission_scope": catalog.get("permission_scope", ""),
        "record_count": catalog.get("record_count", 0),
        "content_hash": catalog.get("content_hash", ""),
    }


def _catalog_matches(
    catalog: dict[str, Any], criteria: dict[str, Any], allowed_permissions: set[str]
) -> bool:
    if allowed_permissions and catalog.get("permission_scope") not in allowed_permissions:
        return False
    metadata = catalog.get("metadata") or {}
    expected_scope = str(criteria.get("catalog_scope") or "").strip()
    if expected_scope and str(catalog.get("catalog_scope") or "").strip() != expected_scope:
        return False
    for key in ("project_type", "jurisdiction_code"):
        expected = str(criteria.get(key) or "").strip()
        actual = str(metadata.get(key) or "").strip()
        if expected and actual not in {"", expected}:
            return False
    version = str(criteria.get("applicable_version") or "").strip()
    actual_version = str(metadata.get("version") or "").strip()
    if version and actual_version not in {"", version}:
        return False
    return int(catalog.get("record_count") or 0) > 0


def select_catalogs(
    connection,
    schema: str,
    catalog_ids: list[str],
    *,
    criteria: dict[str, Any] | None = None,
    permission_scopes: list[str] | None = None,
) -> list[dict[str, Any]]:
    criteria = criteria or {}
    allowed_permissions = set(permission_scopes or [])
    with connection.cursor() as cursor:
        if catalog_ids:
            cursor.execute(
                f"""
                SELECT * FROM {schema}.runtime_policy_catalog
                WHERE catalog_id = ANY(%s)
                ORDER BY published_at,catalog_id
                """,
                (catalog_ids,),
            )
        else:
            cursor.execute(
                f"""
                SELECT * FROM {schema}.runtime_policy_catalog
                ORDER BY published_at,catalog_id
                """
            )
        catalogs = fetch_dicts(cursor)
    if catalog_ids:
        missing = sorted(set(catalog_ids).difference(catalog["catalog_id"] for catalog in catalogs))
        if missing:
            raise KnowledgeSelectionError(
                "policy_catalog_not_found",
                f"published policy catalogs not found: {missing}",
            )
    candidates = [_catalog_summary(catalog) for catalog in catalogs]
    selected = [
        catalog
        for catalog in catalogs
        if _catalog_matches(catalog, criteria, allowed_permissions)
    ]
    if catalog_ids:
        rejected = sorted(set(catalog_ids).difference(item["catalog_id"] for item in selected))
        if rejected:
            raise KnowledgeSelectionError(
                "policy_catalog_not_applicable",
                f"configured policy catalogs fail permission, applicability, or non-empty checks: {rejected}",
                candidates=candidates,
            )
        return selected
    if not catalogs:
        raise KnowledgeSelectionError(
            "policy_catalog_not_found",
            "published policy catalog not found",
        )
    if not selected:
        raise KnowledgeSelectionError(
            "policy_catalog_candidate_missing",
            "no published non-empty policy catalog matches the project and permission filters",
            candidates=candidates,
        )
    if len(selected) > 1:
        selected_ids = {row["catalog_id"] for row in selected}
        raise KnowledgeSelectionError(
            "policy_catalog_ambiguous",
            "multiple published policy catalogs match; configure catalog_ids explicitly",
            candidates=[item for item in candidates if item["catalog_id"] in selected_ids],
        )
    return selected


def catalog_payload_from_rows(
    catalog: dict[str, Any], entries: list[dict[str, Any]]
) -> dict[str, Any]:
    records = [
        {
            "catalog_entry_id": entry["catalog_entry_id"],
            "source_row": entry["source_row"],
            "source_index_no": entry["source_index_no"],
            "index_occurrence": entry["index_occurrence"],
            "index_conflict": entry["index_conflict"],
            "identity_key": entry["identity_key"],
            "catalog_group_code": entry["catalog_group_code"],
            "catalog_group_name": entry["catalog_group_name"],
            "authority_level_label": entry["authority_level_label"],
            "category_name": entry["category_name"],
            "keyword_text": entry["keyword_text"],
            "keyword_tags": entry["keyword_tags"],
            "document_no": entry["document_no"],
            "title": entry["title"],
            "publish_date": iso_value(entry["publish_date"]),
            "publish_date_raw": entry["publish_date_raw"],
            "issuer": entry["issuer"],
            "file_count": entry["file_count"],
            "notes": entry["notes"],
            "external_url": entry["external_url"],
            "verification_status": entry["verification_status"],
            "entry_status": entry["entry_status"],
            "row_hash": entry["row_hash"],
        }
        for entry in entries
    ]
    metadata = catalog.get("metadata") or {}
    return {
        "schema_version": catalog["schema_version"],
        "catalog_id": catalog["catalog_id"],
        "catalog_scope": catalog["catalog_scope"],
        "title": catalog["title"],
        "permission_scope": catalog["permission_scope"],
        "source_file": {
            "file_name": catalog["source_file_name"],
            "sha256": catalog["source_sha256"],
        },
        "worksheet_name": catalog["worksheet_name"],
        "records": records,
        "built_at": metadata.get("built_at", ""),
        "content_hash": catalog["content_hash"],
    }


def build_catalog_snapshot(
    connection, schema: str, catalog: dict[str, Any]
) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT * FROM {schema}.runtime_policy_catalog_entry
            WHERE catalog_id=%s
            ORDER BY source_row,catalog_entry_id
            """,
            (catalog["catalog_id"],),
        )
        entries = fetch_dicts(cursor)
    if len(entries) != catalog["record_count"]:
        raise ValueError(
            f"policy catalog {catalog['catalog_id']} expected {catalog['record_count']} entries, got {len(entries)}"
        )
    return catalog_payload_from_rows(catalog, entries)


def build_policy_snapshot(connection, schema: str, topics: set[str]) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT * FROM {schema}.runtime_policy_clause
            ORDER BY authority_group,authority_rank,publish_date,title,clause_order
            """
        )
        rows = fetch_dicts(cursor)
    if topics:
        rows = [
            row
            for row in rows
            if topics.intersection(
                item.get("topic_code", "") for item in (row.get("topics") or [])
            )
        ]
    policies: dict[str, dict[str, Any]] = {}
    for row in rows:
        policy = policies.setdefault(
            row["policy_id"],
            {
                "policy_id": row["policy_id"],
                "title": row["title"],
                "document_no": row["document_no"],
                "issuer": row["issuer"],
                "authority_group": row["authority_group"],
                "authority_rank": row["authority_rank"],
                "jurisdiction_level": row["jurisdiction_level"],
                "jurisdiction_code": row["jurisdiction_code"],
                "jurisdiction_name": row["jurisdiction_name"],
                "policy_type": row["policy_type"],
                "publish_date": iso_value(row["publish_date"]),
                "effective_date": iso_value(row["effective_date"]),
                "expiry_date": iso_value(row["expiry_date"]),
                "validity_status": row["validity_status"],
                "official_url": row["official_url"],
                "official_domain": row["official_domain"],
                "source_hash": row["source_hash"],
                "retrieved_at": iso_value(row["last_verified_at"]),
                "last_verified_at": iso_value(row["last_verified_at"]),
                "verification_status": "verified",
                "check_method": "postgres_published_snapshot",
                "clauses": [],
            },
        )
        policy["clauses"].append(
            {
                "clause_id": row["clause_id"],
                "article_path": row["article_path"],
                "source_location": row["source_location"],
                "original_text": row["original_text"],
                "normalized_summary": row["normalized_summary"],
                "topic_tags": [item["topic_code"] for item in (row.get("topics") or [])],
                "target_objects": row["target_objects"],
                "requirement_type": row["requirement_type"],
                "applicability_notes": row["applicability_notes"],
                "permitted_sections": row["permitted_sections"],
                "forbidden_claims": row["forbidden_claims"],
                "verification_status": "verified",
                "text_hash": row["text_hash"],
            }
        )
    payload = {"policies": list(policies.values())}
    payload["content_hash"] = sha256_text(canonical_json(payload))
    return payload


def record_snapshot(
    database: Path,
    project_code: str,
    *,
    source_type: str,
    source_id: str,
    content_hash: str,
    server_schema: str,
    items: list[tuple[str, str, str, dict[str, Any]]],
    metadata: dict[str, Any],
    permission_scope: str = "",
    profile_name: str = "",
) -> str:
    fetched_at = now_iso()
    snapshot_id = stable_id("KBSNAPSHOT", project_code, source_type, source_id, content_hash)
    snapshot_metadata = {
        **metadata,
        "permission_scope": permission_scope,
        "profile_name": profile_name,
        "item_count": len(items),
        "snapshot_payload_hash": snapshot_payload_hash(items),
        "integrity_status": "valid",
        "sync_completed": True,
    }
    with connect_sqlite(database) as connection:
        apply_migrations(connection)
        project = connection.execute(
            "SELECT project_id FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if not project:
            raise ValueError(f"project not found: {project_code}")
        connection.execute(
            """
            UPDATE shared_knowledge_snapshot
            SET snapshot_status='superseded'
            WHERE project_id=? AND source_type=? AND snapshot_status='current' AND snapshot_id<>?
            """,
            (project["project_id"], source_type, snapshot_id),
        )
        connection.execute(
            """
            INSERT INTO shared_knowledge_snapshot (
              snapshot_id,project_id,source_type,source_id,content_hash,server_schema,
              snapshot_status,fetched_at,metadata_json
            ) VALUES (?,?,?,?,?,?,'current',?,?)
            ON CONFLICT(snapshot_id) DO UPDATE SET
              snapshot_status='current',fetched_at=excluded.fetched_at,
              metadata_json=excluded.metadata_json
            """,
            (
                snapshot_id,
                project["project_id"],
                source_type,
                source_id,
                content_hash,
                server_schema,
                fetched_at,
                dump_json(snapshot_metadata),
            ),
        )
        connection.execute(
            "DELETE FROM shared_knowledge_snapshot_item WHERE snapshot_id=?", (snapshot_id,)
        )
        connection.executemany(
            """
            INSERT INTO shared_knowledge_snapshot_item (
              snapshot_id,item_type,item_id,item_hash,payload_json
            ) VALUES (?,?,?,?,?)
            """,
            [
                (snapshot_id, item_type, item_id, item_hash, dump_json(payload))
                for item_type, item_id, item_hash, payload in items
            ],
        )
        connection.commit()
    return snapshot_id


def sync(
    database: Path,
    project_code: str,
    connection,
    *,
    schema: str,
    package_ids: list[str],
    catalog_ids: list[str],
    topics: set[str],
    criteria: dict[str, Any] | None = None,
    permission_scopes: dict[str, list[str]] | None = None,
    profile_name: str = "",
) -> dict[str, Any]:
    validate_schema(schema)
    criteria = criteria or {}
    permission_scopes = permission_scopes or {}
    package_results = []
    packages = select_packages(
        connection,
        schema,
        package_ids,
        criteria=criteria,
        permission_scopes=permission_scopes.get("knowledge_package", []),
    )
    for package in packages:
        payload = build_pack_snapshot(connection, schema, package)
        source_corpus_type = payload["corpus"].get(
            "source_corpus_type", "legacy_unspecified"
        )
        requires_capabilities = source_corpus_type == "standard_solution"
        if not payload["corpus"]["blocks"] or (
            requires_capabilities and not payload["capabilities"]
        ):
            raise ValueError(
                f"knowledge package {payload['package_id']} does not satisfy its corpus/capability contract"
            )
        if requires_capabilities:
            require_complete_standard_solution_coverage(
                payload, context=f"服务器标准知识包 {payload['package_id']}"
            )
        local_result = import_local_pack(database, payload)
        items = [
            ("corpus_block", block["block_id"], block["text_hash"], block)
            for block in payload["corpus"]["blocks"]
        ] + [
            (
                "product_capability",
                capability["capability_id"],
                sha256_text(canonical_json(capability)),
                capability,
            )
            for capability in payload["capabilities"]
        ]
        snapshot_id = record_snapshot(
            database,
            project_code,
            source_type="knowledge_package",
            source_id=payload["package_id"],
            content_hash=payload["server_content_hash"],
            server_schema=schema,
            items=items,
            metadata={
                "title": payload["title"],
                "blocks": len(payload["corpus"]["blocks"]),
                "capabilities": len(payload["capabilities"]),
                "package_kind": payload["package_kind"],
                "source_corpus_type": source_corpus_type,
                "review_summary": payload.get("review_summary", {}),
            },
            permission_scope=payload["permission_scope"],
            profile_name=profile_name,
        )
        package_results.append({**local_result, "snapshot_id": snapshot_id})
    catalog_results = []
    catalogs = select_catalogs(
        connection,
        schema,
        catalog_ids,
        criteria=criteria,
        permission_scopes=permission_scopes.get("policy_catalog", []),
    )
    for catalog in catalogs:
        payload = build_catalog_snapshot(connection, schema, catalog)
        if not payload["records"]:
            raise ValueError(f"policy catalog {payload['catalog_id']} has zero records")
        local_result = import_local_catalog(database, payload)
        snapshot_id = record_snapshot(
            database,
            project_code,
            source_type="policy_catalog",
            source_id=payload["catalog_id"],
            content_hash=payload["content_hash"],
            server_schema=schema,
            items=[
                (
                    "policy_catalog_entry",
                    record["catalog_entry_id"],
                    record["row_hash"],
                    record,
                )
                for record in payload["records"]
            ],
            metadata={
                "title": payload["title"],
                "records": len(payload["records"]),
                "candidate_only": True,
            },
            permission_scope=payload["permission_scope"],
            profile_name=profile_name,
        )
        catalog_results.append({**local_result, "snapshot_id": snapshot_id})
    policy_payload = build_policy_snapshot(connection, schema, topics)
    policy_result = {"policies": 0, "clauses": 0}
    policy_snapshot_id = ""
    if policy_payload["policies"]:
        policy_result = ingest_local_policies(database, policy_payload)
        policy_items = [
            (
                "policy_clause",
                clause["clause_id"],
                clause["text_hash"],
                {"policy_id": policy["policy_id"], **clause},
            )
            for policy in policy_payload["policies"]
            for clause in policy["clauses"]
        ]
        policy_source_id = stable_id("POLICYRELEASE", policy_payload["content_hash"])
        policy_snapshot_id = record_snapshot(
            database,
            project_code,
            source_type="policy_release",
            source_id=policy_source_id,
            content_hash=policy_payload["content_hash"],
            server_schema=schema,
            items=policy_items,
            metadata={"topics": sorted(topics), **policy_result},
            permission_scope=(permission_scopes.get("policy_release") or ["public_policy_reference"])[0],
            profile_name=profile_name,
        )
    selected_package_ids = [package["package_id"] for package in packages]
    selected_catalog_ids = [catalog["catalog_id"] for catalog in catalogs]
    validation = validate_snapshots(
        database,
        project_code,
        package_ids=selected_package_ids,
        catalog_ids=selected_catalog_ids,
        permission_scopes=permission_scopes,
        allow_stale=False,
    )
    return {
        "database": str(database.resolve()),
        "project_code": project_code,
        "knowledge_packages": package_results,
        "policy_catalogs": catalog_results,
        "policy": {**policy_result, "snapshot_id": policy_snapshot_id},
        "server_schema": schema,
        "selected_package_ids": selected_package_ids,
        "selected_catalog_ids": selected_catalog_ids,
        "snapshot_validation": validation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--package-id", action="append", default=[])
    parser.add_argument("--catalog-id", action="append", default=[])
    parser.add_argument("--policy-topic", action="append", default=[])
    parser.add_argument("--output", type=Path)
    add_connection_arguments(parser)
    args = parser.parse_args()
    with connect_postgres(args) as connection:
        result = sync(
            args.sqlite_database,
            args.project_code,
            connection,
            schema=args.schema,
            package_ids=args.package_id,
            catalog_ids=args.catalog_id,
            topics=set(args.policy_topic),
        )
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
