#!/usr/bin/env python3
"""Create or reuse an append-only project scope baseline candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_db import apply_migrations, connect, dump_json, now_iso, sha256_text, stable_id


def inclusion_status(scope: dict) -> str:
    if not scope["customer_scope"] or scope["status"] == "not_applicable":
        return "excluded"
    if scope["status"] == "confirmed":
        return "included"
    return "pending"


def build_scope_baseline(database: Path, project_code: str) -> dict:
    timestamp = now_iso()
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT project_id,baseline_version FROM project WHERE project_code=?",
            (project_code,),
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        scopes = [
            dict(row)
            for row in conn.execute(
                """
                SELECT scope_id,source_id,standard_name,customer_scope,status
                FROM project_scope_item WHERE project_id=? ORDER BY scope_id
                """,
                (project["project_id"],),
            )
        ]
        snapshot = [
            {
                "scope_id": scope["scope_id"],
                "inclusion_status": inclusion_status(scope),
                "status": scope["status"],
            }
            for scope in scopes
        ]
        content_hash = sha256_text(dump_json(snapshot))
        existing = conn.execute(
            "SELECT * FROM scope_baseline WHERE project_id=? AND content_hash=?",
            (project["project_id"], content_hash),
        ).fetchone()
        created = existing is None
        if existing:
            baseline_id = existing["baseline_id"]
            version_no = existing["version_no"]
            status = existing["status"]
        else:
            version_no = conn.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM scope_baseline WHERE project_id=?",
                (project["project_id"],),
            ).fetchone()[0]
            baseline_id = stable_id(
                "SCOPEBASELINE", project["project_id"], content_hash
            )
            status = "pending_confirmation"
            source_ids = sorted(
                {scope["source_id"] for scope in scopes if scope["source_id"]}
            )
            conn.execute(
                """
                INSERT INTO scope_baseline (
                  baseline_id,project_id,version_no,content_hash,baseline_name,
                  source_ids_json,status,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    baseline_id,
                    project["project_id"],
                    version_no,
                    content_hash,
                    f"Scope baseline v{version_no}",
                    dump_json(source_ids),
                    status,
                    timestamp,
                    timestamp,
                ),
            )
            for scope in scopes:
                conn.execute(
                    """
                    INSERT INTO scope_baseline_item (
                      baseline_id,scope_id,inclusion_status,notes
                    ) VALUES (?,?,?,?)
                    """,
                    (
                        baseline_id,
                        scope["scope_id"],
                        inclusion_status(scope),
                        f"scope_status={scope['status']}",
                    ),
                )
            conn.commit()
        items = [
            dict(row)
            for row in conn.execute(
                """
                SELECT i.*,s.standard_name,s.status AS scope_status
                FROM scope_baseline_item i
                JOIN project_scope_item s ON s.scope_id=i.scope_id
                WHERE i.baseline_id=? ORDER BY i.scope_id
                """,
                (baseline_id,),
            )
        ]
    return {
        "project_code": project_code,
        "baseline_id": baseline_id,
        "version_no": version_no,
        "content_hash": content_hash,
        "status": status,
        "created": created,
        "items": items,
        "counts": {
            state: sum(item["inclusion_status"] == state for item in items)
            for state in ("included", "excluded", "pending")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = build_scope_baseline(args.database, args.project_code)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
