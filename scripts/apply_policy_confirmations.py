#!/usr/bin/env python3
"""Apply policy workbook decisions to one exact, latest policy match run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect, dump_json, load_json, now_iso, stable_id


def _required(item: dict[str, Any], key: str) -> str:
    value = str(item.get(key, "")).strip()
    if not value:
        raise ValueError(f"policy confirmation is missing {key}: {item.get('policy_id', '')}")
    return value


def _policy_snapshot(rows) -> dict[str, Any]:
    first = rows[0]
    return {
        "policy_id": first["policy_id"],
        "basis_use": bool(first["basis_use"]),
        "background_use": bool(first["background_use"]),
        "basis_order": first["basis_order"],
        "decision_status": first["decision_status"],
        "clause_count": len(rows),
    }


def _place_reordered(current: list[str], desired: dict[str, int]) -> list[str]:
    if not desired:
        return current
    size = len(current)
    positions = list(desired.values())
    if any(position < 1 or position > size for position in positions):
        raise ValueError(f"user order must be between 1 and {size}")
    if len(set(positions)) != len(positions):
        raise ValueError("user order values must be unique")
    output: list[str | None] = [None] * size
    for policy_id, position in desired.items():
        if policy_id not in current:
            raise ValueError(f"cannot reorder a policy outside the current basis: {policy_id}")
        output[position - 1] = policy_id
    remaining = [policy_id for policy_id in current if policy_id not in desired]
    iterator = iter(remaining)
    return [value if value is not None else next(iterator) for value in output]


def apply_policy_confirmations(database: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("pack_type") != "policy_confirmation":
        raise ValueError("input is not a policy_confirmation data pack")
    project_code = str(payload.get("project_code", "")).strip()
    match_run_id = str(payload.get("match_run_id", "")).strip()
    if not project_code or not match_run_id:
        raise ValueError("project_code and match_run_id are required")

    applied = 0
    duplicates = 0
    desired_orders: dict[str, int] = {}
    with connect(database) as conn:
        apply_migrations(conn)
        project = conn.execute("SELECT * FROM project WHERE project_code=?", (project_code,)).fetchone()
        if not project:
            raise ValueError(f"project not found: {project_code}")
        project_id = project["project_id"]
        latest = conn.execute(
            """
            SELECT match_run_id FROM policy_match_run
            WHERE project_id=? AND status='completed'
            ORDER BY completed_at DESC,started_at DESC,match_run_id DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        if not latest or latest["match_run_id"] != match_run_id:
            raise ValueError(
                f"stale policy workbook: workbook={match_run_id}, latest={latest['match_run_id'] if latest else ''}"
            )

        for item in payload.get("decisions", []):
            policy_id = _required(item, "policy_id")
            decision = _required(item, "decision")
            confirmed_by = _required(item, "confirmed_by")
            confirmed_at = _required(item, "confirmed_at")
            rows = conn.execute(
                """
                SELECT m.*,p.validity_status,p.verification_status,p.official_url,
                       c.verification_status AS clause_verification_status
                FROM project_policy_match m JOIN policy_document p ON p.policy_id=m.policy_id
                JOIN policy_clause c ON c.clause_id=m.clause_id
                WHERE m.match_run_id=? AND m.policy_id=? ORDER BY m.sort_key,m.clause_id
                """,
                (match_run_id, policy_id),
            ).fetchall()
            if not rows:
                raise ValueError(f"policy not found in match run: {policy_id}")
            before = _policy_snapshot(rows)
            if decision in {"confirm", "reorder"}:
                if not before["basis_use"]:
                    raise ValueError(f"non-basis policy cannot be confirmed or reordered: {policy_id}")
                if any(
                    row["validity_status"] != "current"
                    or row["verification_status"] != "verified"
                    or row["clause_verification_status"] != "verified"
                    or not row["official_url"]
                    for row in rows
                ):
                    raise ValueError(f"policy fails current/verified/official-url gate: {policy_id}")
                next_status = "user_confirmed"
                basis_use = 1
                background_use = before["background_use"]
            elif decision == "exclude":
                next_status = "user_excluded"
                basis_use = 0
                background_use = 0
            elif decision == "defer":
                next_status = "needs_confirmation"
                basis_use = before["basis_use"]
                background_use = before["background_use"]
            else:
                raise ValueError(f"unsupported policy decision: {decision}")
            if decision == "reorder":
                user_order = item.get("user_order")
                if not isinstance(user_order, int) or user_order < 1:
                    raise ValueError(f"positive integer user_order is required: {policy_id}")
                desired_orders[policy_id] = user_order
            after = {
                **before,
                "basis_use": bool(basis_use),
                "background_use": bool(background_use),
                "decision_status": next_status,
                "user_order": item.get("user_order"),
            }
            confirmation_decision = {
                "confirm": "confirm",
                "exclude": "exclude",
                "reorder": "modify",
                "defer": "defer",
            }[decision]
            confirmation_id = stable_id(
                "CONFIRM", project_id, "policy", policy_id, match_run_id,
                confirmation_decision, confirmed_at
            )
            if conn.execute(
                "SELECT 1 FROM confirmation_record WHERE confirmation_id=?", (confirmation_id,)
            ).fetchone():
                duplicates += 1
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
                    project_id,
                    "policy",
                    policy_id,
                    confirmation_decision,
                    dump_json(before),
                    dump_json(after),
                    str(item.get("decision_note", "")),
                    confirmed_by,
                    confirmed_at,
                    project["baseline_version"],
                ),
            )
            conn.execute(
                """
                UPDATE project_policy_match SET basis_use=?,background_use=?,decision_status=?,
                  decision_reason=?,updated_at=? WHERE match_run_id=? AND policy_id=?
                """,
                (
                    basis_use,
                    background_use,
                    next_status,
                    str(item.get("decision_note", "")),
                    now_iso(),
                    match_run_id,
                    policy_id,
                ),
            )
            if decision == "exclude":
                conn.execute(
                    """
                    UPDATE policy_citation SET citation_status='rejected'
                    WHERE match_id IN (
                      SELECT match_id FROM project_policy_match WHERE match_run_id=? AND policy_id=?
                    )
                    """,
                    (match_run_id, policy_id),
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
                    project_id,
                    "policy",
                    policy_id,
                    f"policy_confirmation:{decision}",
                    dump_json(before),
                    dump_json(after),
                    confirmed_by,
                    confirmed_at,
                ),
            )
            applied += 1

        current_basis = [
            row["policy_id"]
            for row in conn.execute(
                """
                SELECT policy_id,MIN(COALESCE(basis_order,999999)) AS order_no,MIN(sort_key) AS sort_key
                FROM project_policy_match WHERE match_run_id=? AND basis_use=1
                GROUP BY policy_id ORDER BY order_no,sort_key,policy_id
                """,
                (match_run_id,),
            ).fetchall()
        ]
        ordered = _place_reordered(current_basis, desired_orders)
        conn.execute(
            "UPDATE project_policy_match SET basis_order=NULL WHERE match_run_id=?",
            (match_run_id,),
        )
        for index, policy_id in enumerate(ordered, 1):
            conn.execute(
                "UPDATE project_policy_match SET basis_order=? WHERE match_run_id=? AND policy_id=? AND basis_use=1",
                (index, match_run_id, policy_id),
            )
        run_summary_row = conn.execute(
            "SELECT summary_json FROM policy_match_run WHERE match_run_id=?", (match_run_id,)
        ).fetchone()
        run_summary = json.loads(run_summary_row["summary_json"] or "{}")
        run_summary.update(
            {
                "basis_policy_order": ordered,
                "user_confirmed_policy_count": conn.execute(
                    """
                    SELECT COUNT(DISTINCT policy_id) FROM project_policy_match
                    WHERE match_run_id=? AND basis_use=1 AND decision_status='user_confirmed'
                    """,
                    (match_run_id,),
                ).fetchone()[0],
                "user_excluded_policy_count": conn.execute(
                    """
                    SELECT COUNT(DISTINCT policy_id) FROM project_policy_match
                    WHERE match_run_id=? AND decision_status='user_excluded'
                    """,
                    (match_run_id,),
                ).fetchone()[0],
            }
        )
        conn.execute(
            "UPDATE policy_match_run SET summary_json=? WHERE match_run_id=?",
            (dump_json(run_summary), match_run_id),
        )
        conn.commit()

    return {
        "project_code": project_code,
        "match_run_id": match_run_id,
        "decisions_applied": applied,
        "duplicates_ignored": duplicates,
        "basis_policy_order": ordered,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = apply_policy_confirmations(args.database, load_json(args.input))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
