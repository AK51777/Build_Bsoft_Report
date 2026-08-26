#!/usr/bin/env python3
"""Freeze, match, review and assemble module-level construction solutions.

Project scope and review decisions stay in the project-local SQLite database.
Only already-synchronized, approved standard knowledge is read by this module.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from ingest_scope_items_sqlite import normalize_payload
from knowledge_db import (
    apply_migrations,
    connect,
    dump_json,
    load_json,
    now_iso,
    sha256_text,
    stable_id,
)
from standard_solution_coverage import require_complete_standard_solution_coverage


MATCHER_VERSION = "construction-module-subtree-v2"
MAX_REVIEW_CANDIDATES = 5
WEAK_SIMILARITY_THRESHOLD = 0.65
PENDING_MARKER = "【待补充】"
PENDING_CONFIRMATION_MARKER = "【待确认】"


def _json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _score(left: Any, right: Any) -> float:
    a = _normalize_name(left)
    b = _normalize_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    if min(len(a), len(b)) >= 3 and (a in b or b in a):
        ratio = max(ratio, min(len(a), len(b)) / max(len(a), len(b)))
    return round(ratio, 6)


def _canonical_hash(value: Any) -> str:
    return sha256_text(dump_json(value))


def _write_json(path: Path | None, value: Any) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path | None, value: str) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def _project(conn, project_code: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM project WHERE project_code=?", (project_code,)
    ).fetchone()
    if row is None:
        raise ValueError(f"project not found: {project_code}")
    return dict(row)


def capture_scope_snapshot(
    database: Path, project_code: str, payload: dict[str, Any]
) -> dict[str, Any]:
    source = payload.get("source") or {}
    if not source.get("path") or not source.get("sha256"):
        raise ValueError("scope payload must contain source.path and source.sha256")
    with connect(database.resolve()) as conn:
        migrations = apply_migrations(conn)
        project = _project(conn, project_code)
        normalized_items, issues = normalize_payload(payload, project["project_id"])
        source_row_map: dict[str, dict[str, Any]] = {}
        for item in normalized_items:
            for record in item["source_records"]:
                key = f"{record['source_location']}|{dump_json(record['row'])}"
                source_row_map[key] = {
                    "scope_id": item["scope_id"],
                    "hierarchy": record.get("name_path", []),
                }
        missing_scope_ids = [
            item["scope_id"]
            for item in normalized_items
            if conn.execute(
                "SELECT 1 FROM project_scope_item WHERE scope_id=?", (item["scope_id"],)
            ).fetchone()
            is None
        ]
        if missing_scope_ids:
            raise ValueError(
                "scope payload must be ingested before capture; missing scope ids: "
                + ", ".join(missing_scope_ids[:5])
            )
        display_hash = _canonical_hash(payload)
        snapshot_id = stable_id(
            "SCOPESNAPSHOT", project["project_id"], source["sha256"], display_hash
        )
        source_row = conn.execute(
            "SELECT source_id FROM source_document WHERE project_id=? AND sha256=?",
            (project["project_id"], source["sha256"]),
        ).fetchone()
        source_id = source_row["source_id"] if source_row else None
        timestamp = now_iso()
        conn.execute(
            """
            UPDATE construction_scope_snapshot SET snapshot_status='superseded'
            WHERE project_id=? AND snapshot_status='current' AND scope_snapshot_id<>?
            """,
            (project["project_id"], snapshot_id),
        )
        conn.execute(
            """
            INSERT INTO construction_scope_snapshot (
              scope_snapshot_id,project_id,source_id,source_path,source_sha256,
              display_payload_json,display_hash,snapshot_status,created_at
            ) VALUES (?,?,?,?,?,?,?,'current',?)
            ON CONFLICT(scope_snapshot_id) DO UPDATE SET
              source_id=excluded.source_id,source_path=excluded.source_path,
              display_payload_json=excluded.display_payload_json,
              snapshot_status='current'
            """,
            (
                snapshot_id,
                project["project_id"],
                source_id,
                str(source["path"]),
                str(source["sha256"]),
                dump_json(payload),
                display_hash,
                timestamp,
            ),
        )
        ordinal = 0
        captured: list[dict[str, Any]] = []
        for sheet in payload.get("sheets", []):
            sheet_name = str(sheet.get("name", ""))
            for row in sheet.get("rows", []):
                location = f"sheet:{sheet_name};row:{row.get('_source_row', '')}"
                key = f"{location}|{dump_json(row)}"
                mapped = source_row_map.get(key)
                if not mapped:
                    continue
                ordinal += 1
                row_hash = _canonical_hash(row)
                row_id = stable_id("SCOPEROW", snapshot_id, ordinal, row_hash)
                source_row_no = row.get("_source_row")
                conn.execute(
                    """
                    INSERT INTO construction_scope_row (
                      scope_row_id,scope_snapshot_id,scope_id,source_ordinal,
                      worksheet_name,source_row,hierarchy_json,display_cells_json,display_hash
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(scope_row_id) DO UPDATE SET
                      scope_id=excluded.scope_id,hierarchy_json=excluded.hierarchy_json,
                      display_cells_json=excluded.display_cells_json,
                      display_hash=excluded.display_hash
                    """,
                    (
                        row_id,
                        snapshot_id,
                        mapped["scope_id"],
                        ordinal,
                        sheet_name,
                        int(source_row_no) if str(source_row_no or "").isdigit() else None,
                        dump_json(mapped["hierarchy"]),
                        dump_json(row),
                        row_hash,
                    ),
                )
                captured.append(
                    {
                        "scope_row_id": row_id,
                        "scope_id": mapped["scope_id"],
                        "source_ordinal": ordinal,
                        "worksheet_name": sheet_name,
                        "source_row": source_row_no,
                        "hierarchy": mapped["hierarchy"],
                        "display_cells": row,
                        "display_hash": row_hash,
                    }
                )
        conn.commit()
    return {
        "database": str(database.resolve()),
        "project_code": project_code,
        "scope_snapshot_id": snapshot_id,
        "source_sha256": source["sha256"],
        "display_hash": display_hash,
        "scope_rows": captured,
        "scope_row_count": len(captured),
        "normalization_issues": issues,
        "privacy_boundary": "project scope snapshot stored locally; no server write",
        "applied_migrations": migrations,
    }


def _latest_snapshot(conn, project_id: str, snapshot_id: str = "") -> dict[str, Any]:
    if snapshot_id:
        row = conn.execute(
            "SELECT * FROM construction_scope_snapshot WHERE scope_snapshot_id=? AND project_id=?",
            (snapshot_id, project_id),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT * FROM construction_scope_snapshot
            WHERE project_id=? AND snapshot_status='current'
            ORDER BY created_at DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    if row is None:
        raise ValueError("construction scope snapshot not found; run capture-scope first")
    return dict(row)


def _allowed_snapshot_items(
    conn, project_id: str, package_id: str
) -> tuple[set[str], set[str], str]:
    if not package_id:
        return set(), set(), ""
    snapshot = conn.execute(
        """
        SELECT snapshot_id,content_hash,metadata_json FROM shared_knowledge_snapshot
        WHERE project_id=? AND source_type='knowledge_package' AND source_id=?
          AND snapshot_status='current'
        ORDER BY fetched_at DESC LIMIT 1
        """,
        (project_id, package_id),
    ).fetchone()
    if snapshot is None:
        raise ValueError(f"current local snapshot for package not found: {package_id}")
    metadata = _json(snapshot["metadata_json"], {})
    require_complete_standard_solution_coverage(
        metadata, context=f"项目本地标准知识快照 {package_id}"
    )
    rows = conn.execute(
        """
        SELECT item_type,item_id FROM shared_knowledge_snapshot_item
        WHERE snapshot_id=? AND item_type IN ('product_capability','corpus_block')
        """,
        (snapshot["snapshot_id"],),
    ).fetchall()
    capabilities = {row["item_id"] for row in rows if row["item_type"] == "product_capability"}
    blocks = {row["item_id"] for row in rows if row["item_type"] == "corpus_block"}
    return capabilities, blocks, snapshot["content_hash"]


def _load_blocks(conn, allowed_ids: set[str] | None = None) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT rowid AS local_rowid,* FROM corpus_block
            WHERE review_status='approved'
            """
        )
    ]
    if allowed_ids is not None:
        rows = [row for row in rows if row["block_id"] in allowed_ids]
    for row in rows:
        row["heading_path"] = _json(row.get("heading_path_json"), [])
    return rows


