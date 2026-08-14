#!/usr/bin/env python3
"""Select a bounded, review-only policy/standard candidate list for one project."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from knowledge_db import apply_migrations, connect, dump_json, now_iso, stable_id


MATCHER_VERSION = "policy-catalog-candidate-v1"

CURATED_RULES = (
    (r"健康中国\s*2030", "policy", "background", 100),
    (r"十四五.*全民健康信息化规划", "policy", "basis", 99),
    (r"推动公立医院高质量发展的意见", "policy", "basis", 98),
    (r"公立医院高质量发展评价指标", "standard", "performance", 97),
    (r"促进.*互联网\+医疗健康.*意见", "policy", "basis", 96),
    (r"互联网\+医疗健康.*五个一", "policy", "background", 95),
    (r"全国医院信息化建设标准与规范", "standard", "technical", 94),
    (r"进一步推进以电子病历为核心.*信息化建设", "policy", "basis", 93),
    (r"电子病历应用管理规范", "standard", "technical", 92),
    (r"电子病历系统应用水平分级评价", "standard", "performance", 91),
    (r"医院信息互联互通标准化成熟度测评", "standard", "performance", 90),
    (r"医院智慧服务分级评估标准", "standard", "performance", 89),
    (r"医院智慧管理分级评估标准", "standard", "performance", 88),
    (r"电子病历基本规范", "standard", "technical", 87),
    (r"医疗卫生机构网络安全管理办法", "policy", "security", 86),
    (r"医疗机构检查检验结果互认管理办法", "policy", "background", 85),
    (r"进一步完善预约诊疗制度加强智慧医院建设", "policy", "background", 84),
    (r"加强全民健康信息标准化体系建设", "policy", "basis", 83),
)

ENDED_PERIOD_PATTERN = re.compile(r"十三五|2015[—-]2020|2016[—-]2020|2021[—-]2025")


def classify_fallback(title: str, category: str) -> tuple[str, str]:
    if any(term in title + category for term in ("标准", "规范", "测评", "评价", "数据集")):
        return "standard", "performance" if any(term in title for term in ("测评", "评价")) else "technical"
    if any(term in title for term in ("网络安全", "数据安全", "密码")):
        return "policy", "security"
    return "policy", "background"


def score_entry(entry: dict[str, Any], topics: set[str]) -> dict[str, Any] | None:
    title = str(entry["title"])
    for pattern, basis_group, suggested_use, score in CURATED_RULES:
        if re.search(pattern, title):
            ended_period = bool(ENDED_PERIOD_PATTERN.search(title))
            return {
                "basis_group": basis_group,
                "suggested_use": suggested_use,
                "score": score - (25 if ended_period else 0),
                "relevance_level": "supplementary" if ended_period else "core" if score >= 94 else "important",
                "match_reasons": [f"curated:{pattern}"] + (["ended_planning_period"] if ended_period else []),
            }
    searchable = " ".join(
        str(entry.get(key, ""))
        for key in ("title", "category_name", "catalog_group_name", "keyword_text")
    ).casefold()
    topic_terms = {
        "hospital_informationization": ("信息化", "智慧医院"),
        "electronic_medical_record": ("电子病历",),
        "interoperability": ("互联互通", "信息平台"),
        "internet_health": ("互联网+医疗健康", "互联网诊疗"),
        "high_quality_hospital": ("高质量发展",),
        "cybersecurity": ("网络安全", "数据安全"),
        "standardization": ("标准化", "数据元", "共享文档"),
    }
    hits = [
        topic
        for topic in sorted(topics)
        if any(term.casefold() in searchable for term in topic_terms.get(topic, (topic,)))
    ]
    if not hits or ENDED_PERIOD_PATTERN.search(title):
        return None
    basis_group, suggested_use = classify_fallback(title, str(entry.get("category_name", "")))
    return {
        "basis_group": basis_group,
        "suggested_use": suggested_use,
        "score": 60 + min(15, 3 * len(hits)),
        "relevance_level": "supplementary",
        "match_reasons": [f"topic:{topic}" for topic in hits],
    }


def match_candidates(
    database: Path,
    project_code: str,
    *,
    topics: set[str] | None = None,
    limit_per_group: int = 14,
) -> dict[str, Any]:
    topics = set(topics or ())
    with connect(database.resolve()) as connection:
        apply_migrations(connection)
        project = connection.execute(
            "SELECT * FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if not project:
            raise ValueError(f"project not found: {project_code}")
        catalog = connection.execute(
            """
            SELECT * FROM policy_catalog
            WHERE catalog_status='active'
            ORDER BY imported_at DESC,catalog_id DESC LIMIT 1
            """
        ).fetchone()
        if not catalog:
            raise ValueError("active policy catalog not found")
        entries = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM policy_catalog_entry
                WHERE catalog_id=? AND entry_status='active'
                ORDER BY source_row,catalog_entry_id
                """,
                (catalog["catalog_id"],),
            )
        ]
        ranked = []
        for entry in entries:
            scored = score_entry(entry, topics)
            if scored:
                ranked.append({**entry, **scored})
        ranked.sort(
            key=lambda item: (
                item["basis_group"],
                -float(item["score"]),
                item.get("publish_date", ""),
                item["source_index_no"],
                item["source_row"],
            )
        )
        selected = []
        for basis_group in ("policy", "standard"):
            group_entries = [item for item in ranked if item["basis_group"] == basis_group]
            selected.extend(group_entries[:limit_per_group])
        selected.sort(
            key=lambda item: (-float(item["score"]), item["source_index_no"], item["source_row"])
        )
        topic_json = dump_json(sorted(topics))
        run_id = stable_id(
            "POLICYCATMATCHRUN",
            project["project_id"],
            catalog["catalog_id"],
            catalog["content_hash"],
            MATCHER_VERSION,
            topic_json,
            str(limit_per_group),
        )
        timestamp = now_iso()
        connection.execute(
            """
            INSERT INTO policy_catalog_match_run (
              catalog_match_run_id,project_id,catalog_id,matcher_version,
              topic_tags_json,status,started_at,completed_at,summary_json
            ) VALUES (?,?,?,?,?,'completed',?,?,?)
            ON CONFLICT(catalog_match_run_id) DO UPDATE SET
              status='completed',completed_at=excluded.completed_at,
              summary_json=excluded.summary_json
            """,
            (
                run_id,
                project["project_id"],
                catalog["catalog_id"],
                MATCHER_VERSION,
                topic_json,
                timestamp,
                timestamp,
                dump_json(
                    {
                        "candidate_count": len(selected),
                        "policy_count": sum(item["basis_group"] == "policy" for item in selected),
                        "standard_count": sum(item["basis_group"] == "standard" for item in selected),
                    }
                ),
            ),
        )
        for item in selected:
            candidate_match_id = stable_id("POLICYCATMATCH", run_id, item["catalog_entry_id"])
            connection.execute(
                """
                INSERT INTO project_policy_catalog_match (
                  candidate_match_id,catalog_match_run_id,project_id,catalog_entry_id,
                  basis_group,suggested_use,relevance_level,score,match_reasons_json,
                  decision_status,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,'ai_recommended',?,?)
                ON CONFLICT(catalog_match_run_id,catalog_entry_id) DO UPDATE SET
                  basis_group=excluded.basis_group,
                  suggested_use=excluded.suggested_use,
                  relevance_level=excluded.relevance_level,
                  score=excluded.score,
                  match_reasons_json=excluded.match_reasons_json,
                  decision_status=CASE
                    WHEN project_policy_catalog_match.decision_status IN ('user_confirmed','user_excluded')
                    THEN project_policy_catalog_match.decision_status
                    ELSE excluded.decision_status
                  END,
                  updated_at=excluded.updated_at
                """,
                (
                    candidate_match_id,
                    run_id,
                    project["project_id"],
                    item["catalog_entry_id"],
                    item["basis_group"],
                    item["suggested_use"],
                    item["relevance_level"],
                    item["score"],
                    dump_json(item["match_reasons"]),
                    timestamp,
                    timestamp,
                ),
            )
        connection.commit()
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT m.*,e.source_row,e.source_index_no,e.index_occurrence,e.index_conflict,
                       e.title,e.document_no,e.issuer,
                       e.publish_date,e.external_url,e.verification_status,
                       e.category_name,e.catalog_group_name
                FROM project_policy_catalog_match m
                JOIN policy_catalog_entry e ON e.catalog_entry_id=m.catalog_entry_id
                WHERE m.catalog_match_run_id=? AND m.decision_status<>'user_excluded'
                ORDER BY CASE m.basis_group WHEN 'policy' THEN 0 ELSE 1 END,
                         m.score DESC,e.source_index_no,e.source_row
                """,
                (run_id,),
            )
        ]
    return {
        "schema_version": "1.0",
        "project_code": project_code,
        "catalog_id": catalog["catalog_id"],
        "catalog_match_run_id": run_id,
        "matcher_version": MATCHER_VERSION,
        "topics": sorted(topics),
        "candidate_count": len(rows),
        "candidates": rows,
    }


def to_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 部门政策目录项目候选",
        "",
        "> 以下内容仅用于官方原文核验任务；未形成已核验政策文件和条款前，不得作为正式编制依据或扩写政策要求。",
        "",
    ]
    for basis_group, heading in (("policy", "政策法规候选"), ("standard", "标准规范与评价候选")):
        lines.extend([f"## {heading}", "", "| 序号 | 文件 | 文号 | 发布单位 | 日期 | 状态 |", "| --- | --- | --- | --- | --- | --- |"]) 
        rows = [item for item in payload["candidates"] if item["basis_group"] == basis_group]
        for index, item in enumerate(rows, 1):
            lines.append(
                "| " + " | ".join(
                    [
                        str(index),
                        item["title"].replace("|", "／"),
                        (item["document_no"] or "待核实").replace("|", "／"),
                        (item["issuer"] or "待核实").replace("|", "／"),
                        item["publish_date"] or "待核实",
                        "待核验",
                    ]
                ) + " |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--topic", action="append", default=[])
    parser.add_argument("--limit-per-group", type=int, default=14)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    args = parser.parse_args()
    result = match_candidates(
        args.database,
        args.project_code,
        topics=set(args.topic),
        limit_per_group=args.limit_per_group,
    )
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(to_markdown(result), encoding="utf-8")
    print(json.dumps({"candidate_count": result["candidate_count"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
