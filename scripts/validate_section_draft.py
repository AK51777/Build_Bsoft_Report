#!/usr/bin/env python3
"""Validate one section draft before it can be adopted."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

from chapter_rules import resolve_chapter_rule
from knowledge_db import apply_migrations, connect, dump_json, now_iso, sha256_text


PLACEHOLDER_PATTERN = re.compile(r"【(?:待补充|待确认|冲突|分析建议)[^】]*】")
HIGH_RISK_NUMBER_PATTERN = re.compile(
    r"(?:目标|达到|不低于|不高于|不少于|不超过|提升|降低|投资|工期|上线|等级)[^。；\n]{0,28}"
    r"(?:\d+(?:\.\d+)?\s*(?:%|万元|亿元|天|月|年|级|个|套|项))"
)
EVIDENCE_MARKER_PATTERN = re.compile(r"<!--\s*evidence\s*:\s*([^>]+?)\s*-->", re.I)
STANDARD_BLOCK_MARKER_PATTERN = re.compile(
    r"<!--\s*standard-blocks\s*:\s*([^>]+?)\s*-->", re.I
)


def visible_length(content: str) -> int:
    text = re.sub(r"<!--.*?-->", "", content, flags=re.S)
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"[#*_`|>\-\s]", "", text)
    return len(text)


def normalized_standard_text(text: str, forbidden_terms: list[str] | None = None) -> str:
    value = text or ""
    for term in [
        *(forbidden_terms or []),
        "BsoftGPT",
        "Bsoft",
        "创业慧康",
        "创业的产品资源",
    ]:
        if term:
            value = value.replace(term, "")
    for source, target in {
        "本院": "医院",
        "我院": "医院",
        "国家卫生部": "国家卫生健康主管部门",
        "卫生部": "卫生健康主管部门",
        "已经实现": "拟实现",
        "已实现": "拟实现",
        "实现了": "拟实现",
        "达到了": "拟达到",
    }.items():
        value = value.replace(source, target)
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value).casefold()


def assess_content(
    content: str,
    plan: dict[str, Any],
    sources: list[dict[str, Any]],
    *,
    mode: str = "delivery",
) -> dict[str, Any]:
    if mode not in {"working", "delivery"}:
        raise ValueError("mode must be working or delivery")
    issues: list[dict[str, str]] = []

    def add(code: str, description: str, severity: str = "blocking") -> None:
        issues.append({"code": code, "description": description, "severity": severity})

    contract = resolve_chapter_rule(
        str(plan.get("chapter_code") or ""), str(plan.get("section_role") or "")
    )
    contract_validation = contract.get("validation", {})
    matched_process_phrases = [
        phrase
        for phrase in contract.get("forbidden_output_phrases", [])
        if phrase and phrase in content
    ]
    if matched_process_phrases:
        add(
            "authoring_process_language",
            "正文包含仅适用于编制或核验过程的内部提示语："
            + "、".join(matched_process_phrases),
        )

    length = visible_length(content)
    length_min = int(plan.get("length_min") or 0)
    if length_min and length < length_min:
        add("section_too_short", f"有效正文 {length} 字，低于章节下限 {length_min} 字。")
    prose_paragraphs = [
        line.strip()
        for line in re.split(r"\n\s*\n", re.sub(r"<!--.*?-->", "", content, flags=re.S))
        if len(re.sub(r"[#*_`|>\-\s]", "", line)) >= 80
    ]
    minimum_paragraphs = max(2, math.ceil(length_min / 500)) if length_min else 0
    if minimum_paragraphs and len(prose_paragraphs) < minimum_paragraphs:
        add(
            "insufficient_argument_structure",
            f"至少需要 {minimum_paragraphs} 个有实质内容的论证段，当前为 {len(prose_paragraphs)} 个。",
        )
    if PLACEHOLDER_PATTERN.search(content):
        add(
            "unresolved_placeholder",
            "章节仍含待补充、待确认、冲突或分析建议占位。",
            "warning" if mode == "working" else "blocking",
        )
    if re.search(r"^#{8,}\s", content, flags=re.M):
        add("unsupported_heading_level", "内部工作稿标题超过七级。")
    if re.search(r"(?:^|\s)(?:TBD|TBC|TODO)(?:\s|$)", content, flags=re.I):
        add("unresolved_todo", "章节仍含 TBD、TBC 或 TODO。")

    direct_scope_names = [
        str(source.get("standard_name", "")).strip()
        for source in sources
        if source.get("source_type") == "scope" and source.get("usage_mode") == "direct"
    ]
    missing_scope_names = [name for name in direct_scope_names if name and name not in content]
    if contract_validation.get("require_scope_coverage") and missing_scope_names:
        add(
            "scope_not_carried",
            "正文未承载确认范围：" + "、".join(missing_scope_names[:10]),
        )

    required_tables = json.loads(plan.get("required_tables_json") or "[]")
    if required_tables and not re.search(r"^\s*\|.+\|\s*$", content, flags=re.M):
        add("required_table_missing", "章节蓝图要求表格，但正文没有 Markdown 表格。")

    valid_evidence_ids = {
        str(source["source_object_id"])
        for source in sources
        if source.get("usage_mode") in {"direct", "evidence"}
    }
    marker_ids = {
        item.strip()
        for marker in EVIDENCE_MARKER_PATTERN.findall(content)
        for item in re.split(r"[,，;；\s]+", marker)
        if item.strip()
    }
    invalid_marker_ids = sorted(marker_ids - valid_evidence_ids)
    if invalid_marker_ids:
        add("invalid_evidence_marker", "来源标记不属于本章节任务包：" + "、".join(invalid_marker_ids[:10]))
    if HIGH_RISK_NUMBER_PATTERN.search(content) and not marker_ids:
        add("ungrounded_quantitative_assertion", "章节包含高风险数字结论，但没有 `<!-- evidence:来源ID -->` 标记。")

    forbidden_terms: set[str] = set()
    for source in sources:
        if source.get("source_type") != "corpus":
            continue
        for term in json.loads(source.get("forbidden_terms_json") or "[]"):
            if term:
                forbidden_terms.add(str(term))
    matched_forbidden = sorted(term for term in forbidden_terms if term in content)
    if matched_forbidden:
        add("reference_residue", "正文命中语料禁用词：" + "、".join(matched_forbidden))

    standard_sources = [
        source
        for source in sources
        if source.get("source_type") == "corpus"
        and source.get("usage_mode") == "parameterized"
    ]
    if standard_sources and contract_validation.get("require_standard_block_markers"):
        marker_ids = {
            item.strip()
            for marker in STANDARD_BLOCK_MARKER_PATTERN.findall(content)
            for item in re.split(r"[,，;；\s]+", marker)
            if item.strip()
        }
        required_ids = {str(source["source_object_id"]) for source in standard_sources}
        missing_ids = sorted(required_ids - marker_ids)
        if missing_ids:
            add(
                "standard_solution_block_marker_missing",
                "标准方案全量组装缺少语料块追踪标记：" + "、".join(missing_ids[:20]),
            )
    if standard_sources and contract_validation.get("require_standard_block_text"):
        normalized_content = normalized_standard_text(content)
        missing_text_ids: list[str] = []
        for source in standard_sources:
            source_text = normalized_standard_text(
                str(source.get("clean_text") or ""),
                json.loads(source.get("forbidden_terms_json") or "[]"),
            )
            if source_text and source_text not in normalized_content:
                missing_text_ids.append(str(source["source_object_id"]))
        if missing_text_ids:
            add(
                "standard_solution_block_text_missing",
                "标准方案正文未按全量组装契约承载语料块："
                + "、".join(missing_text_ids[:20]),
            )

    outline_nodes = plan.get("outline_nodes") or []
    if outline_nodes:
        actual_headings: list[tuple[int, str]] = []
        for marker, title in re.findall(r"(?m)^(#{4,7})\s+(.+?)\s*$", content):
            title = re.sub(r"^\d+(?:\.\d+){3,6}\s+", "", title).strip()
            actual_headings.append((len(marker), title))
        missing_nodes = [
            node
            for node in outline_nodes
            if (int(node["heading_level"]), str(node["title"]).strip()) not in actual_headings
        ]
        if missing_nodes:
            add(
                "dynamic_outline_incomplete",
                "正文未完整承载动态建设目录："
                + "、".join(f"{node['chapter_code']} {node['title']}" for node in missing_nodes[:12]),
            )
        expected_counts = {
            level: sum(int(node["heading_level"]) == level for node in outline_nodes)
            for level in range(4, 8)
        }
        actual_counts = {
            level: sum(actual_level == level for actual_level, _ in actual_headings)
            for level in range(4, 8)
        }
        if any(actual_counts[level] < expected_counts[level] for level in range(4, 8)):
            add(
                "construction_hierarchy_too_shallow",
                f"建设目录层级不足；要求 {expected_counts}，当前 {actual_counts}。",
            )

    blocking_count = sum(issue["severity"] == "blocking" for issue in issues)
    warning_count = sum(issue["severity"] == "warning" for issue in issues)
    return {
        "status": "passed" if not blocking_count else "failed",
        "validation_mode": mode,
        "content_sha256": sha256_text(content),
        "visible_length": length,
        "length_min": length_min,
        "issue_count": len(issues),
        "blocking_count": blocking_count,
        "warning_count": warning_count,
        "chapter_rule_layers": contract.get("rule_layers", []),
        "assembly_mode": contract.get("assembly_mode"),
        "issues": issues,
        "validated_at": now_iso(),
    }


def validate_draft(
    database: Path,
    project_code: str,
    chapter_code: str,
    version_no: int,
    *,
    mode: str = "delivery",
) -> dict[str, Any]:
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT project_id FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        plan = conn.execute(
            """
            SELECT p.*,b.section_role FROM section_composition_plan p
            LEFT JOIN section_blueprint b ON b.blueprint_id=p.blueprint_id
            WHERE p.project_id=? AND p.chapter_code=?
            ORDER BY version_no DESC LIMIT 1
            """,
            (project["project_id"], chapter_code),
        ).fetchone()
        if plan is None:
            raise RuntimeError(f"section plan not found for chapter {chapter_code}")
        draft = conn.execute(
            "SELECT * FROM draft_section_version WHERE plan_id=? AND version_no=?",
            (plan["plan_id"], version_no),
        ).fetchone()
        if draft is None:
            raise RuntimeError(f"draft version {version_no} not found")
        source_rows = conn.execute(
            "SELECT * FROM section_plan_source WHERE plan_id=?", (plan["plan_id"],)
        ).fetchall()
        outline_nodes = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM section_outline_node WHERE plan_id=? ORDER BY ordinal",
                (plan["plan_id"],),
            )
        ]
        sources: list[dict[str, Any]] = []
        for source in source_rows:
            item = dict(source)
            if source["source_type"] == "scope":
                row = conn.execute(
                    "SELECT standard_name FROM project_scope_item WHERE scope_id=?",
                    (source["source_object_id"],),
                ).fetchone()
                item["standard_name"] = row["standard_name"] if row else ""
            elif source["source_type"] == "corpus":
                row = conn.execute(
                    "SELECT clean_text,forbidden_terms_json FROM corpus_block WHERE block_id=?",
                    (source["source_object_id"],),
                ).fetchone()
                item["clean_text"] = row["clean_text"] if row else ""
                item["forbidden_terms_json"] = row["forbidden_terms_json"] if row else "[]"
            sources.append(item)
        plan_data = dict(plan)
        plan_data["outline_nodes"] = outline_nodes
        result = assess_content(draft["content"], plan_data, sources, mode=mode)
        conn.execute(
            "UPDATE draft_section_version SET check_result_json=?,updated_at=? WHERE draft_version_id=?",
            (dump_json(result), result["validated_at"], draft["draft_version_id"]),
        )
        conn.commit()
    return {
        "project_code": project_code,
        "chapter_code": chapter_code,
        "version_no": version_no,
        "draft_version_id": draft["draft_version_id"],
        **result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("chapter_code")
    parser.add_argument("version_no", type=int)
    parser.add_argument("--mode", choices=("working", "delivery"), default="delivery")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate_draft(
        args.database, args.project_code, args.chapter_code, args.version_no, mode=args.mode
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