def _block_order(block: dict[str, Any]) -> tuple[int, int, int]:
    source_order = int(block.get("source_order") or 0)
    if source_order > 0:
        return (0, source_order, int(block.get("local_rowid") or 0))
    numbers = re.findall(r"(?:paragraph|table|row|block)[:：]?\s*(\d+)", block.get("source_location", ""), re.I)
    if numbers:
        return (1, int(numbers[-1]), int(block.get("local_rowid") or 0))
    return (2, int(block.get("local_rowid") or 0), 0)


def _is_prefix(prefix: list[str], path: list[str]) -> bool:
    return len(path) >= len(prefix) and all(
        _normalize_name(left) == _normalize_name(right)
        for left, right in zip(prefix, path)
    )


def _root_options(
    capability: dict[str, Any], blocks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    module_names = {
        _normalize_name(capability.get("module_name")),
        _normalize_name(capability.get("capability_name")),
    } - {""}
    product_name = _normalize_name(capability.get("product_name"))
    options: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for block in blocks:
        path = [str(item) for item in block.get("heading_path", []) if str(item).strip()]
        normalized = [_normalize_name(item) for item in path]
        module_positions = [index for index, item in enumerate(normalized) if item in module_names]
        if not module_positions:
            continue
        product_positions = [index for index, item in enumerate(normalized) if item == product_name]
        preferred = [
            index
            for index in module_positions
            if any(parent < index for parent in product_positions)
        ]
        root_index = preferred[0] if preferred else module_positions[0]
        root = path[: root_index + 1]
        match_type = "module_and_product" if preferred else "module_only"
        key = (block["corpus_document_id"], tuple(root))
        options[key] = {
            "corpus_document_id": block["corpus_document_id"],
            "root_heading_path": root,
            "root_match_type": match_type,
        }
    result: list[dict[str, Any]] = []
    for option in options.values():
        subtree = [
            block
            for block in blocks
            if block["corpus_document_id"] == option["corpus_document_id"]
            and _is_prefix(option["root_heading_path"], block.get("heading_path", []))
        ]
        subtree.sort(key=_block_order)
        option["blocks"] = subtree
        option["block_ids"] = [block["block_id"] for block in subtree]
        result.append(option)
    preferred = [item for item in result if item["root_match_type"] == "module_and_product"]
    return preferred or result


def _requested_root_options(
    requested_root: list[str], blocks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Resolve an explicitly reviewed title alias to one complete document subtree."""
    root = [str(item).strip() for item in requested_root if str(item).strip()]
    if not root:
        return []
    document_ids = {
        block["corpus_document_id"]
        for block in blocks
        if _is_prefix(root, block.get("heading_path", []))
    }
    result: list[dict[str, Any]] = []
    for document_id in sorted(document_ids):
        subtree = [
            block
            for block in blocks
            if block["corpus_document_id"] == document_id
            and _is_prefix(root, block.get("heading_path", []))
        ]
        subtree.sort(key=_block_order)
        if subtree:
            result.append(
                {
                    "corpus_document_id": document_id,
                    "root_heading_path": root,
                    "root_match_type": "manual_alias",
                    "blocks": subtree,
                    "block_ids": [block["block_id"] for block in subtree],
                }
            )
    return result


def _candidate_sort_key(item: dict[str, Any]) -> tuple[bool, bool, bool, float, float]:
    """Keep an exact module name visible even when its solution root needs repair."""
    return (
        bool(item["module_name_exact"]),
        bool(item["hierarchy_exact"]),
        item["candidate_status"] == "ready",
        float(item["name_score"]),
        float(item["parent_score"]),
    )


def _candidate_markdown(result: dict[str, Any]) -> str:
    lines = ["# 建设清单与标准清单对照复核包", ""]
    lines.append(f"- 对照运行：`{result['match_run_id']}`")
    lines.append(f"- 项目清单行数：{result['summary']['scope_rows']}")
    lines.append(f"- 精确自动确认：{result['summary']['auto_confirmed_exact']}")
    lines.append(f"- 待人工确认：{result['summary']['needs_human_review']}")
    lines.append(f"- 标准内容缺失：{result['summary']['content_missing']}")
    lines.extend(["", "## 逐项核对", ""])
    for item in result["items"]:
        lines.append(f"### {item['source_ordinal']}. {item['original_name']}")
        lines.append("")
        lines.append(item["question"])
        lines.append("")
        if item["candidates"]:
            lines.append("|候选ID|匹配类型|标准系统 / 模块|名称分|上级分|标准方案子树|")
            lines.append("|---|---|---|---:|---:|---|")
            for candidate in item["candidates"]:
                root = " / ".join(candidate["root_heading_path"]) or "未定位"
                lines.append(
                    f"|{candidate['candidate_id']}|{candidate['match_class']}|"
                    f"{candidate['product_name']} / {candidate['module_name']}|"
                    f"{candidate['name_score']:.3f}|{candidate['parent_score']:.3f}|{root}|"
                )
        lines.append("")
    return "\n".join(lines)


def match_scope(
    database: Path,
    project_code: str,
    *,
    scope_snapshot_id: str = "",
    package_id: str = "",
    similar_threshold: float = 0.6,
) -> dict[str, Any]:
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = _project(conn, project_code)
        snapshot = _latest_snapshot(conn, project["project_id"], scope_snapshot_id)
        allowed_capabilities, allowed_blocks, package_hash = _allowed_snapshot_items(
            conn, project["project_id"], package_id
        )
        capability_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM product_capability WHERE review_status='approved'"
            )
        ]
        if package_id:
            capability_rows = [
                row for row in capability_rows if row["capability_id"] in allowed_capabilities
            ]
        blocks = _load_blocks(conn, allowed_blocks if package_id else None)
        scope_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT csr.*,psi.original_name,psi.standard_name,psi.domain,psi.item_type
                FROM construction_scope_row AS csr
                JOIN project_scope_item AS psi ON psi.scope_id=csr.scope_id
                WHERE csr.scope_snapshot_id=?
                ORDER BY csr.source_ordinal
                """,
                (snapshot["scope_snapshot_id"],),
            )
        ]
        input_hash = _canonical_hash(
            {
                "scope_snapshot_hash": snapshot["display_hash"],
                "package_id": package_id,
                "package_content_hash": package_hash,
                "matcher_version": MATCHER_VERSION,
            }
        )
        timestamp = now_iso()
        run_id = stable_id("CONSTRUCTIONMATCH", input_hash, timestamp)
        conn.execute(
            """
            UPDATE construction_match_run SET status='superseded'
            WHERE project_id=? AND scope_snapshot_id=? AND status<>'superseded'
            """,
            (project["project_id"], snapshot["scope_snapshot_id"]),
        )
        result_items: list[dict[str, Any]] = []
        auto_count = 0
        review_count = 0
        missing_count = 0
        candidate_total = 0
        pending_inserts: list[dict[str, Any]] = []
        for scope_row in scope_rows:
            hierarchy = _json(scope_row["hierarchy_json"], [])
            parent_terms = hierarchy[:-1] + [scope_row.get("domain", "")]
            scored: list[dict[str, Any]] = []
            for capability in capability_rows:
                module_name = capability.get("module_name") or capability["capability_name"]
                name_score = max(
                    _score(scope_row["original_name"], module_name),
                    _score(scope_row["standard_name"], module_name),
                    _score(scope_row["original_name"], capability["capability_name"]),
                )
                module_name_exact = name_score == 1.0
                if not module_name_exact and name_score < similar_threshold:
                    continue
                parent_score = max(
                    [_score(term, capability["product_name"]) for term in parent_terms] or [0.0]
                )
                root_options = _root_options(capability, blocks)
                preferred_roots = [
                    option
                    for option in root_options
                    if option["root_match_type"] == "module_and_product"
                ]
                chosen_roots = preferred_roots or root_options
                if len(chosen_roots) == 1:
                    root = chosen_roots[0]
                    root_type = root["root_match_type"]
                    block_ids = root["block_ids"]
                    status = (
                        "ready"
                        if root_type == "module_and_product" and block_ids
                        else "needs_review"
                    )
                    reason = "unique exact module subtree" if status == "ready" else "module-only subtree requires review"
                elif len(chosen_roots) > 1:
                    root = {"root_heading_path": [], "block_ids": []}
                    root_type = "ambiguous"
                    block_ids = []
                    status = "blocked"
                    reason = "multiple standard solution roots; choose a unique root manually"
                else:
                    root = {"root_heading_path": [], "block_ids": []}
                    root_type = "missing"
                    block_ids = []
                    status = "blocked"
                    reason = "standard capability exists but its solution subtree is missing"
                hierarchy_exact = parent_score == 1.0
                scored.append(
                    {
                        "capability": capability,
                        "match_class": "exact" if module_name_exact and hierarchy_exact else "similar",
                        "module_name_exact": module_name_exact,
                        "hierarchy_exact": hierarchy_exact,
                        "name_score": name_score,
                        "parent_score": parent_score,
                        "root_heading_path": root["root_heading_path"],
                        "root_match_type": root_type,
                        "block_ids": block_ids,
                        "candidate_status": status,
                        "reason": reason,
                    }
                )
            scored.sort(key=_candidate_sort_key, reverse=True)
            scored = scored[:MAX_REVIEW_CANDIDATES]
            exact_ready = [
                item
                for item in scored
                if item["match_class"] == "exact" and item["candidate_status"] == "ready"
            ]
            candidates_out: list[dict[str, Any]] = []
            for candidate in scored:
                capability = candidate["capability"]
                candidate_id = stable_id(
                    "CONSTRUCTIONCANDIDATE",
                    run_id,
                    scope_row["scope_row_id"],
                    capability["capability_id"],
                    dump_json(candidate["root_heading_path"]),
                )
                record = {
                    "candidate_id": candidate_id,
                    "scope_row_id": scope_row["scope_row_id"],
                    "capability_id": capability["capability_id"],
                    "product_name": capability["product_name"],
                    "module_name": capability.get("module_name") or capability["capability_name"],
                    **{key: candidate[key] for key in (
                        "match_class","module_name_exact","hierarchy_exact",
                        "name_score","parent_score","root_heading_path",
                        "root_match_type","candidate_status","reason"
                    )},
                    "subtree_block_ids": candidate["block_ids"],
                }
                pending_inserts.append(record)
                candidates_out.append(record)
                candidate_total += 1
            if len(exact_ready) == 1:
                selected_index = scored.index(exact_ready[0])
                selected = candidates_out[selected_index]
                auto_count += 1
                state = "auto_confirmed_exact"
                question = "系统名称、模块名称及标准方案子树均唯一精确匹配，已自动确认；人工可抽查。"
            elif scored:
                selected = None
                review_count += 1
                state = "needs_human_review"
                if any(item["module_name_exact"] for item in scored):
                    question = (
                        f"标准清单存在与“{scope_row['original_name']}”同名的模块，但上级系统或标准方案根未唯一精确闭环；"
                        "请优先核对同名候选，并确认对应的方案根。"
                    )
                elif scored[0]["name_score"] < WEAK_SIMILARITY_THRESHOLD:
                    question = (
                        f"清单“{scope_row['original_name']}”仅检出低置信相似线索。请先确认标准方案是否实际包含该模块；"
                        "不要仅凭字符相似选择候选。"
                    )
                else:
                    question = (
                        f"清单“{scope_row['original_name']}”存在相似或歧义候选，请确认对应的标准系统和模块。"
                    )
            else:
                selected = None
                missing_count += 1
                state = "content_missing"
                question = (
                    f"标准清单未找到“{scope_row['original_name']}”。请确认标准方案中是否实际包含该系统/模块；"
                    "如包含请指定对应能力，如不包含请确认缺口。"
                )
            result_items.append(
                {
                    "scope_row_id": scope_row["scope_row_id"],
                    "scope_id": scope_row["scope_id"],
                    "source_ordinal": scope_row["source_ordinal"],
                    "original_name": scope_row["original_name"],
                    "hierarchy": hierarchy,
                    "state": state,
                    "question": question,
                    "candidates": candidates_out,
                    "auto_selected_candidate_id": selected["candidate_id"] if selected else "",
                }
            )
        summary = {
            "scope_rows": len(scope_rows),
            "candidates": candidate_total,
            "auto_confirmed_exact": auto_count,
            "needs_human_review": review_count,
            "content_missing": missing_count,
        }
        conn.execute(
            """
            INSERT INTO construction_match_run (
              match_run_id,project_id,scope_snapshot_id,package_id,package_content_hash,
              matcher_version,input_hash,status,summary_json,created_at
            ) VALUES (?,?,?,?,?,?,?,'completed',?,?)
            """,
            (
                run_id,
                project["project_id"],
                snapshot["scope_snapshot_id"],
                package_id,
                package_hash,
                MATCHER_VERSION,
                input_hash,
                dump_json(summary),
                timestamp,
            ),
        )
        for candidate in pending_inserts:
            conn.execute(
                """
                INSERT INTO construction_match_candidate (
                  candidate_id,match_run_id,scope_row_id,capability_id,match_class,
                  name_score,parent_score,root_heading_path_json,root_match_type,
                  subtree_block_ids_json,candidate_status,reason,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    candidate["candidate_id"],run_id,candidate["scope_row_id"],
                    candidate["capability_id"],candidate["match_class"],candidate["name_score"],
                    candidate["parent_score"],dump_json(candidate["root_heading_path"]),
                    candidate["root_match_type"],dump_json(candidate["subtree_block_ids"]),
                    candidate["candidate_status"],candidate["reason"],timestamp,
                ),
            )
        for item in result_items:
            selected_id = item["auto_selected_candidate_id"]
            if not selected_id:
                continue
            selected = conn.execute(
                "SELECT * FROM construction_match_candidate WHERE candidate_id=?",
                (selected_id,),
            ).fetchone()
            decision_payload = {
                "run": run_id,
                "row": item["scope_row_id"],
                "candidate": selected_id,
                "decision": "confirmed",
                "source": "auto_exact",
            }
            decision_hash = _canonical_hash(decision_payload)
            conn.execute(
                """
                INSERT INTO construction_match_decision (
                  decision_id,match_run_id,scope_row_id,candidate_id,decision,decision_source,
                  chosen_capability_id,chosen_root_heading_path_json,chosen_block_ids_json,
                  decision_note,reviewed_by,reviewed_at,decision_hash,created_at
                ) VALUES (?,?,?,?,?,'auto_exact',?,?,?,?,?,?,?,?)
                """,
                (
                    stable_id("CONSTRUCTIONDECISION", decision_hash),run_id,item["scope_row_id"],
                    selected_id,"confirmed",selected["capability_id"],
                    selected["root_heading_path_json"],selected["subtree_block_ids_json"],
                    "unique exact module and product subtree","system",timestamp,decision_hash,timestamp,
                ),
            )
        conn.commit()
    return {
        "database": str(database.resolve()),
        "project_code": project_code,
        "match_run_id": run_id,
        "scope_snapshot_id": snapshot["scope_snapshot_id"],
        "package_id": package_id,
        "package_content_hash": package_hash,
        "matcher_version": MATCHER_VERSION,
        "summary": summary,
        "items": result_items,
        "review_gate": "similar, ambiguous and missing items require a human decision before assembly",
    }


def apply_decisions(database: Path, payload: dict[str, Any]) -> dict[str, Any]:
    run_id = str(payload.get("match_run_id") or "")
    reviewed_by = str(payload.get("reviewed_by") or "").strip()
    reviewed_at = str(payload.get("reviewed_at") or now_iso())
    decisions = payload.get("decisions")
    if not run_id or not reviewed_by or not isinstance(decisions, list):
        raise ValueError("decision payload requires match_run_id, reviewed_by and decisions[]")
    applied = 0
    duplicates = 0
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        run = conn.execute(
            "SELECT * FROM construction_match_run WHERE match_run_id=?", (run_id,)
        ).fetchone()
        if run is None or run["status"] == "superseded":
            raise ValueError("match run not found or superseded")
        package_id = str(run["package_id"] or "")
        allowed_capabilities, allowed_blocks, current_package_hash = _allowed_snapshot_items(
            conn, run["project_id"], package_id
        )
        if package_id and current_package_hash != run["package_content_hash"]:
            raise ValueError("knowledge package snapshot changed after matching; rerun match")
        blocks = _load_blocks(conn, allowed_blocks if package_id else None)
        for entry in decisions:
            row_id = str(entry.get("scope_row_id") or "")
            if not row_id and entry.get("source_ordinal"):
                row = conn.execute(
                    """
                    SELECT scope_row_id FROM construction_scope_row
                    WHERE scope_snapshot_id=? AND source_ordinal=?
                    """,
                    (run["scope_snapshot_id"], int(entry["source_ordinal"])),
                ).fetchone()
                row_id = row["scope_row_id"] if row else ""
            if not row_id:
                raise ValueError("each decision requires scope_row_id or source_ordinal")
            decision = str(entry.get("decision") or "")
            if decision not in {"confirmed", "confirmed_gap", "rejected", "deferred"}:
                raise ValueError(f"unsupported decision: {decision}")
            candidate_id = str(entry.get("candidate_id") or "")
            capability_id = ""
            root: list[str] = []
            block_ids: list[str] = []
            requested_root = [str(item) for item in entry.get("root_heading_path", [])]
            if candidate_id:
                candidate = conn.execute(
                    """
                    SELECT * FROM construction_match_candidate
                    WHERE candidate_id=? AND match_run_id=? AND scope_row_id=?
                    """,
                    (candidate_id, run_id, row_id),
                ).fetchone()
                if candidate is None:
                    raise ValueError(f"candidate does not belong to row/run: {candidate_id}")
                capability_id = candidate["capability_id"]
                root = _json(candidate["root_heading_path_json"], [])
                block_ids = _json(candidate["subtree_block_ids_json"], [])
                if package_id and (
                    capability_id not in allowed_capabilities
                    or not set(block_ids).issubset(allowed_blocks)
                ):
                    raise ValueError("candidate is outside the matched knowledge package snapshot")
                if requested_root:
                    options = _requested_root_options(requested_root, blocks)
                    if len(options) != 1:
                        raise ValueError("reviewed title alias must resolve to one unique solution subtree")
                    root = options[0]["root_heading_path"]
                    block_ids = options[0]["block_ids"]
            elif entry.get("capability_id"):
                capability_id = str(entry["capability_id"])
                if package_id and capability_id not in allowed_capabilities:
                    raise ValueError("manual capability choice is outside the matched knowledge package snapshot")
                capability_row = conn.execute(
                    "SELECT * FROM product_capability WHERE capability_id=? AND review_status='approved'",
                    (capability_id,),
                ).fetchone()
                if capability_row is None:
                    raise ValueError(f"approved capability not found: {capability_id}")
                if requested_root:
                    options = _requested_root_options(requested_root, blocks)
                else:
                    options = _root_options(dict(capability_row), blocks)
                if len(options) != 1:
                    raise ValueError("manual capability choice must resolve to one unique solution subtree")
                root = options[0]["root_heading_path"]
                block_ids = options[0]["block_ids"]
            if decision == "confirmed" and (not capability_id or not root or not block_ids):
                raise ValueError("confirmed decision requires a capability with one non-empty subtree")
            if decision == "confirmed_gap":
                candidate_id = ""
                capability_id = ""
                root = []
                block_ids = []
            decision_payload = {
                "match_run_id": run_id,
                "scope_row_id": row_id,
                "candidate_id": candidate_id,
                "decision": decision,
                "capability_id": capability_id,
                "root_heading_path": root,
                "block_ids": block_ids,
                "note": str(entry.get("decision_note") or ""),
                "reviewed_by": reviewed_by,
                "reviewed_at": reviewed_at,
            }
            decision_hash = _canonical_hash(decision_payload)
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO construction_match_decision (
                  decision_id,match_run_id,scope_row_id,candidate_id,decision,decision_source,
                  chosen_capability_id,chosen_root_heading_path_json,chosen_block_ids_json,
                  decision_note,reviewed_by,reviewed_at,decision_hash,created_at
                ) VALUES (?,?,?,?,?,'human',?,?,?,?,?,?,?,?)
                """,
                (
                    stable_id("CONSTRUCTIONDECISION", decision_hash),run_id,row_id,
                    candidate_id or None,decision,capability_id or None,dump_json(root),
                    dump_json(block_ids),decision_payload["note"],reviewed_by,reviewed_at,
                    decision_hash,now_iso(),
                ),
            )
            if cursor.rowcount:
                applied += 1
            else:
                duplicates += 1
        conn.commit()
    return {
        "database": str(database.resolve()),
        "match_run_id": run_id,
        "applied": applied,
        "duplicates": duplicates,
        "decision_policy": "append-only; latest decision per scope row controls assembly",
    }


