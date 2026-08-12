#!/usr/bin/env python3
"""Apply extracted fact/inference decisions as append-only confirmations."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect, dump_json, load_json, now_iso, stable_id


FACT_STATUS = {
    "confirm": "confirmed",
    "reject": "not_applicable",
    "modify": "confirmed",
    "defer": "pending_confirmation",
}
INFERENCE_STATUS = {
    "confirm": "confirmed",
    "reject": "rejected",
    "modify": "confirmed",
    "defer": "pending_confirmation",
}
PLACEHOLDER_TERMS = (
    "待补充",
    "待确认",
    "尚未确定",
    "尚未提供",
    "尚未明确",
    "未提供",
    "未明确",
    "未知",
    "不详",
    "待定",
    "暂缺",
    "暂无",
    "未定",
    "TBD",
    "TBC",
    "N/A",
    "【待",
    "【冲突】",
)


def _is_placeholder(value: Any) -> bool:
    text = str(value or "").strip()
    normalized = text.upper()
    return not text or any(term.upper() in normalized for term in PLACEHOLDER_TERMS)


def _validate_statistical_date(value: str, target_id: str) -> None:
    if not value:
        return
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"proposed statistical date must use YYYY-MM-DD: {target_id}={value}"
        ) from exc


def _required(item: dict[str, Any], key: str) -> str:
    value = str(item.get(key, "")).strip()
    if not value:
        raise ValueError(f"confirmation item is missing {key}: {item.get('target_id', '')}")
    return value


def _insert_confirmation(
    conn,
    *,
    project_id: str,
    baseline_version: str,
    target_type: str,
    target_id: str,
    decision: str,
    before: dict[str, Any],
    after: dict[str, Any],
    decision_note: str,
    confirmed_by: str,
    confirmed_at: str,
) -> tuple[str, bool]:
    confirmation_id = stable_id(
        "CONFIRM", project_id, target_type, target_id, decision, confirmed_at
    )
    if conn.execute(
        "SELECT 1 FROM confirmation_record WHERE confirmation_id=?", (confirmation_id,)
    ).fetchone():
        return confirmation_id, False
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
            target_type,
            target_id,
            decision,
            dump_json(before),
            dump_json(after),
            decision_note,
            confirmed_by,
            confirmed_at,
            baseline_version,
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
            project_id,
            target_type,
            target_id,
            f"confirmation:{decision}",
            dump_json(before),
            dump_json(after),
            confirmed_by,
            confirmed_at,
        ),
    )
    return confirmation_id, True


def _record_fact_confirmation_evidence(
    conn,
    *,
    project_id: str,
    fact_id: str,
    confirmation_id: str,
    decision: str,
    after: dict[str, Any],
    confirmed_by: str,
    confirmed_at: str,
) -> None:
    """Link the user's confirmed value as fresh evidence without altering prior evidence."""
    source_id = stable_id("SOURCE", "fact_confirmation", confirmation_id)
    evidence_id = stable_id("EVIDENCE", "fact_confirmation", confirmation_id)
    parts = [f"用户{confirmed_by}于{confirmed_at}确认事实：{after['fact_content']}"]
    if after.get("normalized_value"):
        parts.append(f"标准值：{after['normalized_value']}")
    if after.get("data_unit"):
        parts.append(f"单位：{after['data_unit']}")
    if after.get("statistical_date"):
        parts.append(f"统计时点：{after['statistical_date']}")
    evidence_text = "；".join(parts)
    conn.execute(
        """
        INSERT OR IGNORE INTO source_document (
          source_id,project_id,source_scope,source_class,file_name,file_type,
          verification_status,imported_at,metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            source_id,
            project_id,
            "project",
            "user_confirmation",
            f"事实核验确认记录-{confirmation_id}",
            "CONFIRMATION",
            "verified",
            confirmed_at,
            dump_json({"confirmation_id": confirmation_id, "decision": decision}),
        ),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO evidence_record (
          evidence_id,source_id,source_location,evidence_text,evidence_hash,
          extraction_method,reliability_level,verified_at,notes
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            evidence_id,
            source_id,
            "事实核验包用户决定",
            evidence_text,
            hashlib.sha256(evidence_text.encode("utf-8")).hexdigest(),
            "user_confirmation",
            "A",
            confirmed_at,
            f"由确认记录{confirmation_id}生成；保留修改前证据，不覆盖。",
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO fact_evidence (fact_id,evidence_id,evidence_role) VALUES (?,?,?)",
        (fact_id, evidence_id, "support"),
    )


def apply_confirmations(database: Path, payload: dict[str, Any], freeze: bool = False) -> dict[str, Any]:
    if payload.get("pack_type") != "fact_confirmation":
        raise ValueError("input is not a fact_confirmation data pack")
    project_code = str(payload.get("project_code", "")).strip()
    baseline_version = str(payload.get("baseline_version", "")).strip()
    if not project_code or not baseline_version:
        raise ValueError("project_code and baseline_version are required")

    applied = 0
    duplicates = 0
    answered = 0
    with connect(database) as conn:
        apply_migrations(conn)
        project = conn.execute("SELECT * FROM project WHERE project_code=?", (project_code,)).fetchone()
        if not project:
            raise ValueError(f"project not found: {project_code}")
        if project["baseline_version"] != baseline_version:
            raise ValueError(
                f"stale confirmation pack: workbook={baseline_version}, database={project['baseline_version']}"
            )
        project_id = project["project_id"]

        for item in payload.get("decisions", []):
            target_type = str(item.get("target_type", "")).strip()
            target_id = _required(item, "target_id")
            decision = _required(item, "decision")
            confirmed_by = _required(item, "confirmed_by")
            confirmed_at = _required(item, "confirmed_at")
            proposed = str(item.get("proposed_value", "")).strip()
            proposed_normalized = str(item.get("proposed_normalized_value", "")).strip()
            proposed_unit = str(item.get("proposed_data_unit", "")).strip()
            proposed_date = str(item.get("proposed_statistical_date", "")).strip()
            decision_note = str(item.get("decision_note", "")).strip()
            if decision not in FACT_STATUS:
                raise ValueError(f"unsupported decision: {decision}")
            if decision == "modify" and not proposed:
                raise ValueError(f"modified value is required: {target_id}")
            if decision == "reject" and not decision_note:
                raise ValueError(f"rejection reason is required: {target_id}")
            if decision == "modify":
                _validate_statistical_date(proposed_date, target_id)

            if target_type == "fact":
                row = conn.execute(
                    "SELECT * FROM project_fact WHERE project_id=? AND fact_id=?",
                    (project_id, target_id),
                ).fetchone()
                if not row:
                    raise ValueError(f"fact not found in project: {target_id}")
                before = dict(row)
                unresolved_before = (
                    before["fact_status"] in {"pending_supplement", "conflict"}
                    or _is_placeholder(before["fact_content"])
                    or _is_placeholder(before["normalized_value"])
                )
                if decision == "confirm" and unresolved_before:
                    raise ValueError(
                        f"unresolved or conflicting fact must be modified with an explicit value: {target_id}"
                    )
                if decision == "modify" and unresolved_before and _is_placeholder(proposed_normalized):
                    raise ValueError(
                        f"modified unresolved fact requires a non-placeholder normalized value: {target_id}"
                    )
                after = {
                    **before,
                    "fact_content": proposed if decision == "modify" else before["fact_content"],
                    "normalized_value": (
                        proposed_normalized if decision == "modify" and proposed_normalized
                        else before["normalized_value"]
                    ),
                    "data_unit": (
                        proposed_unit if decision == "modify" and proposed_unit else before["data_unit"]
                    ),
                    "statistical_date": (
                        proposed_date if decision == "modify" and proposed_date
                        else before["statistical_date"]
                    ),
                    "fact_status": FACT_STATUS[decision],
                    "confirmation_required": 1 if decision == "defer" else 0,
                    "frozen_version": "" if decision == "defer" else baseline_version,
                }
                confirmation_id, inserted = _insert_confirmation(
                    conn,
                    project_id=project_id,
                    baseline_version=baseline_version,
                    target_type=target_type,
                    target_id=target_id,
                    decision=decision,
                    before=before,
                    after=after,
                    decision_note=decision_note,
                    confirmed_by=confirmed_by,
                    confirmed_at=confirmed_at,
                )
                if not inserted:
                    duplicates += 1
                    continue
                conn.execute(
                    """
                    UPDATE project_fact SET fact_content=?,normalized_value=?,data_unit=?,statistical_date=?,
                      fact_status=?,confirmation_required=?,frozen_version=?,updated_at=? WHERE fact_id=?
                    """,
                    (
                        after["fact_content"],
                        after["normalized_value"],
                        after["data_unit"],
                        after["statistical_date"],
                        after["fact_status"],
                        after["confirmation_required"],
                        after["frozen_version"],
                        now_iso(),
                        target_id,
                    ),
                )
                if decision in {"confirm", "modify"}:
                    _record_fact_confirmation_evidence(
                        conn,
                        project_id=project_id,
                        fact_id=target_id,
                        confirmation_id=confirmation_id,
                        decision=decision,
                        after=after,
                        confirmed_by=confirmed_by,
                        confirmed_at=confirmed_at,
                    )
            elif target_type == "inference":
                row = conn.execute(
                    "SELECT * FROM inference_record WHERE project_id=? AND inference_id=?",
                    (project_id, target_id),
                ).fetchone()
                if not row:
                    raise ValueError(f"inference not found in project: {target_id}")
                before = dict(row)
                after = {
                    **before,
                    "proposition": proposed if decision == "modify" else before["proposition"],
                    "status": INFERENCE_STATUS[decision],
                    "confirmation_required": 1 if decision == "defer" else 0,
                }
                _, inserted = _insert_confirmation(
                    conn,
                    project_id=project_id,
                    baseline_version=baseline_version,
                    target_type=target_type,
                    target_id=target_id,
                    decision=decision,
                    before=before,
                    after=after,
                    decision_note=decision_note,
                    confirmed_by=confirmed_by,
                    confirmed_at=confirmed_at,
                )
                if not inserted:
                    duplicates += 1
                    continue
                conn.execute(
                    """
                    UPDATE inference_record SET proposition=?,status=?,confirmation_required=?,updated_at=?
                    WHERE inference_id=?
                    """,
                    (
                        after["proposition"],
                        after["status"],
                        after["confirmation_required"],
                        now_iso(),
                        target_id,
                    ),
                )
            else:
                raise ValueError(f"unsupported target_type: {target_type}")

            question_status = "deferred" if decision == "defer" else "answered"
            conn.execute(
                """
                UPDATE confirmation_question SET status=?,updated_at=?
                WHERE project_id=? AND target_id=? AND status='open'
                """,
                (question_status, now_iso(), project_id, target_id),
            )
            applied += 1

        for item in payload.get("question_answers", []):
            question_id = _required(item, "question_id")
            answer = _required(item, "answer")
            confirmed_by = _required(item, "confirmed_by")
            confirmed_at = _required(item, "confirmed_at")
            row = conn.execute(
                "SELECT * FROM confirmation_question WHERE project_id=? AND question_id=?",
                (project_id, question_id),
            ).fetchone()
            if not row:
                raise ValueError(f"question not found in project: {question_id}")
            before = dict(row)
            after = {**before, "status": "answered", "answer": answer}
            _, inserted = _insert_confirmation(
                conn,
                project_id=project_id,
                baseline_version=baseline_version,
                target_type="question",
                target_id=question_id,
                decision="confirm",
                before=before,
                after=after,
                decision_note=str(item.get("decision_note", "")),
                confirmed_by=confirmed_by,
                confirmed_at=confirmed_at,
            )
            if not inserted:
                duplicates += 1
                continue
            conn.execute(
                "UPDATE confirmation_question SET status='answered',updated_at=? WHERE question_id=?",
                (now_iso(), question_id),
            )
            answered += 1

        unresolved_facts = conn.execute(
            """
            SELECT COUNT(*) FROM project_fact
            WHERE project_id=? AND materiality IN ('A','B')
              AND fact_status NOT IN ('confirmed','material_explicit','not_applicable')
            """,
            (project_id,),
        ).fetchone()[0]
        unresolved_inferences = conn.execute(
            """
            SELECT COUNT(*) FROM inference_record
            WHERE project_id=? AND materiality IN ('A','B')
              AND confirmation_required=1 AND status='pending_confirmation'
            """,
            (project_id,),
        ).fetchone()[0]
        unresolved = int(unresolved_facts) + int(unresolved_inferences)
        if freeze:
            if unresolved:
                raise ValueError(f"baseline cannot be frozen; unresolved A/B items: {unresolved}")
            before_project = dict(project)
            conn.execute(
                "UPDATE project SET status='frozen',updated_at=? WHERE project_id=?",
                (now_iso(), project_id),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO audit_log (
                  audit_id,project_id,target_type,target_id,action,
                  before_value_json,after_value_json,operator,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    stable_id("AUDIT", project_id, baseline_version, "freeze"),
                    project_id,
                    "project",
                    project_id,
                    "freeze_fact_baseline",
                    dump_json(before_project),
                    dump_json({**before_project, "status": "frozen"}),
                    "confirmation_batch",
                    now_iso(),
                ),
            )
        conn.commit()

    return {
        "project_code": project_code,
        "baseline_version": baseline_version,
        "decisions_applied": applied,
        "question_answers_applied": answered,
        "duplicates_ignored": duplicates,
        "unresolved_ab_items": unresolved,
        "baseline_frozen": bool(freeze),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("input", type=Path)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = apply_confirmations(args.database, load_json(args.input), args.freeze)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
