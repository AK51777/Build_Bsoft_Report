#!/usr/bin/env python3
"""Build the problem-to-benefit traceability matrix without guessing links."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect, dump_json, load_json, now_iso, stable_id


FACT_FIELDS = (
    "problem_fact_id",
    "requirement_fact_id",
    "investment_fact_id",
    "indicator_fact_id",
    "benefit_fact_id",
)
REQUIRED_CHAIN = (
    "problem_fact_id",
    "requirement_fact_id",
    "scope_id",
    "investment_fact_id",
    "indicator_fact_id",
    "benefit_fact_id",
    "chapter_location",
)
CSV_FIELDS = (
    "traceability_id",
    "baseline_id",
    "problem_fact_id",
    "problem",
    "requirement_fact_id",
    "requirement",
    "scope_id",
    "construction_content",
    "investment_fact_id",
    "investment_category",
    "indicator_fact_id",
    "target_and_status",
    "evaluation_method",
    "benefit_fact_id",
    "expected_benefit",
    "chapter_location",
    "completeness",
    "missing_items",
    "status",
)


def validate_links(
    links: list[dict[str, Any]], scope_ids: set[str], facts: dict[str, dict]
) -> dict[str, dict[str, Any]]:
    by_scope: dict[str, dict[str, Any]] = {}
    for index, link in enumerate(links, start=1):
        if not isinstance(link, dict) or not link.get("scope_id"):
            raise ValueError(f"link {index} must contain scope_id")
        scope_id = str(link["scope_id"])
        if scope_id not in scope_ids:
            raise ValueError(f"link {index} references unknown baseline scope: {scope_id}")
        if scope_id in by_scope:
            raise ValueError(f"duplicate traceability link for scope: {scope_id}")
        for field in FACT_FIELDS:
            fact_id = str(link.get(field) or "")
            if fact_id and fact_id not in facts:
                raise ValueError(f"link {index} references unknown project fact: {fact_id}")
        by_scope[scope_id] = link
    return by_scope


def build_traceability_matrix(
    database: Path,
    project_code: str,
    *,
    links_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    links_payload = links_payload or {"links": []}
    links = links_payload.get("links", [])
    if not isinstance(links, list):
        raise ValueError("links payload must contain a links array")
    timestamp = now_iso()
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT project_id FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        baseline = conn.execute(
            """
            SELECT * FROM scope_baseline WHERE project_id=?
            ORDER BY CASE status WHEN 'confirmed' THEN 0 WHEN 'pending_confirmation' THEN 1 ELSE 2 END,
                     version_no DESC LIMIT 1
            """,
            (project["project_id"],),
        ).fetchone()
        if baseline is None:
            raise RuntimeError("scope baseline not found; build scope baseline first")
        scope_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT i.inclusion_status,s.* FROM scope_baseline_item i
                JOIN project_scope_item s ON s.scope_id=i.scope_id
                WHERE i.baseline_id=? AND i.inclusion_status<>'excluded'
                ORDER BY s.scope_id
                """,
                (baseline["baseline_id"],),
            )
        ]
        facts = {
            row["fact_id"]: dict(row)
            for row in conn.execute(
                "SELECT * FROM project_fact WHERE project_id=?",
                (project["project_id"],),
            )
        }
        links_by_scope = validate_links(
            links, {scope["scope_id"] for scope in scope_rows}, facts
        )
        rows = []
        for scope in scope_rows:
            link = links_by_scope.get(scope["scope_id"], {})
            values = {
                field: str(link.get(field) or "") for field in FACT_FIELDS
            }
            values["scope_id"] = scope["scope_id"]
            values["chapter_location"] = str(
                link.get("chapter_location") or scope["chapter_location"] or ""
            )
            missing = [field for field in REQUIRED_CHAIN if not values.get(field)]
            linked_facts = [facts[fact_id] for fact_id in values.values() if fact_id in facts]
            has_conflict = any(fact["fact_status"] == "conflict" for fact in linked_facts)
            completeness = not missing and not has_conflict
            status = "conflict" if has_conflict else "complete" if completeness else "incomplete"
            traceability_id = stable_id(
                "TRACE", baseline["baseline_id"], scope["scope_id"]
            )
            target_and_status = str(
                link.get("target_and_status") or scope["acceptance_target"] or ""
            )
            expected_benefit = str(link.get("expected_benefit") or "")
            evaluation_method = str(link.get("evaluation_method") or "")
            conn.execute(
                """
                INSERT INTO project_traceability_item (
                  traceability_id,project_id,baseline_id,scope_id,problem_fact_id,
                  requirement_fact_id,investment_fact_id,indicator_fact_id,
                  benefit_fact_id,construction_content,investment_category,
                  target_and_status,evaluation_method,expected_benefit,
                  chapter_location,completeness,missing_items_json,status,
                  created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(traceability_id) DO UPDATE SET
                  problem_fact_id=excluded.problem_fact_id,
                  requirement_fact_id=excluded.requirement_fact_id,
                  investment_fact_id=excluded.investment_fact_id,
                  indicator_fact_id=excluded.indicator_fact_id,
                  benefit_fact_id=excluded.benefit_fact_id,
                  construction_content=excluded.construction_content,
                  investment_category=excluded.investment_category,
                  target_and_status=excluded.target_and_status,
                  evaluation_method=excluded.evaluation_method,
                  expected_benefit=excluded.expected_benefit,
                  chapter_location=excluded.chapter_location,
                  completeness=excluded.completeness,
                  missing_items_json=excluded.missing_items_json,
                  status=excluded.status,
                  updated_at=excluded.updated_at
                """,
                (
                    traceability_id,
                    project["project_id"],
                    baseline["baseline_id"],
                    scope["scope_id"],
                    values["problem_fact_id"] or None,
                    values["requirement_fact_id"] or None,
                    values["investment_fact_id"] or None,
                    values["indicator_fact_id"] or None,
                    values["benefit_fact_id"] or None,
                    scope["standard_name"],
                    scope["investment_category"],
                    target_and_status,
                    evaluation_method,
                    expected_benefit,
                    values["chapter_location"],
                    int(completeness),
                    dump_json(missing),
                    status,
                    timestamp,
                    timestamp,
                ),
            )
            rows.append(
                {
                    "traceability_id": traceability_id,
                    "baseline_id": baseline["baseline_id"],
                    "problem_fact_id": values["problem_fact_id"],
                    "problem": facts.get(values["problem_fact_id"], {}).get("fact_content", ""),
                    "requirement_fact_id": values["requirement_fact_id"],
                    "requirement": facts.get(values["requirement_fact_id"], {}).get("fact_content", ""),
                    "scope_id": scope["scope_id"],
                    "construction_content": scope["standard_name"],
                    "investment_fact_id": values["investment_fact_id"],
                    "investment_category": scope["investment_category"],
                    "indicator_fact_id": values["indicator_fact_id"],
                    "target_and_status": target_and_status,
                    "evaluation_method": evaluation_method,
                    "benefit_fact_id": values["benefit_fact_id"],
                    "expected_benefit": expected_benefit,
                    "chapter_location": values["chapter_location"],
                    "completeness": completeness,
                    "missing_items": missing,
                    "status": status,
                }
            )
        conn.commit()
    return {
        "project_code": project_code,
        "baseline_id": baseline["baseline_id"],
        "baseline_status": baseline["status"],
        "row_count": len(rows),
        "complete_count": sum(row["status"] == "complete" for row in rows),
        "incomplete_count": sum(row["status"] == "incomplete" for row in rows),
        "conflict_count": sum(row["status"] == "conflict" for row in rows),
        "rows": rows,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "completeness": "complete" if row["completeness"] else "incomplete",
                    "missing_items": ";".join(row["missing_items"]),
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--links", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-csv", type=Path)
    args = parser.parse_args()
    result = build_traceability_matrix(
        args.database,
        args.project_code,
        links_payload=load_json(args.links) if args.links else None,
    )
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.output_csv:
        write_csv(args.output_csv, result["rows"])
    print(
        json.dumps(
            {key: result[key] for key in ("baseline_id", "row_count", "complete_count", "incomplete_count", "conflict_count")},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
