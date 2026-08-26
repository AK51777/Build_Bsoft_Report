#!/usr/bin/env python3
"""Build a reviewable local knowledge pack from a standard solution and scope workbook."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from openpyxl import load_workbook

from standard_solution_coverage import (
    COVERAGE_KEY,
    audit_standard_solution_coverage,
    build_source_section_manifest,
    require_complete_standard_solution_coverage,
)


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}
BUILDER_VERSION = "3.1"


def qn(local: str) -> str:
    return f"{{{W_NS}}}{local}"


def clean_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalized_key(value: object) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", clean_text(value).lower())


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return f"{prefix}-{sha256_bytes(raw.encode('utf-8'))[:16]}"


def paragraph_text(paragraph: ET.Element) -> str:
    pieces: list[str] = []
    for node in paragraph.iter():
        if node.tag == qn("t") and node.text:
            pieces.append(node.text)
        elif node.tag == qn("tab"):
            pieces.append("\t")
        elif node.tag in {qn("br"), qn("cr")}:
            pieces.append("\n")
    return clean_text("".join(pieces))


def read_style_levels(package: zipfile.ZipFile) -> dict[str, int]:
    root = ET.fromstring(package.read("word/styles.xml"))
    levels: dict[str, int] = {}
    for style in root.findall("w:style", NS):
        style_id = style.get(qn("styleId"), "")
        outline = style.find("w:pPr/w:outlineLvl", NS)
        if outline is not None:
            raw = outline.get(qn("val"), "")
            if raw.isdigit():
                levels[style_id] = int(raw) + 1
                continue
        name = style.find("w:name", NS)
        candidates = [style_id, name.get(qn("val"), "") if name is not None else ""]
        for candidate in candidates:
            match = re.search(r"(?:heading|标题)\s*([1-9])", candidate, re.IGNORECASE)
            if match:
                levels[style_id] = int(match.group(1))
                break
    return levels


def heading_level(paragraph: ET.Element, style_levels: dict[str, int]) -> int | None:
    direct = paragraph.find("w:pPr/w:outlineLvl", NS)
    if direct is not None:
        raw = direct.get(qn("val"), "")
        if raw.isdigit():
            return int(raw) + 1
    style = paragraph.find("w:pPr/w:pStyle", NS)
    return style_levels.get(style.get(qn("val"), "") if style is not None else "")


def table_text(table: ET.Element) -> str:
    rows: list[str] = []
    for row in table.findall("w:tr", NS):
        cells = [paragraph_text(cell) for cell in row.findall("w:tc", NS)]
        if any(cells):
            rows.append(" | ".join(cells))
    return clean_text("\n".join(rows))


ROLE_RULES = (
    ("policy", ("政策", "法规", "编制依据")),
    ("implementation_operation", ("实施", "培训", "运维", "上线", "迁移", "测试")),
    ("benefit_performance", ("效益", "绩效")),
    ("risk", ("风险", "应急")),
    ("construction_content", ("建设内容", "功能设计", "业务设计", "系统设计", "平台建设")),
    ("overall_design", ("总体设计", "总体架构", "技术路线", "标准体系", "安全体系", "信创")),
    ("necessity_feasibility", ("必要性", "可行性")),
    ("problem_need", ("需求分析", "问题分析")),
    ("current_state", ("现状", "单位概况")),
)


def section_role(heading_path: list[str]) -> str:
    joined = " / ".join(heading_path)
    for role, terms in ROLE_RULES:
        if any(term in joined for term in terms):
            return role
    return "reference_structure"


def reuse_policy(role: str, text: str) -> tuple[str, str]:
    if role == "policy":
        return "D", "prohibited"
    marketing_terms = ("创业慧康", "Bsoft", "我司", "领先", "标杆", "全面赋能")
    if any(term.lower() in text.lower() for term in marketing_terms):
        return "C", "approved"
    if role in {"construction_content", "overall_design", "implementation_operation"}:
        return "B", "approved"
    return "C", "approved"


def match_module(heading_path: list[str], module_index: list[tuple[str, str]]) -> str:
    joined = normalized_key("/".join(heading_path))
    matches = [(key, code) for key, code in module_index if len(key) >= 3 and key in joined]
    if not matches:
        return ""
    return max(matches, key=lambda item: len(item[0]))[1]


def capability_block_ids(
    capability: dict[str, Any], blocks: list[dict[str, Any]]
) -> tuple[list[str], str]:
    """Bind a capability to the narrowest reviewed construction material."""
    heading_blocks = [
        (block, normalized_key("/".join(block.get("heading_path", []))))
        for block in blocks
        if block.get("section_role") == "construction_content"
        and block.get("review_status") == "approved"
        and block.get("heading_path")
    ]
    product_key = normalized_key(capability.get("product_name", ""))
    specific_keys = [
        normalized_key(capability.get("capability_name", "")),
        normalized_key(capability.get("module_name", "")),
    ]
    specific_keys = [
        key
        for key in dict.fromkeys(specific_keys)
        if len(key) >= 2 and key != product_key
    ]
    specific = [
        block["block_id"]
        for block, heading_key in heading_blocks
        if any(key in heading_key or heading_key in key for key in specific_keys)
    ]
    if specific:
        return list(dict.fromkeys(specific)), "capability_or_module_heading"
    product = [
        block["block_id"]
        for block, heading_key in heading_blocks
        if len(product_key) >= 2 and product_key in heading_key
    ]
    if product:
        return list(dict.fromkeys(product)), "product_heading_fallback"
    return [], "unmatched"


def extract_solution_sections(path: Path) -> list[dict[str, Any]]:
    """Extract every heading occurrence and its direct body without length filtering."""
    source_hash = sha256_bytes(path.read_bytes())
    with zipfile.ZipFile(path) as package:
        style_levels = read_style_levels(package)
        document = ET.fromstring(package.read("word/document.xml"))
    body = document.find("w:body", NS)
    if body is None:
        raise ValueError("DOCX does not contain a document body")
    headings: list[str] = []
    sections: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None
    paragraph_index = 0
    table_index = 0

    def new_section(
        heading_path: list[str], source_location: str, *, source_is_heading: bool
    ) -> dict[str, Any]:
        source_order = len(sections) + 1
        section = {
            "source_section_id": stable_id(
                "STDSECTION", source_hash, source_order, source_location, heading_path
            ),
            "source_order": source_order,
            "source_location": source_location,
            "heading_path": list(heading_path),
            "source_is_heading": source_is_heading,
            "pieces": [],
        }
        sections.append(section)
        return section

    for child in body:
        if child.tag == qn("p"):
            paragraph_index += 1
            text = paragraph_text(child)
            if not text:
                continue
            level = heading_level(child, style_levels)
            if level is not None:
                headings = headings[: level - 1]
                headings.append(text)
                current_section = new_section(
                    headings, f"paragraph:{paragraph_index}", source_is_heading=True
                )
                continue
            if current_section is None:
                current_section = new_section(
                    headings, f"paragraph:{paragraph_index}", source_is_heading=False
                )
            current_section["pieces"].append(text)
        elif child.tag == qn("tbl"):
            table_index += 1
            text = table_text(child)
            if text:
                if current_section is None:
                    current_section = new_section(
                        headings, f"table:{table_index}", source_is_heading=False
                    )
                current_section["pieces"].append(text)

    for section in sections:
        section["clean_text"] = clean_text("\n\n".join(section.pop("pieces")))
    return sections


def blocks_from_solution_sections(
    sections: list[dict[str, Any]],
    module_index: list[tuple[str, str]],
    *,
    max_chars: int = 1800,
) -> list[dict[str, Any]]:
    """Chunk sections without dropping short tails or heading-only structure nodes."""
    if max_chars < 1:
        raise ValueError("max_chars must be greater than zero")

    blocks: list[dict[str, Any]] = []
    for section in sections:
        heading_path = list(section.get("heading_path", []))
        pieces = str(section.get("clean_text", "")).split("\n\n") if section.get("clean_text") else []
        current: list[str] = []
        current_chars = 0
        chunk_no = 0

        def emit(chunk_text: str) -> None:
            nonlocal chunk_no
            chunk_no += 1
            text = clean_text(chunk_text)
            role = section_role(heading_path)
            reuse_class, review_status = reuse_policy(role, text)
            content_type = "structure_only" if not text else (
                "construction_solution" if role == "construction_content" else "common_narrative"
            )
            text_hash = sha256_bytes(text.encode("utf-8"))
            blocks.append(
                {
                    "block_id": stable_id(
                        "STDBLOCK", section["source_section_id"], chunk_no, text_hash
                    ),
                    "source_section_id": section["source_section_id"],
                    "source_order": section["source_order"],
                    "chunk_index": chunk_no,
                    "source_location": section["source_location"],
                    "source_is_heading": section["source_is_heading"],
                    "heading_path": heading_path,
                    "section_role": role,
                    "module_code": match_module(heading_path, module_index),
                    "clean_text": text,
                    "text_hash": text_hash,
                    "visible_text_hash": text_hash,
                    "content_type": content_type,
                    "content_format": "plain_text",
                    "content_payload": {},
                    "asset_manifest": [],
                    "length_band": "empty" if not text else (
                        "short" if len(text) < 120 else "medium" if len(text) <= max_chars else "long"
                    ),
                    "reuse_class": reuse_class,
                    "quality_level": "B",
                    "review_status": review_status,
                    "prerequisites": [],
                    "variable_slots": [],
                    "forbidden_terms": ["创业慧康", "Bsoft", "我司"],
                }
            )

        for piece in pieces:
            if current and current_chars + len(piece) > max_chars:
                emit("\n\n".join(current))
                current = []
                current_chars = 0
            current.append(piece)
            current_chars += len(piece)
        if current:
            emit("\n\n".join(current))
        elif not pieces:
            emit("")
    return blocks


def chunk_solution(
    path: Path,
    module_index: list[tuple[str, str]],
    *,
    min_chars: int = 1,
    max_chars: int = 1800,
) -> list[dict[str, Any]]:
    """Backward-compatible entry point; min_chars is classification-only, never a filter."""
    if min_chars < 0:
        raise ValueError("min_chars cannot be negative")
    sections = extract_solution_sections(path)
    return blocks_from_solution_sections(sections, module_index, max_chars=max_chars)


def workbook_capabilities(path: Path) -> list[dict[str, Any]]:
    workbook = load_workbook(path, read_only=False, data_only=True)
    capabilities: list[dict[str, Any]] = []
    workbook_hash = sha256_bytes(path.read_bytes())
    for sheet in workbook.worksheets:
        header_row = None
        headers: list[str] = []
        for row_number in range(1, min(sheet.max_row, 15) + 1):
            values = [clean_text(sheet.cell(row_number, column).value) for column in range(1, sheet.max_column + 1)]
            if "系统名称" in values and any(value in values for value in ("模块名称", "产品模块")):
                header_row = row_number
                headers = values
                break
        if header_row is None:
            continue
        columns = {name: index + 1 for index, name in enumerate(headers) if name}
        system_column = columns["系统名称"]
        module_column = columns.get("模块名称") or columns.get("产品模块")
        function_column = columns.get("产品功能")
        category_column = columns.get("大类")
        selection_columns = [
            column for name, column in columns.items()
            if "选择" in name or "推荐" in name or "必选" in name
        ]
        current_category = ""
        current_system = ""
        current_module = ""
        for row_number in range(header_row + 1, sheet.max_row + 1):
            if category_column:
                current_category = clean_text(sheet.cell(row_number, category_column).value) or current_category
            current_system = clean_text(sheet.cell(row_number, system_column).value) or current_system
            current_module = clean_text(sheet.cell(row_number, module_column).value) or current_module
            function_name = clean_text(sheet.cell(row_number, function_column).value) if function_column else ""
            capability_name = function_name or current_module
            if not current_system or not capability_name:
                continue
            selection = [clean_text(sheet.cell(row_number, column).value) for column in selection_columns]
            description_parts = [current_category, current_system, current_module]
            if function_name:
                description_parts.append(function_name)
            description_parts.extend(value for value in selection if value)
            product_code = stable_id("PRODUCT", sheet.title, current_system)
            capability_id = stable_id("CAPABILITY", workbook_hash, sheet.title, current_system, capability_name)
            capabilities.append(
                {
                    "capability_id": capability_id,
                    "product_code": product_code,
                    "product_name": current_system,
                    "capability_name": capability_name,
                    "capability_description": " / ".join(dict.fromkeys(part for part in description_parts if part)),
                    "category": current_category,
                    "module_name": current_module,
                    "selection_rules": [value for value in selection if value],
                    "prerequisites": [],
                    "interface_dependencies": [],
                    "exclusions": [],
                    "applicable_versions": ["2025"],
                    "standard_block_ids": [],
                    "review_status": "approved",
                    "source_location": f"{sheet.title}!row:{row_number}",
                }
            )
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for capability in capabilities:
        unique[(capability["product_code"], capability["capability_name"])] = capability
    return list(unique.values())


def build_pack(solution: Path, scope: Path, *, title: str = "") -> dict[str, Any]:
    solution = solution.resolve()
    scope = scope.resolve()
    if not solution.is_file() or solution.suffix.lower() != ".docx":
        raise FileNotFoundError(f"standard solution DOCX not found: {solution}")
    if not scope.is_file() or scope.suffix.lower() != ".xlsx":
        raise FileNotFoundError(f"standard scope XLSX not found: {scope}")
    capabilities = workbook_capabilities(scope)
    module_index: list[tuple[str, str]] = []
    for capability in capabilities:
        for name in (capability["product_name"], capability["module_name"], capability["capability_name"]):
            key = normalized_key(name)
            if key:
                module_index.append((key, capability["product_code"]))
    sections = extract_solution_sections(solution)
    blocks = blocks_from_solution_sections(sections, module_index)
    semantic_by_role = {
        "construction_content": "application_software_solution",
        "overall_design": "technical_route",
        "implementation_operation": "implementation_operation",
        "benefit_performance": "construction_benefit",
        "necessity_feasibility": "necessity_feasibility",
        "problem_need": "requirements_analysis",
        "current_state": "project_background",
        "policy": "policy_basis",
    }
    adaptation_by_reuse = {
        "A": "direct",
        "B": "parameterized",
        "C": "structure_only",
        "D": "prohibited",
    }
    for block in blocks:
        block["source_corpus_type"] = "standard_solution"
        block["content_type"] = (
            "structure_only" if not block["clean_text"] else "construction_solution"
        )
        block["semantic_section"] = semantic_by_role.get(
            block["section_role"], "application_software_solution"
        )
        block["content_slot"] = block.get("module_code") or block["section_role"]
        block["adaptation_mode"] = (
            "structure_only"
            if not block["clean_text"]
            else adaptation_by_reuse[block["reuse_class"]]
        )
        block["assessment_targets"] = []
        block["construction_scope_tags"] = (
            [block["module_code"]] if block.get("module_code") else []
        )
    source_sections = build_source_section_manifest(sections, blocks)
    coverage = audit_standard_solution_coverage(source_sections, blocks)
    for capability in capabilities:
        block_ids, match_scope = capability_block_ids(capability, blocks)
        capability["standard_block_ids"] = block_ids
        capability["block_match_scope"] = match_scope
    source_hashes = {
        "solution": sha256_bytes(solution.read_bytes()),
        "scope": sha256_bytes(scope.read_bytes()),
    }
    package_id = stable_id(
        "STANDARDPACK", BUILDER_VERSION, source_hashes["solution"], source_hashes["scope"]
    )
    result = {
        "schema_version": "1.1",
        "builder_version": BUILDER_VERSION,
        "package_id": package_id,
        "title": title or solution.stem,
        "permission_scope": "internal_company_reuse",
        "source_files": [
            {"role": "standard_solution", "file_name": solution.name, "sha256": source_hashes["solution"]},
            {"role": "standard_scope", "file_name": scope.name, "sha256": source_hashes["scope"]},
        ],
        "corpus": {
            "document_id": stable_id("STDDOC", source_hashes["solution"]),
            "document_type": "feasibility_study",
            "project_type": "hospital_informationization",
            "source_corpus_type": "standard_solution",
            "quality_level": "B",
            "review_status": "approved",
            "source_sections": source_sections,
            "blocks": blocks,
        },
        "capabilities": capabilities,
        "review_summary": {
            "block_status_counts": dict(Counter(block["review_status"] for block in blocks)),
            "reuse_class_counts": dict(Counter(block["reuse_class"] for block in blocks)),
            "capability_count": len(capabilities),
            "capability_block_relation_count": sum(
                len(capability["standard_block_ids"]) for capability in capabilities
            ),
            "capabilities_without_blocks": sum(
                not capability["standard_block_ids"] for capability in capabilities
            ),
            "policy_blocks_prohibited": sum(block["section_role"] == "policy" for block in blocks),
            COVERAGE_KEY: coverage,
        },
    }
    require_complete_standard_solution_coverage(result, context="新生成的标准知识包")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("standard_solution", type=Path)
    parser.add_argument("standard_scope", type=Path)
    parser.add_argument("--title", default="")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = build_pack(args.standard_solution, args.standard_scope, title=args.title)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"package_id": result["package_id"], "blocks": len(result["corpus"]["blocks"]), "capabilities": len(result["capabilities"]), "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
