#!/usr/bin/env python3
"""Adopt, discard, or restore an existing section draft version."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_db import apply_migrations, connect, dump_json, now_iso, sha256_text, stable_id


def manage_draft(
    database: Path,
    project_code: str,
    chapter_code: str,
    version_no: int,
    action: str,
    *,
    operator: str = "codex",
) -> dict:
    if action not in {"adopt", "discard", "restore"}:
        raise ValueError("action must be adopt, discard, or restore")
    timestamp = now_iso()
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT project_id FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        plan = conn.execute(
            """
            SELECT * FROM section_composition_plan WHERE project_id=? AND chapter_code=?
            ORDER BY version_no DESC LIMIT 1
            """,
            (project["project_id"], chapter_code),
        ).fetchone()
        if plan is None:
            raise RuntimeError(f"section plan not found for chapter {chapter_code}")
        draft = conn.execute(
            "SELECT * FROM draft_section_version WHERE plan_id=? AND version_no=?",
            (plan["plan_id"], version_no),
        ).fetchone()
        if draft is None:
            raise RuntimeError(f"draft version {version_no} not found")
        before = dict(draft)
        if action == "restore":
            next_version = conn.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM draft_section_version WHERE plan_id=?",
                (plan["plan_id"],),
            ).fetchone()[0]
            output_hash = sha256_text(draft["content"])
            target_id = stable_id("DRAFT", plan["plan_id"], next_version, output_hash)
            conn.execute(
                """
                INSERT INTO draft_section_version (
                  draft_version_id,plan_id,version_no,source_type,content,status,
                  check_result_json,created_by,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    target_id,
                    plan["plan_id"],
                    next_version,
                    "restored",
                    draft["content"],
                    "needs_review",
                    dump_json({"restored_from_version": version_no, "output_hash": output_hash}),
                    operator,
                    timestamp,
                    timestamp,
                ),
            )
            after = {"version_no": next_version, "status": "needs_review"}
            plan_status = "draft"
        else:
            target_status = "adopted" if action == "adopt" else "discarded"
            if action == "adopt":
                conn.execute(
                    """
                    UPDATE draft_section_version SET status='discarded',updated_at=?
                    WHERE plan_id=? AND status='adopted' AND version_no<>?
                    """,
                    (timestamp, plan["plan_id"], version_no),
                )
            conn.execute(
                "UPDATE draft_section_version SET status=?,updated_at=? WHERE draft_version_id=?",
                (target_status, timestamp, draft["draft_version_id"]),
            )
            target_id = draft["draft_version_id"]
            after = {"version_no": version_no, "status": target_status}
            plan_status = "completed" if action == "adopt" else "draft"
        conn.execute(
            "UPDATE section_composition_plan SET status=?,updated_at=? WHERE plan_id=?",
            (plan_status, timestamp, plan["plan_id"]),
        )
        conn.execute(
            """
            INSERT INTO audit_log (
              audit_id,project_id,target_type,target_id,action,before_value_json,
              after_value_json,operator,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                stable_id("AUDIT", target_id, action, timestamp),
                project["project_id"],
                "draft_section_version",
                target_id,
                action,
                dump_json(before),
                dump_json(after),
                operator,
                timestamp,
            ),
        )
        conn.commit()
    return {
        "project_code": project_code,
        "chapter_code": chapter_code,
        "action": action,
        "target_draft_version_id": target_id,
        **after,
        "plan_status": plan_status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("chapter_code")
    parser.add_argument("version_no", type=int)
    parser.add_argument("action", choices=("adopt", "discard", "restore"))
    parser.add_argument("--operator", default="codex")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = manage_draft(
        args.database,
        args.project_code,
        args.chapter_code,
        args.version_no,
        args.action,
        operator=args.operator,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