def _markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def _scope_snapshot_markdown(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = ["## 软件建设清单", ""]
    for sheet in payload.get("sheets", []):
        headers = [str(item) for item in sheet.get("headers", [])]
        if not headers:
            continue
        lines.append(f"### {sheet.get('name', '')}")
        lines.append("")
        lines.append("|" + "|".join(_markdown_cell(item) for item in headers) + "|")
        lines.append("|" + "|".join("---" for _ in headers) + "|")
        for row in sheet.get("rows", []):
            lines.append(
                "|" + "|".join(_markdown_cell(row.get(header, "")) for header in headers) + "|"
            )
        lines.append("")
    return lines


def _solution_item_markdown(item: dict[str, Any], heading_level: int = 3) -> list[str]:
    hashes = "#" * heading_level
    lines = [f"{hashes} {item['original_name']}", ""]
    if item["status"] == "pending_supplement":
        return lines + [PENDING_MARKER, ""]
    if item["status"] == "pending_confirmation":
        return lines + [PENDING_CONFIRMATION_MARKER, ""]
    last_relative: list[str] = []
    last_source_section_id = ""
    for fragment in item["fragments"]:
        relative = fragment["relative_heading_path"]
        common = 0
        while common < min(len(last_relative), len(relative)) and last_relative[common] == relative[common]:
            common += 1
        if (
            relative
            and fragment.get("source_section_id")
            and fragment.get("source_section_id") != last_source_section_id
            and relative == last_relative
        ):
            common = len(relative) - 1
        for index in range(common, len(relative)):
            level = min(6, heading_level + 1 + index)
            lines.extend([f"{'#' * level} {relative[index]}", ""])
        lines.extend([fragment["clean_text"], ""])
        last_relative = relative
        last_source_section_id = str(fragment.get("source_section_id") or "")
    return lines


def render_scope_fragment(manifest: dict[str, Any]) -> str:
    payload = manifest["construction_list_import"]["display_payload"]
    return "\n".join(_scope_snapshot_markdown(payload))


def render_solution_fragment(manifest: dict[str, Any]) -> str:
    lines = ["## 应用软件建设方案", ""]
    for item in manifest["application_software_solution"]["items"]:
        lines.extend(_solution_item_markdown(item))
    return "\n".join(lines)


def assemble(
    database: Path,
    project_code: str,
    match_run_id: str,
    *,
    allow_unresolved_preview: bool = False,
) -> tuple[dict[str, Any], str]:
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = _project(conn, project_code)
        run = conn.execute(
            "SELECT * FROM construction_match_run WHERE match_run_id=? AND project_id=?",
            (match_run_id, project["project_id"]),
        ).fetchone()
        if run is None or run["status"] == "superseded":
            raise ValueError("active match run not found")
        allowed_capability_ids, allowed_block_ids, package_content_hash = _allowed_snapshot_items(
            conn, project["project_id"], run["package_id"]
        )
        if package_content_hash != run["package_content_hash"]:
            raise ValueError("standard package snapshot changed after matching; rerun match and review")
        snapshot = dict(
            conn.execute(
                "SELECT * FROM construction_scope_snapshot WHERE scope_snapshot_id=?",
                (run["scope_snapshot_id"],),
            ).fetchone()
        )
        scope_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT csr.*,psi.original_name,psi.standard_name
                FROM construction_scope_row AS csr
                JOIN project_scope_item AS psi ON psi.scope_id=csr.scope_id
                WHERE csr.scope_snapshot_id=? ORDER BY csr.source_ordinal
                """,
                (run["scope_snapshot_id"],),
            )
        ]
        items: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for scope_row in scope_rows:
            decision = conn.execute(
                """
                SELECT * FROM construction_match_decision
                WHERE match_run_id=? AND scope_row_id=?
                ORDER BY rowid DESC LIMIT 1
                """,
                (match_run_id, scope_row["scope_row_id"]),
            ).fetchone()
            if decision is None or decision["decision"] in {"deferred", "rejected"}:
                unresolved_item = {
                    "scope_row_id": scope_row["scope_row_id"],
                    "source_ordinal": scope_row["source_ordinal"],
                    "original_name": scope_row["original_name"],
                    "reason": "missing, deferred or rejected mapping decision",
                }
                unresolved.append(unresolved_item)
                if allow_unresolved_preview:
                    items.append(
                        {
                            **unresolved_item,
                            "scope_id": scope_row["scope_id"],
                            "status": "pending_confirmation",
                            "capability_id": "",
                            "product_name": "",
                            "module_name": "",
                            "root_heading_path": [],
                            "block_ids": [],
                            "fragments": [],
                            "content_hash": sha256_text(PENDING_CONFIRMATION_MARKER),
                        }
                    )
                continue
            if decision["decision"] == "confirmed_gap":
                item = {
                    "scope_row_id": scope_row["scope_row_id"],
                    "scope_id": scope_row["scope_id"],
                    "source_ordinal": scope_row["source_ordinal"],
                    "original_name": scope_row["original_name"],
                    "status": "pending_supplement",
                    "capability_id": "",
                    "product_name": "",
                    "module_name": "",
                    "root_heading_path": [],
                    "block_ids": [],
                    "fragments": [],
                    "content_hash": sha256_text(PENDING_MARKER),
                }
                items.append(item)
                continue
            capability = dict(
                conn.execute(
                    "SELECT * FROM product_capability WHERE capability_id=?",
                    (decision["chosen_capability_id"],),
                ).fetchone()
            )
            if allowed_capability_ids and capability["capability_id"] not in allowed_capability_ids:
                raise ValueError(f"chosen capability is outside the frozen package: {capability['capability_id']}")
            root = _json(decision["chosen_root_heading_path_json"], [])
            chosen_ids = _json(decision["chosen_block_ids_json"], [])
            placeholders = ",".join("?" for _ in chosen_ids)
            selected_blocks = [
                dict(row)
                for row in conn.execute(
                    f"SELECT rowid AS local_rowid,* FROM corpus_block WHERE block_id IN ({placeholders})",
                    chosen_ids,
                )
            ]
            for block in selected_blocks:
                block["heading_path"] = _json(block.get("heading_path_json"), [])
            selected_blocks.sort(key=_block_order)
            if allowed_block_ids and not set(chosen_ids).issubset(allowed_block_ids):
                raise ValueError(f"chosen subtree contains blocks outside the frozen package: {scope_row['original_name']}")
            if [block["block_id"] for block in selected_blocks] != chosen_ids:
                raise ValueError(f"chosen subtree block order/content changed for {scope_row['original_name']}")
            if not selected_blocks or any(not _is_prefix(root, block["heading_path"]) for block in selected_blocks):
                raise ValueError(f"chosen blocks are not one complete heading subtree: {scope_row['original_name']}")
            all_blocks = _load_blocks(conn, allowed_block_ids if allowed_block_ids else None)
            document_ids = {block["corpus_document_id"] for block in selected_blocks}
            expected = [
                block for block in all_blocks
                if block["corpus_document_id"] in document_ids and _is_prefix(root, block["heading_path"])
            ]
            expected.sort(key=_block_order)
            if [block["block_id"] for block in expected] != chosen_ids:
                raise ValueError(f"selected blocks do not equal the complete standard subtree: {scope_row['original_name']}")
            fragments = []
            for block in selected_blocks:
                if sha256_text(block["clean_text"]) != block["text_hash"]:
                    raise ValueError(f"standard block text hash mismatch: {block['block_id']}")
                fragments.append(
                    {
                        "block_id": block["block_id"],
                        "source_section_id": block.get("source_section_id", ""),
                        "source_order": int(block.get("source_order", 0)),
                        "chunk_index": int(block.get("chunk_index", 0)),
                        "source_is_heading": bool(block.get("source_is_heading", False)),
                        "heading_path": block["heading_path"],
                        "relative_heading_path": block["heading_path"][len(root):],
                        "clean_text": block["clean_text"],
                        "text_hash": block["text_hash"],
                        "content_format": block.get("content_format", "plain_text"),
                        "content_payload": _json(block.get("content_payload_json"), {}),
                        "asset_manifest": _json(block.get("asset_manifest_json"), []),
                        "visible_text_hash": block.get("visible_text_hash") or block["text_hash"],
                        "source_location": block["source_location"],
                    }
                )
            content_hash = _canonical_hash(
                [{"block_id": block["block_id"], "text_hash": block["text_hash"]} for block in selected_blocks]
            )
            items.append(
                {
                    "scope_row_id": scope_row["scope_row_id"],
                    "scope_id": scope_row["scope_id"],
                    "source_ordinal": scope_row["source_ordinal"],
                    "original_name": scope_row["original_name"],
                    "status": "verbatim",
                    "capability_id": capability["capability_id"],
                    "product_name": capability["product_name"],
                    "module_name": capability.get("module_name") or capability["capability_name"],
                    "root_heading_path": root,
                    "block_ids": chosen_ids,
                    "fragments": fragments,
                    "content_hash": content_hash,
                }
            )
        if unresolved and not allow_unresolved_preview:
            raise ValueError(
                "assembly blocked by unresolved scope rows: "
                + ", ".join(item["original_name"] for item in unresolved[:8])
            )
        if [item["source_ordinal"] for item in items] != sorted(item["source_ordinal"] for item in items):
            raise ValueError("assembly order does not preserve customer scope order")
        raw_payload = _json(snapshot["display_payload_json"], {})
        manifest_core = {
            "schema_version": "construction-assembly-v1",
            "project_code": project_code,
            "match_run_id": match_run_id,
            "scope_snapshot_id": snapshot["scope_snapshot_id"],
            "scope_snapshot_hash": snapshot["display_hash"],
            "package_id": run["package_id"],
            "package_content_hash": run["package_content_hash"],
            "construction_list_import": {
                "mode": "verbatim_display_snapshot",
                "target_section_roles": [
                    "project_scope.software_construction_list",
                    "overall_design.software_construction_list",
                ],
                "source_path": snapshot["source_path"],
                "source_sha256": snapshot["source_sha256"],
                "display_payload": raw_payload,
            },
            "application_software_solution": {
                "target_section_role": "overall_design.application_software_solution",
                "order_rule": "customer_scope_source_order",
                "standard_content_rule": "complete_unique_heading_subtree_without_rewrite",
                "items": items,
            },
            "unresolved_scope_rows": unresolved,
            "preview_only": bool(unresolved),
        }
        manifest_hash = _canonical_hash(manifest_core)
        manifest_id = stable_id("CONSTRUCTIONMANIFEST", match_run_id, manifest_hash)
        status = (
            "blocked"
            if unresolved
            else "with_pending_supplement"
            if any(item["status"] == "pending_supplement" for item in items)
            else "complete"
        )
        manifest = {
            **manifest_core,
            "manifest_id": manifest_id,
            "manifest_hash": manifest_hash,
            "status": status,
        }
        timestamp = now_iso()
        if not unresolved:
            conn.execute(
                """
                INSERT INTO construction_assembly_manifest (
                  manifest_id,project_id,match_run_id,target_section_role,scope_snapshot_hash,
                  package_content_hash,manifest_json,manifest_hash,status,created_at
                ) VALUES (?,?,?,'application_software_solution',?,?,?,?,?,?)
                ON CONFLICT(manifest_id) DO UPDATE SET manifest_json=excluded.manifest_json,status=excluded.status
                """,
                (
                    manifest_id,project["project_id"],match_run_id,snapshot["display_hash"],
                    run["package_content_hash"],dump_json(manifest),manifest_hash,status,timestamp,
                ),
            )
            conn.execute("DELETE FROM construction_assembly_item WHERE manifest_id=?", (manifest_id,))
            for item in items:
                conn.execute(
                    """
                    INSERT INTO construction_assembly_item (
                      manifest_id,item_order,scope_row_id,capability_id,root_heading_path_json,
                      block_ids_json,content_hash,item_status
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        manifest_id,item["source_ordinal"],item["scope_row_id"],
                        item["capability_id"] or None,dump_json(item["root_heading_path"]),
                        dump_json(item["block_ids"]),item["content_hash"],item["status"],
                    ),
                )
            conn.commit()
    combined = render_scope_fragment(manifest) + "\n\n" + render_solution_fragment(manifest)
    return manifest, combined


