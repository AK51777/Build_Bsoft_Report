#!/usr/bin/env python3
"""Sync explicitly selected remote MCP knowledge into a project SQLite snapshot."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from import_policy_catalog_sqlite import import_catalog as import_local_catalog
from import_standard_knowledge_pack import import_pack as import_local_pack
from knowledge_db import sha256_text
from knowledge_snapshot import validate_snapshots
from postgres_knowledge_db import canonical_json
from remote_knowledge_mcp_bridge import BridgeConfig, LocalCredentialFile, RemoteKnowledgeClient
from sync_postgres_knowledge_snapshot import (
    build_catalog_snapshot,
    build_pack_snapshot,
    record_snapshot,
)


@dataclass(frozen=True)
class _Column:
    name: str


class RemotePageCursor:
    """Small cursor adapter for the existing deterministic snapshot builders."""

    def __init__(self, connection: "RemotePageConnection"):
        self.connection = connection
        self.description: list[_Column] = []
        self._rows: list[tuple[Any, ...]] = []

    def __enter__(self) -> "RemotePageCursor":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False

    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> None:
        normalized = " ".join(statement.split())
        entity = self._entity(normalized)
        if entity == "capability-block":
            asset_id = self.connection.package_id
        else:
            raw_asset = params[0] if params else ""
            if isinstance(raw_asset, (list, tuple)):
                raise ValueError("remote snapshot query requires one explicit asset ID")
            asset_id = str(raw_asset)
        if not asset_id:
            raise ValueError(f"asset ID is required for {entity}")
        if entity in {
            "package-source",
            "corpus-document",
            "corpus-block",
            "capability",
        }:
            self.connection.package_id = asset_id
        rows = self.connection.fetch_all(entity, asset_id)
        columns = list(rows[0]) if rows else []
        self.description = [_Column(name) for name in columns]
        self._rows = [tuple(row.get(name) for name in columns) for row in rows]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    @staticmethod
    def _entity(statement: str) -> str:
        mappings = (
            ("runtime_package_source", "package-source"),
            ("runtime_corpus_document", "corpus-document"),
            ("runtime_corpus_block", "corpus-block"),
            ("runtime_capability_block", "capability-block"),
            ("runtime_product_capability", "capability"),
            ("runtime_policy_catalog_entry", "policy-catalog-entry"),
        )
        for marker, entity in mappings:
            if marker in statement:
                return entity
        raise ValueError("snapshot builder requested an unsupported remote runtime view")


class RemotePageConnection:
    def __init__(self, client: RemoteKnowledgeClient, *, page_size: int):
        self.client = client
        self.page_size = page_size
        self.package_id = ""

    def cursor(self) -> RemotePageCursor:
        return RemotePageCursor(self)

    def fetch_all(self, entity: str, asset_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            result = self.client.call_tool(
                "knowledge_page",
                {
                    "entity": entity,
                    "asset_id": asset_id,
                    "offset": offset,
                    "limit": self.page_size,
                },
            )
            if result.get("isError"):
                message = (result.get("structuredContent") or {}).get("message")
                raise RuntimeError(str(message or f"remote {entity} page failed"))
            payload = result.get("structuredContent") or {}
            page_rows = payload.get("rows")
            if not isinstance(page_rows, list) or not all(isinstance(row, dict) for row in page_rows):
                raise RuntimeError(f"remote {entity} page returned invalid rows")
            rows.extend(page_rows)
            next_offset = payload.get("next_offset")
            if next_offset is None:
                return rows
            next_offset = int(next_offset)
            if next_offset <= offset:
                raise RuntimeError(f"remote {entity} page did not advance next_offset")
            offset = next_offset


def _single_metadata(
    connection: RemotePageConnection,
    *,
    entity: str,
    asset_id: str,
) -> dict[str, Any]:
    rows = connection.fetch_all(entity, asset_id)
    if len(rows) != 1:
        raise ValueError(f"published {entity} {asset_id} was not found or is ambiguous")
    return rows[0]


def sync_remote(
    database: Path,
    project_code: str,
    client: RemoteKnowledgeClient,
    *,
    package_ids: list[str],
    catalog_ids: list[str],
    page_size: int,
    profile_name: str,
) -> dict[str, Any]:
    if not package_ids and not catalog_ids:
        raise ValueError("at least one --package-id or --catalog-id is required")
    connection = RemotePageConnection(client, page_size=page_size)
    package_results = []
    for package_id in package_ids:
        package = _single_metadata(
            connection,
            entity="knowledge-package",
            asset_id=package_id,
        )
        payload = build_pack_snapshot(connection, "remote_mcp", package)
        if not payload["corpus"]["blocks"]:
            raise ValueError(f"knowledge package {package_id} has zero published blocks")
        local_result = import_local_pack(database, payload)
        items = [
            ("corpus_block", block["block_id"], block["text_hash"], block)
            for block in payload["corpus"]["blocks"]
        ] + [
            (
                "product_capability",
                capability["capability_id"],
                sha256_text(canonical_json(capability)),
                capability,
            )
            for capability in payload["capabilities"]
        ]
        snapshot_id = record_snapshot(
            database,
            project_code,
            source_type="knowledge_package",
            source_id=package_id,
            content_hash=payload["server_content_hash"],
            server_schema="remote_mcp",
            items=items,
            metadata={
                "title": payload["title"],
                "blocks": len(payload["corpus"]["blocks"]),
                "capabilities": len(payload["capabilities"]),
                "transport": "remote_readonly_mcp",
                "review_summary": payload.get("review_summary", {}),
            },
            permission_scope=payload["permission_scope"],
            profile_name=profile_name,
        )
        package_results.append({**local_result, "snapshot_id": snapshot_id})

    catalog_results = []
    for catalog_id in catalog_ids:
        catalog = _single_metadata(
            connection,
            entity="policy-catalog",
            asset_id=catalog_id,
        )
        payload = build_catalog_snapshot(connection, "remote_mcp", catalog)
        if not payload["records"]:
            raise ValueError(f"policy catalog {catalog_id} has zero published records")
        local_result = import_local_catalog(database, payload)
        snapshot_id = record_snapshot(
            database,
            project_code,
            source_type="policy_catalog",
            source_id=catalog_id,
            content_hash=payload["content_hash"],
            server_schema="remote_mcp",
            items=[
                (
                    "policy_catalog_entry",
                    record["catalog_entry_id"],
                    record["row_hash"],
                    record,
                )
                for record in payload["records"]
            ],
            metadata={
                "title": payload["title"],
                "records": len(payload["records"]),
                "candidate_only": True,
                "transport": "remote_readonly_mcp",
            },
            permission_scope=payload["permission_scope"],
            profile_name=profile_name,
        )
        catalog_results.append({**local_result, "snapshot_id": snapshot_id})

    validation = validate_snapshots(
        database,
        project_code,
        package_ids=package_ids,
        catalog_ids=catalog_ids,
        permission_scopes={},
        allow_stale=False,
    )
    return {
        "database": str(database.resolve()),
        "project_code": project_code,
        "knowledge_packages": package_results,
        "policy_catalogs": catalog_results,
        "selected_package_ids": package_ids,
        "selected_catalog_ids": catalog_ids,
        "transport": "remote_readonly_mcp",
        "snapshot_validation": validation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--package-id", action="append", default=[])
    parser.add_argument("--catalog-id", action="append", default=[])
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--profile-name", default="remote-readonly")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = BridgeConfig.load(args.config)
    if not 1 <= args.page_size <= config.max_query_limit:
        raise ValueError(f"page-size must be between 1 and {config.max_query_limit}")
    credentials = LocalCredentialFile(config.credential_file)
    client = RemoteKnowledgeClient(config, credentials)
    result = sync_remote(
        args.sqlite_database,
        args.project_code,
        client,
        package_ids=args.package_id,
        catalog_ids=args.catalog_id,
        page_size=args.page_size,
        profile_name=args.profile_name,
    )
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
