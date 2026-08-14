#!/usr/bin/env python3
"""Deterministically match verified policy clauses to a project's jurisdiction and topics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect, dump_json, now_iso, stable_id


JURISDICTION_RANK = {"national": 10, "province": 20, "prefecture": 30, "county": 40, "other": 90}
BACKGROUND_POLICY_TYPES = {"law", "regulation", "plan", "opinion", "guidance", "policy"}
MATCHER_VERSION = "p1-rules-20260804"


def _jurisdiction_applies(project_code: str, level: str, policy_code: str) -> bool:
    if level == "national":
        return True
    if not project_code or not policy_code:
        return False
    if level == "province":
        return project_code[:2] == policy_code[:2]
    if level == "prefecture":
        return project_code[:4] == policy_code[:4]
    if level == "county":
        return project_code[:6] == policy_code[:6]
    return False


def _sort_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            f"{int(row['authority_group']):03d}",
            f"{JURISDICTION_RANK.get(row['jurisdiction_level'], 90):03d}",
            f"{int(row['authority_rank']):03d}",
            row.get("publish_date") or "9999-99-99",
            row.get("document_no") or "~",
            row.get("title") or "~",
            row.get("article_path") or "~",
        ]
    )


def match(database: Path, project_code: str, topics: set[str], output_json: Path | None, output_md: Path | None) -> dict:
    with connect(database) as conn:
        apply_migrations(conn)
        project = conn.execute("SELECT * FROM project WHERE project_code=?", (project_code,)).fetchone()
        if not project:
            raise SystemExit(f"未找到项目：{project_code}")
        run_started_at = now_iso()
        match_run_id = stable_id(
            "PMRUN", project["project_id"], MATCHER_VERSION, ",".join(sorted(topics)), run_started_at
        )
        conn.execute(
            """
            INSERT INTO policy_match_run (
              match_run_id,project_id,topic_tags_json,matcher_version,status,started_at
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                match_run_id,
                project["project_id"],
                dump_json(sorted(topics)),
                MATCHER_VERSION,
                "running",
                run_started_at,
            ),
        )
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT p.*, c.clause_id, c.article_path, c.original_text,
                       c.normalized_summary, c.topic_tags_json,
                       c.requirement_type, c.applicability_notes,
                       c.permitted_sections_json,c.forbidden_claims_json,
                       c.verification_status AS clause_verification_status
                FROM policy_document p
                JOIN policy_clause c ON c.policy_id=p.policy_id
                ORDER BY p.authority_group,p.publish_date,p.title,c.article_path
                """
            )
        ]

        candidates: list[dict[str, Any]] = []
        for row in rows:
            if not _jurisdiction_applies(
                project["jurisdiction_code"], row["jurisdiction_level"], row["jurisdiction_code"]
            ):
                continue
            clause_topics = set(json.loads(row["topic_tags_json"] or "[]"))
            overlap = sorted(topics.intersection(clause_topics))
            if not overlap:
                continue
            verified = row["verification_status"] == "verified" and row["clause_verification_status"] == "verified"
            current = row["validity_status"] == "current"
            core_topic = bool({"electronic_medical_record", "interoperability"}.intersection(overlap))
            relevance = "core" if core_topic else ("important" if len(overlap) >= 2 else "supplementary")
            basis_use = int(verified and current and relevance in {"core", "important", "supplementary"})
            background_use = int(
                basis_use
                and (
                    row["policy_type"] in BACKGROUND_POLICY_TYPES
                    or core_topic
                    or row["jurisdiction_level"] in {"province", "prefecture"}
                )
            )
            sort_key = _sort_key(row)
            project_relation = (
                f"与项目主题 {', '.join(overlap)} 直接相关；"
                f"适用于{project['jurisdiction_name'] or '本项目所在地'}。"
            )
            match_id = stable_id("PMATCH", match_run_id, row["policy_id"], row["clause_id"])
            decision_status = "ai_recommended" if basis_use else "needs_confirmation"
            timestamp = now_iso()
            conn.execute(
                """
                INSERT INTO project_policy_match (
                  match_id,match_run_id,project_id,policy_id,clause_id,match_dimensions_json,
                  relevance_level,basis_use,background_use,other_chapter_use_json,
                  project_relation,sort_key,basis_order,decision_status,decision_reason,
                  created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(match_id) DO UPDATE SET
                  match_dimensions_json=excluded.match_dimensions_json,
                  relevance_level=excluded.relevance_level,
                  basis_use=excluded.basis_use,
                  background_use=excluded.background_use,
                  other_chapter_use_json=excluded.other_chapter_use_json,
                  project_relation=excluded.project_relation,
                  sort_key=excluded.sort_key,
                  decision_status=excluded.decision_status,
                  decision_reason=excluded.decision_reason,
                  updated_at=excluded.updated_at
                """,
                (
                    match_id,
                    match_run_id,
                    project["project_id"],
                    row["policy_id"],
                    row["clause_id"],
                    dump_json({"topics": overlap, "jurisdiction": row["jurisdiction_level"]}),
                    relevance,
                    basis_use,
                    background_use,
                    dump_json([]),
                    project_relation,
                    sort_key,
                    None,
                    decision_status,
                    "基于地域、项目类型、建设主题和官方核验状态确定性匹配。",
                    timestamp,
                    timestamp,
                ),
            )
            candidates.append(
                {
                    **row,
                    "match_id": match_id,
                    "topics": overlap,
                    "relevance_level": relevance,
                    "basis_use": bool(basis_use),
                    "background_use": bool(background_use),
                    "project_relation": project_relation,
                    "sort_key": sort_key,
                    "decision_status": decision_status,
                }
            )

        ordered_policy_ids: list[str] = []
        for item in sorted((row for row in candidates if row["basis_use"]), key=lambda row: row["sort_key"]):
            if item["policy_id"] not in ordered_policy_ids:
                ordered_policy_ids.append(item["policy_id"])
        for index, policy_id in enumerate(ordered_policy_ids, 1):
            conn.execute(
                "UPDATE project_policy_match SET basis_order=? WHERE match_run_id=? AND policy_id=? AND basis_use=1",
                (index, match_run_id, policy_id),
            )
        for item in candidates:
            if item["basis_use"]:
                conn.execute(
                    """
                    INSERT INTO policy_citation (
                      citation_id,project_id,match_id,report_section,citation_purpose,citation_status
                    ) VALUES (?,?,?,?,?,?)
                    ON CONFLICT(citation_id) DO NOTHING
                    """,
                    (
                        stable_id("PCITE", item["match_id"], "basis"),
                        project["project_id"],
                        item["match_id"],
                        "第一章 编制依据",
                        "basis",
                        "planned",
                    ),
                )
            if item["background_use"]:
                conn.execute(
                    """
                    INSERT INTO policy_citation (
                      citation_id,project_id,match_id,report_section,citation_purpose,citation_status
                    ) VALUES (?,?,?,?,?,?)
                    ON CONFLICT(citation_id) DO NOTHING
                    """,
                    (
                        stable_id("PCITE", item["match_id"], "background"),
                        project["project_id"],
                        item["match_id"],
                        "第二章 政策背景",
                        "background",
                        "planned",
                    ),
                )
        conn.execute(
            """
            UPDATE policy_match_run
            SET status='completed', completed_at=?, summary_json=?
            WHERE match_run_id=?
            """,
            (
                now_iso(),
                dump_json(
                    {
                        "policy_count": len({row["policy_id"] for row in candidates}),
                        "clause_match_count": len(candidates),
                        "basis_policy_order": ordered_policy_ids,
                    }
                ),
                match_run_id,
            ),
        )
        conn.commit()

    candidates.sort(key=lambda row: (not row["basis_use"], row["sort_key"]))
    result = {
        "project": dict(project),
        "topics": sorted(topics),
        "match_run_id": match_run_id,
        "matcher_version": MATCHER_VERSION,
        "policy_count": len({row["policy_id"] for row in candidates}),
        "clause_match_count": len(candidates),
        "basis_policy_order": ordered_policy_ids,
        "matches": candidates,
    }
    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if output_md:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        output_md.write_text(to_markdown(result), encoding="utf-8")
    return result


def to_markdown(result: dict) -> str:
    lines = [
        f"# {result['project']['official_name']}政策匹配与引用矩阵",
        "",
        "> 本文件由确定性规则生成。AI推荐不等于用户确认；正式进入报告前须核验有效性并确认排序。",
        "",
        "## 编制依据建议顺序",
        "",
    ]
    policy_seen: set[str] = set()
    order = 0
    for row in result["matches"]:
        if not row["basis_use"] or row["policy_id"] in policy_seen:
            continue
        policy_seen.add(row["policy_id"])
        order += 1
        doc_no = f"（{row['document_no']}）" if row["document_no"] else ""
        lines.append(f"{order}. 《{row['title']}》{doc_no}，{row['issuer']}，{row['publish_date']}。")
    lines.extend(
        [
            "",
            "## 政策背景证据矩阵",
            "",
            "| 顺序 | 政策 | 条款位置 | 原文证据 | 审慎概括 | 项目关系 | 状态 |",
            "|---:|---|---|---|---|---|---|",
        ]
    )
    policy_order = {pid: i + 1 for i, pid in enumerate(result["basis_policy_order"])}
    for row in result["matches"]:
        if not row["background_use"]:
            continue
        original = row["original_text"].replace("|", "｜").replace("\n", " ")
        summary = row["normalized_summary"].replace("|", "｜").replace("\n", " ")
        relation = row["project_relation"].replace("|", "｜")
        lines.append(
            f"| {policy_order.get(row['policy_id'], '')} | 《{row['title']}》 | {row['article_path']} | {original} | {summary} | {relation} | {row['decision_status']} |"
        )
    lines.extend(
        [
            "",
            "## 未进入正式依据的候选",
            "",
            "以下项目因规划期届满、有效性待核验、证据不完整或仅供参考而未自动进入正式依据：",
            "",
        ]
    )
    rejected = [row for row in result["matches"] if not row["basis_use"]]
    if not rejected:
        lines.append("- 无。")
    else:
        for row in rejected:
            lines.append(
                f"- 《{row['title']}》— validity={row['validity_status']}，verification={row['verification_status']}，状态={row['decision_status']}。"
            )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--topics", required=True, help="Comma-separated controlled topic tags")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    args = parser.parse_args()
    result = match(
        args.database,
        args.project_code,
        {item.strip() for item in args.topics.split(",") if item.strip()},
        args.output_json,
        args.output_md,
    )
    print(json.dumps({k: result[k] for k in ("policy_count", "clause_match_count", "basis_policy_order")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
