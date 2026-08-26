#!/usr/bin/env python3
"""Load layered generation contracts for one feasibility-report chapter."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHAPTER_RULES = (
    SKILL_ROOT
    / "assets"
    / "knowledge-base"
    / "seeds"
    / "chapter_generation_rules_v1.json"
)


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


@lru_cache(maxsize=4)
def load_chapter_rules(path: str | None = None) -> dict[str, Any]:
    source = Path(path).resolve() if path else DEFAULT_CHAPTER_RULES
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0":
        raise ValueError(f"unsupported chapter-rule schema: {payload.get('schema_version')}")
    for key in ("global", "roles", "chapters", "regression_groups"):
        if not isinstance(payload.get(key), dict):
            raise ValueError(f"chapter-rule field must be an object: {key}")
    return payload


def resolve_chapter_rule(
    chapter_code: str,
    section_role: str | None = None,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or load_chapter_rules()
    global_rule = payload["global"]
    chapter_rule = payload["chapters"].get(chapter_code, {})
    resolved_role = section_role or chapter_rule.get("section_role") or ""
    role_rule = payload["roles"].get(resolved_role, {})
    validation = {
        **global_rule.get("validation", {}),
        **role_rule.get("validation", {}),
        **chapter_rule.get("validation", {}),
    }
    return {
        "schema_version": payload["schema_version"],
        "chapter_code": chapter_code,
        "section_role": resolved_role,
        "rule_layers": _unique(
            [
                "global",
                *([f"role:{resolved_role}"] if role_rule else []),
                *([f"chapter:{chapter_code}"] if chapter_rule else []),
            ]
        ),
        "argument_outline": chapter_rule.get(
            "argument_outline", role_rule.get("argument_outline", [])
        ),
        "assembly_mode": chapter_rule.get(
            "assembly_mode", role_rule.get("assembly_mode", "evidence_bound_argument")
        ),
        "required_source_types": _unique(
            [
                *role_rule.get("required_source_types", []),
                *chapter_rule.get("required_source_types", []),
            ]
        ),
        "semantic_sections": _unique(
            [
                *role_rule.get("semantic_sections", []),
                *chapter_rule.get("semantic_sections", []),
            ]
        ),
        "canonical_semantic_sections": _unique(
            chapter_rule.get("canonical_semantic_sections", [])
        ),
        "content_slots": _unique(chapter_rule.get("content_slots", [])),
        "content_slot_prefixes": _unique(
            chapter_rule.get("content_slot_prefixes", [])
        ),
        "canonical_chapter_code": chapter_rule.get("canonical_chapter_code", ""),
        "max_corpus_blocks": int(chapter_rule.get("max_corpus_blocks", 16)),
        "forbidden_output_phrases": _unique(
            [
                *global_rule.get("forbidden_output_phrases", []),
                *role_rule.get("forbidden_output_phrases", []),
                *chapter_rule.get("forbidden_output_phrases", []),
            ]
        ),
        "completion_checks": _unique(
            [
                *global_rule.get("completion_checks", []),
                *role_rule.get("completion_checks", []),
                *chapter_rule.get("completion_checks", []),
            ]
        ),
        "validation": validation,
        "regression_group": chapter_rule.get("regression_group", resolved_role),
    }


def argument_outline_maps(
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    payload = payload or load_chapter_rules()
    role_map = {
        key: list(value.get("argument_outline", []))
        for key, value in payload["roles"].items()
    }
    chapter_map = {
        key: list(value["argument_outline"])
        for key, value in payload["chapters"].items()
        if value.get("argument_outline")
    }
    return role_map, chapter_map


ROLE_ARGUMENT_OUTLINES, CHAPTER_ARGUMENT_OUTLINES = argument_outline_maps()


def regression_targets(
    chapter_code: str,
    tier: str,
    *,
    payload: dict[str, Any] | None = None,
) -> list[str]:
    payload = payload or load_chapter_rules()
    if tier == "full":
        return []
    contract = resolve_chapter_rule(chapter_code, payload=payload)
    group_name = contract.get("regression_group") or ""
    group = payload["regression_groups"].get(group_name)
    if not group:
        raise ValueError(f"regression group is not configured for chapter {chapter_code}")
    if tier == "chapter":
        targets = group.get("chapter_tests", {}).get(chapter_code, [])
    elif tier == "family":
        targets = group.get("family_tests", [])
    else:
        raise ValueError("tier must be chapter, family, or full")
    if not targets:
        raise ValueError(f"no {tier} regression targets configured for chapter {chapter_code}")
    return list(targets)
