#!/usr/bin/env python3
"""Build a conservative reference-reuse review pack from non-project sources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_db import apply_migrations, connect, stable_id


FORBIDDEN = (
    "地域、单位、人员、现状、范围、投资、工期、指标、结论、专属政策、供应商和元数据"
)
ALLOWED_BY_CLASS = {
    "external_reference": "仅候选复用章节结构、论证功能和通用表格形式",
    "vendor_reference": "仅候选复用经核验的通用技术能力与接口线索",
    "policy_reference": "仅作为政策核验线索，正式引用必须进入政策证据流程",
    "format_reference": "仅复用经确认的样式与页面几何，不复用正文",
}


def build_workpack(database: Path, project_code: str) -> dict:
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT project_id FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        rows = conn.execute(
            """
            SELECT s.source_id,s.source_class,s.file_name,s.source_path,s.sha256,
                   b.block_id,b.source_location,b.section_role,b.review_status
            FROM source_document s
            LEFT JOIN corpus_document d ON d.source_id=s.source_id
            LEFT JOIN corpus_block b ON b.corpus_document_id=d.corpus_document_id
            WHERE s.project_id=? AND s.source_class<>'project_material'
            ORDER BY s.source_id,b.source_location,b.block_id
            """,
            (project["project_id"],),
        ).fetchall()
    items = []
    residue_candidates = set()
    for row in rows:
        source_stem = Path(row["file_name"]).stem.strip()
        if len(source_stem) >= 4:
            residue_candidates.add(source_stem)
        items.append(
            {
                "reference_id": stable_id(
                    "REFERENCE", row["source_id"], row["block_id"] or "document"
                ),
                "source_id": row["source_id"],
                "source_class": row["source_class"],
                "source_file": row["file_name"],
                "source_sha256": row["sha256"],
                "source_location": row["source_location"] or "document",
                "source_chapter": row["section_role"] or "",
                "target_chapter": "",
                "structure_similarity": "pending_confirmation",
                "business_similarity": "pending_confirmation",
                "reuse_type": "pending_confirmation",
                "allowed_content": ALLOWED_BY_CLASS.get(
                    row["source_class"], "仅作为受限线索"
                ),
                "forbidden_content": FORBIDDEN,
                "review_status": row["review_status"] or "pending",
            }
        )
    return {
        "schema_version": "1.0",
        "project_code": project_code,
        "reference_source_count": len({item["source_id"] for item in items}),
        "review_item_count": len(items),
        "status": "not_applicable" if not items else "pending_confirmation",
        "rules": [
            "No reference text is approved for direct reuse by this work pack.",
            "Project-specific facts remain prohibited even after structural reuse is approved.",
            "Residue candidates must be reviewed before adding them to the confirmed scan list.",
        ],
        "residue_candidates": sorted(residue_candidates),
        "items": items,
    }


def to_markdown(payload: dict) -> str:
    lines = [
        "# 参考方案复用地图",
        "",
        f"> 状态：{payload['status']}。所有条目默认未获准直接复用。",
        "",
        "| reference_id | source_id | source_chapter | target_chapter | structure_similarity | business_similarity | reuse_type | allowed_content | forbidden_content | notes |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in payload["items"]:
        values = [
            item["reference_id"],
            item["source_id"],
            item["source_chapter"],
            item["target_chapter"],
            item["structure_similarity"],
            item["business_similarity"],
            item["reuse_type"],
            item["allowed_content"],
            item["forbidden_content"],
            f"{item['source_class']}; {item['review_status']}",
        ]
        lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |")
    if not payload["items"]:
        lines.append("| - | - | - | - | - | - | not_applicable | 当前未识别到参考材料 | - | - |")
    lines.extend(["", "## 待确认残留词候选", ""])
    lines.extend(f"- {term}" for term in payload["residue_candidates"])
    if not payload["residue_candidates"]:
        lines.append("- 无")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-md", required=True, type=Path)
    args = parser.parse_args()
    result = build_workpack(args.database, args.project_code)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(to_markdown(result), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "reference_source_count", "review_item_count")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
