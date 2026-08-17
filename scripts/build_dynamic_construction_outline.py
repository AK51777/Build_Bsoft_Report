#!/usr/bin/env python3
"""Build traceable level-4 to level-7 construction outline nodes."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from knowledge_db import dump_json, stable_id


def normalized_title(value: str) -> str:
    value = re.sub(r"^\s*(?:第?[一二三四五六七八九十百]+|\d+(?:\.\d+)*)[、.．)）\-—:]\s*", "", value)
    return re.sub(r"\s+", " ", value).strip(" ：:。；;")


def title_key(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value).casefold()


def presentation_title(value: str) -> str:
    value = re.sub(r"Bsoft\s*GPT", "大模型智能", value, flags=re.I)
    value = re.sub(r"Bsoft", "", value, flags=re.I)
    value = value.replace("创业慧康", "").replace("创业", "")
    return normalized_title(value) or "功能设计"


def subfeature_titles(text: str, excluded: set[str], limit: int = 6) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        value = normalized_title(raw)
        key = title_key(value)
        if not key or key in excluded or key in seen:
            continue
        if not 2 <= len(value) <= 32:
            continue
        if value.startswith(("表", "图", "注")) or any(mark in value for mark in "。；，!?！？"):
            continue
        if re.search(r"(?:如下|包括|实现|提供|支持|通过|系统|平台).{12,}", value):
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def ranked_blocks(
    block_ids: list[str],
    corpus_by_id: dict[str, Any],
    query: str,
    *,
    limit: int = 3,
) -> list[Any]:
    query_chars = set(title_key(query))
    candidates = []
    for block_id in block_ids:
        block = corpus_by_id.get(block_id)
        if block is None or block["section_role"] != "construction_content":
            continue
        heading_path = json.loads(block["heading_path_json"] or "[]")
        heading_text = " / ".join(heading_path)
        overlap = len(query_chars & set(title_key(heading_text))) / max(1, len(query_chars))
        candidates.append((-overlap, len(block["clean_text"]), block["block_id"], block))
    candidates.sort(key=lambda item: item[:3])
    selected = []
    seen_titles: set[str] = set()
    for _, _, _, block in candidates:
        heading_path = json.loads(block["heading_path_json"] or "[]")
        leaf = presentation_title(heading_path[-1] if heading_path else block["source_location"])
        key = title_key(leaf)
        if not key or key in seen_titles:
            continue
        seen_titles.add(key)
        selected.append(block)
        if len(selected) >= limit:
            break
    return selected


def ordered_reviewed_blocks(
    block_ids: list[str], corpus_by_id: dict[str, Any]
) -> list[Any]:
    """Return every approved construction block in the capability's stored order."""
    selected: list[Any] = []
    seen: set[str] = set()
    for block_id in block_ids:
        if block_id in seen:
            continue
        seen.add(block_id)
        block = corpus_by_id.get(block_id)
        if block is None:
            continue
        if block["section_role"] != "construction_content":
            continue
        if block["review_status"] != "approved" or block["reuse_class"] == "D":
            continue
        selected.append(block)
    return selected


def semantic_block_path(block: Any, excluded: set[str]) -> tuple[str, ...]:
    """Keep the last two meaningful source headings for report levels 6 and 7."""
    heading_path = json.loads(block["heading_path_json"] or "[]")
    cleaned: list[str] = []
    seen: set[str] = set()
    generic = {
        title_key(value)
        for value in ("建设内容", "系统建设", "软件建设", "功能建设", "总体方案")
    }
    for raw in heading_path:
        title = presentation_title(raw)
        key = title_key(title)
        if not key or key in excluded or key in generic or key in seen:
            continue
        cleaned.append(title)
        seen.add(key)
    if not cleaned:
        return ("功能概述",)
    return tuple(cleaned[-2:])


def grouped_full_blocks(
    blocks: list[Any], excluded: set[str]
) -> list[tuple[tuple[str, ...], list[Any]]]:
    """Group adjacent chunks from one source heading without losing source order."""
    groups: list[tuple[tuple[str, ...], list[Any]]] = []
    for block in blocks:
        path = semantic_block_path(block, excluded)
        if groups and groups[-1][0] == path:
            groups[-1][1].append(block)
        else:
            groups.append((path, [block]))
    return groups


