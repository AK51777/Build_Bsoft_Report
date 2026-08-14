#!/usr/bin/env python3
"""Initialize and verify the PostgreSQL shared knowledge schema."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from postgres_knowledge_db import add_connection_arguments, apply_migrations, connect


REQUIRED_RUNTIME_VIEWS = (
    "runtime_knowledge_package",
    "runtime_package_source",
    "runtime_corpus_document",
    "runtime_corpus_block",
    "runtime_product_capability",
    "runtime_capability_block",
    "runtime_policy_catalog_entry",
    "runtime_policy_catalog",
    "runtime_policy_clause",
)


def initialize(connection, schema: str) -> dict:
    migrations = apply_migrations(connection, schema=schema)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.views
            WHERE table_schema=%s AND table_name = ANY(%s)
            ORDER BY table_name
            """,
            (schema, list(REQUIRED_RUNTIME_VIEWS)),
        )
        views = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema=%s AND table_type='BASE TABLE'
            ORDER BY table_name
            """,
            (schema,),
        )
        tables = [row[0] for row in cursor.fetchall()]
    missing_views = sorted(set(REQUIRED_RUNTIME_VIEWS).difference(views))
    if missing_views:
        raise RuntimeError(f"PostgreSQL knowledge schema is missing views: {missing_views}")
    return {
        "schema": schema,
        "applied_migrations": migrations,
        "table_count": len(tables),
        "runtime_views": views,
        "ready": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    add_connection_arguments(parser)
    args = parser.parse_args()
    with connect(args) as connection:
        result = initialize(connection, args.schema)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
