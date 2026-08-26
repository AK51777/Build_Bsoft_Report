#!/usr/bin/env python3
"""Create a human-review decision pack for reference-feasibility corpus blocks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


BLOCKING_TEXT_PATTERNS = {
    "undeclared_variable": r"\{\{[a-z][a-z0-9_]*\}\}",
    "fixed_technology_anchor": r"RPC协议|ESB技术|ISO9001|CMMI|永久性备份|大型关系数据库或非关系数据库",
    "overstated_security_claim": r"抵御大规模|抵抗较为严重的自然灾害",
}
WARNING_TEXT_PATTERNS = {
    "marketing_language": r"打造|全方位|全面提升|最终确保|坚实基础|良性循环",
}


def review_findings(block: dict[str, Any]) -> list[dict[str, str]]:
    text = str(block.get("clean_text") or "")
    declared_slots = set(block.get("variable_slots") or [])
    placeholders = set(re.findall(r"\{\{([a-z][a-z0-9_]*)\}\}", text))
    findings: list[dict[str, str]] = []
    undeclared = sorted(placeholders - declared_slots)
    if undeclared:
        findings.append(
            {
                "severity": "blocking",
                "code": "undeclared_variable",
                "details": ",".join(undeclared),
            }
        )
    for code, pattern in BLOCKING_TEXT_PATTERNS.items():
        if code == "undeclared_variable":
            continue
        matched = sorted(set(re.findall(pattern, text)))
        if matched:
            findings.append(
                {"severity": "blocking", "code": code, "details": ",".join(matched)}
            )
    for code, pattern in WARNING_TEXT_PATTERNS.items():
        matched = sorted(set(re.findall(pattern, text)))
        if matched:
            findings.append(
                {"severity": "warning", "code": code, "details": ",".join(matched)}
            )
    return findings


def recommended_decision(block: dict[str, Any], findings: list[dict[str, str]]) -> str:
    structurally_publishable = (
        block.get("content_type")
        in {"feasibility_narrative", "common_narrative", "structure_only"}
        and block.get("reuse_class") in {"A", "B", "C"}
        and not block.get("review_flags")
    )
    return (
        "approved"
        if structurally_publishable
        and not any(item["severity"] == "blocking" for item in findings)
        else "prohibited"
    )


def build_review_pack(
    workpack: dict[str, Any],
    *,
    workpack_sha256: str,
    semantic_sections: list[str] | None = None,
) -> dict[str, Any]:
    if workpack.get("schema_version") != "1.0" or not isinstance(
        workpack.get("blocks"), list
    ):
        raise ValueError("unsupported or incomplete reference corpus workpack")
    selected_sections = set(semantic_sections or [])
    blocks = [
        block
        for block in workpack["blocks"]
        if not selected_sections
        or str(block.get("semantic_section") or "") in selected_sections
    ]
    if not blocks:
        raise ValueError("review scope contains no candidate blocks")
    result = {
        "schema_version": "1.0",
        "workpack_sha256": workpack_sha256,
        "source_document_id": workpack.get("source", {}).get("document_id", ""),
        "source_corpus_type": workpack.get("source", {}).get(
            "source_corpus_type", "reference_feasibility"
        ),
        "project_type": workpack.get("source", {}).get("project_type", ""),
        "review_scope": {
            "semantic_sections": sorted(selected_sections),
            "complete_document": not bool(selected_sections),
            "candidate_block_count": len(blocks),
        },
        "confirmation": {
            "status": "pending",
            "confirmed_by": "",
            "confirmed_at": "",
            "decision_note": "",
        },
        "decisions": [],
    }
    for block in blocks:
        findings = review_findings(block)
        result["decisions"].append(
            {
                "block_id": block["block_id"],
                "decision_status": "pending",
                "recommended_decision": recommended_decision(block, findings),
                "recommended_action": (
                    "revise_then_review"
                    if any(item["severity"] == "blocking" for item in findings)
                    else "language_cleanup_before_confirmation"
                    if findings
                    else "confirm_metadata_and_text"
                ),
                "automated_findings": findings,
                "semantic_section": block.get("semantic_section", ""),
                "content_slot": block.get("content_slot", ""),
                "content_type": block.get("content_type", ""),
                "reuse_class": block.get("reuse_class", "D"),
                "adaptation_mode": block.get("adaptation_mode", "prohibited"),
                "applicable_project_types": block.get(
                    "applicable_project_types", []
                ),
                "variable_slots": block.get("variable_slots", []),
                "forbidden_terms": block.get("forbidden_terms", []),
                "review_note": "",
                "replacement_text": "",
                "heading_path": block.get("heading_path", []),
                "source_location": block.get("source_location", ""),
                "text_preview": str(block.get("clean_text") or "")[:260],
            }
        )
    return result


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 参考可研语料人工确认表",
        "",
        "> 本表必须由人工逐块确认。`pending` 不得导入、发布或进入正文。",
        "",
        "## 确认信息",
        "",
        "- 状态：`pending`",
        "- 确认人：待填写",
        "- 确认时间：待填写",
        "- 项目类型：`" + str(payload.get("project_type") or "") + "`",
        "- 语义范围："
        + "、".join(payload["review_scope"]["semantic_sections"] or ["全部候选"]),
        "",
        "## 逐块决定",
        "",
        "在 JSON 中把 `decision_status` 改为 `approved`、`prohibited` 或 `retired`；需要时同步修正语义章节、槽位、复用级别和变量。",
        "",
    ]
    for index, item in enumerate(payload["decisions"], start=1):
        lines.extend(
            [
                f"### {index}. `{item['block_id']}`",
                "",
                f"- 来源：{' / '.join(item['heading_path'])}；{item['source_location']}",
                f"- 建议：`{item['semantic_section']}` / `{item['content_slot']}` / "
                f"`{item['content_type']}` / `{item['reuse_class']}` / `{item['adaptation_mode']}` / "
                f"推荐决定 `{item['recommended_decision']}`（不等于人工确认）",
                f"- 自动检查：`{item['recommended_action']}`；"
                + (
                    "；".join(
                        f"{finding['severity']}:{finding['code']}={finding['details']}"
                        for finding in item["automated_findings"]
                    )
                    or "未发现规则化问题"
                ),
                f"- 预览：{item['text_preview']}",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workpack", type=Path)
    parser.add_argument("--semantic-section", action="append", default=[])
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    source_bytes = args.workpack.read_bytes()
    result = build_review_pack(
        load_json(args.workpack),
        workpack_sha256=sha256_bytes(source_bytes),
        semantic_sections=args.semantic_section,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.output_md.write_text(render_markdown(result), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "pending_human_confirmation",
                "candidate_block_count": len(result["decisions"]),
                "output_json": str(args.output_json.resolve()),
                "output_md": str(args.output_md.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
