#!/usr/bin/env python3
"""Save a versioned section draft and its reproducibility metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_db import (
    apply_migrations,
    connect,
    dump_json,
    load_json,
    now_iso,
    sha256_text,
    stable_id,
)


def save_draft(
    database: Path,
    project_code: str,
    chapter_code: str,
    content: str,
    input_package: dict,
    *,
    provider: str,
    model: str,
    prompt_version: str,
    source_type: str = "ai",
    created_by: str = "codex",
    duration_ms: int = 0,
) -> dict:
    if source_type not in {"ai", "manual"}:
        raise ValueError("source_type must be ai or manual")
    if not content.strip():
        raise ValueError("draft content must not be empty")
    input_text = json.dumps(input_package, ensure_ascii=False, sort_keys=True)
    input_hash = sha256_text(input_text)
    output_hash = sha256_text(content)
    timestamp = now_iso()
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT project_id FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        plan = conn.execute(
            """
            SELECT * FROM section_composition_plan
            WHERE project_id=? AND chapter_code=?
            ORDER BY version_no DESC LIMIT 1
            """,
            (project["project_id"], chapter_code),
        ).fetchone()
        if plan is None:
            raise RuntimeError(f"section plan not found for chapter {chapter_code}")
        package_plan = input_package.get("plan", {})
        package_project = input_package.get("project", {})
        if package_plan.get("plan_id") != plan["plan_id"]:
            raise ValueError("input package plan_id does not match the active section plan")
        if package_project.get("project_code") != project_code:
            raise ValueError("input package project_code does not match")
        if source_type == "ai" and plan["status"] not in {"ready", "draft"}:
            raise RuntimeError(
                f"AI draft blocked: section plan status is {plan['status']}"
            )
        existing = conn.execute(
            "SELECT * FROM draft_section_version WHERE plan_id=? ORDER BY version_no",
            (plan["plan_id"],),
        ).fetchall()
        for row in existing:
            if row["source_type"] == source_type and sha256_text(row["content"]) == output_hash:
                return {
                    "project_code": project_code,
                    "chapter_code": chapter_code,
                    "plan_id": plan["plan_id"],
                    "draft_version_id": row["draft_version_id"],
                    "version_no": row["version_no"],
                    "status": row["status"],
                    "created": False,
                    "input_hash": input_hash,
                    "output_hash": output_hash,
                }
        version_no = max((row["version_no"] for row in existing), default=0) + 1
        draft_version_id = stable_id("DRAFT", plan["plan_id"], version_no, output_hash)
        conn.execute(
            """
            INSERT INTO draft_section_version (
              draft_version_id,plan_id,version_no,source_type,content,status,
              check_result_json,created_by,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                draft_version_id,
                plan["plan_id"],
                version_no,
                source_type,
                content,
                "needs_review",
                dump_json(
                    {
                        "input_hash": input_hash,
                        "output_hash": output_hash,
                        "prompt_version": prompt_version,
                    }
                ),
                created_by,
                timestamp,
                timestamp,
            ),
        )
        llm_call_id = stable_id(
            "LLMCALL", plan["plan_id"], prompt_version, input_hash, output_hash
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO llm_call_log (
              llm_call_id,project_id,stage_code,provider,model,prompt_version,
              input_hash,output_hash,status,error_message,duration_ms,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                llm_call_id,
                project["project_id"],
                f"S6_DRAFT:{chapter_code}",
                provider,
                model,
                prompt_version,
                input_hash,
                output_hash,
                "completed",
                "",
                duration_ms,
                timestamp,
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
                stable_id("AUDIT", draft_version_id, "create", timestamp),
                project["project_id"],
                "draft_section_version",
                draft_version_id,
                "create",
                "{}",
                dump_json({"version_no": version_no, "status": "needs_review"}),
                created_by,
                timestamp,
            ),
        )
        conn.execute(
            "UPDATE section_composition_plan SET status='draft',updated_at=? WHERE plan_id=?",
            (timestamp, plan["plan_id"]),
        )
        conn.commit()
    return {
        "project_code": project_code,
        "chapter_code": chapter_code,
        "plan_id": plan["plan_id"],
        "draft_version_id": draft_version_id,
        "version_no": version_no,
        "status": "needs_review",
        "created": True,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "llm_call_id": llm_call_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("chapter_code")
    parser.add_argument("content", type=Path)
    parser.add_argument("input_package", type=Path)
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt-version", required=True)
    parser.add_argument("--source-type", choices=("ai", "manual"), default="ai")
    parser.add_argument("--created-by", default="codex")
    parser.add_argument("--duration-ms", type=int, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = save_draft(
        args.database,
        args.project_code,
        args.chapter_code,
        args.content.read_text(encoding="utf-8-sig"),
        load_json(args.input_package),
        provider=args.provider,
        model=args.model,
        prompt_version=args.prompt_version,
        source_type=args.source_type,
        created_by=args.created_by,
        duration_ms=args.duration_ms,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
