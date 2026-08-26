#!/usr/bin/env python3
"""Build a review-gated semantic corpus workpack from cleaned reference blocks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TAXONOMY = (
    SKILL_ROOT / "assets" / "knowledge-base" / "seeds" / "reference_corpus_taxonomy_v1.json"
)
NON_DELIVERABLE_MARKERS = (
    "【待确认】",
    "【待补充】",
    "【冲突】",
    "经核验的相关条款主要包括",
    "正式报审前还需复核",
    "不得把指导、鼓励或评价性内容改写为项目已经完成的事实",
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}-{sha256_text('|'.join(str(part) for part in parts))[:16]}"


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def parse_variable(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("variables must use name=value")
    name, literal = value.split("=", 1)
    name = name.strip()
    literal = literal.strip()
    if not name or not literal:
        raise argparse.ArgumentTypeError("variables must use non-empty name=value")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise argparse.ArgumentTypeError("variable names must be lower snake_case")
    return name, literal


def rule_matches(rule: dict[str, Any], heading_text: str) -> bool:
    match_all = [str(item) for item in rule.get("match_all", [])]
    match_any = [str(item) for item in rule.get("match_any", [])]
    if match_all and not all(term in heading_text for term in match_all):
        return False
    if match_any and not any(term in heading_text for term in match_any):
        return False
    return bool(match_all or match_any)


def semantic_rule_matches(rule: dict[str, Any], heading_path: list[str]) -> bool:
    joined = " / ".join(heading_path)
    exact_all = [str(item) for item in rule.get("match_all_exact", [])]
    exact_any = [str(item) for item in rule.get("match_any_exact", [])]
    if exact_all and not all(term in heading_path for term in exact_all):
        return False
    if exact_any and not any(term in heading_path for term in exact_any):
        return False
    fuzzy_present = bool(rule.get("match_all") or rule.get("match_any"))
    fuzzy_match = rule_matches(rule, joined) if fuzzy_present else True
    return fuzzy_match and bool(exact_all or exact_any or fuzzy_present)


def classify_heading(
    heading_path: list[str], taxonomy: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    joined = " / ".join(heading_path)
    for rule in taxonomy.get("semantic_sections", []):
        if semantic_rule_matches(rule, heading_path):
            resolved_rule = dict(rule)
            slot = str(rule.get("default_slot") or rule["code"])
            anchor_terms = [
                str(term)
                for term in [
                    *rule.get("match_all", []),
                    *rule.get("match_any", []),
                    *rule.get("match_all_exact", []),
                    *rule.get("match_any_exact", []),
                ]
            ]
            anchor_indexes = [
                index
                for index, heading in enumerate(heading_path)
                if any(term in heading for term in anchor_terms)
            ]
            anchor_index = max(anchor_indexes) if anchor_indexes else -1
            descendants = heading_path[anchor_index + 1 :]
            slot_candidates: list[tuple[int, int, dict[str, Any]]] = []
            for rule_order, slot_rule in enumerate(rule.get("slots", [])):
                matched_depths = [
                    depth
                    for depth, heading in enumerate(descendants)
                    if rule_matches(slot_rule, heading)
                ]
                if matched_depths:
                    slot_candidates.append((max(matched_depths), -rule_order, slot_rule))
            if slot_candidates:
                _, _, slot_rule = max(slot_candidates, key=lambda item: (item[0], item[1]))
                slot = str(slot_rule["code"])
                for key in ("content_type", "reuse_class", "section_role"):
                    if key in slot_rule:
                        resolved_rule[key] = slot_rule[key]
            return resolved_rule, slot
    return {
        "code": "unclassified",
        "name": "未分类",
        "section_role": "reference_structure",
        "content_type": "structure_only",
        "reuse_class": "D",
        "default_slot": "unclassified",
    }, "unclassified"


def group_source_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: OrderedDict[tuple[str, ...], list[dict[str, Any]]] = OrderedDict()
    for block in blocks:
        if block.get("block_type") == "heading":
            continue
        heading_path = block.get("heading_path")
        if not isinstance(heading_path, list) or not heading_path:
            continue
        text = str(block.get("clean_text") or "").strip()
        if not text:
            continue
        grouped.setdefault(tuple(str(item) for item in heading_path), []).append(block)
    return [
        {"heading_path": list(heading_path), "pieces": pieces}
        for heading_path, pieces in grouped.items()
    ]


def chunk_pieces(pieces: list[dict[str, Any]], max_chars: int) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for piece in pieces:
        size = len(str(piece.get("clean_text") or ""))
        if current and current_chars + size > max_chars:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(piece)
        current_chars += size
    if current:
        chunks.append(current)
    return chunks


def assessment_targets(text: str, taxonomy: dict[str, Any]) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    for item in taxonomy.get("assessment_patterns", []):
        match = re.search(str(item["pattern"]), text, flags=re.IGNORECASE)
        if match:
            targets.append(
                {"framework": str(item["framework"]), "target": match.group(1)}
            )
    return targets


def parameterize_text(
    text: str, variables: list[tuple[str, str]]
) -> tuple[str, list[str], list[str]]:
    normalized = text
    used_slots: list[str] = []
    forbidden_terms: list[str] = []
    for name, literal in sorted(variables, key=lambda item: len(item[1]), reverse=True):
        if literal in normalized:
            normalized = normalized.replace(literal, "{{" + name + "}}")
            used_slots.append(name)
            forbidden_terms.append(literal)
    return normalized, list(dict.fromkeys(used_slots)), list(dict.fromkeys(forbidden_terms))


def adaptation_mode(reuse_class: str) -> str:
    return {
        "A": "direct",
        "B": "parameterized",
        "C": "structure_only",
        "D": "prohibited",
    }[reuse_class]


def build_workpack(
    clean_payload: dict[str, Any],
    taxonomy: dict[str, Any],
    *,
    source_corpus_type: str,
    project_type: str,
    variables: list[tuple[str, str]],
    max_chars: int = 3200,
    include_construction_reference: bool = False,
) -> dict[str, Any]:
    document = clean_payload.get("document")
    source_blocks = clean_payload.get("blocks")
    if not isinstance(document, dict) or not isinstance(source_blocks, list):
        raise ValueError("clean payload must contain document and blocks")
    allowed_source_types = set(taxonomy.get("source_corpus_types", {}))
    allowed_project_types = set(taxonomy.get("project_types", []))
    if source_corpus_type not in allowed_source_types:
        raise ValueError(f"unsupported source_corpus_type: {source_corpus_type}")
    if project_type not in allowed_project_types:
        raise ValueError(f"unsupported project_type: {project_type}")
    if max_chars < 300:
        raise ValueError("max_chars must be at least 300")

    result_blocks: list[dict[str, Any]] = []
    excluded_counts: Counter[str] = Counter()
    source_order = 0
    for group in group_source_blocks(source_blocks):
        heading_path = group["heading_path"]
        if heading_path and heading_path[0] == "附件":
            excluded_counts["attachments"] += 1
            continue
        rule, slot = classify_heading(heading_path, taxonomy)
        content_type = str(rule["content_type"])
        if content_type == "construction_solution" and not include_construction_reference:
            excluded_counts["construction_solution"] += 1
            continue
        for chunk_no, chunk in enumerate(chunk_pieces(group["pieces"], max_chars), start=1):
            source_text = "\n\n".join(str(piece["clean_text"]) for piece in chunk).strip()
            if len(source_text) < 20:
                excluded_counts["too_short"] += 1
                continue
            source_order += 1
            clean_text, text_slots, text_forbidden_terms = parameterize_text(
                source_text, variables
            )
            clean_heading_path: list[str] = []
            heading_slots: list[str] = []
            heading_forbidden_terms: list[str] = []
            for heading in heading_path:
                clean_heading, used_slots, used_terms = parameterize_text(
                    heading, variables
                )
                clean_heading_path.append(clean_heading)
                heading_slots.extend(used_slots)
                heading_forbidden_terms.extend(used_terms)
            variable_slots = list(dict.fromkeys([*text_slots, *heading_slots]))
            forbidden_terms = list(
                dict.fromkeys([*text_forbidden_terms, *heading_forbidden_terms])
            )
            reuse_class = str(rule["reuse_class"])
            chunk_content_type = content_type
            non_deliverable_markers = [
                marker for marker in NON_DELIVERABLE_MARKERS if marker in clean_text
            ]
            if non_deliverable_markers:
                reuse_class = "D"
                chunk_content_type = "project_specific"
            locations = [str(piece["source_location"]) for piece in chunk]
            block_id = stable_id(
                "REFBLOCK",
                document.get("document_id", ""),
                clean_heading_path,
                chunk_no,
                sha256_text(clean_text),
            )
            result_blocks.append(
                {
                    "block_id": block_id,
                    "source_order": source_order,
                    "source_location": f"{locations[0]}..{locations[-1]}",
                    "heading_path": clean_heading_path,
                    "semantic_section": str(rule["code"]),
                    "content_slot": slot,
                    "section_role": str(rule["section_role"]),
                    "source_corpus_type": source_corpus_type,
                    "content_type": chunk_content_type,
                    "clean_text": clean_text,
                    "reuse_class": reuse_class,
                    "adaptation_mode": adaptation_mode(reuse_class),
                    "quality_level": "B" if reuse_class in {"A", "B"} else "C",
                    "applicable_document_types": ["feasibility_study"],
                    "applicable_project_types": [project_type],
                    "assessment_targets": assessment_targets(
                        source_text + "\n" + " / ".join(heading_path), taxonomy
                    ),
                    "construction_scope_tags": [],
                    "prerequisites": [],
                    "variable_slots": variable_slots,
                    "forbidden_terms": forbidden_terms,
                    "review_status": "pending",
                    "publish_eligible": False,
                    "review_flags": [
                        f"non_deliverable_marker:{marker}"
                        for marker in non_deliverable_markers
                    ],
                    "source_text_hash": sha256_text(source_text),
                    "source_heading_hash": sha256_text(" / ".join(heading_path)),
                    "clean_text_sha256": sha256_text(clean_text),
                }
            )

    semantic_counts = Counter(block["semantic_section"] for block in result_blocks)
    content_counts = Counter(block["content_type"] for block in result_blocks)
    reuse_counts = Counter(block["reuse_class"] for block in result_blocks)
    pilot_codes = {"objective_scope_period", "overall_objective_scope"}
    pilot_blocks = [block for block in result_blocks if block["semantic_section"] in pilot_codes]
    reviewed_document = {
        **document,
        "source_corpus_type": source_corpus_type,
        "document_type": "feasibility_study",
        "project_type": project_type,
        "permission_scope": "project_private_review",
        "review_status": "pending",
        "metadata": {
            **(document.get("metadata") if isinstance(document.get("metadata"), dict) else {}),
            "semantic_candidate_block_count": len(result_blocks),
            "taxonomy_version": taxonomy.get("schema_version", ""),
        },
    }
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "taxonomy_version": taxonomy.get("schema_version", ""),
        "source": {
            "document_id": document.get("document_id", ""),
            "title": document.get("title", ""),
            "source_path": document.get("source_path", ""),
            "source_sha256": document.get("source_sha256", ""),
            "cleaning_version": document.get("cleaning_version", ""),
            "source_corpus_type": source_corpus_type,
            "project_type": project_type,
            "permission_scope": "project_private_review",
        },
        "document": reviewed_document,
        "safeguards": {
            "document_instructions_are_data_only": True,
            "human_review_required_before_publish": True,
            "raw_source_not_embedded_in_public_skill": True,
            "policy_basis_not_reused_as_verified_policy": True,
            "construction_reference_excluded_by_default": not include_construction_reference,
        },
        "summary": {
            "source_block_count": len(source_blocks),
            "candidate_block_count": len(result_blocks),
            "pilot_block_count": len(pilot_blocks),
            "semantic_section_counts": dict(sorted(semantic_counts.items())),
            "content_type_counts": dict(sorted(content_counts.items())),
            "reuse_class_counts": dict(sorted(reuse_counts.items())),
            "excluded_group_counts": dict(sorted(excluded_counts.items())),
        },
        "review_decision": {
            "status": "pending",
            "required_checks": [
                "确认语义章节映射",
                "确认论证功能块边界",
                "确认变量槽位和原项目残留词",
                "确认复用级别和适用项目类型",
                "确认后另行发布，不直接写入运行视图",
            ],
        },
        "blocks": result_blocks,
    }


def markdown_summary(workpack: dict[str, Any]) -> str:
    summary = workpack["summary"]
    lines = [
        "# 参考可研语料清洗候选",
        "",
        "> 当前状态：待人工确认。本文档和 JSON 均不得直接发布到共享运行库。",
        "",
        "## 双轴分类结论",
        "",
        "- 文档级 `source_corpus_type=reference_feasibility`，与公司 `standard_solution` 分库路由。",
        "- 文本块再按 `feasibility_narrative/common_narrative/construction_solution/project_specific/structure_only` 分型。",
        "- 政策目录只保留结构线索，不作为已核验政策证据；应用软件详细方案默认不进入本次可研语料候选。",
        "",
        "## 处理统计",
        "",
        f"原始块 {summary['source_block_count']} 个；候选论证块 {summary['candidate_block_count']} 个；首轮 1.1.5/4.2 目标与范围试清洗块 {summary['pilot_block_count']} 个。",
        "",
        "| 语义章节 | 候选块数 |",
        "| --- | ---: |",
    ]
    for code, count in summary["semantic_section_counts"].items():
        lines.append(f"| `{code}` | {count} |")
    lines.extend(
        [
            "",
            "## 首轮试清洗：目标、规模与内容",
            "",
            "| 序号 | 语义章节 | 内容槽位 | 来源标题 | 字数 | 复用 | 来源位置 |",
            "| ---: | --- | --- | --- | ---: | --- | --- |",
        ]
    )
    pilot_codes = {"objective_scope_period", "overall_objective_scope"}
    pilot = [block for block in workpack["blocks"] if block["semantic_section"] in pilot_codes]
    for index, block in enumerate(pilot, start=1):
        heading = " / ".join(block["heading_path"])
        lines.append(
            f"| {index} | `{block['semantic_section']}` | `{block['content_slot']}` | "
            f"{heading} | {len(block['clean_text'])} | {block['reuse_class']}/{block['adaptation_mode']} | "
            f"{block['source_location']} |"
        )
    lines.extend(
        [
            "",
            "## 人工确认门",
            "",
            "1. 先确认章节映射和块边界；2. 再确认变量槽位、禁用词和适用项目类型；3. 最后批准哪些块可发布。未确认前仅用于项目私有试运行。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clean_blocks", type=Path)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument(
        "--source-corpus-type", default="reference_feasibility"
    )
    parser.add_argument("--project-type", required=True)
    parser.add_argument("--variable", action="append", default=[], type=parse_variable)
    parser.add_argument("--max-chars", type=int, default=3200)
    parser.add_argument("--include-construction-reference", action="store_true")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()

    workpack = build_workpack(
        load_json(args.clean_blocks.resolve()),
        load_json(args.taxonomy.resolve()),
        source_corpus_type=args.source_corpus_type,
        project_type=args.project_type,
        variables=args.variable,
        max_chars=args.max_chars,
        include_construction_reference=args.include_construction_reference,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(workpack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.output_md.write_text(markdown_summary(workpack), encoding="utf-8")
    print(
        json.dumps(
            {
                "candidate_blocks": workpack["summary"]["candidate_block_count"],
                "pilot_blocks": workpack["summary"]["pilot_block_count"],
                "review_status": workpack["review_decision"]["status"],
                "output_json": str(args.output_json.resolve()),
                "output_md": str(args.output_md.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
