#!/usr/bin/env python3
"""Apply signed human decisions and build a publishable reference-corpus package."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ACTIVE_HOSPITAL_TYPES = {"smart_hospital", "hospital_informationization"}
FINAL_STATUSES = {"approved", "prohibited", "retired"}
PUBLISHABLE_CONTENT_TYPES = {
    "feasibility_narrative",
    "common_narrative",
    "structure_only",
}
NON_DELIVERABLE_MARKERS = (
    "经核验的相关条款主要包括",
    "正式报审前还需复核",
    "不得把指导、鼓励或评价性内容改写为项目已经完成的事实",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}-{sha256_text('|'.join(str(part) for part in parts))[:16]}"


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _normalized_block(block: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    status = str(decision.get("decision_status") or "")
    replacement_text = str(decision.get("replacement_text") or "").strip()
    reviewed_text = replacement_text or str(block.get("clean_text") or "")
    result = {
        **block,
        "clean_text": reviewed_text,
        "clean_text_sha256": sha256_text(reviewed_text),
        "semantic_section": str(decision.get("semantic_section") or ""),
        "content_slot": str(decision.get("content_slot") or ""),
        "content_type": str(decision.get("content_type") or ""),
        "reuse_class": str(decision.get("reuse_class") or "D"),
        "adaptation_mode": str(decision.get("adaptation_mode") or "prohibited"),
        "applicable_project_types": list(
            decision.get("applicable_project_types") or []
        ),
        "variable_slots": list(decision.get("variable_slots") or []),
        "forbidden_terms": list(decision.get("forbidden_terms") or []),
        "review_status": status,
        "publish_eligible": status == "approved",
        "review_note": str(decision.get("review_note") or ""),
        "human_text_revision": bool(replacement_text),
    }
    if status != "approved":
        result["reuse_class"] = "D"
        result["adaptation_mode"] = "prohibited"
        result["publish_eligible"] = False
    return result


def _validate_approved_block(block: dict[str, Any]) -> None:
    block_id = block.get("block_id", "<unknown>")
    if block.get("content_type") not in PUBLISHABLE_CONTENT_TYPES:
        raise ValueError(f"approved block {block_id} has a non-publishable content_type")
    reuse_class = block.get("reuse_class")
    expected_mode = {"A": "direct", "B": "parameterized", "C": "structure_only"}.get(
        reuse_class
    )
    if not expected_mode or block.get("adaptation_mode") != expected_mode:
        raise ValueError(f"approved block {block_id} has inconsistent reuse/adaptation")
    if not block.get("semantic_section") or not block.get("content_slot"):
        raise ValueError(f"approved block {block_id} lacks semantic routing")
    if not set(block.get("applicable_project_types") or []).issubset(
        ACTIVE_HOSPITAL_TYPES
    ):
        raise ValueError(
            f"approved block {block_id} uses an unimplemented project type"
        )
    text = str(block.get("clean_text") or "")
    if any(marker in text for marker in NON_DELIVERABLE_MARKERS):
        raise ValueError(f"approved block {block_id} contains a work-layer marker")
    residual_terms = [term for term in block.get("forbidden_terms", []) if term in text]
    if residual_terms:
        raise ValueError(f"approved block {block_id} contains forbidden source terms")
    placeholders = set(re.findall(r"\{\{([a-z][a-z0-9_]*)\}\}", text))
    declared = set(block.get("variable_slots") or [])
    if not placeholders.issubset(declared):
        raise ValueError(f"approved block {block_id} contains undeclared variables")
    if reuse_class == "A" and placeholders:
        raise ValueError(f"approved A-class block {block_id} contains variables")


def build_knowledge_pack(
    workpack: dict[str, Any],
    decisions: dict[str, Any],
    *,
    workpack_sha256: str,
    version_label: str,
) -> dict[str, Any]:
    if decisions.get("workpack_sha256") != workpack_sha256:
        raise ValueError("review decisions do not match the current workpack hash")
    if decisions.get("source_document_id") != workpack.get("source", {}).get(
        "document_id"
    ):
        raise ValueError("review decisions target a different source document")
    confirmation = decisions.get("confirmation") or {}
    if confirmation.get("status") != "confirmed":
        raise ValueError("review confirmation status must be confirmed")
    if not str(confirmation.get("confirmed_by") or "").strip() or not str(
        confirmation.get("confirmed_at") or ""
    ).strip():
        raise ValueError("confirmed_by and confirmed_at are required")
    project_type = str(workpack.get("source", {}).get("project_type") or "")
    if project_type not in ACTIVE_HOSPITAL_TYPES:
        raise ValueError(
            "only the hospital reference-corpus chain is implemented; other project types remain reserved"
        )
    candidates = {block["block_id"]: block for block in workpack.get("blocks", [])}
    decision_rows = decisions.get("decisions")
    if not isinstance(decision_rows, list) or not decision_rows:
        raise ValueError("review decisions must contain at least one block")
    seen: set[str] = set()
    reviewed_blocks: list[dict[str, Any]] = []
    for decision in decision_rows:
        block_id = str(decision.get("block_id") or "")
        if not block_id or block_id in seen or block_id not in candidates:
            raise ValueError(f"invalid or duplicate review block_id: {block_id}")
        seen.add(block_id)
        status = str(decision.get("decision_status") or "")
        if status not in FINAL_STATUSES:
            raise ValueError(f"block {block_id} is not finally reviewed")
        block = _normalized_block(candidates[block_id], decision)
        if status == "approved":
            _validate_approved_block(block)
        reviewed_blocks.append(block)
    approved = [block for block in reviewed_blocks if block["review_status"] == "approved"]
    if not approved:
        raise ValueError("reference package must contain at least one approved block")
    published_blocks = []
    for block in approved:
        forbidden_terms = [
            str(term) for term in block.get("forbidden_terms", []) if str(term)
        ]
        published_blocks.append(
            {
                **block,
                "forbidden_terms": [],
                "forbidden_term_hashes": [sha256_text(term) for term in forbidden_terms],
            }
        )
    source = workpack["source"]
    review_digest = sha256_text(canonical_json(decisions))
    package_id = stable_id(
        "REFPACK", source.get("source_sha256", ""), version_label, review_digest
    )
    document_id = stable_id(
        "REFDOC", source.get("document_id", ""), version_label, review_digest
    )
    source_suffix = Path(str(source.get("source_path") or "reference.docx")).suffix or ".bin"
    published_source_name = f"reference-feasibility-{source.get('document_id', 'source')}{source_suffix}"
    return {
        "schema_version": "1.0",
        "package_id": package_id,
        "package_kind": "reference_corpus",
        "title": f"医院信息化参考可研语料包-{version_label}",
        "permission_scope": "internal_company_reuse",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source_files": [
            {
                "role": "reference_feasibility",
                "file_name": published_source_name,
                "sha256": source.get("source_sha256", ""),
            }
        ],
        "corpus": {
            "document_id": document_id,
            "document_type": "feasibility_study",
            "project_type": project_type,
            "source_corpus_type": "reference_feasibility",
            "quality_level": "B",
            "review_status": "approved",
            "version": version_label,
            # The signed review file is the audit record for prohibited/retired
            # candidates.  A runtime knowledge package carries publishable text
            # only, so rejected customer-specific material never reaches either
            # the shared database or a project snapshot.
            "blocks": published_blocks,
        },
        "capabilities": [],
        "review_summary": {
            "status": "confirmed",
            "confirmed_by": confirmation["confirmed_by"],
            "confirmed_at": confirmation["confirmed_at"],
            "decision_note": confirmation.get("decision_note", ""),
            "source_workpack_sha256": workpack_sha256,
            "decision_sha256": review_digest,
            "reviewed_block_count": len(reviewed_blocks),
            "approved_block_count": len(approved),
            "prohibited_or_retired_count": len(reviewed_blocks) - len(approved),
            "review_scope": decisions.get("review_scope", {}),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workpack", type=Path)
    parser.add_argument("review_decisions", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_knowledge_pack(
        load_json(args.workpack),
        load_json(args.review_decisions),
        workpack_sha256=sha256_file(args.workpack),
        version_label=args.version,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "package_id": result["package_id"],
                "package_kind": result["package_kind"],
                "approved_blocks": result["review_summary"]["approved_block_count"],
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
