#!/usr/bin/env python3
"""Confirm one scope baseline with an append-only confirmation record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_db import apply_migrations, connect, dump_json, now_iso, stable_id


def confirm_scope_baseline(
    database: Path,
    project_code: str,
    baseline_id: str,
    *,
    confirmed_by: str,
    confirmed_at: str,
    decision_note: str = "",
) -> dict:
    if not confirmed_by.strip():
        raise ValueError("confirmed_by must not be empty")
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT * FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        baseline = conn.execute(
            "SELECT * FROM scope_baseline WHERE baseline_id=? AND project_id=?",
            (baseline_id, project["project_id"]),
        ).fetchone()
        if baseline is None:
            raise RuntimeError(f"scope baseline not found: {baseline_id}")
        pending_items = conn.execute(
            "SELECT COUNT(*) FROM scope_baseline_item WHERE baseline_id=? AND inclusion_status='pending'",
            (baseline_id,),
        ).fetchone()[0]
        item_count = conn.execute(
            "SELECT COUNT(*) FROM scope_baseline_item WHERE baseline_id=?",
            (baseline_id,),
        ).fetchone()[0]
        if not item_count:
            raise RuntimeError("empty scope baseline cannot be confirmed")
        if pending_items:
            raise RuntimeError(
                f"scope baseline contains {pending_items} pending items and cannot be confirmed"
            )
        confirmation_id = stable_id(
            "CONFIRM",
            project["project_id"],
            "scope_baseline",
            baseline_id,
            confirmed_at,
            "confirm",
        )
        existing = conn.execute(
            "SELECT 1 FROM confirmation_record WHERE confirmation_id=?",
            (confirmation_id,),
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO confirmation_record (
              confirmation_id,project_id,target_type,target_id,decision,
              before_value_json,after_value_json,decision_note,confirmed_by,
              confirmed_at,baseline_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                confirmation_id,
                project["project_id"],
                "scope_baseline",
                baseline_id,
                "confirm",
                dump_json({"status": baseline["status"]}),
                dump_json({"status": "confirmed"}),
                decision_note,
                confirmed_by,
                confirmed_at,
                project["baseline_version"],
            ),
        )
        if baseline["status"] != "confirmed":
            conn.execute(
                """
                UPDATE scope_baseline SET status='superseded',updated_at=?
                WHERE project_id=? AND baseline_id<>? AND status='confirmed'
                """,
                (now_iso(), project["project_id"], baseline_id),
            )
            conn.execute(
                """
                UPDATE scope_baseline SET status='confirmed',confirmation_id=?,updated_at=?
                WHERE baseline_id=?
                """,
                (confirmation_id, now_iso(), baseline_id),
            )
        conn.commit()
    return {
        "project_code": project_code,
        "baseline_id": baseline_id,
        "confirmation_id": confirmation_id,
        "status": "confirmed",
        "created": existing is None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("baseline_id")
    parser.add_argument("--confirmed-by", required=True)
    parser.add_argument("--confirmed-at", required=True)
    parser.add_argument("--decision-note", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = confirm_scope_baseline(
        args.database,
        args.project_code,
        args.baseline_id,
        confirmed_by=args.confirmed_by,
        confirmed_at=args.confirmed_at,
        decision_note=args.decision_note,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
