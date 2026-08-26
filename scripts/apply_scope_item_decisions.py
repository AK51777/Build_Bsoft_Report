#!/usr/bin/env python3
"""Apply baseline-bound customer scope decisions with append-only audit records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect, dump_json, load_json, now_iso, stable_id


DECISION_STATUS = {
    "include": (1, "confirmed"),
    "exclude": (0, "not_applicable"),
    "defer": (1, "pending_confirmation"),
}
CONFIRMATION_DECISION = {
    "include": "confirm",
    "exclude": "exclude",
    "defer": "defer",
}
CONSTRUCTION_MODES = {
    "new",
    "upgrade",
    "reuse",
    "replace",
    "migrate",
    "pending_confirmation",
}


def required_text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def apply_scope_item_decisions(database: Path, payload: dict[str, Any]) -> dict[str, Any]:
    project_code = required_text(payload, "project_code")
    baseline_id = required_text(payload, "baseline_id")
    expected_content_hash = required_text(payload, "expected_content_hash")
    confirmed_by = required_text(payload, "confirmed_by")
    confirmed_at = required_text(payload, "confirmed_at")
    default_decision = str(payload.get("default_decision") or "").strip()
    if default_decision and default_decision not in DECISION_STATUS:
        raise ValueError(f"unsupported default_decision: {default_decision}")

    decision_rows = payload.get("decisions") or []
    if not isinstance(decision_rows, list):
        raise ValueError("decisions must be an array")
    decisions: dict[str, dict[str, Any]] = {}
    for item in decision_rows:
        if not isinstance(item, dict):
            raise ValueError("each scope decision must be an object")
        scope_id = required_text(item, "scope_id")
        if scope_id in decisions:
            raise ValueError(f"duplicate scope decision: {scope_id}")
        decision = required_text(item, "decision")
        if decision not in DECISION_STATUS:
            raise ValueError(f"unsupported scope decision: {scope_id}={decision}")
        mode = str(item.get("construction_mode") or "").strip()
        if mode and mode not in CONSTRUCTION_MODES:
            raise ValueError(f"unsupported construction_mode: {scope_id}={mode}")
        decisions[scope_id] = item

    timestamp = now_iso()
    applied = 0
    duplicates = 0
    counts = {decision: 0 for decision in DECISION_STATUS}
    with connect(database.resolve()) as conn:
        applied_migrations = apply_migrations(conn)
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
        if baseline["content_hash"] != expected_content_hash:
            raise RuntimeError("scope decision payload does not match the baseline content hash")
        if baseline["status"] == "confirmed":
            raise RuntimeError("confirmed scope baseline is immutable")

        rows = conn.execute(
            """
            SELECT s.*
            FROM scope_baseline_item i
            JOIN project_scope_item s ON s.scope_id=i.scope_id
            WHERE i.baseline_id=? AND s.project_id=?
            ORDER BY s.scope_id
            """,
            (baseline_id, project["project_id"]),
        ).fetchall()
        scope_ids = {row["scope_id"] for row in rows}
        unknown_ids = sorted(set(decisions).difference(scope_ids))
        if unknown_ids:
            raise ValueError(f"scope decisions contain unknown IDs: {unknown_ids[:5]}")
        unresolved_ids = [
            row["scope_id"]
            for row in rows
            if row["scope_id"] not in decisions and not default_decision
        ]
        if unresolved_ids:
            raise ValueError(f"scope decisions are incomplete: {unresolved_ids[:5]}")

        for row in rows:
            item = decisions.get(row["scope_id"], {})
            decision = str(item.get("decision") or default_decision)
            customer_scope, status = DECISION_STATUS[decision]
            construction_mode = str(item.get("construction_mode") or row["construction_mode"])
            chapter_location = str(item.get("chapter_location") or row["chapter_location"])
            decision_note = str(
                item.get("decision_note")
                or payload.get("decision_note")
                or ""
            )
            before = dict(row)
            after = {
                **before,
                "customer_scope": customer_scope,
                "status": status,
                "construction_mode": construction_mode,
                "chapter_location": chapter_location,
            }
            confirmation_id = stable_id(
                "CONFIRM",
                project["project_id"],
                "scope_item",
                row["scope_id"],
                decision,
                confirmed_at,
                baseline_id,
            )
            confirmation_decision = CONFIRMATION_DECISION[decision]
            if conn.execute(
                "SELECT 1 FROM confirmation_record WHERE confirmation_id=?",
                (confirmation_id,),
            ).fetchone():
                duplicates += 1
                counts[decision] += 1
                continue
            conn.execute(
                """
                INSERT INTO confirmation_record (
                  confirmation_id,project_id,target_type,target_id,decision,
                  before_value_json,after_value_json,decision_note,confirmed_by,
                  confirmed_at,baseline_version
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    confirmation_id,
                    project["project_id"],
                    "scope_item",
                    row["scope_id"],
                    confirmation_decision,
                    dump_json(before),
                    dump_json(after),
                    decision_note,
                    confirmed_by,
                    confirmed_at,
                    project["baseline_version"],
                ),
            )
            conn.execute(
                """
                UPDATE project_scope_item
                SET customer_scope=?,status=?,construction_mode=?,chapter_location=?,updated_at=?
                WHERE project_id=? AND scope_id=?
                """,
                (
                    customer_scope,
                    status,
                    construction_mode,
                    chapter_location,
                    timestamp,
                    project["project_id"],
                    row["scope_id"],
                ),
            )
            conn.execute(
                """
                INSERT INTO audit_log (
                  audit_id,project_id,target_type,target_id,action,
                  before_value_json,after_value_json,operator,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    stable_id("AUDIT", confirmation_id),
                    project["project_id"],
                    "scope_item",
                    row["scope_id"],
                    f"scope_decision:{decision}",
                    dump_json(before),
                    dump_json(after),
                    confirmed_by,
                    confirmed_at,
                ),
            )
            applied += 1
            counts[decision] += 1
        conn.commit()

    return {
        "project_code": project_code,
        "baseline_id": baseline_id,
        "expected_content_hash": expected_content_hash,
        "applied": applied,
        "duplicates": duplicates,
        "decision_counts": counts,
        "next_step": "rebuild the scope baseline, then confirm the new zero-pending baseline",
        "applied_migrations": applied_migrations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("decisions", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = apply_scope_item_decisions(args.database, load_json(args.decisions))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
