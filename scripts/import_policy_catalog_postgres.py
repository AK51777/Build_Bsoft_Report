#!/usr/bin/env python3
"""Import a reviewed department policy catalog into PostgreSQL."""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from knowledge_db import load_json, now_iso, stable_id
from postgres_knowledge_db import add_connection_arguments, apply_migrations, connect, jsonb, validate_schema


IMPORTER_VERSION = "policy-catalog-postgres-v2"
JURISDICTION_LEVELS = {"national", "province", "prefecture", "county", "unclassified"}
NATIONAL_AUTHORITY_LABELS = {"国家", "国家级", "中央", "国务院", "部委", "司局"}


def normalize_jurisdiction(record: dict[str, Any]) -> None:
    level = str(record.get("jurisdiction_level") or "").strip().casefold()
    authority = str(record.get("authority_level_label") or "").strip()
    if not level and authority in NATIONAL_AUTHORITY_LABELS:
        level = "national"
    if not level:
        level = "unclassified"
    if level not in JURISDICTION_LEVELS:
        raise ValueError(f"unsupported policy jurisdiction_level: {level}")
    code = str(record.get("jurisdiction_code") or "").strip()
    name = str(record.get("jurisdiction_name") or "").strip()
    if level == "national":
        code = code or "100000"
        name = name or "全国"
    record["jurisdiction_level"] = level
    record["jurisdiction_code"] = code
    record["jurisdiction_name"] = name


