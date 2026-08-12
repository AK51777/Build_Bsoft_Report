#!/usr/bin/env python3
"""Classify inventoried files into safe, reviewable source roles."""

from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path
from typing import Any


VALID_ROLES = {
    "project_material",
    "external_reference",
    "vendor_reference",
    "policy_reference",
    "format_reference",
}
ROLE_KEYWORDS = (
    ("format_reference", ("word模板", "格式模板", "排版模板", "样式模板")),
    ("policy_reference", ("政策", "标准", "规范", "办法", "指南")),
    ("vendor_reference", ("厂商", "厂家", "供应商", "产品方案", "技术方案")),
    ("external_reference", ("参考", "历史", "外地", "样例", "范本", "模板")),
)


def normalize_path(value: str) -> str:
    return value.replace("\\", "/").strip("./")


def matching_override(relative_path: str, overrides: dict[str, str]) -> tuple[str, str] | None:
    normalized = normalize_path(relative_path)
    for pattern, role in sorted(overrides.items()):
        if role not in VALID_ROLES:
            raise ValueError(f"invalid source role for {pattern}: {role}")
        if fnmatch.fnmatchcase(normalized.casefold(), normalize_path(pattern).casefold()):
            return role, pattern
    return None


def classify_inventory(
    inventory: dict[str, Any],
    *,
    overrides: dict[str, str] | None = None,
    default_role: str = "project_material",
) -> dict[str, Any]:
    if default_role not in VALID_ROLES:
        raise ValueError(f"invalid default role: {default_role}")
    overrides = overrides or {}
    records = []
    for source in inventory.get("files", []):
        relative_path = normalize_path(str(source.get("relative_path", "")))
        override = matching_override(relative_path, overrides)
        if override:
            role, matched_rule = override
            basis = "explicit_override"
            needs_review = False
        else:
            lowered = relative_path.casefold()
            match = next(
                (
                    (candidate_role, keyword)
                    for candidate_role, keywords in ROLE_KEYWORDS
                    for keyword in keywords
                    if keyword.casefold() in lowered
                ),
                None,
            )
            if match:
                role, matched_rule = match
                basis = "protective_keyword_heuristic"
                needs_review = True
            else:
                role = default_role
                matched_rule = "default_role"
                basis = "configured_default"
                needs_review = role != "project_material"
        records.append(
            {
                "source_id": source.get("source_id", ""),
                "relative_path": relative_path,
                "sha256": source.get("sha256", ""),
                "role": role,
                "classification_basis": basis,
                "matched_rule": matched_rule,
                "needs_review": needs_review,
                "fact_candidate_eligible": role == "project_material",
            }
        )
    counts = {role: 0 for role in sorted(VALID_ROLES)}
    for record in records:
        counts[record["role"]] += 1
    return {
        "schema_version": "1.0",
        "default_role": default_role,
        "counts": counts,
        "review_required_count": sum(record["needs_review"] for record in records),
        "sources": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--default-role", choices=sorted(VALID_ROLES), default="project_material")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8-sig"))
    overrides = (
        json.loads(args.overrides.read_text(encoding="utf-8-sig"))
        if args.overrides
        else {}
    )
    result = classify_inventory(
        inventory, overrides=overrides, default_role=args.default_role
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
