#!/usr/bin/env python3
"""Import verified document-type and jurisdiction standards from JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_db import apply_migrations, connect, dump_json, load_json, stable_id


def ingest(database: Path, payload: dict) -> dict:
    count = 0
    with connect(database) as conn:
        apply_migrations(conn)
        for item in payload.get("standards", []):
            standard_id = item.get("standard_id") or stable_id(
                "DOCSTD",
                item.get("document_type"),
                item.get("jurisdiction_code"),
                item.get("title"),
                item.get("version"),
            )
            conn.execute(
                """
                INSERT INTO document_standard (
                  standard_id,document_type,jurisdiction_code,jurisdiction_name,
                  authority_name,title,version,effective_date,expiry_date,
                  required_sections_json,optional_sections_json,table_requirements_json,
                  official_url,source_id,verification_status,status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(standard_id) DO UPDATE SET
                  document_type=excluded.document_type,
                  jurisdiction_code=excluded.jurisdiction_code,
                  jurisdiction_name=excluded.jurisdiction_name,
                  authority_name=excluded.authority_name,
                  title=excluded.title,
                  version=excluded.version,
                  effective_date=excluded.effective_date,
                  expiry_date=excluded.expiry_date,
                  required_sections_json=excluded.required_sections_json,
                  optional_sections_json=excluded.optional_sections_json,
                  table_requirements_json=excluded.table_requirements_json,
                  official_url=excluded.official_url,
                  verification_status=excluded.verification_status,
                  status=excluded.status
                """,
                (
                    standard_id,
                    item["document_type"],
                    item.get("jurisdiction_code", ""),
                    item.get("jurisdiction_name", ""),
                    item.get("authority_name", ""),
                    item["title"],
                    item.get("version", ""),
                    item.get("effective_date", ""),
                    item.get("expiry_date", ""),
                    dump_json(item.get("required_sections", [])),
                    dump_json(item.get("optional_sections", [])),
                    dump_json(item.get("table_requirements", [])),
                    item.get("official_url", ""),
                    item.get("source_id"),
                    item.get("verification_status", "unverified"),
                    item.get("status", "candidate"),
                ),
            )
            count += 1
        conn.commit()
    return {"standards": count}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = ingest(args.database, load_json(args.input))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
