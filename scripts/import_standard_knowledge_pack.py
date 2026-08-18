#!/usr/bin/env python3
"""Import a reviewed standard knowledge pack into a project SQLite database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect, dump_json, load_json, now_iso, sha256_text, stable_id


def validate_pack(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != "1.0" or not payload.get("package_id"):
        raise ValueError("unsupported or incomplete standard knowledge pack")
    corpus = payload.get("corpus")
    if not isinstance(corpus, dict) or not isinstance(corpus.get("blocks"), list):
        raise ValueError("standard knowledge pack is missing corpus blocks")
    if not isinstance(payload.get("capabilities"), list):
        raise ValueError("standard knowledge pack is missing capabilities")
    if payload.get("permission_scope") != "internal_company_reuse":
        raise ValueError("standard knowledge pack permission_scope is not approved")


def import_pack(database: Path, payload: dict[str, Any]) -> dict[str, Any]:
    validate_pack(payload)
    timestamp = now_iso()
    corpus = payload["corpus"]
    sources = {item["role"]: item for item in payload.get("source_files", [])}
    solution = sources.get("standard_solution")
    if not solution:
        raise ValueError("standard_solution source metadata is required")
    source_id = stable_id("SHAREDSOURCE", solution["sha256"])
    corpus_document_id = str(corpus["document_id"])
    with connect(database.resolve()) as conn:
        applied_migrations = apply_migrations(conn)
        conn.execute(
            """
            INSERT INTO source_document (
              source_id,project_id,source_scope,source_class,file_name,file_type,source_path,
              sha256,usage_scope,restriction_note,contains_personal_data,verification_status,
              imported_at,metadata_json
            ) VALUES (?,NULL,'shared','company_standard_solution',?,'DOCX','',?,? ,?,0,'verified',?,?)
            ON CONFLICT(source_id) DO UPDATE SET
              file_name=excluded.file_name,sha256=excluded.sha256,usage_scope=excluded.usage_scope,
              restriction_note=excluded.restriction_note,metadata_json=excluded.metadata_json
            """,
            (
                source_id,
                solution["file_name"],
                solution["sha256"],
                payload["permission_scope"],
                "Shared internal standard; never establishes customer facts or project scope.",
                timestamp,
                dump_json(
                    {
                        "package_id": payload["package_id"],
                        "title": payload.get("title", ""),
                        "version": corpus.get("version", ""),
                    }
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO corpus_document (
              corpus_document_id,source_id,document_type,jurisdiction_code,project_type,
              quality_level,permission_scope,review_status,version,created_at,updated_at
            ) VALUES (?,?,?,'',?,?,?,?,?,?,?)
            ON CONFLICT(corpus_document_id) DO UPDATE SET
              source_id=excluded.source_id,document_type=excluded.document_type,
              project_type=excluded.project_type,quality_level=excluded.quality_level,
              permission_scope=excluded.permission_scope,review_status=excluded.review_status,
              version=excluded.version,updated_at=excluded.updated_at
            """,
            (
                corpus_document_id,
                source_id,
                corpus.get("document_type", "feasibility_study"),
                corpus.get("project_type", "hospital_informationization"),
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
                  heading_path_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(block_id) DO UPDATE SET
                  corpus_document_id=excluded.corpus_document_id,source_location=excluded.source_location,
                  section_role=excluded.section_role,module_code=excluded.module_code,
                  clean_text=excluded.clean_text,reuse_class=excluded.reuse_class,
                  quality_level=excluded.quality_level,prerequisites_json=excluded.prerequisites_json,
                  variable_slots_json=excluded.variable_slots_json,
                  forbidden_terms_json=excluded.forbidden_terms_json,
                  review_status=excluded.review_status,text_hash=excluded.text_hash,
                  heading_path_json=excluded.heading_path_json,
                  updated_at=excluded.updated_at
                """,
                (
                    block["block_id"], corpus_document_id,
                    " / ".join(block.get("heading_path", []))
                    + (f" [{block['source_location']}]" if block.get("source_location") else ""),
                    block["section_role"], block.get("module_code", ""), block["clean_text"],
                    block["reuse_class"], block.get("quality_level", "B"),
                    dump_json([corpus.get("document_type", "feasibility_study")]),
                    dump_json([corpus.get("project_type", "hospital_informationization")]),
                    dump_json(block.get("prerequisites", [])),
                    dump_json(block.get("variable_slots", [])),
                    dump_json(block.get("forbidden_terms", [])),
                    "", block["review_status"], sha256_text(block["clean_text"]), timestamp, timestamp,
                    dump_json(block.get("heading_path", [])),
                ),
            )
            block_count += 1
        capability_count = 0
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
            capability_count += 1
        conn.commit()
    return {
        "database": str(database.resolve()),
        "package_id": payload["package_id"],
        "source_id": source_id,
        "corpus_document_id": corpus_document_id,
        "blocks_imported": block_count,
        "capabilities_imported": capability_count,
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
