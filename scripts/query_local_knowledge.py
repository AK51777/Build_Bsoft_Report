#!/usr/bin/env python3
"""Run deterministic hard-filtered queries against a project knowledge.sqlite snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _contains_all(values: list[Any], required: set[str]) -> bool:
    normalized = {str(item).casefold() for item in values}
    return all(item.casefold() in normalized for item in required)


def query_local(
    database: Path,
    *,
    kind: str = "corpus",
    package_ids: list[str] | None = None,
    catalog_ids: list[str] | None = None,
    document_type: str = "",
    project_type: str = "",
    section_role: str = "",
    module_code: str = "",
    topic: str = "",
    reuse_class: str = "",
    permission_scope: str = "",
    applicable_version: str = "",
    tags: list[str] | None = None,
    prerequisites: list[str] | None = None,
    search: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    package_set = set(package_ids or [])
    catalog_set = set(catalog_ids or [])
    tag_set = set(tags or ([] if not topic else [topic]))
    prerequisite_set = set(prerequisites or [])
    search_folded = search.casefold().strip()
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        if kind == "status":
            row = conn.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM corpus_block WHERE review_status='approved') AS corpus_blocks,
                  (SELECT COUNT(*) FROM product_capability WHERE review_status='approved') AS capabilities,
                  (SELECT COUNT(*) FROM policy_catalog_entry WHERE entry_status='active') AS catalog_records,
                  (SELECT COUNT(*) FROM policy_clause WHERE verification_status='verified') AS policy_clauses,
                  (SELECT COUNT(*) FROM shared_knowledge_snapshot WHERE snapshot_status IN ('current','stale')) AS snapshots
                """
            ).fetchone()
            return {"kind": kind, "count": 1, "rows": [dict(row)]}
        if kind == "corpus":
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT b.*,d.document_type,d.project_type,d.permission_scope,
                           d.version AS stored_version,d.jurisdiction_code,
                           s.file_name AS source_file,s.sha256 AS source_hash,
                           s.metadata_json AS source_metadata_json
                    FROM corpus_block b
                    JOIN corpus_document d ON d.corpus_document_id=b.corpus_document_id
                    JOIN source_document s ON s.source_id=d.source_id
                    WHERE b.review_status='approved' AND d.review_status='approved'
                    ORDER BY d.version,b.section_role,b.module_code,b.block_id
                    """
                )
            ]
            filtered = []
            for row in rows:
                source_metadata = {}
                try:
                    source_metadata = json.loads(row.get("source_metadata_json") or "{}")
                except (TypeError, json.JSONDecodeError):
                    pass
                row["package_id"] = str(
                    source_metadata.get("package_id") or row.get("stored_version") or ""
                )
                row["applicable_version"] = str(source_metadata.get("version") or "")
                if package_set and row["package_id"] not in package_set:
                    continue
                if document_type and row["document_type"] != document_type:
                    continue
                if project_type and row["project_type"] != project_type:
                    continue
                if section_role and row["section_role"] != section_role:
                    continue
                if module_code and row["module_code"] != module_code:
                    continue
                if reuse_class and row["reuse_class"] != reuse_class:
                    continue
                if permission_scope and row["permission_scope"] != permission_scope:
                    continue
                if applicable_version and row["applicable_version"] != applicable_version:
                    continue
                if prerequisite_set and not _contains_all(_json_list(row.get("prerequisites_json")), prerequisite_set):
                    continue
                if tag_set:
                    block_tags = {
                        item[0]
                        for item in conn.execute(
                            """
                            SELECT t.tag_code FROM corpus_block_tag bt
                            JOIN corpus_tag t ON t.tag_id=bt.tag_id
                            WHERE bt.block_id=? AND t.status='active'
                            """,
                            (row["block_id"],),
                        )
                    }
                    if not _contains_all(list(block_tags), tag_set):
                        continue
                haystack = " ".join(
                    str(row.get(key) or "")
                    for key in ("clean_text", "source_location", "section_role", "module_code")
                ).casefold()
                if search_folded and search_folded not in haystack:
                    continue
                filtered.append(row)
            filtered.sort(
                key=lambda row: (
                    0 if search_folded and search_folded in str(row["clean_text"]).casefold() else 1,
                    row["package_id"],
                    row["section_role"],
                    row["module_code"],
                    row["block_id"],
                )
            )
        elif kind == "capability":
            rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM product_capability WHERE review_status='approved' ORDER BY product_code,capability_name,capability_id"
                )
            ]
            filtered = []
            for row in rows:
                block_ids = _json_list(row.get("standard_block_ids_json"))
                block_packages = {
                    item[0]
                    for block_id in block_ids
                    for item in conn.execute(
                        """
                        SELECT d.version FROM corpus_block b
                        JOIN corpus_document d ON d.corpus_document_id=b.corpus_document_id
                        WHERE b.block_id=?
                        """,
                        (block_id,),
                    )
                }
                if package_set and not package_set.intersection(block_packages):
                    continue
                if applicable_version and applicable_version not in _json_list(row.get("applicable_versions_json")):
                    continue
                if prerequisite_set and not _contains_all(_json_list(row.get("prerequisites_json")), prerequisite_set):
                    continue
                haystack = " ".join(
                    str(row.get(key) or "")
                    for key in ("product_code", "product_name", "capability_name", "capability_description", "category", "module_name")
                ).casefold()
                if module_code and module_code.casefold() not in haystack:
                    continue
                if search_folded and search_folded not in haystack:
                    continue
                row["package_ids"] = sorted(block_packages)
                row["standard_block_ids"] = block_ids
                filtered.append(row)
        elif kind == "policy-catalog":
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT e.*,c.permission_scope,c.content_hash AS catalog_content_hash
                    FROM policy_catalog_entry e
                    JOIN policy_catalog c ON c.catalog_id=e.catalog_id
                    WHERE e.entry_status='active' AND c.catalog_status='active'
                    ORDER BY e.catalog_id,e.catalog_group_code,e.source_row,e.catalog_entry_id
                    """
                )
            ]
            filtered = []
            for row in rows:
                if catalog_set and row["catalog_id"] not in catalog_set:
                    continue
                if permission_scope and row["permission_scope"] != permission_scope:
                    continue
                keywords = _json_list(row.get("keyword_tags_json"))
                if tag_set and not _contains_all(keywords, tag_set):
                    continue
                haystack = " ".join(str(row.get(key) or "") for key in ("title", "keyword_text", "category_name", "document_no")).casefold()
                if search_folded and search_folded not in haystack:
                    continue
                filtered.append(row)
        elif kind == "policy-clause":
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT c.*,p.title,p.document_no,p.issuer,p.jurisdiction_code,p.validity_status
                    FROM policy_clause c JOIN policy_document p ON p.policy_id=c.policy_id
                    WHERE c.verification_status='verified' AND p.verification_status='verified'
                    ORDER BY p.authority_group,p.authority_rank,p.publish_date,p.policy_id,c.clause_id
                    """
                )
            ]
            filtered = []
            for row in rows:
                clause_topics = set(_json_list(row.get("topic_tags_json")))
                if tag_set and not _contains_all(list(clause_topics), tag_set):
                    continue
                haystack = " ".join(str(row.get(key) or "") for key in ("title", "original_text", "normalized_summary")).casefold()
                if search_folded and search_folded not in haystack:
                    continue
                filtered.append(row)
        else:
            raise ValueError(f"unsupported local query kind: {kind}")
    selected = filtered[:limit]
    return {
        "kind": kind,
        "source": "project_sqlite_snapshot",
        "count": len(selected),
        "limit": limit,
        "rows": selected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("kind", choices=("status", "corpus", "capability", "policy-catalog", "policy-clause"))
    parser.add_argument("--package-id", action="append", default=[])
    parser.add_argument("--catalog-id", action="append", default=[])
    parser.add_argument("--document-type", default="")
    parser.add_argument("--project-type", default="")
    parser.add_argument("--section-role", default="")
    parser.add_argument("--module-code", default="")
    parser.add_argument("--topic", default="")
    parser.add_argument("--reuse-class", choices=("", "A", "B", "C", "D"), default="")
    parser.add_argument("--permission-scope", default="")
    parser.add_argument("--applicable-version", default="")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--prerequisite", action="append", default=[])
    parser.add_argument("--search", default="")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = query_local(
        args.database,
        kind=args.kind,
        package_ids=args.package_id,
        catalog_ids=args.catalog_id,
        document_type=args.document_type,
        project_type=args.project_type,
        section_role=args.section_role,
        module_code=args.module_code,
        topic=args.topic,
        reuse_class=args.reuse_class,
        permission_scope=args.permission_scope,
        applicable_version=args.applicable_version,
        tags=args.tag,
        prerequisites=args.prerequisite,
        search=args.search,
        limit=args.limit,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
