#!/usr/bin/env python3
"""Export a read-only audit of the PostgreSQL knowledge schema and migrations."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from postgres_knowledge_db import (
    add_connection_arguments,
    connect,
    migration_dir,
    sha256_text,
    validate_schema,
)


def json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def fetch_dicts(cursor) -> list[dict[str, Any]]:
    columns = [column.name for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def local_migration_hashes() -> list[dict[str, str]]:
    return [
        {
            "version": path.stem,
            "file_name": path.name,
            "file_hash": sha256_text(path.read_text(encoding="utf-8")),
        }
        for path in sorted(migration_dir().glob("*.sql"))
    ]


def audit_schema(connection, schema: str) -> dict[str, Any]:
    validate_schema(schema)
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(
            f"SELECT version,file_hash,applied_at FROM {schema}.schema_migration ORDER BY version"
        )
        migrations = fetch_dicts(cursor)
        cursor.execute(
            """
            SELECT table_name,column_name,ordinal_position,data_type,is_nullable,column_default
            FROM information_schema.columns
            WHERE table_schema=%s
            ORDER BY table_name,ordinal_position
            """,
            (schema,),
        )
        columns = fetch_dicts(cursor)
        cursor.execute(
            """
            SELECT tc.table_name,tc.constraint_name,tc.constraint_type,
                   kcu.column_name,kcu.ordinal_position
            FROM information_schema.table_constraints AS tc
            LEFT JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_schema=kcu.constraint_schema
             AND tc.constraint_name=kcu.constraint_name
            WHERE tc.table_schema=%s
            ORDER BY tc.table_name,tc.constraint_name,kcu.ordinal_position
            """,
            (schema,),
        )
        constraints = fetch_dicts(cursor)
        cursor.execute(
            """
            SELECT tablename,indexname,indexdef
            FROM pg_indexes
            WHERE schemaname=%s
            ORDER BY tablename,indexname
            """,
            (schema,),
        )
        indexes = fetch_dicts(cursor)
    connection.rollback()
    tables = sorted({row["table_name"] for row in columns})
    return {
        "schema": schema,
        "server_migrations": migrations,
        "local_migrations": local_migration_hashes(),
        "summary": {
            "table_count": len(tables),
            "column_count": len(columns),
            "constraint_count": len(constraints),
            "index_count": len(indexes),
        },
        "tables": tables,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    add_connection_arguments(parser)
    args = parser.parse_args()
    with connect(args) as connection:
        result = audit_schema(connection, args.schema)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=json_default) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
