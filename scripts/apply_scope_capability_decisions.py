#!/usr/bin/env python3
"""Apply human decisions to candidate scope-capability mappings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect, dump_json, load_json, now_iso, stable_id


ALLOWED_DECISIONS = {"confirmed", "ignored", "conflict", "missing"}


def apply_decisions(database: Path, payload: dict[str, Any]) -> dict[str, Any]:
    project_code = str(payload.get("project_code", "")).strip()
    reviewed_by = str(payload.get("reviewed_by", "")).strip()
    reviewed_at = str(payload.get("reviewed_at", "")).strip() or now_iso()
    if not project_code or not reviewed_by:
        raise ValueError("project_code and reviewed_by are required")
    decisions = payload.get("decisions", [])
    updated = 0
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT project_id FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        for item in decisions:
            map_id = str(item.get("map_id", "")).strip()
            decision = str(item.get("decision", "")).strip()
            review_note = str(item.get("review_note", "")).strip()
            if decision not in ALLOWED_DECISIONS:
                raise ValueError(f"unsupported mapping decision: {decision}")
            row = conn.execute(
                "SELECT * FROM scope_product_map WHERE map_id=? AND project_id=?",
                (map_id, project["project_id"]),
            ).fetchone()
            if row is None:
                raise ValueError(f"mapping does not belong to project: {map_id}")
            before = dict(row)
            conn.execute(
                """
                UPDATE scope_product_map SET status=?,review_note=?,reviewed_by=?,
                  reviewed_at=?,updated_at=? WHERE map_id=?
                """,
                (decision, review_note, reviewed_by, reviewed_at, reviewed_at, map_id),
            )
            conn.execute(
                """
                INSERT INTO audit_log (
                  audit_id,project_id,target_type,target_id,action,before_value_json,
                  after_value_json,operator,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    stable_id("AUDIT", map_id, decision, reviewed_at),
                    project["project_id"], "scope_product_map", map_id,
                    f"mapping_review:{decision}", dump_json(before),
                    dump_json({"status": decision, "review_note": review_note}),
                    reviewed_by, reviewed_at,
                ),
            )
            updated += 1
        conn.commit()
    return {
        "project_code": project_code,
        "updated": updated,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "scope_policy": "mapping decisions never create or expand project scope",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("decisions", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = apply_decisions(args.database, load_json(args.decisions))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
