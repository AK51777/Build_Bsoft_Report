#!/usr/bin/env python3
"""Validate one section draft before it can be adopted."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

from build_policy_section_material import build_material as build_policy_material
from chapter_rules import resolve_chapter_rule
from knowledge_db import apply_migrations, connect, dump_json, now_iso, sha256_text
from match_policy_catalog_candidates import SECTION_ORDER
from validate_project_gates import policy_material_quality_checks


PLACEHOLDER_PATTERN = re.compile(r"【(?:待补充|待确认|待核验|冲突|分析建议)[^】]*】")
HIGH_RISK_NUMBER_PATTERN = re.compile(
    r"(?:目标|达到|不低于|不高于|不少于|不超过|提升|降低|投资|工期|上线|等级)[^。；\n]{0,28}"
    r"(?:\d+(?:\.\d+)?\s*(?:%|万元|亿元|天|月|年|级|个|套|项))"
)
EVIDENCE_MARKER_PATTERN = re.compile(r"<!--\s*evidence\s*:\s*([^>]+?)\s*-->", re.I)
STANDARD_BLOCK_MARKER_PATTERN = re.compile(
    r"<!--\s*standard-blocks\s*:\s*([^>]+?)\s*-->", re.I
)
UNRESOLVED_FACT_MARKER_PATTERN = re.compile(
    r"<!--\s*unresolved-fact\s*:\s*([^>]+?)\s*-->", re.I
)
CURRENT_STATE_DIMENSION_PATTERN = re.compile(
    r"<!--\s*current-state-dimension\s*:\s*([a-z_]+)\s*-->", re.I
)
CURRENT_STATE_REQUIRED_DIMENSIONS = {
    "application",
    "data_interface",
    "infrastructure",
    "security",
    "operation",
}
ASSESSMENT_LEVEL_PATTERN = re.compile(
    r"(?:[一二三四五六七八九]|\d)(?:级甲等|级|甲等)|四甲|4A",
    re.I,
)
POLICY_MATERIAL_MARKER_PATTERN = re.compile(
    r'<!--\s*policy-material\s+chapter="([^"]*)"\s+match-run="([^"]*)"\s+'
    r'signature="([^"]*)"\s*-->',
    re.I,
)
POLICY_ITEM_MARKER_PATTERN = re.compile(
    r'<!--\s*policy-item\s+id="([^"]*)"\s+clauses="([^"]*)"\s*-->', re.I
)
POLICY_BACKGROUND_MARKER_PATTERN = re.compile(
    r'<!--\s*policy-background\s+id="([^"]*)"\s+clauses="([^"]*)"\s+'
    r'text-hash="([^"]*)"\s*-->',
    re.I,
)
BASIS_SECTION_HEADINGS = (
    ("policy_basis", "政策类依据"),
    ("industry_standard", "行业标准依据"),
    ("security_standard", "安全类标准依据"),
    ("investment_basis", "投资估算编制依据"),
)
POLICY_BACKGROUND_HEADINGS = (
    "国家政策背景",
    "省/自治区政策背景",
    "市/项目建设地区政策背景",
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


def fact_assessment_framework(fact_key: str, fact_content: str) -> str:
    key = fact_key.casefold()
    text = fact_content.casefold()
    if "电子病历" in fact_content or ".emr" in key:
        return "emr"
    if "互联互通" in fact_content or "interop" in key:
        return "interoperability"
    if "智慧服务" in fact_content or "smart_service" in key:
        return "smart_service"
    if "智慧管理" in fact_content or "smart_management" in key:
        return "smart_management"
    return ""


def fact_assessment_kind(fact_key: str) -> str:
    key = fact_key.casefold()
    if key.startswith("acceptance.") or ".target" in key or key.endswith(".target"):
        return "target"
    if "assessment" in key:
        return "current"
    return ""


def fact_assessment_levels(fact_content: str) -> set[str]:
    return {
        re.sub(r"\s+", "", match.group(0)).casefold()
        for match in ASSESSMENT_LEVEL_PATTERN.finditer(fact_content)
    }


def _marker_clause_ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _normalized_block(value: str) -> str:
    return re.sub(r"\s+", "", value)


def policy_content_quality_issues(
    content: str,
    chapter_code: str,
    material: dict[str, Any] | None,
    *,
    mode: str,
    material_error: str = "",
    check_quality_gates: bool = True,
) -> list[dict[str, str]]:
    severity = "warning" if mode == "working" else "blocking"
    issues: list[dict[str, str]] = []

    def add(code: str, description: str, *, force_blocking: bool = False) -> None:
        issues.append(
            {
                "code": code,
                "description": description,
                "severity": "blocking" if force_blocking else severity,
            }
        )

    if chapter_code not in {"1.2.1", "2.1.1"}:
        return issues
    if material is None:
        add(
            "policy_material_unavailable",
            "无法重建当前政策章节材料：" + (material_error or "材料不可用"),
        )
        return issues

    if mode == "delivery" and check_quality_gates:
        for check in policy_material_quality_checks(material):
            if check["result"] == "fail":
                add(
                    "policy_quality_gate_failed",
                    f"{check['gate_code']}：{check['message']}",
                    force_blocking=True,
                )

    material_markers = POLICY_MATERIAL_MARKER_PATTERN.findall(content)
    expected_material_marker = (
        chapter_code,
        str(material.get("match_run_id") or ""),
        str(material.get("material_signature") or ""),
    )
    if material_markers != [expected_material_marker]:
        add(
            "policy_material_signature_mismatch",
            "正文未唯一绑定当前政策匹配运行、项目事实/范围和政策材料签名。",
        )

    if chapter_code == "1.2.1":
        expected_items = [
            item
            for section in SECTION_ORDER
            for item in material.get("basis_groups", {}).get(section, [])
        ]
        expected_markers = [
            (
                str(item.get("policy_id") or ""),
                [str(value) for value in item.get("clause_ids", [])],
            )
            for item in expected_items
        ]
        actual_markers = [
            (policy_id, _marker_clause_ids(clause_ids))
            for policy_id, clause_ids in POLICY_ITEM_MARKER_PATTERN.findall(content)
        ]
        if actual_markers != expected_markers:
            add(
                "policy_basis_items_mismatch",
                "正文四类依据中的政策ID、条款ID、数量或顺序与当前正式材料不一致。",
            )
        formal_rows = [
            line
            for line in content.splitlines()
            if re.match(r"^\s*\|\s*(?:\d+|—)\s*\|", line)
        ]
        expected_rows = []
        for section, _heading in BASIS_SECTION_HEADINGS:
            for index, item in enumerate(material.get("basis_groups", {}).get(section, []), 1):
                title = str(item.get("title") or "").strip().strip("《》〈〉")
                document_no = str(item.get("document_no") or "—").strip()
                clause_ids = ",".join(
                    str(value) for value in item.get("clause_ids", [])
                )
                expected_rows.append(
                    "| "
                    + " | ".join(
                        value.replace("|", "\\|").replace("\n", " ")
                        for value in (str(index), f"《{title}》", document_no, "正式采用")
                    )
                    + " | "
                    + f'<!-- policy-item id="{item.get("policy_id", "")}" clauses="{clause_ids}" -->'
                )
        if len(formal_rows) != len(expected_rows):
            add(
                "policy_basis_visible_row_count_mismatch",
                "正文全部可见依据数据行数与当前正式材料不一致。",
            )
        elif [_normalized_block(row) for row in formal_rows] != [
            _normalized_block(row) for row in expected_rows
        ]:
            add(
                "policy_basis_visible_row_mismatch",
                "正式依据的分组序号、名称、文号、状态或追踪标记与当前材料不一致。",
            )
        if mode == "delivery":
            stripped = re.sub(r"<!--.*?-->", "", content, flags=re.S)
            actual_visible_lines = [
                line.strip() for line in stripped.splitlines() if line.strip()
            ]
            intro = (
                f"本节依据{material.get('project_name', '')}的项目类型、建设范围、属地和投资管理事实，"
                "按政策类、行业标准、安全类标准和投资估算四组列示编制依据。"
                "正式采用的政策必须具备现行有效的官方文件和已核验条款；待核验目录项只保留名称，不据其标题扩写政策要求。"
            )
            expected_visible_lines = [intro]
            for index, (section, heading) in enumerate(BASIS_SECTION_HEADINGS, 1):
                expected_visible_lines.extend(
                    [
                        f"#### {chapter_code}.{index} {heading}",
                        "| 序号 | 依据名称 | 文号/标准号 | 使用状态 |",
                        "|---:|---|---|---|",
                    ]
                )
                expected_visible_lines.extend(
                    re.sub(r"<!--.*?-->", "", row, flags=re.S).strip()
                    for row in expected_rows[
                        sum(
                            len(material.get("basis_groups", {}).get(previous, []))
                            for previous, _ in BASIS_SECTION_HEADINGS[: index - 1]
                        ) : sum(
                            len(material.get("basis_groups", {}).get(previous, []))
                            for previous, _ in BASIS_SECTION_HEADINGS[:index]
                        )
                    ]
                )
            allowed_sequences = (
                expected_visible_lines,
                [expected_visible_lines[0], "**编制依据表**", *expected_visible_lines[1:]],
            )
            normalized_actual = [_normalized_block(line) for line in actual_visible_lines]
            if normalized_actual not in (
                [_normalized_block(line) for line in sequence]
                for sequence in allowed_sequences
            ):
                add(
                    "policy_basis_unbound_content",
                    "编制依据章节包含当前正式四类清单之外的标题、表格行或政策陈述。",
                    force_blocking=True,
                )
    else:
        expected_background = list(material.get("background_paragraphs", []))
        expected_markers = [
            (
                str(item.get("policy_id") or ""),
                [str(value) for value in item.get("clause_ids", [])],
                str(item.get("text_hash") or ""),
            )
            for item in expected_background
        ]
        marker_matches = list(POLICY_BACKGROUND_MARKER_PATTERN.finditer(content))
        actual_markers = [
            (match.group(1), _marker_clause_ids(match.group(2)), match.group(3))
            for match in marker_matches
        ]
        if actual_markers != expected_markers:
            add(
                "policy_background_items_mismatch",
                "政策背景的政策ID、条款ID、段落哈希或顺序与政策类依据链不一致。",
            )
        else:
            for marker, expected in zip(marker_matches, expected_background):
                tail = content[marker.end() :]
                paragraph = next(
                    (
                        line.strip()
                        for line in tail.splitlines()
                        if line.strip() and not line.lstrip().startswith("<!--")
                    ),
                    "",
                )
                if sha256_text(paragraph) != str(expected.get("text_hash") or ""):
                    add(
                        "policy_background_paragraph_hash_mismatch",
                        f"政策背景正文与已核验条款生成段落不一致：{expected.get('title', '')}。",
                    )
                    break
        if mode == "delivery":
            actual_headings = [
                line.strip()
                for line in content.splitlines()
                if line.lstrip().startswith("#")
            ]
            expected_headings = [
                f"#### {chapter_code}.{index} {heading}"
                for index, heading in enumerate(POLICY_BACKGROUND_HEADINGS, 1)
            ]
            if actual_headings != expected_headings:
                add(
                    "policy_background_heading_mismatch",
                    "政策背景必须且只能保留国家、省/自治区、市/项目地区三个固定层级标题及顺序。",
                    force_blocking=True,
                )
            stripped = re.sub(r"<!--.*?-->", "", content, flags=re.S)
            blocks = []
            for block in re.split(r"\n\s*\n", stripped):
                lines = [
                    line.strip()
                    for line in block.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                ]
                if lines:
                    blocks.append(" ".join(lines))
            intro = (
                f"{material.get('project_name', '')}政策背景按照国家、省或自治区、市或项目建设地区三个层级展开。"
                "本节政策顺序与前述政策类依据一致，且每段只改写已核验条款；仅有目录标题的文件不生成政策要求正文。"
            )
            allowed = [intro, *[str(item.get("text") or "") for item in expected_background]]
            if [_normalized_block(item) for item in blocks] != [
                _normalized_block(item) for item in allowed
            ]:
                add(
                    "policy_background_unbound_prose",
                    "政策背景包含未绑定到当前已核验条款的新增、缺失或改写正文。",
                    force_blocking=True,
                )
    return issues


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
            "章节仍含待补充、待确认、待核验、冲突或分析建议占位。",
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
    unresolved_marker_ids = {
        item.strip()
        for marker in UNRESOLVED_FACT_MARKER_PATTERN.findall(content)
        for item in re.split(r"[,，;；\s]+", marker)
        if item.strip()
    }
    invalid_marker_ids = sorted(marker_ids - valid_evidence_ids)
    if invalid_marker_ids:
        add("invalid_evidence_marker", "来源标记不属于本章节任务包：" + "、".join(invalid_marker_ids[:10]))
    if HIGH_RISK_NUMBER_PATTERN.search(content) and not (
        marker_ids or unresolved_marker_ids
    ):
        add("ungrounded_quantitative_assertion", "章节包含高风险数字结论，但没有 `<!-- evidence:来源ID -->` 标记。")

    current_state_metrics: dict[str, Any] | None = None
    if contract_validation.get("require_current_state_fact_traceability"):
        direct_facts = [
            source
            for source in sources
            if source.get("source_type") == "fact"
            and source.get("usage_mode") in {"direct", "evidence"}
        ]
        direct_fact_ids = {
            str(source.get("source_object_id") or "") for source in direct_facts
        }
        facts_without_evidence = sorted(
            str(source.get("source_object_id") or "")
            for source in direct_facts
            if int(source.get("evidence_count") or 0) < 1
        )
        facts_not_cited = sorted(direct_fact_ids - marker_ids)
        invalid_direct_statuses = sorted(
            str(source.get("source_object_id") or "")
            for source in direct_facts
            if source.get("fact_status") not in {"confirmed", "material_explicit"}
        )
        if facts_without_evidence:
            add(
                "current_state_fact_without_evidence",
                "确定性现状事实没有证据记录：" + "、".join(facts_without_evidence[:20]),
            )
        if facts_not_cited:
            add(
                "current_state_fact_not_cited",
                "确定性现状事实未在正文绑定来源标记：" + "、".join(facts_not_cited[:20]),
            )
        if invalid_direct_statuses:
            add(
                "current_state_invalid_direct_fact_status",
                "非确认状态事实被作为确定性现状来源：" + "、".join(invalid_direct_statuses[:20]),
            )
        current_state_metrics = {
            "direct_fact_count": len(direct_facts),
            "fact_with_evidence_count": len(direct_facts) - len(facts_without_evidence),
            "cited_fact_count": len(direct_fact_ids & marker_ids),
            "deterministic_fact_source_coverage": (
                1.0
                if not direct_facts
                else (len(direct_facts) - len(facts_without_evidence)) / len(direct_facts)
            ),
            "deterministic_fact_citation_coverage": (
                1.0
                if not direct_facts
                else len(direct_fact_ids & marker_ids) / len(direct_facts)
            ),
        }

    if contract_validation.get("require_unresolved_fact_disclosure"):
        unresolved_facts = {
            str(source.get("source_object_id") or "")
            for source in sources
            if source.get("source_type") == "fact"
            and source.get("usage_mode") not in {"direct", "evidence"}
            and source.get("fact_status")
            in {"pending_confirmation", "pending_supplement", "conflict"}
        }
        missing_unresolved = sorted(unresolved_facts - unresolved_marker_ids)
        invalid_unresolved = sorted(
            unresolved_marker_ids
            - {
                str(source.get("source_object_id") or "")
                for source in sources
                if source.get("source_type") == "fact"
            }
        )
        if missing_unresolved:
            add(
                "current_state_unresolved_fact_not_disclosed",
                "待确认、待补充或冲突现状事实未显式披露："
                + "、".join(missing_unresolved[:20]),
            )
        if invalid_unresolved:
            add(
                "invalid_unresolved_fact_marker",
                "待核实事实标记不属于本章节任务包："
                + "、".join(invalid_unresolved[:20]),
            )

    if contract_validation.get("require_current_state_dimensions"):
        dimensions = {
            item.casefold() for item in CURRENT_STATE_DIMENSION_PATTERN.findall(content)
        }
        missing_dimensions = sorted(CURRENT_STATE_REQUIRED_DIMENSIONS - dimensions)
        if missing_dimensions:
            add(
                "current_state_dimension_incomplete",
                "信息化现状维度不完整，缺少：" + "、".join(missing_dimensions),
            )

    if contract_validation.get("forbid_scope_as_current_state"):
        current_fact_text = "；".join(
            str(source.get("fact_content") or "")
            for source in sources
            if source.get("source_type") == "fact"
            and source.get("usage_mode") in {"direct", "evidence"}
        )
        unsupported_scope_claims: list[str] = []
        for source in sources:
            if source.get("source_type") != "scope":
                continue
            name = str(source.get("standard_name") or "").strip()
            if not name or name in current_fact_text:
                continue
            patterns = (
                rf"(?:现有|已建|已建设|已部署|已上线|正在使用)[^。；\n]{{0,12}}{re.escape(name)}",
                rf"{re.escape(name)}[^。；\n]{{0,12}}(?:现有|已建|已建设|已部署|已上线|正在使用)",
            )
            if any(re.search(pattern, content) for pattern in patterns):
                unsupported_scope_claims.append(name)
        if unsupported_scope_claims:
            add(
                "planned_scope_as_current_state",
                "拟建清单被写成现状且没有现状事实支持："
                + "、".join(sorted(set(unsupported_scope_claims))[:20]),
            )

    if contract_validation.get("enforce_assessment_state_separation"):
        current_levels: dict[str, set[str]] = {}
        target_levels: dict[str, set[str]] = {}
        not_participated_frameworks: set[str] = set()
        for source in sources:
            if source.get("source_type") != "fact":
                continue
            key = str(source.get("fact_key") or "")
            fact_text = str(source.get("fact_content") or "")
            framework = fact_assessment_framework(key, fact_text)
            kind = fact_assessment_kind(key)
            if not framework or not kind:
                continue
            levels = fact_assessment_levels(fact_text)
            (target_levels if kind == "target" else current_levels).setdefault(
                framework, set()
            ).update(levels)
            if kind == "current" and ("未参评" in fact_text or "未参与" in fact_text):
                not_participated_frameworks.add(framework)
        for framework, levels in target_levels.items():
            target_only = levels - current_levels.get(framework, set())
            for level in target_only:
                if re.search(
                    rf"(?:已通过|已达到|已获评|当前(?:为|达到)|现状(?:为|达到))[^。；\n]{{0,24}}{re.escape(level)}",
                    content,
                    flags=re.I,
                ):
                    add(
                        "assessment_target_as_current_state",
                        f"评级目标被改写为当前已达到状态：{framework}/{level}",
                    )
        for framework in not_participated_frameworks:
            zero_level_claim = re.search(
                r"(?:未参评|未参与)[^。；\n]{0,50}(?:现状|评级|等级)(?:为|是|等同于|视为)(?:零级|0级)",
                content,
            )
            capability_absence_claim = re.search(
                r"(?:未参评|未参与)[^。；\n]{0,50}(?:因此|说明|表明|意味着|即)(?:医院)?(?:不具备[^。；\n]{0,12}能力|尚未建设)",
                content,
            )
            if zero_level_claim or capability_absence_claim:
                add(
                    "assessment_not_participated_as_zero_or_absence",
                    f"未参评被换算为零级或能力缺失：{framework}",
                )

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
    result = {
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
    if current_state_metrics is not None:
        result["current_state_metrics"] = current_state_metrics
    return result


def validate_draft(
    database: Path,
    project_code: str,
    chapter_code: str,
    version_no: int,
    *,
    mode: str = "delivery",
) -> dict[str, Any]:
    policy_material = None
    policy_material_error = ""
    if chapter_code in {"1.2.1", "2.1.1"}:
        try:
            policy_material = build_policy_material(database, project_code, mode=mode)
        except ValueError as exc:
            policy_material_error = str(exc)
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
            elif source["source_type"] == "fact":
                row = conn.execute(
                    """
                    SELECT fact_key,fact_content,normalized_value,fact_status
                    FROM project_fact WHERE fact_id=?
                    """,
                    (source["source_object_id"],),
                ).fetchone()
                if row:
                    item.update(dict(row))
                evidence_rows = conn.execute(
                    """
                    SELECT e.source_location
                    FROM fact_evidence fe
                    JOIN evidence_record e ON e.evidence_id=fe.evidence_id
                    WHERE fe.fact_id=? AND fe.evidence_role='support'
                    ORDER BY e.evidence_id
                    """,
                    (source["source_object_id"],),
                ).fetchall()
                item["evidence_count"] = len(evidence_rows)
                item["evidence_locations"] = [row["source_location"] for row in evidence_rows]
            elif source["source_type"] == "corpus":
                row = conn.execute(
                    "SELECT clean_text,forbidden_terms_json FROM corpus_block WHERE block_id=?",
                    (source["source_object_id"],),
                ).fetchone()
                item["clean_text"] = row["clean_text"] if row else ""
                item["forbidden_terms_json"] = row["forbidden_terms_json"] if row else "[]"
            sources.append(item)
        if plan["section_role"] == "current_state":
            known_scope_ids = {
                str(source.get("source_object_id") or "")
                for source in sources
                if source.get("source_type") == "scope"
            }
            for row in conn.execute(
                """
                SELECT scope_id,standard_name FROM project_scope_item
                WHERE project_id=? AND customer_scope=1
                  AND status NOT IN ('rejected','not_applicable')
                ORDER BY standard_name,scope_id
                """,
                (project["project_id"],),
            ):
                if row["scope_id"] in known_scope_ids:
                    continue
                sources.append(
                    {
                        "source_type": "scope",
                        "source_object_id": row["scope_id"],
                        "usage_mode": "survey_lead",
                        "standard_name": row["standard_name"],
                    }
                )
        plan_data = dict(plan)
        plan_data["outline_nodes"] = outline_nodes
        result = assess_content(draft["content"], plan_data, sources, mode=mode)
        policy_issues = policy_content_quality_issues(
            draft["content"],
            chapter_code,
            policy_material,
            mode=mode,
            material_error=policy_material_error,
        )
        if policy_issues:
            result["issues"].extend(policy_issues)
            result["issue_count"] = len(result["issues"])
            result["blocking_count"] = sum(
                item["severity"] == "blocking" for item in result["issues"]
            )
            result["warning_count"] = sum(
                item["severity"] == "warning" for item in result["issues"]
            )
            result["status"] = "failed" if result["blocking_count"] else "passed"
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
