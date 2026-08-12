#!/usr/bin/env python3
"""Match document standards by document type, jurisdiction, validity, and evidence status."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_db import apply_migrations, connect, dump_json, now_iso, stable_id


MATCHER_VERSION = "p1-document-standard-20260804"


def jurisdiction_score(project_code: str, standard_code: str) -> tuple[float, str]:
    if standard_code in {"", "100000"}:
        return 0.18, "全国通用"
    if project_code == standard_code:
        return 0.25, "行政区划完全匹配"
    if project_code and standard_code and project_code.startswith(standard_code.rstrip("0")):
        return 0.22, "上级行政区划匹配"
    return 0.0, "地域不匹配"


def match(database: Path, project_code: str) -> dict:
    with connect(database) as conn:
        apply_migrations(conn)
        project = conn.execute("SELECT * FROM project WHERE project_code=?", (project_code,)).fetchone()
        if not project:
            raise SystemExit(f"未找到项目：{project_code}")
        started_at = now_iso()
        run_id = stable_id("DSTRUN", project["project_id"], MATCHER_VERSION, started_at)
        conn.execute(
            "INSERT INTO document_standard_match_run(standard_match_run_id,project_id,matcher_version,status,started_at) VALUES(?,?,?,?,?)",
            (run_id, project["project_id"], MATCHER_VERSION, "running", started_at),
        )
        matches = []
        for row in conn.execute("SELECT * FROM document_standard ORDER BY jurisdiction_code,title,version"):
            item = dict(row)
            type_score = 0.45 if item["document_type"] == project["document_type"] else 0.0
            region_score, region_reason = jurisdiction_score(project["jurisdiction_code"], item["jurisdiction_code"])
            verified_score = 0.15 if item["verification_status"] == "verified" else 0.0
            active_score = 0.15 if item["status"] == "active" else 0.0
            score = round(type_score + region_score + verified_score + active_score, 4)
            if score < 0.45:
                continue
            needs_funding_confirmation = "政府投资" in item["title"]
            decision_status = "needs_confirmation" if needs_funding_confirmation else "ai_recommended"
            reason = (
                f"文种{'匹配' if type_score else '不匹配'}；{region_reason}；"
                f"标准{item['verification_status']}、{item['status']}。"
            )
            if needs_funding_confirmation:
                reason += " 本项目资金来源未确认，需先确认政府投资属性。"
            match_id = stable_id("DSTMATCH", run_id, item["standard_id"])
            conn.execute(
                """
                INSERT INTO project_document_standard_match (
                  standard_match_id,standard_match_run_id,project_id,standard_id,
                  match_score,match_dimensions_json,match_reason,decision_status,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    match_id,
                    run_id,
                    project["project_id"],
                    item["standard_id"],
                    score,
                    dump_json(
                        {
                            "document_type": type_score,
                            "jurisdiction": region_score,
                            "verified": verified_score,
                            "active": active_score,
                        }
                    ),
                    reason,
                    decision_status,
                    started_at,
                ),
            )
            matches.append({**item, "match_score": score, "match_reason": reason, "decision_status": decision_status})
        matches.sort(key=lambda item: (-item["match_score"], item["standard_id"]))
        conn.execute(
            "UPDATE document_standard_match_run SET status='completed',completed_at=?,summary_json=? WHERE standard_match_run_id=?",
            (now_iso(), dump_json({"match_count": len(matches)}), run_id),
        )
        conn.commit()
    return {"project": dict(project), "standard_match_run_id": run_id, "matcher_version": MATCHER_VERSION, "matches": matches}


def export_latest_match(database: Path, project_code: str) -> dict:
    with connect(database) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT * FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if not project:
            raise ValueError(f"project not found: {project_code}")
        run = conn.execute(
            """
            SELECT * FROM document_standard_match_run
            WHERE project_id=? AND matcher_version=? AND status='completed'
            ORDER BY completed_at DESC,started_at DESC,standard_match_run_id DESC
            LIMIT 1
            """,
            (project["project_id"], MATCHER_VERSION),
        ).fetchone()
        if not run:
            raise ValueError("completed document standard match run not found")
        rows = conn.execute(
            """
            SELECT s.*,m.match_score,m.match_reason,m.decision_status
            FROM project_document_standard_match m
            JOIN document_standard s ON s.standard_id=m.standard_id
            WHERE m.standard_match_run_id=?
            ORDER BY m.match_score DESC,s.standard_id
            """,
            (run["standard_match_run_id"],),
        ).fetchall()
    return {
        "project": dict(project),
        "standard_match_run_id": run["standard_match_run_id"],
        "matcher_version": run["matcher_version"],
        "matches": [dict(row) for row in rows],
    }


def to_markdown(result: dict) -> str:
    lines = [
        f"# {result['project']['official_name']}文档标准匹配结果",
        "",
        "> 标准负责章节完整性，Word格式画像负责页面和样式；两者必须分别确认。",
        "",
        "| 排名 | 标准 | 地域 | 匹配分 | 状态 | 匹配说明 |",
        "|---:|---|---|---:|---|---|",
    ]
    for index, item in enumerate(result["matches"], 1):
        lines.append(
            f"| {index} | 《{item['title']}》 | {item['jurisdiction_name']} | {item['match_score']:.2f} | {item['decision_status']} | {item['match_reason']} |"
        )
    if not result["matches"]:
        lines.append("| - | 【待补充】没有匹配到已核验标准 | - | - | needs_confirmation | 应补充属地或行业文档标准 |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    args = parser.parse_args()
    result = match(args.database, args.project_code)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(to_markdown(result), encoding="utf-8")
    print(json.dumps({"standard_match_run_id": result["standard_match_run_id"], "match_count": len(result["matches"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
