#!/usr/bin/env python3
"""Import a reviewed standard knowledge pack into PostgreSQL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from import_standard_knowledge_pack import validate_pack
from knowledge_db import load_json, now_iso, sha256_text, stable_id
from postgres_knowledge_db import add_connection_arguments, apply_migrations, canonical_json, connect, jsonb, validate_schema


IMPORTER_VERSION = "standard-pack-postgres-v1"


def source_type(file_name: str) -> str:
    suffix = Path(file_name).suffix.lower().lstrip(".")
    return suffix.upper() or "UNKNOWN"


def retire_superseded_packages(
    cursor,
    *,
    schema: str,
    package_id: str,
    solution_source_id: str,
    publish: bool,
) -> int:
    if not publish:
        return 0
    validate_schema(schema)
    cursor.execute(
        f"""
        UPDATE {schema}.knowledge_package AS package
        SET package_status='retired',retired_at=NOW()
        WHERE package.package_id<>%s
          AND package.package_status='published'
          AND EXISTS (
            SELECT 1
            FROM {schema}.package_source AS source
            WHERE source.package_id=package.package_id
              AND source.source_id=%s
              AND source.source_role='standard_solution'
          )
        """,
        (package_id, solution_source_id),
    )
    return cursor.rowcount


def import_pack(connection, payload: dict[str, Any], *, publish: bool, schema: str) -> dict[str, Any]:
    validate_pack(payload)
    validate_schema(schema)
    migrations = apply_migrations(connection, schema=schema)
    package_id = payload["package_id"]
    package_hash = sha256_text(canonical_json(payload))
    package_status = "published" if publish else "reviewed"
    import_run_id = stable_id("PGIMPORT", IMPORTER_VERSION, package_id, package_hash)
    timestamp = now_iso()
    sources = {item["role"]: item for item in payload.get("source_files", [])}
    solution = sources.get("standard_solution")
    if not solution:
        raise ValueError("standard_solution source metadata is required")
    solution_source_id = stable_id("SHAREDSOURCE", solution["sha256"])
    corpus = payload["corpus"]
    block_ids = {block["block_id"] for block in corpus["blocks"]}
    approved_block_ids = {
        block["block_id"]
        for block in corpus["blocks"]
        if block.get("review_status") == "approved"
    }
    with connection.cursor() as cursor:
        retired_package_count = retire_superseded_packages(
            cursor,
            schema=schema,
            package_id=package_id,
            solution_source_id=solution_source_id,
            publish=publish,
        )
        cursor.execute(
            f"""
            INSERT INTO {schema}.import_run (
              import_run_id,target_type,target_id,input_hash,importer_version,run_status
            ) VALUES (%s,'knowledge_package',%s,%s,%s,'running')
            ON CONFLICT (target_type,target_id,input_hash,importer_version) DO UPDATE SET
              run_status='running',error_details='{{}}'::jsonb,started_at=NOW(),completed_at=NULL
            """,
            (import_run_id, package_id, package_hash, IMPORTER_VERSION),
        )
        cursor.execute(
            f"""
            INSERT INTO {schema}.knowledge_package (
              package_id,schema_version,title,permission_scope,package_status,
              content_hash,review_summary,metadata,published_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (package_id) DO UPDATE SET
              title=EXCLUDED.title,permission_scope=EXCLUDED.permission_scope,
              package_status=EXCLUDED.package_status,content_hash=EXCLUDED.content_hash,
              review_summary=EXCLUDED.review_summary,metadata=EXCLUDED.metadata,
              published_at=EXCLUDED.published_at,retired_at=NULL
            """,
            (
                package_id,
                payload["schema_version"],
                payload.get("title", package_id),
                payload["permission_scope"],
                package_status,
                package_hash,
                jsonb(payload.get("review_summary", {})),
                jsonb({}),
                timestamp if publish else None,
            ),
        )
        for role, source in sources.items():
            source_id = stable_id("SHAREDSOURCE", source["sha256"])
            cursor.execute(
                f"""
                INSERT INTO {schema}.source_document (
                  source_id,source_scope,file_name,file_type,source_uri,source_sha256,
                  permission_scope,verification_status,contains_personal_data,metadata
                ) VALUES (%s,'company_shared',%s,%s,'',%s,%s,'verified',FALSE,%s)
                ON CONFLICT (source_id) DO UPDATE SET
                  file_name=EXCLUDED.file_name,file_type=EXCLUDED.file_type,
                  permission_scope=EXCLUDED.permission_scope,
                  verification_status=EXCLUDED.verification_status,
                  metadata=EXCLUDED.metadata,updated_at=NOW()
                """,
                (
                    source_id,
                    source["file_name"],
                    source_type(source["file_name"]),
                    source["sha256"],
                    payload["permission_scope"],
                    jsonb({"source_role": role}),
                ),
            )
            cursor.execute(
                f"""
                INSERT INTO {schema}.package_source(package_id,source_id,source_role)
                VALUES (%s,%s,%s)
                ON CONFLICT (package_id,source_id,source_role) DO NOTHING
                """,
                (package_id, source_id, role),
            )
        cursor.execute(
            f"""
            INSERT INTO {schema}.corpus_document (
              corpus_document_id,package_id,source_id,document_type,jurisdiction_code,
              project_type,quality_level,permission_scope,review_status,version,metadata
            ) VALUES (%s,%s,%s,%s,'',%s,%s,%s,%s,%s,%s)
            ON CONFLICT (corpus_document_id) DO UPDATE SET
              package_id=EXCLUDED.package_id,source_id=EXCLUDED.source_id,
              document_type=EXCLUDED.document_type,project_type=EXCLUDED.project_type,
              quality_level=EXCLUDED.quality_level,permission_scope=EXCLUDED.permission_scope,
              review_status=EXCLUDED.review_status,version=EXCLUDED.version,
              metadata=EXCLUDED.metadata,updated_at=NOW()
            """,
            (
                corpus["document_id"],
                package_id,
                solution_source_id,
                corpus.get("document_type", "feasibility_study"),
                corpus.get("project_type", "hospital_informationization"),
                corpus.get("quality_level", "B"),
                payload["permission_scope"],
                corpus.get("review_status", "approved"),
                package_id,
                jsonb({}),
            ),
        )
        block_rows = []
        for block_index, block in enumerate(corpus["blocks"], 1):
            if block.get("review_status") not in {"approved", "prohibited", "retired"}:
                raise ValueError(f"block {block.get('block_id')} lacks a completed review status")
            block_rows.append(
                (
                    block["block_id"], corpus["document_id"], block_index,
                    block.get("source_location", ""), jsonb(block.get("heading_path", [])),
                    block["section_role"], block.get("module_code", ""), block["clean_text"],
                    block["reuse_class"], block.get("quality_level", "B"),
                    jsonb([corpus.get("document_type", "feasibility_study")]),
                    jsonb([corpus.get("project_type", "hospital_informationization")]),
                    jsonb(block.get("prerequisites", [])), jsonb(block.get("variable_slots", [])),
                    jsonb(block.get("forbidden_terms", [])), block.get("length_band", ""),
                    block["review_status"], sha256_text(block["clean_text"]),
                )
            )
        cursor.executemany(
            f"""
            INSERT INTO {schema}.corpus_block (
              block_id,corpus_document_id,block_index,source_location,heading_path,
              section_role,module_code,clean_text,reuse_class,quality_level,
              applicable_document_types,applicable_project_types,prerequisites,
              variable_slots,forbidden_terms,length_band,review_status,text_hash
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (block_id) DO UPDATE SET
              block_index=EXCLUDED.block_index,source_location=EXCLUDED.source_location,
              heading_path=EXCLUDED.heading_path,section_role=EXCLUDED.section_role,
              module_code=EXCLUDED.module_code,clean_text=EXCLUDED.clean_text,
              reuse_class=EXCLUDED.reuse_class,quality_level=EXCLUDED.quality_level,
              applicable_document_types=EXCLUDED.applicable_document_types,
              applicable_project_types=EXCLUDED.applicable_project_types,
              prerequisites=EXCLUDED.prerequisites,variable_slots=EXCLUDED.variable_slots,
              forbidden_terms=EXCLUDED.forbidden_terms,length_band=EXCLUDED.length_band,
              review_status=EXCLUDED.review_status,text_hash=EXCLUDED.text_hash,
              updated_at=NOW()
            """,
            block_rows,
        )
        capability_rows = []
        relation_rows = []
        for capability in payload["capabilities"]:
            if capability.get("review_status") != "approved":
                raise ValueError(f"capability {capability.get('capability_id')} is not approved")
            referenced_blocks = capability.get("standard_block_ids", [])
            missing_blocks = sorted(set(referenced_blocks).difference(block_ids))
            if missing_blocks:
                raise ValueError(
                    f"capability {capability['capability_id']} references missing blocks: {missing_blocks[:5]}"
                )
            unapproved_blocks = sorted(set(referenced_blocks).difference(approved_block_ids))
            if unapproved_blocks:
                raise ValueError(
                    f"capability {capability['capability_id']} references unapproved blocks: {unapproved_blocks[:5]}"
                )
            capability_rows.append(
                (
                    capability["capability_id"], package_id, capability["product_code"],
                    capability["product_name"], capability["capability_name"],
                    capability["capability_description"], capability.get("category", ""),
                    capability.get("module_name", ""), jsonb(capability.get("selection_rules", [])),
                    jsonb(capability.get("prerequisites", [])),
                    jsonb(capability.get("interface_dependencies", [])),
                    jsonb(capability.get("exclusions", [])),
                    jsonb(capability.get("applicable_versions", [])),
                    capability.get("source_location", ""),
                    capability.get("block_match_scope", ""),
                )
            )
            for priority, block_id in enumerate(referenced_blocks, 1):
                relation_rows.append(
                    (capability["capability_id"], block_id, priority)
                )
        cursor.executemany(
            f"""
            INSERT INTO {schema}.product_capability (
              capability_id,package_id,product_code,product_name,capability_name,
              capability_description,category,module_name,selection_rules,prerequisites,
              interface_dependencies,exclusions,applicable_versions,source_location,
              block_match_scope,review_status
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'approved')
            ON CONFLICT (capability_id) DO UPDATE SET
              package_id=EXCLUDED.package_id,product_code=EXCLUDED.product_code,
              product_name=EXCLUDED.product_name,capability_name=EXCLUDED.capability_name,
              capability_description=EXCLUDED.capability_description,category=EXCLUDED.category,
              module_name=EXCLUDED.module_name,selection_rules=EXCLUDED.selection_rules,
              prerequisites=EXCLUDED.prerequisites,
              interface_dependencies=EXCLUDED.interface_dependencies,
              exclusions=EXCLUDED.exclusions,applicable_versions=EXCLUDED.applicable_versions,
              source_location=EXCLUDED.source_location,
              block_match_scope=EXCLUDED.block_match_scope,
              review_status=EXCLUDED.review_status,
              updated_at=NOW()
            """,
            capability_rows,
        )
        capability_ids = [row[0] for row in capability_rows]
        if capability_ids:
            cursor.execute(
                f"DELETE FROM {schema}.capability_block WHERE capability_id = ANY(%s)",
                (capability_ids,),
            )
        if relation_rows:
            cursor.executemany(
                f"""
                INSERT INTO {schema}.capability_block (
                  capability_id,block_id,relation_type,priority,review_status
                ) VALUES (%s,%s,'standard_description',%s,'approved')
                """,
                relation_rows,
            )
        row_counts = {
            "corpus_blocks": len(corpus["blocks"]),
            "approved_corpus_blocks": sum(
                block.get("review_status") == "approved" for block in corpus["blocks"]
            ),
            "capabilities": len(payload["capabilities"]),
            "capability_block_relations": sum(
                len(capability.get("standard_block_ids", []))
                for capability in payload["capabilities"]
            ),
        }
        cursor.execute(
            f"""
            UPDATE {schema}.import_run
            SET run_status='completed',row_counts=%s,completed_at=NOW()
            WHERE import_run_id=%s
            """,
            (jsonb(row_counts), import_run_id),
        )
    connection.commit()
    return {
        "package_id": package_id,
        "package_status": package_status,
        "package_content_hash": package_hash,
        "blocks_imported": len(corpus["blocks"]),
        "blocks_published": row_counts["approved_corpus_blocks"] if publish else 0,
        "blocks_prohibited": len(corpus["blocks"]) - row_counts["approved_corpus_blocks"],
        "capabilities_imported": len(payload["capabilities"]),
        "retired_package_count": retired_package_count,
        "import_run_id": import_run_id,
        "applied_migrations": migrations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("knowledge_pack", type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--output", type=Path)
    add_connection_arguments(parser)
    args = parser.parse_args()
    payload = load_json(args.knowledge_pack)
    with connect(args) as connection:
        result = import_pack(connection, payload, publish=args.publish, schema=args.schema)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