def validate_manifest(database: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    manifest_core = {
        key: value
        for key, value in manifest.items()
        if key not in {"manifest_id", "manifest_hash", "status"}
    }
    calculated_manifest_hash = _canonical_hash(manifest_core)
    if calculated_manifest_hash != manifest.get("manifest_hash"):
        issues.append({"code": "manifest_hash_mismatch", "blocking": True})
    calculated_manifest_id = stable_id(
        "CONSTRUCTIONMANIFEST", manifest.get("match_run_id"), calculated_manifest_hash
    )
    if calculated_manifest_id != manifest.get("manifest_id"):
        issues.append({"code": "manifest_id_mismatch", "blocking": True})
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT project_id FROM project WHERE project_code=?", (manifest.get("project_code"),)
        ).fetchone()
        run = conn.execute(
            "SELECT * FROM construction_match_run WHERE match_run_id=?",
            (manifest.get("match_run_id"),),
        ).fetchone()
        allowed_block_ids: set[str] | None = None
        if project is None:
            issues.append({"code": "project_missing", "blocking": True})
        if run is None:
            issues.append({"code": "match_run_missing", "blocking": True})
        else:
            if project is not None and run["project_id"] != project["project_id"]:
                issues.append({"code": "match_run_project_mismatch", "blocking": True})
            if run["scope_snapshot_id"] != manifest.get("scope_snapshot_id"):
                issues.append({"code": "match_run_scope_snapshot_mismatch", "blocking": True})
            if run["package_id"] != manifest.get("package_id"):
                issues.append({"code": "match_run_package_mismatch", "blocking": True})
            if run["package_content_hash"] != manifest.get("package_content_hash"):
                issues.append({"code": "match_run_package_hash_mismatch", "blocking": True})
            if project is not None:
                try:
                    _, frozen_block_ids, current_package_hash = _allowed_snapshot_items(
                        conn, project["project_id"], run["package_id"]
                    )
                    allowed_block_ids = frozen_block_ids if frozen_block_ids else None
                    if current_package_hash != run["package_content_hash"]:
                        issues.append({"code": "package_snapshot_changed", "blocking": True})
                except ValueError as exc:
                    issues.append(
                        {
                            "code": "standard_knowledge_coverage_invalid",
                            "blocking": True,
                            "detail": str(exc),
                        }
                    )
        if not manifest.get("preview_only") and manifest.get("manifest_id"):
            persisted = conn.execute(
                "SELECT manifest_hash,manifest_json FROM construction_assembly_manifest WHERE manifest_id=?",
                (manifest.get("manifest_id"),),
            ).fetchone()
            if persisted is None:
                issues.append({"code": "persisted_manifest_missing", "blocking": True})
            elif (
                persisted["manifest_hash"] != manifest.get("manifest_hash")
                or _json(persisted["manifest_json"], {}) != manifest
            ):
                issues.append({"code": "persisted_manifest_mismatch", "blocking": True})
        snapshot = conn.execute(
            "SELECT * FROM construction_scope_snapshot WHERE scope_snapshot_id=?",
            (manifest.get("scope_snapshot_id"),),
        ).fetchone()
        if snapshot is None:
            issues.append({"code": "snapshot_missing", "blocking": True})
        else:
            payload = _json(snapshot["display_payload_json"], {})
            if _canonical_hash(payload) != manifest.get("scope_snapshot_hash"):
                issues.append({"code": "scope_snapshot_hash_mismatch", "blocking": True})
            manifest_scope = manifest.get("construction_list_import", {})
            if payload != manifest_scope.get("display_payload"):
                issues.append({"code": "scope_display_payload_mismatch", "blocking": True})
            if (
                snapshot["source_path"] != manifest_scope.get("source_path")
                or snapshot["source_sha256"] != manifest_scope.get("source_sha256")
            ):
                issues.append({"code": "scope_source_binding_mismatch", "blocking": True})
        items = manifest.get("application_software_solution", {}).get("items", [])
        if manifest.get("status") == "blocked" or manifest.get("preview_only"):
            issues.append({"code": "unresolved_working_preview", "blocking": True})
        orders = [_int(item.get("source_ordinal")) for item in items]
        if orders != sorted(orders) or len(orders) != len(set(orders)):
            issues.append({"code": "customer_order_not_preserved", "blocking": True})
        for item in items:
            scope_row = conn.execute(
                """
                SELECT csr.source_ordinal,csr.scope_row_id,psi.scope_id,psi.original_name
                FROM construction_scope_row AS csr
                JOIN project_scope_item AS psi ON psi.scope_id=csr.scope_id
                WHERE csr.scope_snapshot_id=? AND csr.scope_row_id=?
                """,
                (manifest.get("scope_snapshot_id"), item.get("scope_row_id")),
            ).fetchone()
            if (
                scope_row is None
                or _int(scope_row["source_ordinal"]) != _int(item.get("source_ordinal"))
                or scope_row["scope_id"] != item.get("scope_id")
                or scope_row["original_name"] != item.get("original_name")
            ):
                issues.append(
                    {
                        "code": "scope_item_binding_mismatch",
                        "blocking": True,
                        "item": item.get("original_name"),
                    }
                )
            if item.get("status") == "pending_supplement":
                if item.get("fragments") or item.get("block_ids"):
                    issues.append({"code": "gap_contains_standard_content", "blocking": True, "item": item.get("original_name")})
                if item.get("content_hash") != sha256_text(PENDING_MARKER):
                    issues.append({"code": "gap_content_hash_mismatch", "blocking": True, "item": item.get("original_name")})
                continue
            if item.get("status") == "pending_confirmation":
                if item.get("fragments") or item.get("block_ids"):
                    issues.append({"code": "pending_confirmation_contains_standard_content", "blocking": True, "item": item.get("original_name")})
                if item.get("content_hash") != sha256_text(PENDING_CONFIRMATION_MARKER):
                    issues.append({"code": "pending_confirmation_hash_mismatch", "blocking": True, "item": item.get("original_name")})
                continue
            ids = item.get("block_ids", [])
            fragments = item.get("fragments", [])
            if ids != [fragment.get("block_id") for fragment in fragments]:
                issues.append({"code": "fragment_order_mismatch", "blocking": True, "item": item.get("original_name")})
            expected_content_hash = _canonical_hash(
                [
                    {"block_id": fragment.get("block_id"), "text_hash": fragment.get("text_hash")}
                    for fragment in fragments
                ]
            )
            if expected_content_hash != item.get("content_hash"):
                issues.append({"code": "item_content_hash_mismatch", "blocking": True, "item": item.get("original_name")})
            root = item.get("root_heading_path", [])
            for fragment in fragments:
                row = conn.execute(
                    """SELECT clean_text,text_hash,source_section_id,source_order,chunk_index,
                              source_is_heading,source_location,heading_path_json,content_format,
                              content_payload_json,asset_manifest_json,visible_text_hash
                       FROM corpus_block WHERE block_id=?""",
                    (fragment.get("block_id"),),
                ).fetchone()
                row_heading_path = _json(row["heading_path_json"], []) if row is not None else []
                metadata_mismatch = row is not None and (
                    row_heading_path != fragment.get("heading_path", [])
                    or not _is_prefix(root, row_heading_path)
                    or row_heading_path[len(root):] != fragment.get("relative_heading_path", [])
                    or row["source_location"] != fragment.get("source_location", "")
                    or row["content_format"] != fragment.get("content_format", "plain_text")
                    or _json(row["content_payload_json"], {}) != fragment.get("content_payload", {})
                    or _json(row["asset_manifest_json"], []) != fragment.get("asset_manifest", [])
                    or (row["visible_text_hash"] or row["text_hash"])
                    != fragment.get("visible_text_hash", fragment.get("text_hash"))
                )
                if (
                    row is None
                    or row["clean_text"] != fragment.get("clean_text")
                    or row["text_hash"] != fragment.get("text_hash")
                    or row["source_section_id"] != fragment.get("source_section_id", "")
                    or _int(row["source_order"]) != _int(fragment.get("source_order"))
                    or _int(row["chunk_index"]) != _int(fragment.get("chunk_index"))
                    or bool(row["source_is_heading"])
                    != bool(fragment.get("source_is_heading", False))
                ):
                    issues.append({"code": "standard_text_not_verbatim", "blocking": True, "block_id": fragment.get("block_id")})
                if metadata_mismatch:
                    issues.append({"code": "standard_fragment_metadata_mismatch", "blocking": True, "block_id": fragment.get("block_id")})
            if ids:
                block_rows = []
                for block_id in ids:
                    row = conn.execute(
                        "SELECT rowid AS local_rowid,* FROM corpus_block WHERE block_id=?",
                        (block_id,),
                    ).fetchone()
                    if row is not None:
                        block = dict(row)
                        block["heading_path"] = _json(block.get("heading_path_json"), [])
                        block_rows.append(block)
                document_ids = {block["corpus_document_id"] for block in block_rows}
                all_blocks = _load_blocks(conn, allowed_block_ids)
                expected = [
                    block
                    for block in all_blocks
                    if block["corpus_document_id"] in document_ids
                    and _is_prefix(root, block["heading_path"])
                ]
                expected.sort(key=_block_order)
                if [block["block_id"] for block in expected] != ids:
                    issues.append(
                        {
                            "code": "standard_subtree_incomplete",
                            "blocking": True,
                            "item": item.get("original_name"),
                        }
                    )
    blocking = sum(1 for issue in issues if issue.get("blocking"))
    result = {
        "valid": blocking == 0,
        "blocking_issue_count": blocking,
        "manifest_id": manifest.get("manifest_id", ""),
        "manifest_hash": manifest.get("manifest_hash", ""),
        "calculated_manifest_hash": calculated_manifest_hash,
        "calculated_manifest_id": calculated_manifest_id,
        "package_id": manifest.get("package_id", ""),
        "package_content_hash": manifest.get("package_content_hash", ""),
        "issues": issues,
        "checks": [
            "scope display snapshot hash",
            "manifest hash and persisted manifest binding",
            "match run and complete standard package snapshot binding",
            "customer source order",
            "explicit pending supplement gaps",
            "complete subtree, block order and verbatim text/hash equality",
        ],
    }
    result["validation_hash"] = _canonical_hash(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_parser = subparsers.add_parser("capture-scope", help="freeze the uploaded scope display and source order locally")
    capture_parser.add_argument("database", type=Path)
    capture_parser.add_argument("project_code")
    capture_parser.add_argument("scope_payload", type=Path)
    capture_parser.add_argument("--output", type=Path)

    match_parser = subparsers.add_parser("match", help="match scope modules to unique standard solution subtrees")
    match_parser.add_argument("database", type=Path)
    match_parser.add_argument("project_code")
    match_parser.add_argument("--scope-snapshot-id", default="")
    match_parser.add_argument("--package-id", default="")
    match_parser.add_argument("--similar-threshold", type=float, default=0.6)
    match_parser.add_argument("--output-json", type=Path)
    match_parser.add_argument("--output-md", type=Path)

    decision_parser = subparsers.add_parser("apply-decisions", help="append reviewed similar/missing mapping decisions")
    decision_parser.add_argument("database", type=Path)
    decision_parser.add_argument("decisions", type=Path)
    decision_parser.add_argument("--output", type=Path)

    assembly_parser = subparsers.add_parser("assemble", help="assemble the raw scope snapshot and verbatim standard subtrees")
    assembly_parser.add_argument("database", type=Path)
    assembly_parser.add_argument("project_code")
    assembly_parser.add_argument("match_run_id")
    assembly_parser.add_argument("--output-json", type=Path)
    assembly_parser.add_argument("--output-md", type=Path)
    assembly_parser.add_argument("--output-scope-md", type=Path)
    assembly_parser.add_argument("--output-solution-md", type=Path)
    assembly_parser.add_argument(
        "--allow-unresolved-preview",
        action="store_true",
        help="emit a blocked working preview with 【待确认】 headings; never a complete assembly",
    )

    validate_parser = subparsers.add_parser("validate", help="validate a construction assembly manifest")
    validate_parser.add_argument("database", type=Path)
    validate_parser.add_argument("manifest", type=Path)
    validate_parser.add_argument("--output", type=Path)

    args = parser.parse_args()
    if args.command == "capture-scope":
        result = capture_scope_snapshot(args.database, args.project_code, load_json(args.scope_payload))
        _write_json(args.output, result)
    elif args.command == "match":
        result = match_scope(
            args.database,args.project_code,scope_snapshot_id=args.scope_snapshot_id,
            package_id=args.package_id,similar_threshold=args.similar_threshold,
        )
        _write_json(args.output_json, result)
        _write_text(args.output_md, _candidate_markdown(result))
    elif args.command == "apply-decisions":
        result = apply_decisions(args.database, load_json(args.decisions))
        _write_json(args.output, result)
    elif args.command == "assemble":
        result, markdown = assemble(
            args.database,
            args.project_code,
            args.match_run_id,
            allow_unresolved_preview=args.allow_unresolved_preview,
        )
        _write_json(args.output_json, result)
        _write_text(args.output_md, markdown)
        _write_text(args.output_scope_md, render_scope_fragment(result))
        _write_text(args.output_solution_md, render_solution_fragment(result))
    else:
        result = validate_manifest(args.database, load_json(args.manifest))
        _write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
