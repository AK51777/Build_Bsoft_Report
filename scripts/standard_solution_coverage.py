#!/usr/bin/env python3
"""Audit lossless transfer from a standard solution DOCX into a knowledge pack."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Iterable


COVERAGE_KEY = "standard_solution_coverage"
_SUMMARY_FIELDS = (
    "status",
    "source_section_count",
    "emitted_section_count",
    "source_heading_count",
    "emitted_heading_count",
    "source_block_count",
    "emitted_block_count",
    "missing_section_count",
    "unexpected_section_count",
    "mismatched_section_count",
    "short_nonempty_section_count",
    "short_nonempty_sections_preserved",
    "empty_heading_section_count",
    "empty_heading_sections_preserved",
    "source_structure_hash",
    "emitted_structure_hash",
    "source_content_hash",
    "emitted_content_hash",
)


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _text_hash(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def build_source_section_manifest(
    sections: Iterable[dict[str, Any]], blocks: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Create a compact, independently verifiable manifest for every source section."""
    block_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        block_groups[str(block.get("source_section_id", ""))].append(block)
    result: list[dict[str, Any]] = []
    for section in sorted(sections, key=lambda item: int(item.get("source_order", 0))):
        section_id = str(section["source_section_id"])
        expected_blocks = sorted(
            block_groups.get(section_id, []), key=lambda item: int(item.get("chunk_index", 0))
        )
        text = str(section.get("clean_text", ""))
        result.append(
            {
                "source_section_id": section_id,
                "source_order": int(section.get("source_order", 0)),
                "source_location": str(section.get("source_location", "")),
                "heading_path": list(section.get("heading_path", [])),
                "source_is_heading": bool(section.get("source_is_heading", False)),
                "clean_text_hash": _text_hash(text),
                "char_count": len(text),
                "expected_block_count": len(expected_blocks),
                "expected_block_ids": [str(block.get("block_id", "")) for block in expected_blocks],
            }
        )
    return result


