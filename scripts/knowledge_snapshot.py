#!/usr/bin/env python3
"""Validate project-local shared-knowledge snapshots before report generation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from knowledge_db import apply_migrations, connect, dump_json, now_iso, sha256_text


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class KnowledgeSnapshotError(RuntimeError):
    def __init__(self, reason: str, message: str, *, details: Any = None):
        super().__init__(message)
        self.reason = reason
        self.details = details


def snapshot_payload_hash(items: Iterable[tuple[str, str, str, dict[str, Any]]]) -> str:
    normalized = [
        {
            "item_type": item_type,
            "item_id": item_id,
            "item_hash": item_hash,
            "payload": payload,
        }
        for item_type, item_id, item_hash, payload in items
    ]
    normalized.sort(key=lambda item: (item["item_type"], item["item_id"]))
    return sha256_text(dump_json(normalized))


def _permission_scope(conn, snapshot: dict[str, Any], metadata: dict[str, Any]) -> str:
    configured = str(metadata.get("permission_scope") or "").strip()
    if configured:
        return configured
    if snapshot["source_type"] == "knowledge_package":
        row = conn.execute(
            "SELECT permission_scope FROM corpus_document WHERE version=? LIMIT 1",
            (snapshot["source_id"],),
        ).fetchone()
        return str(row[0]) if row else ""
    if snapshot["source_type"] == "policy_catalog":
        row = conn.execute(
            "SELECT permission_scope FROM policy_catalog WHERE catalog_id=? LIMIT 1",
            (snapshot["source_id"],),
        ).fetchone()
        return str(row[0]) if row else ""
    return str(metadata.get("permission_scope") or "public_policy_reference")


def _validate_item(item: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    if not item["item_id"] or not HASH_PATTERN.fullmatch(item["item_hash"]):
        return {}, "snapshot item has an invalid id or hash"
    try:
        payload = json.loads(item["payload_json"])
    except (TypeError, json.JSONDecodeError):
        return {}, "snapshot item payload is not valid JSON"
    if not isinstance(payload, dict):
        return {}, "snapshot item payload must be an object"
    kind = item["item_type"]
    if kind == "corpus_block":
        expected = str(payload.get("text_hash") or "")
        if expected != item["item_hash"] or sha256_text(str(payload.get("clean_text") or "")) != expected:
            return payload, "corpus block hash mismatch"
    elif kind == "product_capability":
        if sha256_text(dump_json(payload)) != item["item_hash"]:
            return payload, "product capability payload hash mismatch"
    elif kind == "policy_catalog_entry":
        if str(payload.get("row_hash") or "") != item["item_hash"]:
            return payload, "policy catalog row hash mismatch"
    elif kind == "policy_clause":
        if str(payload.get("text_hash") or "") != item["item_hash"]:
            return payload, "policy clause hash mismatch"
    return payload, None


def validate_snapshots(
    database: Path,
    project_code: str,
    *,
    package_ids: list[str] | None = None,
    catalog_ids: list[str] | None = None,
    permission_scopes: dict[str, list[str]] | None = None,
    allow_stale: bool = False,
    require_package: bool = True,
) -> dict[str, Any]:
    requested_packages = set(package_ids or [])
    requested_catalogs = set(catalog_ids or [])
    permissions = permission_scopes or {}
    allowed_statuses = {"current", "stale"} if allow_stale else {"current"}
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT project_id FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if not project:
            raise KnowledgeSnapshotError("project_not_found", f"project not found: {project_code}")
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM shared_knowledge_snapshot
                WHERE project_id=? AND snapshot_status IN ('current','stale')
                ORDER BY source_type,source_id,fetched_at DESC
                """,
                (project["project_id"],),
            )
        ]
        snapshots = []
        problems = []
        seen_sources: set[tuple[str, str]] = set()
        for row in rows:
            source_key = (row["source_type"], row["source_id"])
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            metadata = json.loads(row["metadata_json"] or "{}")
            reasons = []
            if row["snapshot_status"] not in allowed_statuses:
                reasons.append("stale snapshot is not allowed")
            if not row["source_id"] or not HASH_PATTERN.fullmatch(row["content_hash"]):
                reasons.append("snapshot source id or content hash is invalid")
            if metadata.get("sync_completed") is not True:
                reasons.append("snapshot is not marked as a completed synchronization")
            if metadata.get("integrity_status") != "valid":
                reasons.append("snapshot integrity status is missing or not valid")
            item_rows = [
                dict(item)
                for item in conn.execute(
                    """
                    SELECT item_type,item_id,item_hash,payload_json
                    FROM shared_knowledge_snapshot_item
                    WHERE snapshot_id=? ORDER BY item_type,item_id
                    """,
                    (row["snapshot_id"],),
                )
            ]
            parsed_items = []
            counts: dict[str, int] = {}
            for item in item_rows:
                payload, issue = _validate_item(item)
                if issue:
                    reasons.append(f"{item['item_type']}/{item['item_id']}: {issue}")
                parsed_items.append((item["item_type"], item["item_id"], item["item_hash"], payload))
                counts[item["item_type"]] = counts.get(item["item_type"], 0) + 1
            computed_payload_hash = snapshot_payload_hash(parsed_items)
            stored_payload_hash = str(metadata.get("snapshot_payload_hash") or "")
            if not HASH_PATTERN.fullmatch(stored_payload_hash):
                reasons.append("snapshot aggregate payload hash is missing or invalid")
            elif stored_payload_hash != computed_payload_hash:
                reasons.append("snapshot aggregate payload hash mismatch")
            if row["source_type"] == "knowledge_package":
                source_corpus_type = str(
                    metadata.get("source_corpus_type") or "legacy_unspecified"
                )
                if (
                    source_corpus_type == "legacy_unspecified"
                    and counts.get("product_capability", 0) > 0
                ):
                    source_corpus_type = "standard_solution"
                if counts.get("corpus_block", 0) < 1:
                    reasons.append("knowledge package snapshot has zero corpus blocks")
                if (
                    source_corpus_type == "standard_solution"
                    and counts.get("product_capability", 0) < 1
                ):
                    reasons.append(
                        "standard_solution snapshot has zero product capabilities"
                    )
                if source_corpus_type not in {
                    "standard_solution",
                    "reference_feasibility",
                    "generic_reference",
                }:
                    reasons.append(
                        "knowledge package snapshot lacks a supported source_corpus_type marker"
                    )
            elif row["source_type"] == "policy_catalog" and counts.get("policy_catalog_entry", 0) < 1:
                reasons.append("policy catalog snapshot has zero records")
            elif row["source_type"] == "policy_release" and counts.get("policy_clause", 0) < 1:
                reasons.append("policy release snapshot has zero clauses")
            permission = _permission_scope(conn, row, metadata)
            allowed = permissions.get(row["source_type"], [])
            if allowed and permission not in allowed:
                reasons.append(
                    f"permission scope mismatch: expected one of {allowed}, got {permission or '<empty>'}"
                )
            snapshot = {
                "snapshot_id": row["snapshot_id"],
                "source_type": row["source_type"],
                "source_id": row["source_id"],
                "content_hash": row["content_hash"],
                "snapshot_payload_hash": computed_payload_hash,
                "snapshot_status": row["snapshot_status"],
                "synced_at": row["fetched_at"],
                "permission_scope": permission,
                "source_corpus_type": str(
                    metadata.get("source_corpus_type") or ""
                ),
                "counts": counts,
                "valid": not reasons,
                "problems": reasons,
            }
            snapshots.append(snapshot)
            if reasons:
                problems.append({"snapshot_id": row["snapshot_id"], "reasons": reasons})

    valid = [item for item in snapshots if item["valid"]]
    package_snapshots = [item for item in valid if item["source_type"] == "knowledge_package"]
    catalog_snapshots = [item for item in valid if item["source_type"] == "policy_catalog"]
    visible_packages = {item["source_id"] for item in package_snapshots}
    visible_catalogs = {item["source_id"] for item in catalog_snapshots}
    missing_packages = sorted(requested_packages - visible_packages)
    missing_catalogs = sorted(requested_catalogs - visible_catalogs)
    if require_package and not package_snapshots:
        problems.append({"reason": "valid knowledge package snapshot not found"})
    if missing_packages:
        problems.append({"reason": "required knowledge packages missing", "ids": missing_packages})
    if missing_catalogs:
        problems.append({"reason": "required policy catalogs missing", "ids": missing_catalogs})
    if problems:
        raise KnowledgeSnapshotError(
            "knowledge_snapshot_invalid",
            "project knowledge snapshot failed integrity or selection validation",
            details={"problems": problems, "snapshots": snapshots},
        )
    counts = {
        "corpus_blocks": sum(item["counts"].get("corpus_block", 0) for item in valid),
        "capabilities": sum(item["counts"].get("product_capability", 0) for item in valid),
        "catalog_records": sum(item["counts"].get("policy_catalog_entry", 0) for item in valid),
        "policy_clauses": sum(item["counts"].get("policy_clause", 0) for item in valid),
    }
    return {
        "status": "valid",
        "validated_at": now_iso(),
        "snapshot_count": len(valid),
        "package_ids": sorted(visible_packages),
        "catalog_ids": sorted(visible_catalogs),
        "content_hashes": {
            item["source_id"]: item["content_hash"] for item in valid
        },
        "synced_at": {item["source_id"]: item["synced_at"] for item in valid},
        "permission_scopes": {
            item["source_id"]: item["permission_scope"] for item in valid
        },
        "counts": counts,
        "snapshots": valid,
    }
