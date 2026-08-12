#!/usr/bin/env python3
"""Import a project, sources, facts, inferences, and confirmation questions from JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from knowledge_db import (
    STATUS_MAP,
    apply_migrations,
    connect,
    dump_json,
    load_json,
    now_iso,
    sha256_text,
    stable_id,
    upsert_project,
)


def _fact_status(value: str) -> str:
    return STATUS_MAP.get(value, value or "pending_supplement")


def _upsert_source(conn, project_id: str, source: dict[str, Any]) -> str:
    source_id = source.get("source_id") or stable_id(
        "SRC", project_id, source.get("file_name"), source.get("source_path"), source.get("official_url")
    )
    conn.execute(
        """
        INSERT INTO source_document (
          source_id, project_id, source_scope, source_class, file_name, file_type,
          source_path, official_url, issuer, document_date, statistical_date,
          sha256, usage_scope, restriction_note, contains_personal_data,
          verification_status, imported_at, metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_id) DO UPDATE SET
          project_id=excluded.project_id,
          source_class=excluded.source_class,
          file_name=excluded.file_name,
          file_type=excluded.file_type,
          source_path=excluded.source_path,
          official_url=excluded.official_url,
          issuer=excluded.issuer,
          document_date=excluded.document_date,
          statistical_date=excluded.statistical_date,
          sha256=excluded.sha256,
          usage_scope=excluded.usage_scope,
          restriction_note=excluded.restriction_note,
          contains_personal_data=excluded.contains_personal_data,
          verification_status=excluded.verification_status,
          metadata_json=excluded.metadata_json
        """,
        (
            source_id,
            project_id,
            source.get("source_scope", "project"),
            source.get("source_class", "project_material"),
            source.get("file_name", ""),
            source.get("file_type", ""),
            source.get("source_path", ""),
            source.get("official_url", ""),
            source.get("issuer", ""),
            source.get("document_date", ""),
            source.get("statistical_date", ""),
            source.get("sha256", ""),
            source.get("usage_scope", ""),
            source.get("restriction_note", ""),
            int(bool(source.get("contains_personal_data", False))),
            source.get("verification_status", "registered"),
            now_iso(),
            dump_json(source.get("metadata", {})),
        ),
    )
    return source_id


def ingest(database: Path, payload: dict[str, Any]) -> dict[str, Any]:
    with connect(database) as conn:
        apply_migrations(conn)
        project_id = upsert_project(conn, payload["project"])
        source_ids: dict[str, str] = {}
        for source in payload.get("sources", []):
            source_id = _upsert_source(conn, project_id, source)
            source_ids[source.get("source_id", source_id)] = source_id

        fact_count = 0
        evidence_count = 0
        for fact in payload.get("facts", []):
            fact_id = fact.get("fact_id") or stable_id(
                "FACT", project_id, fact.get("fact_key"), fact.get("fact_content")
            )
            timestamp = now_iso()
            conn.execute(
                """
                INSERT INTO project_fact (
                  fact_id, project_id, fact_key, fact_category, fact_content,
                  normalized_value, data_unit, statistical_date, fact_status,
                  materiality, confirmation_required, conflict_group_id,
                  allowed_chapters_json, sensitivity, frozen_version, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(fact_id) DO UPDATE SET
                  fact_key=excluded.fact_key,
                  fact_category=excluded.fact_category,
                  fact_content=excluded.fact_content,
                  normalized_value=excluded.normalized_value,
                  data_unit=excluded.data_unit,
                  statistical_date=excluded.statistical_date,
                  fact_status=excluded.fact_status,
                  materiality=excluded.materiality,
                  confirmation_required=excluded.confirmation_required,
                  conflict_group_id=excluded.conflict_group_id,
                  allowed_chapters_json=excluded.allowed_chapters_json,
                  sensitivity=excluded.sensitivity,
                  frozen_version=excluded.frozen_version,
                  updated_at=excluded.updated_at
                """,
                (
                    fact_id,
                    project_id,
                    fact.get("fact_key", fact_id),
                    fact.get("fact_category", "other"),
                    fact.get("fact_content", ""),
                    str(fact.get("normalized_value", "")),
                    fact.get("data_unit", ""),
                    fact.get("statistical_date", ""),
                    _fact_status(fact.get("fact_status", "pending_supplement")),
                    fact.get("materiality", "B"),
                    int(bool(fact.get("confirmation_required", True))),
                    fact.get("conflict_group_id", ""),
                    dump_json(fact.get("allowed_chapters", [])),
                    fact.get("sensitivity", "normal"),
                    fact.get("frozen_version", ""),
                    timestamp,
                    timestamp,
                ),
            )
            fact_count += 1

            for evidence in fact.get("evidence", []):
                source_ref = evidence.get("source_id", "")
                source_id = source_ids.get(source_ref, source_ref)
                if not source_id:
                    continue
                evidence_text = evidence.get("evidence_text", fact.get("fact_content", ""))
                evidence_id = evidence.get("evidence_id") or stable_id(
                    "EVID", source_id, evidence.get("source_location", ""), evidence_text
                )
                conn.execute(
                    """
                    INSERT INTO evidence_record (
                      evidence_id, source_id, source_location, evidence_text, evidence_hash,
                      extraction_method, reliability_level, verified_at, notes
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(evidence_id) DO UPDATE SET
                      source_location=excluded.source_location,
                      evidence_text=excluded.evidence_text,
                      evidence_hash=excluded.evidence_hash,
                      extraction_method=excluded.extraction_method,
                      reliability_level=excluded.reliability_level,
                      verified_at=excluded.verified_at,
                      notes=excluded.notes
                    """,
                    (
                        evidence_id,
                        source_id,
                        evidence.get("source_location", ""),
                        evidence_text,
                        sha256_text(evidence_text),
                        evidence.get("extraction_method", "manual"),
                        evidence.get("reliability_level", "B"),
                        evidence.get("verified_at"),
                        evidence.get("notes", ""),
                    ),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO fact_evidence(fact_id,evidence_id,evidence_role) VALUES(?,?,?)",
                    (fact_id, evidence_id, evidence.get("evidence_role", "support")),
                )
                evidence_count += 1

        inference_count = 0
        for item in payload.get("inferences", []):
            inference_id = item.get("inference_id") or stable_id(
                "INFER", project_id, item.get("proposition")
            )
            timestamp = now_iso()
            conn.execute(
                """
                INSERT INTO inference_record (
                  inference_id, project_id, proposition, inference_type, premise_ids_json,
                  reasoning_note, materiality, confirmation_required, allowed_expression,
                  status, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(inference_id) DO UPDATE SET
                  proposition=excluded.proposition,
                  inference_type=excluded.inference_type,
                  premise_ids_json=excluded.premise_ids_json,
                  reasoning_note=excluded.reasoning_note,
                  materiality=excluded.materiality,
                  confirmation_required=excluded.confirmation_required,
                  allowed_expression=excluded.allowed_expression,
                  status=excluded.status,
                  updated_at=excluded.updated_at
                """,
                (
                    inference_id,
                    project_id,
                    item.get("proposition", ""),
                    item.get("inference_type", "context_inference"),
                    dump_json(item.get("premise_ids", [])),
                    item.get("reasoning_note", ""),
                    item.get("materiality", "B"),
                    int(bool(item.get("confirmation_required", True))),
                    item.get("allowed_expression", ""),
                    item.get("status", "pending_confirmation"),
                    timestamp,
                    timestamp,
                ),
            )
            inference_count += 1

        question_count = 0
        for item in payload.get("questions", [])[:15]:
            question_id = item.get("question_id") or stable_id(
                "QUESTION", project_id, item.get("target_type"), item.get("target_id"), item.get("question_text")
            )
            timestamp = now_iso()
            conn.execute(
                """
                INSERT INTO confirmation_question (
                  question_id, project_id, target_type, target_id, question_text,
                  materiality, impact_type, status, batch_no, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(question_id) DO UPDATE SET
                  question_text=excluded.question_text,
                  materiality=excluded.materiality,
                  impact_type=excluded.impact_type,
                  status=excluded.status,
                  batch_no=excluded.batch_no,
                  updated_at=excluded.updated_at
                """,
                (
                    question_id,
                    project_id,
                    item.get("target_type", "fact"),
                    item.get("target_id", ""),
                    item.get("question_text", ""),
                    item.get("materiality", "B"),
                    item.get("impact_type", "text"),
                    item.get("status", "open"),
                    int(item.get("batch_no", 1)),
                    timestamp,
                    timestamp,
                ),
            )
            question_count += 1

        confirmation_count = 0
        for item in payload.get("confirmations", []):
            confirmation_id = item.get("confirmation_id") or stable_id(
                "CONFIRM",
                project_id,
                item.get("target_type"),
                item.get("target_id"),
                item.get("confirmed_at"),
                item.get("decision"),
            )
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
                    project_id,
                    item.get("target_type", "fact"),
                    item.get("target_id", ""),
                    item.get("decision", "confirm"),
                    dump_json(item.get("before_value", {})),
                    dump_json(item.get("after_value", {})),
                    item.get("decision_note", ""),
                    item.get("confirmed_by", ""),
                    item.get("confirmed_at", now_iso()),
                    item.get("baseline_version", payload["project"].get("baseline_version", "working")),
                ),
            )
            confirmation_count += 1

        conn.commit()
    return {
        "project_id": project_id,
        "sources": len(source_ids),
        "facts": fact_count,
        "evidence": evidence_count,
        "inferences": inference_count,
        "questions": question_count,
        "confirmations": confirmation_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = ingest(args.database, load_json(args.input))
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
