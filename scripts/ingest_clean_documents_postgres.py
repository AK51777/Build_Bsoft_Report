#!/usr/bin/env python3
"""Import clean document blocks into PostgreSQL without storing original files."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def validate_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    document = payload.get("document")
    blocks = payload.get("blocks")
    if not isinstance(document, dict) or not isinstance(blocks, list):
        raise ValueError("input must contain document and blocks")
    for field in (
        "document_id",
        "project_code",
        "title",
        "source_path",
        "source_sha256",
        "source_type",
        "cleaning_version",
        "cleaning_method",
    ):
        require_text(document.get(field), f"document.{field}")
    indexes: set[int] = set()
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError("every block must be an object")
        for field in ("block_id", "block_type", "source_location", "clean_text", "clean_text_sha256"):
            require_text(block.get(field), f"block.{field}")
        index = block.get("block_index")
        if not isinstance(index, int) or index < 1 or index in indexes:
            raise ValueError("block_index must be unique positive integers")
        indexes.add(index)
        if block["block_type"] not in {"heading", "paragraph", "table"}:
            raise ValueError("block_type must be heading, paragraph, or table")
        if not isinstance(block.get("heading_path", []), list):
            raise ValueError("heading_path must be an array")
    return document, blocks


def schema_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "knowledge-base" / "postgres" / "001_clean_document_schema.sql"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON emitted by extract_clean_document_blocks.py")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5432, type=int)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password-env", default="MEDICAL_FEASIBILITY_DB_PASSWORD")
    parser.add_argument("--schema-file", type=Path, default=schema_path())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    password = os.environ.get(args.password_env)
    if not password:
        raise RuntimeError(f"database password is missing from environment variable {args.password_env}")
    document, blocks = validate_payload(load_json(args.input))
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required; install psycopg[binary] first") from exc

    schema_sql = args.schema_file.read_text(encoding="utf-8")
    connection_kwargs = {
        "host": args.host,
        "port": args.port,
        "dbname": args.database,
        "user": args.user,
        "password": password,
        "connect_timeout": 10,
    }
    with psycopg.connect(**connection_kwargs) as conn:
        with conn.cursor() as cursor:
            cursor.execute(schema_sql)
            cursor.execute(
                """
                INSERT INTO public.feasibility_clean_document (
                  document_id, project_code, title, source_path, source_sha256, source_type,
                  cleaning_version, cleaning_method, contains_personal_data, metadata
                ) VALUES (%(document_id)s, %(project_code)s, %(title)s, %(source_path)s,
                  %(source_sha256)s, %(source_type)s, %(cleaning_version)s,
                  %(cleaning_method)s, %(contains_personal_data)s, %(metadata)s::jsonb)
                ON CONFLICT (document_id) DO UPDATE SET
                  project_code = EXCLUDED.project_code,
                  title = EXCLUDED.title,
                  source_path = EXCLUDED.source_path,
                  source_sha256 = EXCLUDED.source_sha256,
                  source_type = EXCLUDED.source_type,
                  cleaning_version = EXCLUDED.cleaning_version,
                  cleaning_method = EXCLUDED.cleaning_method,
                  contains_personal_data = EXCLUDED.contains_personal_data,
                  metadata = EXCLUDED.metadata,
                  updated_at = NOW()
                """,
                {**document, "metadata": json.dumps(document.get("metadata", {}), ensure_ascii=False)},
            )
            for block in blocks:
                cursor.execute(
                    """
                    INSERT INTO public.feasibility_clean_document_block (
                      block_id, document_id, block_index, block_type, heading_path,
                      source_location, clean_text, clean_text_sha256, metadata
                    ) VALUES (%(block_id)s, %(document_id)s, %(block_index)s, %(block_type)s,
                      %(heading_path)s::jsonb, %(source_location)s, %(clean_text)s,
                      %(clean_text_sha256)s, %(metadata)s::jsonb)
                    ON CONFLICT (document_id, block_index) DO UPDATE SET
                      block_id = EXCLUDED.block_id,
                      block_type = EXCLUDED.block_type,
                      heading_path = EXCLUDED.heading_path,
                      source_location = EXCLUDED.source_location,
                      clean_text = EXCLUDED.clean_text,
                      clean_text_sha256 = EXCLUDED.clean_text_sha256,
                      metadata = EXCLUDED.metadata,
                      updated_at = NOW()
                    """,
                    {
                        **block,
                        "document_id": document["document_id"],
                        "heading_path": json.dumps(block.get("heading_path", []), ensure_ascii=False),
                        "metadata": json.dumps(block.get("metadata", {}), ensure_ascii=False),
                    },
                )
        conn.commit()

    result = {"document_id": document["document_id"], "project_code": document["project_code"], "blocks_upserted": len(blocks)}
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
