#!/usr/bin/env python3
"""Build a traceable candidate-fact work pack without asserting new facts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_db import apply_migrations, connect, fetch_all


def build_workpack(database: Path, project_code: str) -> dict:
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT * FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        blocks = fetch_all(
            conn,
            """
            SELECT b.block_id,b.source_location,b.section_role,b.clean_text,b.text_hash,
                   b.quality_level,b.review_status,d.corpus_document_id,s.source_id,
                   s.file_name,s.source_class,s.verification_status
            FROM corpus_block b
            JOIN corpus_document d ON d.corpus_document_id=b.corpus_document_id
            JOIN source_document s ON s.source_id=d.source_id
            WHERE s.project_id=? AND s.source_class='project_material'
              AND b.review_status NOT IN ('retired','prohibited')
            ORDER BY s.source_id,b.source_location,b.block_id
            """,
            (project["project_id"],),
        )
        scopes = fetch_all(
            conn,
            """
            SELECT scope_id,source_id,original_name,standard_name,domain,item_type,
                   construction_mode,quantity,unit,investment_category,
                   acceptance_target,status
            FROM project_scope_item WHERE project_id=?
            ORDER BY scope_id
            """,
            (project["project_id"],),
        )
    return {
        "schema_version": "1.0",
        "project": {
            "project_id": project["project_id"],
            "project_code": project["project_code"],
            "official_name": project["official_name"],
            "baseline_version": project["baseline_version"],
        },
        "rules": [
            "Each candidate fact must cite source_id and source_location.",
            "A text block is evidence input, not an automatically confirmed fact.",
            "Unknown, ambiguous, conflicting, investment, schedule and acceptance values remain pending.",
            "Use only the eight project fact statuses defined by the skill.",
            "Do not infer customer scope from product capabilities or reference reports.",
        ],
        "candidate_fact_schema": {
            "fact_key": "",
            "fact_value": "",
            "fact_unit": "",
            "statistical_date": "",
            "materiality": "C",
            "fact_status": "pending_confirmation",
            "source_id": "",
            "source_location": "",
            "notes": "",
        },
        "source_blocks": blocks,
        "scope_candidates": scopes,
        "counts": {"source_blocks": len(blocks), "scope_candidates": len(scopes)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_workpack(args.database, args.project_code)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
