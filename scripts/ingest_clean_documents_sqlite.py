#!/usr/bin/env python3
"""Ingest cleaned document blocks into the local SQLite knowledge base."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect, dump_json, load_json, now_iso, stable_id


VALID_LEVELS = {"A", "B", "C", "D"}
VALID_REVIEW_STATUSES = {"pending", "approved", "retired", "prohibited"}


def validate_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    document = payload.get("document")
    blocks = payload.get("blocks")
    if not isinstance(document, dict) or not isinstance(blocks, list):
        raise ValueError("input must contain one document object and one blocks array")
    required_document_fields = (
        "document_id",
        "project_code",
        "title",
        "source_path",
        "source_sha256",
        "source_type",
    )
    missing = [field for field in required_document_fields if not document.get(field)]
    if missing:
        raise ValueError(f"document is missing required fields: {', '.join(missing)}")
    seen_block_ids: set[str] = set()
    for index, block in enumerate(blocks, start=1):
        if not isinstance(block, dict):
            raise ValueError(f"block {index} must be an object")
        for field in ("block_id", "source_location", "clean_text", "clean_text_sha256"):
            if not block.get(field):
                raise ValueError(f"block {index} is missing required field: {field}")
        if block["block_id"] in seen_block_ids:
            raise ValueError(f"duplicate block_id: {block['block_id']}")
        seen_block_ids.add(block["block_id"])
    return document, blocks


def section_role(block: dict[str, Any]) -> str:
    if str(block.get("section_role") or "").strip():
        return str(block["section_role"])
    heading_path = block.get("heading_path")
    if isinstance(heading_path, list) and heading_path:
        return str(heading_path[-1])
    return str(block.get("block_type") or "unclassified")


def ingest_payload(
    database: Path,
    payload: dict[str, Any],
    *,
    quality_level: str = "C",
    reuse_class: str = "C",
    permission_scope: str = "project_only",
    review_status: str = "pending",
    source_class: str = "project_material",
) -> dict[str, Any]:
    if quality_level not in VALID_LEVELS:
        raise ValueError(f"quality_level must be one of {sorted(VALID_LEVELS)}")
    if reuse_class not in VALID_LEVELS:
        raise ValueError(f"reuse_class must be one of {sorted(VALID_LEVELS)}")
    if review_status not in VALID_REVIEW_STATUSES:
        raise ValueError(
            f"review_status must be one of {sorted(VALID_REVIEW_STATUSES)}"
        )
    if not permission_scope.strip():
        raise ValueError("permission_scope must not be empty")
    if not source_class.strip():
        raise ValueError("source_class must not be empty")

    document, blocks = validate_payload(payload)
    timestamp = now_iso()
    with connect(database.resolve()) as conn:
        applied_migrations = apply_migrations(conn)
        project = conn.execute(
            """
            SELECT project_id, project_code, document_type, jurisdiction_code, project_type
            FROM project WHERE project_code=?
            """,
            (document["project_code"],),
        ).fetchone()
        if project is None:
            raise RuntimeError(
                f"project_code {document['project_code']} is not initialized in {database}"
            )

        source_id = stable_id(
            "SOURCE", project["project_id"], document["source_sha256"]
        )
        corpus_document_id = str(document["document_id"])
        existing_source = conn.execute(
            "SELECT 1 FROM source_document WHERE source_id=?", (source_id,)
        ).fetchone()
        existing_document = conn.execute(
            "SELECT 1 FROM corpus_document WHERE corpus_document_id=?",
            (corpus_document_id,),
        ).fetchone()
        existing_blocks = {
            row["block_id"]
            for row in conn.execute(
                "SELECT block_id FROM corpus_block WHERE corpus_document_id=?",
                (corpus_document_id,),
            )
        }

        source_path = Path(str(document["source_path"]))
        source_metadata = {
            "title": document["title"],
            "cleaning_version": document.get("cleaning_version", ""),
            "cleaning_method": document.get("cleaning_method", ""),
            "clean_document_id": corpus_document_id,
            "clean_metadata": document.get("metadata", {}),
        }
        conn.execute(
            """
            INSERT INTO source_document (
              source_id, project_id, source_scope, source_class, file_name, file_type,
              source_path, sha256, usage_scope, restriction_note,
              contains_personal_data, verification_status, imported_at, metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(source_id) DO UPDATE SET
              source_class=excluded.source_class,
              file_name=excluded.file_name,
              file_type=excluded.file_type,
              source_path=excluded.source_path,
              sha256=excluded.sha256,
              usage_scope=excluded.usage_scope,
              restriction_note=excluded.restriction_note,
              contains_personal_data=excluded.contains_personal_data,
              metadata_json=excluded.metadata_json
            """,
            (
                source_id,
                project["project_id"],
                "project",
                source_class,
                source_path.name or str(document["title"]),
                str(document["source_type"]),
                str(document["source_path"]),
                str(document["source_sha256"]),
                permission_scope,
                "Contains personal data; project-only use."
                if document.get("contains_personal_data")
                else "",
                int(bool(document.get("contains_personal_data"))),
                "registered",
                timestamp,
                dump_json(source_metadata),
            ),
        )
        conn.execute(
            """
            INSERT INTO corpus_document (
              corpus_document_id, source_id, document_type, jurisdiction_code,
              project_type, source_corpus_type, quality_level, permission_scope, review_status,
              version, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(corpus_document_id) DO UPDATE SET
              source_id=excluded.source_id,
              document_type=excluded.document_type,
              jurisdiction_code=excluded.jurisdiction_code,
              project_type=excluded.project_type,
              source_corpus_type=excluded.source_corpus_type,
              quality_level=excluded.quality_level,
              permission_scope=excluded.permission_scope,
              version=excluded.version,
              updated_at=excluded.updated_at
            """,
            (
                corpus_document_id,
                source_id,
                document.get("document_type", project["document_type"]),
                project["jurisdiction_code"],
                document.get("project_type", project["project_type"]),
                document.get("source_corpus_type", "legacy_unspecified"),
                quality_level,
                permission_scope,
                review_status,
                str(document.get("cleaning_version", "")),
                timestamp,
                timestamp,
            ),
        )

        for block in blocks:
            conn.execute(
                """
                INSERT INTO corpus_block (
                  block_id, corpus_document_id, source_location, section_role,
                  module_code, clean_text, reuse_class, quality_level,
                  applicable_document_types_json, applicable_project_types_json,
                  prerequisites_json, variable_slots_json, forbidden_terms_json,
                  length_band, review_status, text_hash, created_at, updated_at,
                  heading_path_json, content_type, semantic_section, content_slot,
                  source_order, adaptation_mode, assessment_targets_json,
                  construction_scope_tags_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(block_id) DO UPDATE SET
                  corpus_document_id=excluded.corpus_document_id,
                  source_location=excluded.source_location,
                  section_role=excluded.section_role,
                  module_code=excluded.module_code,
                  clean_text=excluded.clean_text,
                  reuse_class=excluded.reuse_class,
                  quality_level=excluded.quality_level,
                  applicable_document_types_json=excluded.applicable_document_types_json,
                  applicable_project_types_json=excluded.applicable_project_types_json,
                  prerequisites_json=excluded.prerequisites_json,
                  variable_slots_json=excluded.variable_slots_json,
                  forbidden_terms_json=excluded.forbidden_terms_json,
                  heading_path_json=excluded.heading_path_json,
                  content_type=excluded.content_type,
                  semantic_section=excluded.semantic_section,
                  content_slot=excluded.content_slot,
                  source_order=excluded.source_order,
                  adaptation_mode=excluded.adaptation_mode,
                  assessment_targets_json=excluded.assessment_targets_json,
                  construction_scope_tags_json=excluded.construction_scope_tags_json,
                  text_hash=excluded.text_hash,
                  updated_at=excluded.updated_at
                """,
                (
                    block["block_id"],
                    corpus_document_id,
                    str(block["source_location"]),
                    section_role(block),
                    str(block.get("module_code", "")),
                    str(block["clean_text"]),
                    str(block.get("reuse_class", reuse_class)),
                    str(block.get("quality_level", quality_level)),
                    dump_json(block.get("applicable_document_types", [document.get("document_type", project["document_type"])])),
                    dump_json(block.get("applicable_project_types", [document.get("project_type", project["project_type"])])),
                    dump_json(block.get("prerequisites", [])),
                    dump_json(block.get("variable_slots", [])),
                    dump_json(block.get("forbidden_terms", [])),
                    str(block.get("length_band", "")),
                    str(block.get("review_status", review_status)),
                    str(block["clean_text_sha256"]),
                    timestamp,
                    timestamp,
                    dump_json(block.get("heading_path", [])),
                    str(block.get("content_type", "legacy_unspecified")),
                    str(block.get("semantic_section", "")),
                    str(block.get("content_slot", "")),
                    int(block.get("source_order", block.get("block_index", 0))),
                    str(block.get("adaptation_mode", "structure_only")),
                    dump_json(block.get("assessment_targets", [])),
                    dump_json(block.get("construction_scope_tags", [])),
                ),
            )
        conn.commit()

        total_blocks = conn.execute(
            "SELECT COUNT(*) FROM corpus_block WHERE corpus_document_id=?",
            (corpus_document_id,),
        ).fetchone()[0]

    incoming_ids = {str(block["block_id"]) for block in blocks}
    return {
        "database": str(database.resolve()),
        "project_code": document["project_code"],
        "source_id": source_id,
        "corpus_document_id": corpus_document_id,
        "source_created": existing_source is None,
        "document_created": existing_document is None,
        "blocks_created": len(incoming_ids - existing_blocks),
        "blocks_updated": len(incoming_ids & existing_blocks),
        "blocks_total": total_blocks,
        "review_status_policy": "preserve-existing-on-reimport",
        "source_class": source_class,
        "applied_migrations": applied_migrations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("input", type=Path, help="Clean-block JSON produced by the extractor")
    parser.add_argument("--quality-level", choices=sorted(VALID_LEVELS), default="C")
    parser.add_argument("--reuse-class", choices=sorted(VALID_LEVELS), default="C")
    parser.add_argument("--permission-scope", default="project_only")
    parser.add_argument("--source-class", default="project_material")
    parser.add_argument(
        "--review-status", choices=sorted(VALID_REVIEW_STATUSES), default="pending"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = ingest_payload(
        args.database,
        load_json(args.input),
        quality_level=args.quality_level,
        reuse_class=args.reuse_class,
        permission_scope=args.permission_scope,
        review_status=args.review_status,
        source_class=args.source_class,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