def normalize_catalog(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != "1.0" or not payload.get("catalog_id"):
        raise ValueError("unsupported or incomplete policy catalog")
    if payload.get("permission_scope") != "internal_company_reference":
        raise ValueError("policy catalog permission_scope is not approved")
    if not isinstance(payload.get("records"), list) or not payload["records"]:
        raise ValueError("policy catalog records are missing")
    normalized = copy.deepcopy(payload)
    records = normalized["records"]
    index_counts = Counter(str(record.get("source_index_no", "")).strip() for record in records)
    entry_id_counts = Counter(str(record.get("catalog_entry_id", "")).strip() for record in records)
    occurrences: defaultdict[str, int] = defaultdict(int)
    rows: set[int] = set()
    entry_ids: set[str] = set()
    for record in sorted(records, key=lambda item: (item.get("source_row", 0), str(item.get("title", "")))):
        normalize_jurisdiction(record)
        index_no = str(record.get("source_index_no", "")).strip()
        source_row = record.get("source_row")
        if not index_no:
            raise ValueError("missing policy source_index_no")
        if not isinstance(source_row, int) or source_row < 1 or source_row in rows:
            raise ValueError(f"duplicate or invalid policy source_row: {source_row}")
        if not str(record.get("title", "")).strip():
            raise ValueError(f"policy title is missing at row {source_row}")
        occurrences[index_no] += 1
        record["source_index_no"] = index_no
        record["index_occurrence"] = occurrences[index_no]
        record["index_conflict"] = index_counts[index_no] > 1
        original_id = str(record.get("catalog_entry_id", "")).strip()
        if not original_id or entry_id_counts[original_id] > 1:
            if original_id:
                record["legacy_catalog_entry_id"] = original_id
            record["catalog_entry_id"] = stable_id(
                "POLICYCATENTRY", normalized["catalog_id"], index_no, source_row
            )
        if record["catalog_entry_id"] in entry_ids:
            raise ValueError(f"duplicate policy catalog_entry_id: {record['catalog_entry_id']}")
        if not str(record.get("row_hash", "")).strip():
            raise ValueError(f"policy row_hash is missing at row {source_row}")
        entry_ids.add(record["catalog_entry_id"])
        rows.add(source_row)
    duplicate_groups = [count for count in index_counts.values() if count > 1]
    normalized["quality_summary"] = {
        **normalized.get("quality_summary", {}),
        "duplicate_index_groups": len(duplicate_groups),
        "duplicate_index_rows": sum(duplicate_groups),
    }
    return normalized


def validate_catalog(payload: dict[str, Any]) -> None:
    normalize_catalog(payload)


def import_catalog(connection, payload: dict[str, Any], *, publish: bool, schema: str) -> dict[str, Any]:
    payload = normalize_catalog(payload)
    validate_schema(schema)
    migrations = apply_migrations(connection, schema=schema)
    catalog_id = payload["catalog_id"]
    input_hash = payload["content_hash"]
    import_run_id = stable_id("PGIMPORT", IMPORTER_VERSION, catalog_id, input_hash)
    status = "published" if publish else "reviewed"
    timestamp = now_iso()
    source_file = payload["source_file"]
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {schema}.import_run (
              import_run_id,target_type,target_id,input_hash,importer_version,run_status
            ) VALUES (%s,'policy_catalog',%s,%s,%s,'running')
            ON CONFLICT (target_type,target_id,input_hash,importer_version) DO UPDATE SET
              run_status='running',error_details='{{}}'::jsonb,started_at=NOW(),completed_at=NULL
            """,
            (import_run_id, catalog_id, input_hash, IMPORTER_VERSION),
        )
        if publish:
            cursor.execute(
                f"""
                UPDATE {schema}.policy_catalog
                SET catalog_status='superseded',superseded_at=NOW()
                WHERE catalog_scope=%s AND catalog_status='published' AND catalog_id<>%s
                """,
                (payload["catalog_scope"], catalog_id),
            )
        cursor.execute(
            f"""
            INSERT INTO {schema}.policy_catalog (
              catalog_id,schema_version,catalog_scope,title,source_file_name,source_sha256,
              worksheet_name,permission_scope,catalog_status,record_count,content_hash,
              metadata,published_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (catalog_id) DO UPDATE SET
              title=EXCLUDED.title,permission_scope=EXCLUDED.permission_scope,
              catalog_status=EXCLUDED.catalog_status,record_count=EXCLUDED.record_count,
              content_hash=EXCLUDED.content_hash,metadata=EXCLUDED.metadata,
              published_at=EXCLUDED.published_at
            """,
            (
                catalog_id,
                payload["schema_version"],
                payload["catalog_scope"],
                payload["title"],
                source_file["file_name"],
                source_file["sha256"],
                payload["worksheet_name"],
                payload["permission_scope"],
                status,
                len(payload["records"]),
                payload.get("content_hash", input_hash),
                jsonb({"built_at": payload.get("built_at", "")}),
                timestamp if publish else None,
            ),
        )
        record_rows = []
        for record in payload["records"]:
            record_rows.append(
                (
                    record["catalog_entry_id"],
                    catalog_id,
                    record["source_row"],
                    record["source_index_no"],
                    record["index_occurrence"],
                    record["index_conflict"],
                    record["identity_key"],
                    record.get("catalog_group_code", ""),
                    record.get("catalog_group_name", ""),
                    record.get("authority_level_label", ""),
                    record["jurisdiction_level"],
                    record["jurisdiction_code"],
                    record["jurisdiction_name"],
                    record.get("category_name", ""),
                    record.get("keyword_text", ""),
                    jsonb(record.get("keyword_tags", [])),
                    record.get("document_no", ""),
                    record["title"],
                    record.get("publish_date") or None,
                    record.get("publish_date_raw", ""),
                    record.get("issuer", ""),
                    record.get("file_count"),
                    record.get("notes", ""),
                    record.get("external_url", ""),
                    record.get("verification_status", "unverified"),
                    record.get("entry_status", "active"),
                    record["row_hash"],
                    jsonb(
                        {
                            "legacy_catalog_entry_id": record.get("legacy_catalog_entry_id", ""),
                            "index_conflict": record["index_conflict"],
                        }
                    ),
                )
            )
        cursor.executemany(
            f"""
            INSERT INTO {schema}.policy_catalog_entry (
              catalog_entry_id,catalog_id,source_row,source_index_no,index_occurrence,index_conflict,identity_key,
              catalog_group_code,catalog_group_name,authority_level_label,
              jurisdiction_level,jurisdiction_code,jurisdiction_name,category_name,
              keyword_text,keyword_tags,document_no,title,publish_date,publish_date_raw,
              issuer,file_count,notes,external_url,verification_status,entry_status,row_hash,metadata
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (catalog_entry_id) DO UPDATE SET
              source_row=EXCLUDED.source_row,source_index_no=EXCLUDED.source_index_no,
              index_occurrence=EXCLUDED.index_occurrence,index_conflict=EXCLUDED.index_conflict,
              identity_key=EXCLUDED.identity_key,catalog_group_code=EXCLUDED.catalog_group_code,
              catalog_group_name=EXCLUDED.catalog_group_name,
              authority_level_label=EXCLUDED.authority_level_label,
              jurisdiction_level=EXCLUDED.jurisdiction_level,
              jurisdiction_code=EXCLUDED.jurisdiction_code,
              jurisdiction_name=EXCLUDED.jurisdiction_name,
              category_name=EXCLUDED.category_name,keyword_text=EXCLUDED.keyword_text,
              keyword_tags=EXCLUDED.keyword_tags,document_no=EXCLUDED.document_no,
              title=EXCLUDED.title,publish_date=EXCLUDED.publish_date,
              publish_date_raw=EXCLUDED.publish_date_raw,issuer=EXCLUDED.issuer,
              file_count=EXCLUDED.file_count,notes=EXCLUDED.notes,
              external_url=EXCLUDED.external_url,row_hash=EXCLUDED.row_hash,
              metadata=EXCLUDED.metadata,updated_at=NOW()
            """,
            record_rows,
        )
        cursor.execute(
            f"""
            UPDATE {schema}.import_run
            SET run_status='completed',row_counts=%s,completed_at=NOW()
            WHERE import_run_id=%s
            """,
            (jsonb({"policy_catalog_entries": len(payload["records"])}), import_run_id),
        )
    connection.commit()
    return {
        "catalog_id": catalog_id,
        "catalog_status": status,
        "records_imported": len(payload["records"]),
        "duplicate_index_groups": payload["quality_summary"]["duplicate_index_groups"],
        "duplicate_index_rows": payload["quality_summary"]["duplicate_index_rows"],
        "import_run_id": import_run_id,
        "applied_migrations": migrations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy_catalog", type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--output", type=Path)
    add_connection_arguments(parser)
    args = parser.parse_args()
    payload = load_json(args.policy_catalog)
    with connect(args) as connection:
        result = import_catalog(connection, payload, publish=args.publish, schema=args.schema)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
