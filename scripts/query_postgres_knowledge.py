#!/usr/bin/env python3
"""Run bounded read-only queries against published PostgreSQL knowledge views."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from postgres_knowledge_db import add_connection_arguments, connect, validate_schema


def json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def fetch_dicts(cursor) -> list[dict[str, Any]]:
    columns = [column.name for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def query(connection, *, schema: str, kind: str, search: str, section_role: str, module_code: str, topic: str, limit: int) -> dict[str, Any]:
    validate_schema(schema)
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    with connection.cursor() as cursor:
        if kind == "status":
            cursor.execute(
                f"""
                SELECT
                  (SELECT COUNT(*) FROM {schema}.runtime_knowledge_package) AS packages,
                  (SELECT COUNT(*) FROM {schema}.runtime_corpus_block) AS corpus_blocks,
                  (SELECT COUNT(*) FROM {schema}.runtime_product_capability) AS capabilities,
                  (SELECT COUNT(*) FROM {schema}.runtime_policy_catalog_entry) AS policy_catalog_entries,
                  (SELECT COUNT(*) FROM {schema}.runtime_policy_clause) AS verified_policy_clauses
                """
            )
        elif kind == "package":
            cursor.execute(
                f"""
                SELECT package_id,schema_version,title,permission_scope,content_hash,
                       review_summary,published_at
                FROM {schema}.runtime_knowledge_package
                WHERE (%s='' OR title ILIKE '%%' || %s || '%%')
                ORDER BY published_at DESC NULLS LAST,package_id
                LIMIT %s
                """,
                (search, search, limit),
            )
        elif kind == "corpus":
            cursor.execute(
                f"""
                SELECT package_id,block_id,source_location,heading_path,section_role,module_code,
                       clean_text,reuse_class,quality_level,prerequisites,variable_slots,
                       forbidden_terms,text_hash
                FROM {schema}.runtime_corpus_block
                WHERE (%s='' OR clean_text ILIKE '%%' || %s || '%%')
                  AND (%s='' OR section_role=%s)
                  AND (%s='' OR module_code=%s)
                ORDER BY package_id,block_index
                LIMIT %s
                """,
                (search, search, section_role, section_role, module_code, module_code, limit),
            )
        elif kind == "capability":
            cursor.execute(
                f"""
                SELECT package_id,capability_id,product_code,product_name,capability_name,
                       capability_description,category,module_name,selection_rules,
                       prerequisites,interface_dependencies,exclusions,applicable_versions,
                       source_location,package_content_hash
                FROM {schema}.runtime_product_capability
                WHERE (%s='' OR product_name ILIKE '%%' || %s || '%%'
                   OR capability_name ILIKE '%%' || %s || '%%'
                   OR capability_description ILIKE '%%' || %s || '%%')
                ORDER BY product_name,capability_name
                LIMIT %s
                """,
                (search, search, search, search, limit),
            )
        elif kind == "policy-catalog":
            cursor.execute(
                f"""
                SELECT catalog_entry_id,source_row,source_index_no,index_occurrence,index_conflict,
                       catalog_group_code,catalog_group_name,
                       authority_level_label,category_name,keyword_text,document_no,title,
                       publish_date,publish_date_raw,issuer,notes,external_url,
                       verification_status,row_hash
                FROM {schema}.runtime_policy_catalog_entry
                WHERE (%s='' OR source_index_no ILIKE '%%' || %s || '%%'
                   OR title ILIKE '%%' || %s || '%%'
                   OR keyword_text ILIKE '%%' || %s || '%%'
                   OR category_name ILIKE '%%' || %s || '%%')
                ORDER BY catalog_group_code,source_index_no,source_row
                LIMIT %s
                """,
                (search, search, search, search, search, limit),
            )
        elif kind == "policy-clause":
            cursor.execute(
                f"""
                SELECT *
                FROM {schema}.runtime_policy_clause
                WHERE (%s='' OR original_text ILIKE '%%' || %s || '%%'
                   OR normalized_summary ILIKE '%%' || %s || '%%'
                   OR title ILIKE '%%' || %s || '%%')
                  AND (%s='' OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements(topics) AS item
                    WHERE item->>'topic_code'=%s
                  ))
                ORDER BY authority_group,authority_rank,publish_date,title,clause_order
                LIMIT %s
                """,
                (search, search, search, search, topic, topic, limit),
            )
        else:
            raise ValueError(f"unsupported query kind: {kind}")
        rows = fetch_dicts(cursor)
    return {
        "kind": kind,
        "evidence_status": "index_only_unverified" if kind == "policy-catalog" else "published_runtime_view",
        "count": len(rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "kind",
        choices=("status", "package", "corpus", "capability", "policy-catalog", "policy-clause"),
    )
    parser.add_argument("--search", default="")
    parser.add_argument("--section-role", default="")
    parser.add_argument("--module-code", default="")
    parser.add_argument("--topic", default="")
    parser.add_argument("--limit", default=50, type=int)
    parser.add_argument("--output", type=Path)
    add_connection_arguments(parser)
    args = parser.parse_args()
    with connect(args) as connection:
        result = query(
            connection,
            schema=args.schema,
            kind=args.kind,
            search=args.search,
            section_role=args.section_role,
            module_code=args.module_code,
            topic=args.topic,
            limit=args.limit,
        )
    output = json.dumps(result, ensure_ascii=False, indent=2, default=json_default)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