def build_outline_nodes(
    conn,
    *,
    plan_id: str,
    chapter_code: str,
    scopes: list[Any],
    capability_maps: list[Any],
    corpus_blocks: list[Any],
    timestamp: str,
) -> list[dict[str, Any]]:
    conn.execute("DELETE FROM section_outline_node WHERE plan_id=?", (plan_id,))
    corpus_by_id = {block["block_id"]: block for block in corpus_blocks}
    maps_by_scope: dict[str, list[Any]] = defaultdict(list)
    for mapping in capability_maps:
        if mapping["status"] in {"confirmed", "candidate"}:
            maps_by_scope[mapping["scope_id"]].append(mapping)

    output: list[dict[str, Any]] = []
    ordinal = 0

    def add_node(
        *,
        parent_node_id: str | None,
        code: str,
        level: int,
        title: str,
        kind: str,
        source_type: str,
        source_object_id: str,
        usage_mode: str,
        length_min: int,
        length_max: int,
        status: str,
        metadata: dict[str, Any],
    ) -> str:
        nonlocal ordinal
        ordinal += 1
        node_id = stable_id("OUTLINENODE", plan_id, code, source_type, source_object_id)
        conn.execute(
            """
            INSERT INTO section_outline_node (
              outline_node_id,plan_id,parent_node_id,chapter_code,heading_level,title,
              node_kind,source_type,source_object_id,usage_mode,ordinal,length_min,
              length_max,status,metadata_json,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                node_id, plan_id, parent_node_id, code, level, title, kind,
                source_type, source_object_id, usage_mode, ordinal, length_min,
                length_max, status, dump_json(metadata), timestamp, timestamp,
            ),
        )
        output.append(
            {
                "outline_node_id": node_id,
                "parent_node_id": parent_node_id,
                "chapter_code": code,
                "heading_level": level,
                "title": title,
                "node_kind": kind,
                "source_type": source_type,
                "source_object_id": source_object_id,
                "usage_mode": usage_mode,
                "length_min": length_min,
                "length_max": length_max,
                "status": status,
                "metadata": metadata,
            }
        )
        return node_id

    ordered_scopes = sorted(scopes, key=lambda row: (row["standard_name"], row["scope_id"]))
    for scope_index, scope in enumerate(ordered_scopes, start=1):
        scope_direct = scope["status"] == "confirmed"
        scope_code = f"{chapter_code}.{scope_index}"
        scope_node = add_node(
            parent_node_id=None,
            code=scope_code,
            level=4,
            title=presentation_title(scope["standard_name"]),
            kind="scope",
            source_type="scope",
            source_object_id=scope["scope_id"],
            usage_mode="direct" if scope_direct else "parameterized",
            length_min=650,
            length_max=1600,
            status="ready" if scope_direct else "working_only",
            metadata={
                "scope_status": scope["status"],
                "construction_mode": scope["construction_mode"],
                "acceptance_target": scope["acceptance_target"],
                "boundary": "The customer scope item is the outer boundary; child capabilities cannot enlarge it.",
            },
        )
        mappings = sorted(
            maps_by_scope.get(scope["scope_id"], []),
            key=lambda row: (
                0 if row["status"] == "confirmed" else 1,
                -float(row["confidence"] or 0),
                row["capability_name"],
                row["capability_id"],
            ),
        )
        for capability_index, mapping in enumerate(mappings, start=1):
            mapping_direct = scope_direct and mapping["status"] == "confirmed"
            capability_code = f"{scope_code}.{capability_index}"
            capability_title = presentation_title(
                mapping["capability_name"] or mapping["product_name"]
            )
            block_ids = json.loads(mapping["standard_block_ids_json"] or "[]")
            reviewed_blocks = ordered_reviewed_blocks(block_ids, corpus_by_id)
            block_match_scope = mapping["block_match_scope"] or "unspecified"
            full_selection = (
                mapping_direct and block_match_scope != "product_heading_fallback"
            )
            blocks = (
                reviewed_blocks
                if full_selection
                else ranked_blocks(
                    block_ids,
                    corpus_by_id,
                    f"{scope['standard_name']} {mapping['product_name']} {capability_title}",
                )
            )
            capability_node = add_node(
                parent_node_id=scope_node,
                code=capability_code,
                level=5,
                title=capability_title,
                kind="capability",
                source_type="capability",
                source_object_id=mapping["capability_id"],
                usage_mode="parameterized" if full_selection else "structure_only",
                length_min=300,
                length_max=900,
                status="ready" if full_selection else "working_only",
                metadata={
                    "map_id": mapping["map_id"],
                    "map_status": mapping["status"],
                    "confidence": mapping["confidence"],
                    "product_name": mapping["product_name"],
                    "module_name": mapping["module_name"],
                    "selection_rules": json.loads(mapping["selection_rules_json"] or "[]"),
                    "block_match_scope": block_match_scope,
                    "block_selection_mode": (
                        "full_confirmed_capability" if full_selection else "bounded_working_preview"
                    ),
                    "available_reviewed_block_count": len(reviewed_blocks),
                    "selected_block_count": len(blocks),
                    "selected_all_reviewed_blocks": full_selection and len(blocks) == len(reviewed_blocks),
                },
            )
            excluded = {
                title_key(scope["standard_name"]),
                title_key(mapping["product_name"]),
                title_key(capability_title),
            }
            if full_selection:
                feature_index = 0
                for semantic_path, grouped_blocks in grouped_full_blocks(blocks, excluded):
                    block_id_list = [block["block_id"] for block in grouped_blocks]
                    common_metadata = {
                        "heading_paths": [
                            json.loads(block["heading_path_json"] or "[]")
                            for block in grouped_blocks
                        ],
                        "source_locations": [block["source_location"] for block in grouped_blocks],
                        "reuse_classes": [block["reuse_class"] for block in grouped_blocks],
                        "block_ids": block_id_list,
                        "block_selection_mode": "full_confirmed_capability",
                    }
                    feature_index += 1
                    feature_code = f"{capability_code}.{feature_index}"
                    if len(semantic_path) == 1:
                        add_node(
                            parent_node_id=capability_node,
                            code=feature_code,
                            level=6,
                            title=semantic_path[0],
                            kind="feature",
                            source_type="corpus",
                            source_object_id=block_id_list[0],
                            usage_mode="parameterized",
                            length_min=180,
                            length_max=max(750, sum(len(block["clean_text"]) for block in grouped_blocks)),
                            status="ready",
                            metadata=common_metadata,
                        )
                        continue
                    group_node = add_node(
                        parent_node_id=capability_node,
                        code=feature_code,
                        level=6,
                        title=semantic_path[0],
                        kind="feature",
                        source_type="capability",
                        source_object_id=mapping["capability_id"],
                        usage_mode="parameterized",
                        length_min=0,
                        length_max=0,
                        status="ready",
                        metadata={
                            "content_mode": "heading_only",
                            "block_selection_mode": "full_confirmed_capability",
                        },
                    )
                    add_node(
                        parent_node_id=group_node,
                        code=f"{feature_code}.1",
                        level=7,
                        title=semantic_path[1],
                        kind="subfeature",
                        source_type="corpus",
                        source_object_id=block_id_list[0],
                        usage_mode="parameterized",
                        length_min=180,
                        length_max=max(750, sum(len(block["clean_text"]) for block in grouped_blocks)),
                        status="ready",
                        metadata=common_metadata,
                    )
                continue
            subfeature_budget = 2
            for feature_index, block in enumerate(blocks, start=1):
                heading_path = json.loads(block["heading_path_json"] or "[]")
                feature_title = presentation_title(
                    heading_path[-1] if heading_path else block["source_location"]
                )
                if title_key(feature_title) in excluded:
                    feature_title = "功能概述" if feature_index == 1 else f"功能设计{feature_index}"
                feature_code = f"{capability_code}.{feature_index}"
                feature_node = add_node(
                    parent_node_id=capability_node,
                    code=feature_code,
                    level=6,
                    title=feature_title,
                    kind="feature",
                    source_type="corpus",
                    source_object_id=block["block_id"],
                    usage_mode="structure_only",
                    length_min=180,
                    length_max=750,
                    status="working_only",
                    metadata={
                        "heading_path": heading_path,
                        "source_location": block["source_location"],
                        "reuse_class": block["reuse_class"],
                        "block_ids": [block["block_id"]],
                        "block_selection_mode": "bounded_working_preview",
                    },
                )
                subfeatures = (
                    subfeature_titles(
                        block["clean_text"],
                        excluded | {title_key(feature_title)},
                        limit=subfeature_budget,
                    )
                    if subfeature_budget > 0
                    else []
                )
                for subfeature_index, subfeature in enumerate(subfeatures, start=1):
                    add_node(
                        parent_node_id=feature_node,
                        code=f"{feature_code}.{subfeature_index}",
                        level=7,
                        title=subfeature,
                        kind="subfeature",
                        source_type="corpus",
                        source_object_id=block["block_id"],
                        usage_mode="structure_only",
                        length_min=100,
                        length_max=420,
                        status="ready" if mapping_direct else "working_only",
                        metadata={"derived_from_block": block["block_id"]},
                    )
                    subfeature_budget -= 1
    return output