def audit_standard_solution_coverage(
    source_sections: Iterable[dict[str, Any]], blocks: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Reconstruct every source section from blocks and report any structural/content loss."""
    expected = sorted(source_sections, key=lambda item: int(item.get("source_order", 0)))
    emitted_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    emitted_blocks = list(blocks)
    for block in emitted_blocks:
        emitted_groups[str(block.get("source_section_id", ""))].append(block)

    expected_ids = {str(item.get("source_section_id", "")) for item in expected}
    missing: list[str] = []
    mismatched: list[str] = []
    emitted_structure: list[dict[str, Any]] = []
    emitted_content: list[dict[str, Any]] = []
    emitted_heading_count = 0
    short_preserved = 0
    empty_preserved = 0

    for section in expected:
        section_id = str(section.get("source_section_id", ""))
        chunks = sorted(
            emitted_groups.get(section_id, []), key=lambda item: int(item.get("chunk_index", 0))
        )
        if not chunks:
            missing.append(section_id)
            continue
        first = chunks[0]
        chunk_indexes = [int(item.get("chunk_index", 0)) for item in chunks]
        reconstructed = "\n\n".join(str(item.get("clean_text", "")) for item in chunks)
        structure = {
            "source_section_id": section_id,
            "source_order": int(first.get("source_order", -1)),
            "source_location": str(first.get("source_location", "")),
            "heading_path": list(first.get("heading_path", [])),
            "source_is_heading": bool(first.get("source_is_heading", False)),
        }
        emitted_structure.append(structure)
        emitted_content.append(
            {"source_section_id": section_id, "clean_text_hash": _text_hash(reconstructed)}
        )
        if structure["source_is_heading"]:
            emitted_heading_count += 1
        expected_ids_for_section = [str(value) for value in section.get("expected_block_ids", [])]
        actual_ids = [str(item.get("block_id", "")) for item in chunks]
        checks = (
            structure["source_order"] == int(section.get("source_order", 0)),
            structure["source_location"] == str(section.get("source_location", "")),
            structure["heading_path"] == list(section.get("heading_path", [])),
            structure["source_is_heading"] == bool(section.get("source_is_heading", False)),
            _text_hash(reconstructed) == str(section.get("clean_text_hash", "")),
            len(chunks) == int(section.get("expected_block_count", 0)),
            chunk_indexes == list(range(1, len(chunks) + 1)),
            actual_ids == expected_ids_for_section,
        )
        if not all(checks):
            mismatched.append(section_id)
        char_count = int(section.get("char_count", 0))
        if 0 < char_count < 120 and _text_hash(reconstructed) == str(section.get("clean_text_hash", "")):
            short_preserved += 1
        if (
            char_count == 0
            and bool(section.get("source_is_heading", False))
            and len(chunks) == 1
            and reconstructed == ""
        ):
            empty_preserved += 1

    unexpected = sorted(section_id for section_id in emitted_groups if section_id not in expected_ids)
    source_structure = [
        {
            "source_section_id": str(item.get("source_section_id", "")),
            "source_order": int(item.get("source_order", 0)),
            "source_location": str(item.get("source_location", "")),
            "heading_path": list(item.get("heading_path", [])),
            "source_is_heading": bool(item.get("source_is_heading", False)),
        }
        for item in expected
    ]
    source_content = [
        {
            "source_section_id": str(item.get("source_section_id", "")),
            "clean_text_hash": str(item.get("clean_text_hash", "")),
        }
        for item in expected
    ]
    source_heading_count = sum(bool(item.get("source_is_heading", False)) for item in expected)
    short_count = sum(0 < int(item.get("char_count", 0)) < 120 for item in expected)
    empty_heading_count = sum(
        int(item.get("char_count", 0)) == 0 and bool(item.get("source_is_heading", False))
        for item in expected
    )
    source_block_count = sum(int(item.get("expected_block_count", 0)) for item in expected)
    status = "pass"
    if missing or unexpected or mismatched or len(emitted_blocks) != source_block_count:
        status = "blocked"
    return {
        "status": status,
        "source_section_count": len(expected),
        "emitted_section_count": len(emitted_structure),
        "source_heading_count": source_heading_count,
        "emitted_heading_count": emitted_heading_count,
        "source_block_count": source_block_count,
        "emitted_block_count": len(emitted_blocks),
        "missing_section_count": len(missing),
        "unexpected_section_count": len(unexpected),
        "mismatched_section_count": len(mismatched),
        "short_nonempty_section_count": short_count,
        "short_nonempty_sections_preserved": short_preserved,
        "empty_heading_section_count": empty_heading_count,
        "empty_heading_sections_preserved": empty_preserved,
        "source_structure_hash": _canonical_hash(source_structure),
        "emitted_structure_hash": _canonical_hash(emitted_structure),
        "source_content_hash": _canonical_hash(source_content),
        "emitted_content_hash": _canonical_hash(emitted_content),
        "missing_section_ids": missing,
        "unexpected_section_ids": unexpected,
        "mismatched_section_ids": mismatched,
    }


def coverage_issues(value: dict[str, Any]) -> list[str]:
    """Return blocking coverage issues for a package payload or persisted summary."""
    if "corpus" in value:
        corpus = value.get("corpus") or {}
        source_sections = corpus.get("source_sections")
        blocks = corpus.get("blocks")
        if not isinstance(source_sections, list) or not isinstance(blocks, list):
            return ["知识包缺少 corpus.source_sections 或 corpus.blocks，不能证明标准方案完整导入"]
        actual = audit_standard_solution_coverage(source_sections, blocks)
        recorded = (value.get("review_summary") or {}).get(COVERAGE_KEY)
        issues = []
        if not isinstance(recorded, dict):
            issues.append(f"知识包缺少 review_summary.{COVERAGE_KEY}")
        else:
            for field in _SUMMARY_FIELDS:
                if recorded.get(field) != actual.get(field):
                    issues.append(f"完整性证明字段不一致: {field}")
        if actual["status"] != "pass":
            issues.append(
                "标准方案抽取不完整: "
                f"缺失{actual['missing_section_count']}、异常{actual['unexpected_section_count']}、"
                f"不一致{actual['mismatched_section_count']}"
            )
        return issues

    review_summary = value.get("review_summary") if "review_summary" in value else value
    coverage = (review_summary or {}).get(COVERAGE_KEY) if isinstance(review_summary, dict) else None
    if not isinstance(coverage, dict):
        return [f"知识快照缺少 {COVERAGE_KEY} 完整性证明"]
    issues = []
    if coverage.get("status") != "pass":
        issues.append("标准方案完整性状态不是 pass")
    equality_pairs = (
        ("source_section_count", "emitted_section_count"),
        ("source_heading_count", "emitted_heading_count"),
        ("source_block_count", "emitted_block_count"),
        ("short_nonempty_section_count", "short_nonempty_sections_preserved"),
        ("empty_heading_section_count", "empty_heading_sections_preserved"),
        ("source_structure_hash", "emitted_structure_hash"),
        ("source_content_hash", "emitted_content_hash"),
    )
    for source_field, emitted_field in equality_pairs:
        if coverage.get(source_field) != coverage.get(emitted_field):
            issues.append(f"标准方案完整性字段不相等: {source_field}/{emitted_field}")
    for field in ("missing_section_count", "unexpected_section_count", "mismatched_section_count"):
        if coverage.get(field) != 0:
            issues.append(f"标准方案完整性字段必须为0: {field}")
    return issues


def require_complete_standard_solution_coverage(
    value: dict[str, Any], *, context: str = "标准知识包"
) -> None:
    issues = coverage_issues(value)
    if issues:
        details = "；".join(issues[:8])
        raise ValueError(
            f"{context}未通过标准方案完整导入门禁：{details}。"
            "请使用当前版本重新生成并同步标准知识包，禁止继续清单匹配或成稿。"
        )
