#!/usr/bin/env python3
"""Import a reviewed standard-solution or reference-corpus pack into SQLite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect, dump_json, load_json, now_iso, sha256_text, stable_id
from standard_solution_coverage import require_complete_standard_solution_coverage


PACK_CONTRACTS = {
    "standard_solution": {
        "package_kind": "standard_solution",
        "source_role": "standard_solution",
        "source_class": "company_standard_solution",
        "requires_capabilities": True,
    },
    "reference_feasibility": {
        "package_kind": "reference_corpus",
        "source_role": "reference_feasibility",
        "source_class": "company_reference_feasibility",
        "requires_capabilities": False,
    },
    "generic_reference": {
        "package_kind": "reference_corpus",
        "source_role": "generic_reference",
        "source_class": "company_generic_reference",
        "requires_capabilities": False,
    },
}


def validate_pack(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") not in {"1.0", "1.1"} or not payload.get("package_id"):
        raise ValueError("unsupported or incomplete standard knowledge pack")
    corpus = payload.get("corpus")
    if not isinstance(corpus, dict) or not isinstance(corpus.get("blocks"), list):
        raise ValueError("standard knowledge pack is missing corpus blocks")
    if not isinstance(payload.get("capabilities"), list):
        raise ValueError("knowledge pack capabilities must be an array")
    if payload.get("permission_scope") != "internal_company_reuse":
        raise ValueError("knowledge pack permission_scope is not approved")
    source_roles = {item.get("role") for item in payload.get("source_files", [])}
    source_corpus_type = str(corpus.get("source_corpus_type") or "")
    if not source_corpus_type and "standard_solution" in source_roles:
        source_corpus_type = "standard_solution"
        corpus["source_corpus_type"] = source_corpus_type
    contract = PACK_CONTRACTS.get(source_corpus_type)
    if contract is None:
        raise ValueError(f"unsupported source_corpus_type: {source_corpus_type}")
    if payload.get("package_kind", contract["package_kind"]) != contract["package_kind"]:
        raise ValueError("package_kind conflicts with source_corpus_type")
    sources = {item.get("role"): item for item in payload.get("source_files", [])}
    if contract["source_role"] not in sources:
        raise ValueError(f"{contract['source_role']} source metadata is required")
    if contract["requires_capabilities"] and not payload["capabilities"]:
        raise ValueError("standard_solution pack must contain capabilities")
    if not contract["requires_capabilities"] and payload["capabilities"]:
        raise ValueError("reference corpus pack must not contain product capabilities")
    if source_corpus_type == "standard_solution":
        if payload.get("schema_version") != "1.1":
            raise ValueError(
                "standard_solution pack schema_version must be 1.1; rebuild the package"
            )
        require_complete_standard_solution_coverage(payload, context="待导入的标准知识包")
    return contract


def import_pack(database: Path, payload: dict[str, Any]) -> dict[str, Any]:
    contract = validate_pack(payload)
    timestamp = now_iso()
    corpus = payload["corpus"]
    sources = {item["role"]: item for item in payload.get("source_files", [])}
    primary_source = sources[contract["source_role"]]
    source_id = stable_id("SHAREDSOURCE", primary_source["sha256"])
    corpus_document_id = str(corpus["document_id"])
    package_content_hash = str(
        payload.get("server_content_hash")
        or sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    )
    with connect(database.resolve()) as conn:
        applied_migrations = apply_migrations(conn)
        conn.execute(
            """
            INSERT INTO source_document (
              source_id,project_id,source_scope,source_class,file_name,file_type,source_path,
              sha256,usage_scope,restriction_note,contains_personal_data,verification_status,
              imported_at,metadata_json
            ) VALUES (?,NULL,'shared',?,?,?,'',?,? ,?,0,'verified',?,?)
            ON CONFLICT(source_id) DO UPDATE SET
              file_name=excluded.file_name,sha256=excluded.sha256,usage_scope=excluded.usage_scope,
              restriction_note=excluded.restriction_note,metadata_json=excluded.metadata_json
            """,
            (
                source_id,
                contract["source_class"],
                primary_source["file_name"],
                Path(primary_source["file_name"]).suffix.lstrip(".").upper() or "UNKNOWN",
                primary_source["sha256"],
                payload["permission_scope"],
                "Shared reviewed knowledge; never establishes customer facts or expands project scope.",
                timestamp,
                dump_json(
                    {
                        "package_id": payload["package_id"],
                        "title": payload.get("title", ""),
                        "version": corpus.get("version", ""),
                        "package_kind": contract["package_kind"],
                        "source_corpus_type": corpus.get("source_corpus_type", ""),
                    }
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO corpus_document (
              corpus_document_id,source_id,document_type,jurisdiction_code,project_type,
              source_corpus_type,quality_level,permission_scope,review_status,version,
              created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(corpus_document_id) DO UPDATE SET
              source_id=excluded.source_id,document_type=excluded.document_type,
              project_type=excluded.project_type,quality_level=excluded.quality_level,
              source_corpus_type=excluded.source_corpus_type,
              permission_scope=excluded.permission_scope,review_status=excluded.review_status,
              version=excluded.version,updated_at=excluded.updated_at
            """,
            (
                corpus_document_id,
                source_id,
                corpus.get("document_type", "feasibility_study"),
                "",
                corpus.get("project_type", "hospital_informationization"),
                corpus.get("source_corpus_type", "standard_solution"),
                corpus.get("quality_level", "B"),
                payload["permission_scope"],
                corpus.get("review_status", "approved"),
                payload["package_id"],
                timestamp,
                timestamp,
            ),
        )
        block_count = 0
        for block in corpus["blocks"]:
            if block.get("review_status") not in {"approved", "prohibited", "retired"}:
                raise ValueError(f"block {block.get('block_id')} lacks a completed review status")
            conn.execute(
                """
                INSERT INTO corpus_block (
                  block_id,corpus_document_id,source_location,section_role,module_code,clean_text,
                  reuse_class,quality_level,applicable_document_types_json,
                  applicable_project_types_json,prerequisites_json,variable_slots_json,
                  forbidden_terms_json,length_band,review_status,text_hash,created_at,updated_at,
                  heading_path_json,content_type,semantic_section,content_slot,source_order,
                  adaptation_mode,assessment_targets_json,construction_scope_tags_json,
                  content_format,content_payload_json,asset_manifest_json,visible_text_hash,
                  source_section_id,chunk_index,source_is_heading
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(block_id) DO UPDATE SET
                  corpus_document_id=excluded.corpus_document_id,source_location=excluded.source_location,
                  section_role=excluded.section_role,module_code=excluded.module_code,
                  clean_text=excluded.clean_text,reuse_class=excluded.reuse_class,
                  quality_level=excluded.quality_level,prerequisites_json=excluded.prerequisites_json,
                  variable_slots_json=excluded.variable_slots_json,
                  forbidden_terms_json=excluded.forbidden_terms_json,
                  review_status=excluded.review_status,text_hash=excluded.text_hash,
                  heading_path_json=excluded.heading_path_json,
                  content_type=excluded.content_type,
                  semantic_section=excluded.semantic_section,
                  content_slot=excluded.content_slot,
                  source_order=excluded.source_order,
                  adaptation_mode=excluded.adaptation_mode,
                  assessment_targets_json=excluded.assessment_targets_json,
                  construction_scope_tags_json=excluded.construction_scope_tags_json,
                  content_format=excluded.content_format,
                  content_payload_json=excluded.content_payload_json,
                  asset_manifest_json=excluded.asset_manifest_json,
                  visible_text_hash=excluded.visible_text_hash,
                  source_section_id=excluded.source_section_id,
                  chunk_index=excluded.chunk_index,
                  source_is_heading=excluded.source_is_heading,
                  updated_at=excluded.updated_at
                """,
                (
                    block["block_id"], corpus_document_id,
                    block.get("source_location", ""),
                    block["section_role"], block.get("module_code", ""), block["clean_text"],
                    block["reuse_class"], block.get("quality_level", "B"),
                    dump_json([corpus.get("document_type", "feasibility_study")]),
                    dump_json([corpus.get("project_type", "hospital_informationization")]),
                    dump_json(block.get("prerequisites", [])),
                    dump_json(block.get("variable_slots", [])),
                    dump_json(block.get("forbidden_terms", [])),
                    "", block["review_status"], sha256_text(block["clean_text"]), timestamp, timestamp,
                    dump_json(block.get("heading_path", [])),
                    block.get("content_type", "construction_solution"),
                    block.get("semantic_section", "application_software_solution"),
                    block.get("content_slot", block.get("module_code", "")),
                    int(block.get("source_order", block_count + 1)),
                    block.get("adaptation_mode", "parameterized"),
                    dump_json(block.get("assessment_targets", [])),
                    dump_json(block.get("construction_scope_tags", [])),
                    block.get("content_format", "plain_text"),
                    dump_json(block.get("content_payload", {})),
                    dump_json(block.get("asset_manifest", [])),
                    block.get("visible_text_hash", block.get("text_hash", sha256_text(block["clean_text"]))),
                    block.get("source_section_id", ""),
                    int(block.get("chunk_index", 0)),
                    int(bool(block.get("source_is_heading", False))),
                ),
            )
            block_count += 1
        capability_count = 0
        relation_count = 0
        for capability in payload["capabilities"]:
            if capability.get("review_status") != "approved":
                raise ValueError(f"capability {capability.get('capability_id')} is not approved")
            conn.execute(
                """
                INSERT INTO product_capability (
                  capability_id,product_code,product_name,capability_name,capability_description,
                  prerequisites_json,interface_dependencies_json,exclusions_json,
                  applicable_versions_json,standard_block_ids_json,review_status,
                  category,module_name,selection_rules_json,source_location,block_match_scope
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(capability_id) DO UPDATE SET
                  product_code=excluded.product_code,product_name=excluded.product_name,
                  capability_name=excluded.capability_name,
                  capability_description=excluded.capability_description,
                  prerequisites_json=excluded.prerequisites_json,
                  interface_dependencies_json=excluded.interface_dependencies_json,
                  exclusions_json=excluded.exclusions_json,
                  applicable_versions_json=excluded.applicable_versions_json,
                  standard_block_ids_json=excluded.standard_block_ids_json,
                  category=excluded.category,module_name=excluded.module_name,
                  selection_rules_json=excluded.selection_rules_json,
                  source_location=excluded.source_location,
                  block_match_scope=excluded.block_match_scope,
                  review_status=excluded.review_status
                """,
                (
                    capability["capability_id"], capability["product_code"], capability["product_name"],
                    capability["capability_name"], capability["capability_description"],
                    dump_json(capability.get("prerequisites", [])),
                    dump_json(capability.get("interface_dependencies", [])),
                    dump_json(capability.get("exclusions", [])),
                    dump_json(capability.get("applicable_versions", [])),
                    dump_json(capability.get("standard_block_ids", [])),
                    "approved",
                    capability.get("category", ""), capability.get("module_name", ""),
                    dump_json(capability.get("selection_rules", [])),
                    capability.get("source_location", ""),
                    capability.get("block_match_scope", ""),
                ),
            )
            conn.execute(
                """
                DELETE FROM capability_solution_block_relation
                WHERE source_package_id=? AND capability_id=?
                """,
                (payload["package_id"], capability["capability_id"]),
            )
            relations = capability.get("standard_block_relations")
            if not isinstance(relations, list):
                relations = [
                    {
                        "block_id": block_id,
                        "priority": index,
                        "relation_order": index,
                        "verbatim_eligible": True,
                    }
                    for index, block_id in enumerate(
                        capability.get("standard_block_ids", []), start=1
                    )
                ]
            for index, relation in enumerate(relations, start=1):
                block_id = str(relation.get("block_id") or "")
                if not block_id:
                    raise ValueError(
                        f"capability {capability['capability_id']} has a relation without block_id"
                    )
                conn.execute(
                    """
                    INSERT INTO capability_solution_block_relation (
                      source_package_id,package_content_hash,capability_id,block_id,
                      relation_type,priority,review_status,root_heading_path_json,
                      relation_order,verbatim_eligible
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        payload["package_id"],package_content_hash,capability["capability_id"],
                        block_id,relation.get("relation_type", "standard_description"),
                        int(relation.get("priority") or index),
                        relation.get("review_status", "approved"),
                        dump_json(relation.get("root_heading_path", [])),
                        int(relation.get("relation_order") or index),
                        1 if relation.get("verbatim_eligible", True) else 0,
                    ),
                )
                relation_count += 1
            capability_count += 1
        conn.commit()
    return {
        "database": str(database.resolve()),
        "package_id": payload["package_id"],
        "source_id": source_id,
        "corpus_document_id": corpus_document_id,
        "blocks_imported": block_count,
        "capabilities_imported": capability_count,
        "capability_block_relations_imported": relation_count,
        "package_kind": contract["package_kind"],
        "source_corpus_type": corpus.get("source_corpus_type", ""),
        "applied_migrations": applied_migrations,
        "scope_policy": "shared capabilities may only map to existing customer scope",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("knowledge_pack", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = import_pack(args.database, load_json(args.knowledge_pack))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
