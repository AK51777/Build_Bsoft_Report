#!/usr/bin/env python3
"""Import a reviewed department policy catalog into one local project database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from import_policy_catalog_postgres import normalize_catalog
from knowledge_db import apply_migrations, connect, dump_json, load_json, now_iso


def import_catalog(database: Path, payload: dict[str, Any]) -> dict[str, Any]:
    payload = normalize_catalog(payload)
    source_file = payload.get("source_file", {})
    timestamp = now_iso()
    with connect(database.resolve()) as connection:
        apply_migrations(connection)
        existing = connection.execute(
            "SELECT content_hash FROM policy_catalog WHERE catalog_id=?",
            (payload["catalog_id"],),
        ).fetchone()
        if existing and existing["content_hash"] != payload["content_hash"]:
            raise RuntimeError("policy catalog content hash changed for an existing catalog_id")
        connection.execute(
            """
            UPDATE policy_catalog
            SET catalog_status='superseded'
            WHERE catalog_scope=? AND catalog_id<>? AND catalog_status='active'
            """,
            (payload["catalog_scope"], payload["catalog_id"]),
        )
        connection.execute(
            """
            INSERT INTO policy_catalog (
              catalog_id,schema_version,catalog_scope,title,permission_scope,
              source_file_name,source_sha256,worksheet_name,content_hash,
              catalog_status,imported_at
            ) VALUES (?,?,?,?,?,?,?,?,?,'active',?)
            ON CONFLICT(catalog_id) DO UPDATE SET
              title=excluded.title,permission_scope=excluded.permission_scope,
              source_file_name=excluded.source_file_name,
              source_sha256=excluded.source_sha256,
              worksheet_name=excluded.worksheet_name,
              catalog_status='active',imported_at=excluded.imported_at
            """,
            (
                payload["catalog_id"],
                payload["schema_version"],
                payload["catalog_scope"],
                payload["title"],
                payload["permission_scope"],
                source_file.get("file_name", ""),
                source_file.get("sha256", ""),
                payload.get("worksheet_name", ""),
                payload["content_hash"],
                timestamp,
            ),
        )
        for record in payload["records"]:
            connection.execute(
                """
                INSERT INTO policy_catalog_entry (
                  catalog_entry_id,catalog_id,source_row,source_index_no,index_occurrence,index_conflict,identity_key,
                  catalog_group_code,catalog_group_name,authority_level_label,
                  category_name,keyword_text,keyword_tags_json,document_no,title,
                  publish_date,publish_date_raw,issuer,file_count,notes,external_url,
                  verification_status,entry_status,row_hash
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(catalog_entry_id) DO UPDATE SET
                  source_row=excluded.source_row,source_index_no=excluded.source_index_no,
                  index_occurrence=excluded.index_occurrence,
                  index_conflict=excluded.index_conflict,identity_key=excluded.identity_key,
                  catalog_group_code=excluded.catalog_group_code,
                  catalog_group_name=excluded.catalog_group_name,
                  authority_level_label=excluded.authority_level_label,
                  category_name=excluded.category_name,
                  keyword_text=excluded.keyword_text,
                  keyword_tags_json=excluded.keyword_tags_json,
                  document_no=excluded.document_no,title=excluded.title,
                  publish_date=excluded.publish_date,
                  publish_date_raw=excluded.publish_date_raw,issuer=excluded.issuer,
                  file_count=excluded.file_count,notes=excluded.notes,
                  external_url=excluded.external_url,row_hash=excluded.row_hash
                """,
                (
                    record["catalog_entry_id"],
                    payload["catalog_id"],
                    record["source_row"],
                    record["source_index_no"],
                    record["index_occurrence"],
                    int(record["index_conflict"]),
                    record["identity_key"],
                    record.get("catalog_group_code", ""),
                    record.get("catalog_group_name", ""),
                    record.get("authority_level_label", ""),
                    record.get("category_name", ""),
                    record.get("keyword_text", ""),
                    dump_json(record.get("keyword_tags", [])),
                    record.get("document_no", ""),
                    record["title"],
                    record.get("publish_date", ""),
                    record.get("publish_date_raw", ""),
                    record.get("issuer", ""),
                    record.get("file_count"),
                    record.get("notes", ""),
                    record.get("external_url", ""),
                    record.get("verification_status", "unverified"),
                    record.get("entry_status", "active"),
                    record["row_hash"],
                ),
            )
        connection.commit()
        count = connection.execute(
            "SELECT COUNT(*) AS count FROM policy_catalog_entry WHERE catalog_id=?",
            (payload["catalog_id"],),
        ).fetchone()["count"]
    return {
        "catalog_id": payload["catalog_id"],
        "content_hash": payload["content_hash"],
        "records_imported": len(payload["records"]),
        "records_available": count,
        "duplicate_index_groups": payload["quality_summary"]["duplicate_index_groups"],
        "duplicate_index_rows": payload["quality_summary"]["duplicate_index_rows"],
        "catalog_status": "active",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("policy_catalog", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = import_catalog(args.database, load_json(args.policy_catalog))
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
