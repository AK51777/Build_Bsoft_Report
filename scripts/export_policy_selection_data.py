#!/usr/bin/env python3
"""Export one completed policy match run from SQLite for Markdown/Excel rebuilds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_db import apply_migrations, connect
from match_project_policies import to_markdown


def export_selection(database: Path, project_code: str, match_run_id: str = "") -> dict:
    with connect(database) as conn:
        apply_migrations(conn)
        project = conn.execute("SELECT * FROM project WHERE project_code=?", (project_code,)).fetchone()
        if not project:
            raise ValueError(f"project not found: {project_code}")
        if match_run_id:
            run = conn.execute(
                """
                SELECT * FROM policy_match_run
                WHERE project_id=? AND match_run_id=? AND status='completed'
                """,
                (project["project_id"], match_run_id),
            ).fetchone()
        else:
            run = conn.execute(
                """
                SELECT * FROM policy_match_run
                WHERE project_id=? AND status='completed'
                ORDER BY completed_at DESC,started_at DESC,match_run_id DESC LIMIT 1
                """,
                (project["project_id"],),
            ).fetchone()
        if not run:
            raise ValueError("completed policy match run not found")
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT p.*,c.clause_id,c.article_path,c.original_text,c.normalized_summary,
                       c.topic_tags_json,c.target_objects_json,c.requirement_type,
                       c.applicability_notes,c.verification_status AS clause_verification_status,
                       m.match_id,m.match_dimensions_json,m.relevance_level,m.basis_use,
                       m.background_use,m.other_chapter_use_json,m.project_relation,m.sort_key,
                       m.basis_order,m.decision_status,m.decision_reason,m.created_at AS match_created_at,
                       m.updated_at AS match_updated_at
                FROM project_policy_match m
                JOIN policy_document p ON p.policy_id=m.policy_id
                JOIN policy_clause c ON c.clause_id=m.clause_id
                WHERE m.match_run_id=?
                ORDER BY CASE WHEN m.basis_use=1 THEN 0 ELSE 1 END,
                         COALESCE(m.basis_order,999999),m.sort_key,m.policy_id,m.clause_id
                """,
                (run["match_run_id"],),
            ).fetchall()
        ]
    for row in rows:
        dimensions = json.loads(row.get("match_dimensions_json") or "{}")
        row["topics"] = dimensions.get("topics") or json.loads(row.get("topic_tags_json") or "[]")
        row["basis_use"] = bool(row["basis_use"])
        row["background_use"] = bool(row["background_use"])
    basis_policy_order: list[str] = []
    for row in rows:
        if row["basis_use"] and row["policy_id"] not in basis_policy_order:
            basis_policy_order.append(row["policy_id"])
    return {
        "project": dict(project),
        "topics": json.loads(run["topic_tags_json"] or "[]"),
        "match_run_id": run["match_run_id"],
        "matcher_version": run["matcher_version"],
        "policy_count": len({row["policy_id"] for row in rows}),
        "clause_match_count": len(rows),
        "basis_policy_order": basis_policy_order,
        "matches": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--match-run-id", default="")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    args = parser.parse_args()
    result = export_selection(args.database, args.project_code, args.match_run_id)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(to_markdown(result), encoding="utf-8")
    print(
        json.dumps(
            {
                "match_run_id": result["match_run_id"],
                "policy_count": result["policy_count"],
                "basis_policy_order": result["basis_policy_order"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
