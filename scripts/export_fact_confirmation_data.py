#!/usr/bin/env python3
"""Export project fact, inference, conflict, and question data for the Excel confirmation pack."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_db import apply_migrations, connect


STATUS_ZH = {
    "confirmed": "【已确认】",
    "material_explicit": "【材料明确】",
    "pending_confirmation": "【待确认】",
    "pending_supplement": "【待补充】",
    "conflict": "【冲突】",
    "analysis_recommendation": "【分析建议】",
    "reference_only": "【仅作参考】",
    "not_applicable": "【不适用】",
}


def export(database: Path, project_code: str) -> dict:
    with connect(database) as conn:
        apply_migrations(conn)
        project = conn.execute("SELECT * FROM project WHERE project_code=?", (project_code,)).fetchone()
        if not project:
            raise SystemExit(f"未找到项目：{project_code}")
        project_id = project["project_id"]
        facts = []
        for row in conn.execute(
            """
            SELECT f.*,
                   GROUP_CONCAT(DISTINCT s.file_name) AS source_files,
                   GROUP_CONCAT(DISTINCT e.source_location) AS source_locations,
                   GROUP_CONCAT(DISTINCT e.evidence_text) AS evidence_texts
            FROM project_fact f
            LEFT JOIN fact_evidence fe ON fe.fact_id=f.fact_id
            LEFT JOIN evidence_record e ON e.evidence_id=fe.evidence_id
            LEFT JOIN source_document s ON s.source_id=e.source_id
            WHERE f.project_id=?
            GROUP BY f.fact_id
            ORDER BY CASE f.materiality WHEN 'A' THEN 1 WHEN 'B' THEN 2 WHEN 'C' THEN 3 ELSE 4 END,
                     f.fact_id
            """,
            (project_id,),
        ):
            item = dict(row)
            item["fact_status_zh"] = STATUS_ZH.get(item["fact_status"], item["fact_status"])
            facts.append(item)
        inferences = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM inference_record WHERE project_id=? ORDER BY materiality,inference_id",
                (project_id,),
            )
        ]
        questions = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM confirmation_question WHERE project_id=? ORDER BY batch_no,materiality,question_id",
                (project_id,),
            )
        ]
        confirmations = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM confirmation_record WHERE project_id=? ORDER BY confirmed_at",
                (project_id,),
            )
        ]
    return {
        "project": dict(project),
        "facts": facts,
        "inferences": inferences,
        "questions": questions,
        "confirmations": confirmations,
        "decision_options": ["确认", "否决", "修改", "暂缓"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = export(args.database, args.project_code)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "facts": len(payload["facts"]), "inferences": len(payload["inferences"]), "questions": len(payload["questions"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
