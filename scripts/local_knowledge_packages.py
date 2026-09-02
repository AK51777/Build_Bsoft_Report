#!/usr/bin/env python3
"""Build, validate, query, install, and project-sync split local knowledge packages."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import secrets
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from import_policy_catalog_postgres import normalize_catalog
from import_policy_catalog_sqlite import import_catalog
from import_standard_knowledge_pack import import_pack, validate_pack
from import_verified_policies_postgres import validate_policies
from ingest_document_standards import ingest as ingest_document_standards
from ingest_policies import ingest as ingest_policies
from knowledge_db import (
    apply_migrations,
    connect,
    connect_readonly,
    dump_json,
    load_json,
    now_iso,
    sha256_file,
    sha256_text,
    stable_id,
)
from knowledge_snapshot import validate_snapshots
from query_local_knowledge import query_local
from sync_postgres_knowledge_snapshot import record_snapshot


CONFIG_ENV = "MEDICAL_REPORT_LOCAL_KB_CONFIG"
DEFAULT_CONFIG_RELATIVE = Path(".codex") / "config" / "medical-report-local-kb.json"
PACKAGE_KINDS = ("standard", "policy")
QUERY_DATABASE = {
    "status": None,
    "corpus": "standard",
    "capability": "standard",
    "policy-catalog": "policy",
    "policy-clause": "policy",
    "document-standard": "policy",
}
RELEASE_SCHEMA_VERSION = "1.0"
MANAGER_VERSION = "local-knowledge-packages-v1"


RELEASE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS local_knowledge_release (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  schema_version TEXT NOT NULL,
  knowledge_kind TEXT NOT NULL CHECK(knowledge_kind IN ('standard','policy')),
  release_id TEXT NOT NULL UNIQUE,
  release_version TEXT NOT NULL,
  title TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  permission_scope TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  update_cadence TEXT NOT NULL,
  source_ids_json TEXT NOT NULL,
  counts_json TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  manager_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS local_knowledge_payload (
  payload_name TEXT PRIMARY KEY,
  payload_hash TEXT NOT NULL,
  payload_gzip BLOB NOT NULL
);
"""


class LocalKnowledgeError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return dump_json(value)


def _payload_hash(value: Any) -> str:
    return sha256_text(_canonical(value))


def _payload_set_hash(payloads: Mapping[str, Any]) -> str:
    return sha256_text(
        _canonical({name: _payload_hash(value) for name, value in sorted(payloads.items())})
    )


def _encode_payload(value: Any) -> bytes:
    return gzip.compress(_canonical(value).encode("utf-8"), compresslevel=9, mtime=0)


def _decode_payload(value: bytes) -> Any:
    return json.loads(gzip.decompress(value).decode("utf-8"))


def _cleanup_database_files(path: Path) -> None:
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if candidate.exists():
            candidate.unlink()


def _finalize_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("VACUUM")
    finally:
        connection.close()


def _database_counts(database: Path) -> dict[str, int]:
    with connect_readonly(database) as connection:
        row = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM corpus_block) AS corpus_blocks,
              (SELECT COUNT(*) FROM corpus_block WHERE review_status='approved') AS approved_corpus_blocks,
              (SELECT COUNT(*) FROM product_capability WHERE review_status='approved') AS capabilities,
              (SELECT COUNT(*) FROM policy_catalog_entry WHERE entry_status='active') AS catalog_records,
              (SELECT COUNT(*) FROM policy_clause WHERE verification_status='verified') AS verified_policy_clauses,
              (SELECT COUNT(*) FROM document_standard WHERE verification_status='verified' AND status IN ('active','candidate')) AS verified_document_standards
            """
        ).fetchone()
    return {key: int(row[key]) for key in row.keys()}


def _write_release(
    database: Path,
    *,
    knowledge_kind: str,
    release_version: str,
    title: str,
    permission_scope: str,
    update_cadence: str,
    source_ids: list[str],
    payloads: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    counts = _database_counts(database)
    content_hash = _payload_set_hash(payloads)
    release_id = stable_id("LOCALKBR", knowledge_kind, release_version, content_hash)
    with connect(database) as connection:
        connection.executescript(RELEASE_SCHEMA_SQL)
        connection.execute("DELETE FROM local_knowledge_release")
        connection.execute("DELETE FROM local_knowledge_payload")
        connection.execute(
            """
            INSERT INTO local_knowledge_release (
              singleton,schema_version,knowledge_kind,release_id,release_version,title,
              content_hash,permission_scope,generated_at,update_cadence,source_ids_json,
              counts_json,metadata_json,manager_version
            ) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                RELEASE_SCHEMA_VERSION,
                knowledge_kind,
                release_id,
                release_version,
                title,
                content_hash,
                permission_scope,
                now_iso(),
                update_cadence,
                dump_json(source_ids),
                dump_json(counts),
                dump_json(dict(metadata)),
                MANAGER_VERSION,
            ),
        )
        connection.executemany(
            """
            INSERT INTO local_knowledge_payload(payload_name,payload_hash,payload_gzip)
            VALUES (?,?,?)
            """,
            [
                (name, _payload_hash(payload), _encode_payload(payload))
                for name, payload in sorted(payloads.items())
            ],
        )
        connection.commit()
    return {
        "release_id": release_id,
        "release_version": release_version,
        "content_hash": content_hash,
        "counts": counts,
    }


def _build_atomic(output: Path, builder) -> dict[str, Any]:
    output = output.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"local knowledge package already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{secrets.token_hex(6)}.tmp")
    _cleanup_database_files(temporary)
    try:
        result = builder(temporary)
        _finalize_database(temporary)
        validated = validate_local_package(temporary, expected_kind=result["knowledge_kind"])
        os.replace(temporary, output)
        validated["database"] = str(output)
        validated["file_sha256"] = sha256_file(output)
        validated["file_bytes"] = output.stat().st_size
        return validated
    finally:
        _cleanup_database_files(temporary)


def build_standard_package(
    source_pack: Path,
    output: Path,
    *,
    release_version: str,
) -> dict[str, Any]:
    payload = load_json(source_pack.resolve())
    validate_pack(payload)

    def builder(database: Path) -> dict[str, Any]:
        imported = import_pack(database, payload)
        coverage = (payload.get("review_summary") or {}).get("standard_solution_coverage") or {}
        release = _write_release(
            database,
            knowledge_kind="standard",
            release_version=release_version,
            title=str(payload.get("title") or payload["package_id"]),
            permission_scope=str(payload["permission_scope"]),
            update_cadence="monthly_or_release",
            source_ids=[str(payload["package_id"])],
            payloads={"standard_pack": payload},
            metadata={
                "package_id": payload["package_id"],
                "package_schema_version": payload["schema_version"],
                "source_corpus_type": payload["corpus"].get("source_corpus_type", ""),
                "review_summary": payload.get("review_summary", {}),
                "coverage_status": coverage.get("status", ""),
                "source_file_hashes": {
                    item.get("role", ""): item.get("sha256", "")
                    for item in payload.get("source_files", [])
                },
            },
        )
        return {**imported, **release, "knowledge_kind": "standard"}

    return _build_atomic(output, builder)


def build_policy_package(
    output: Path,
    *,
    release_version: str,
    catalog_path: Path | None = None,
    verified_policies_path: Path | None = None,
    document_standards_path: Path | None = None,
) -> dict[str, Any]:
    payloads: dict[str, Any] = {}
    if catalog_path:
        payloads["policy_catalog"] = normalize_catalog(load_json(catalog_path.resolve()))
    if verified_policies_path:
        policies = load_json(verified_policies_path.resolve())
        validate_policies(policies, publish=True)
        payloads["verified_policies"] = policies
    if document_standards_path:
        standards = load_json(document_standards_path.resolve())
        if not isinstance(standards.get("standards"), list) or not standards["standards"]:
            raise ValueError("document standards payload must contain standards")
        payloads["document_standards"] = standards
    if not payloads:
        raise ValueError("policy package requires a catalog, verified policies, or document standards")

    def builder(database: Path) -> dict[str, Any]:
        apply_result: dict[str, Any] = {}
        source_ids: list[str] = []
        permission_scopes: list[str] = []
        if "policy_catalog" in payloads:
            catalog = payloads["policy_catalog"]
            apply_result["policy_catalog"] = import_catalog(database, catalog)
            source_ids.append(str(catalog["catalog_id"]))
            permission_scopes.append(str(catalog["permission_scope"]))
        if "verified_policies" in payloads:
            policies = payloads["verified_policies"]
            apply_result["verified_policies"] = ingest_policies(database, policies)
            source_ids.append(stable_id("POLICYRELEASE", _payload_hash(policies)))
            permission_scopes.append("public_policy_reference")
        if "document_standards" in payloads:
            standards = payloads["document_standards"]
            apply_result["document_standards"] = ingest_document_standards(database, standards)
            source_ids.append(stable_id("DOCSTDRELEASE", _payload_hash(standards)))
            permission_scopes.append("public_standard_reference")
        release = _write_release(
            database,
            knowledge_kind="policy",
            release_version=release_version,
            title="医疗可研政策与标准依据本地知识包",
            permission_scope="+".join(sorted(set(permission_scopes))),
            update_cadence="weekly_or_policy_event",
            source_ids=source_ids,
            payloads=payloads,
            metadata={
                "candidate_catalog_only": bool(
                    "policy_catalog" in payloads and "verified_policies" not in payloads
                ),
                "formal_policy_gate": "verified_policy_clauses_only",
                "contains_document_standards": "document_standards" in payloads,
            },
        )
        return {**apply_result, **release, "knowledge_kind": "policy"}

    return _build_atomic(output, builder)


def _release_row(connection: sqlite3.Connection) -> dict[str, Any]:
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_knowledge_release'"
    ).fetchone():
        raise LocalKnowledgeError("local knowledge release metadata is missing")
    rows = connection.execute("SELECT * FROM local_knowledge_release").fetchall()
    if len(rows) != 1:
        raise LocalKnowledgeError("local knowledge package must contain exactly one release")
    row = dict(rows[0])
    row["source_ids"] = json.loads(row.pop("source_ids_json") or "[]")
    row["counts"] = json.loads(row.pop("counts_json") or "{}")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _read_payloads(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT payload_name,payload_hash,payload_gzip FROM local_knowledge_payload ORDER BY payload_name"
    ).fetchall()
    payloads: dict[str, Any] = {}
    for row in rows:
        payload = _decode_payload(row["payload_gzip"])
        if _payload_hash(payload) != row["payload_hash"]:
            raise LocalKnowledgeError(f"local payload hash mismatch: {row['payload_name']}")
        payloads[str(row["payload_name"])] = payload
    return payloads


def validate_local_package(database: Path, *, expected_kind: str | None = None) -> dict[str, Any]:
    database = database.expanduser().resolve()
    if not database.is_file():
        raise FileNotFoundError(f"local knowledge package not found: {database}")
    with connect_readonly(database) as connection:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise LocalKnowledgeError(f"SQLite quick_check failed: {quick_check}")
        release = _release_row(connection)
        if release["schema_version"] != RELEASE_SCHEMA_VERSION:
            raise LocalKnowledgeError("unsupported local knowledge release schema")
        if expected_kind and release["knowledge_kind"] != expected_kind:
            raise LocalKnowledgeError(
                f"local package kind mismatch: expected {expected_kind}, got {release['knowledge_kind']}"
            )
        payloads = _read_payloads(connection)
    computed_hash = _payload_set_hash(payloads)
    if computed_hash != release["content_hash"]:
        raise LocalKnowledgeError("local knowledge release content hash mismatch")
    actual_counts = _database_counts(database)
    if actual_counts != release["counts"]:
        raise LocalKnowledgeError("local knowledge release counts do not match SQLite content")
    if release["knowledge_kind"] == "standard":
        if set(payloads) != {"standard_pack"}:
            raise LocalKnowledgeError("standard package must contain one standard_pack payload")
        validate_pack(payloads["standard_pack"])
        coverage = (
            (payloads["standard_pack"].get("review_summary") or {}).get(
                "standard_solution_coverage"
            )
            or {}
        )
        if coverage.get("status") != "pass":
            raise LocalKnowledgeError("standard package coverage status is not pass")
        if actual_counts["approved_corpus_blocks"] < 1 or actual_counts["capabilities"] < 1:
            raise LocalKnowledgeError("standard package has no approved blocks or capabilities")
    elif release["knowledge_kind"] == "policy":
        if not payloads:
            raise LocalKnowledgeError("policy package has no payloads")
        if not any(
            actual_counts[key] > 0
            for key in ("catalog_records", "verified_policy_clauses", "verified_document_standards")
        ):
            raise LocalKnowledgeError("policy package has no usable catalog, clauses, or standards")
    return {
        "status": "valid",
        "database": str(database),
        "file_sha256": sha256_file(database),
        "file_bytes": database.stat().st_size,
        "release": release,
        "payload_names": sorted(payloads),
        "counts": actual_counts,
        "warnings": (
            ["formal_policy_clauses_empty"]
            if release["knowledge_kind"] == "policy"
            and actual_counts["verified_policy_clauses"] == 0
            else []
        ),
    }


def _default_config_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / DEFAULT_CONFIG_RELATIVE


def load_config(
    explicit: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    env = environ if environ is not None else os.environ
    raw = explicit or (Path(env[CONFIG_ENV]) if env.get(CONFIG_ENV) else _default_config_path(home))
    path = raw.expanduser().resolve()
    payload = load_json(path)
    if payload.get("schema_version") != "1.0" or not isinstance(payload.get("packages"), dict):
        raise ValueError("unsupported or incomplete local knowledge config")
    packages: dict[str, Any] = {}
    for kind in PACKAGE_KINDS:
        value = payload["packages"].get(kind)
        if not isinstance(value, dict) or not str(value.get("path") or "").strip():
            raise ValueError(f"local knowledge config is missing packages.{kind}.path")
        candidate = Path(os.path.expandvars(str(value["path"]))).expanduser()
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        packages[kind] = {
            "path": candidate.resolve(),
            "max_age_days": int(value.get("max_age_days", 45 if kind == "standard" else 8)),
            "required": bool(value.get("required", True)),
        }
        if packages[kind]["max_age_days"] < 1:
            raise ValueError(f"packages.{kind}.max_age_days must be positive")
    return {"config_path": path, "packages": packages}


def package_status(config: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    results: dict[str, Any] = {}
    overall = "ready"
    for kind in PACKAGE_KINDS:
        item = config["packages"][kind]
        try:
            validated = validate_local_package(item["path"], expected_kind=kind)
            generated = datetime.fromisoformat(validated["release"]["generated_at"])
            if generated.tzinfo is None:
                generated = generated.replace(tzinfo=timezone.utc)
            age_days = max(0, (now - generated.astimezone(timezone.utc)).days)
            due = age_days > item["max_age_days"]
            results[kind] = {
                **validated,
                "age_days": age_days,
                "max_age_days": item["max_age_days"],
                "update_status": "update_due" if due else "current",
            }
            if due and overall == "ready":
                overall = "update_due"
        except Exception as exc:
            results[kind] = {
                "status": "missing_or_invalid",
                "database": str(item["path"]),
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
            if item["required"]:
                overall = "blocked"
    return {
        "status": overall,
        "config_path": str(config["config_path"]),
        "packages": results,
        "remote_mcp_retained": True,
    }


def _query_document_standards(
    database: Path,
    *,
    search: str,
    document_type: str,
    limit: int,
) -> dict[str, Any]:
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    with connect_readonly(database) as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM document_standard
                WHERE verification_status='verified' AND status IN ('active','candidate')
                ORDER BY jurisdiction_code,title,version,standard_id
                """
            )
        ]
    search_folded = search.casefold().strip()
    selected = []
    for row in rows:
        if document_type and row["document_type"] != document_type:
            continue
        haystack = " ".join(
            str(row.get(key) or "")
            for key in ("title", "authority_name", "jurisdiction_name", "version")
        ).casefold()
        if search_folded and search_folded not in haystack:
            continue
        selected.append(row)
    return {"kind": "document-standard", "count": len(selected[:limit]), "rows": selected[:limit]}


def query_packages(config: dict[str, Any], *, kind: str, **filters: Any) -> dict[str, Any]:
    if kind == "status":
        return package_status(config)
    package_kind = QUERY_DATABASE[kind]
    package = config["packages"][package_kind]
    validated = validate_local_package(package["path"], expected_kind=package_kind)
    if kind == "document-standard":
        result = _query_document_standards(
            package["path"],
            search=str(filters.get("search") or ""),
            document_type=str(filters.get("document_type") or ""),
            limit=int(filters.get("limit", 50)),
        )
    else:
        result = query_local(
            package["path"],
            kind=kind,
            package_ids=list(filters.get("package_ids") or []),
            catalog_ids=list(filters.get("catalog_ids") or []),
            document_type=str(filters.get("document_type") or ""),
            project_type=str(filters.get("project_type") or ""),
            section_role=str(filters.get("section_role") or ""),
            module_code=str(filters.get("module_code") or ""),
            topic=str(filters.get("topic") or ""),
            reuse_class=str(filters.get("reuse_class") or ""),
            permission_scope=str(filters.get("permission_scope") or ""),
            applicable_version=str(filters.get("applicable_version") or ""),
            tags=list(filters.get("tags") or []),
            prerequisites=list(filters.get("prerequisites") or []),
            search=str(filters.get("search") or ""),
            limit=int(filters.get("limit", 50)),
            migrate=False,
        )
    return {
        **result,
        "source": "shared_local_knowledge_package",
        "knowledge_kind": package_kind,
        "release_id": validated["release"]["release_id"],
        "release_version": validated["release"]["release_version"],
        "release_content_hash": validated["release"]["content_hash"],
    }


def install_local_package(
    config: dict[str, Any],
    *,
    kind: str,
    candidate: Path,
    expected_sha256: str,
) -> dict[str, Any]:
    if kind not in PACKAGE_KINDS:
        raise ValueError(f"unsupported local package kind: {kind}")
    candidate = candidate.expanduser().resolve()
    expected_sha256 = expected_sha256.strip().lower()
    if len(expected_sha256) != 64 or any(character not in "0123456789abcdef" for character in expected_sha256):
        raise ValueError("expected_sha256 must be a lowercase SHA-256 hex digest")
    actual_hash = sha256_file(candidate)
    if actual_hash != expected_sha256:
        raise LocalKnowledgeError("candidate file SHA-256 does not match the expected digest")
    validated = validate_local_package(candidate, expected_kind=kind)
    target = config["packages"][kind]["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if candidate == target:
        raise ValueError("candidate and installed target must be different files")
    if target.exists() and sha256_file(target) == actual_hash:
        return {**validated, "status": "unchanged", "kind": kind, "target": str(target)}
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(6)}.tmp")
    backup = target.with_name(f"{target.stem}.previous{target.suffix}")
    _cleanup_database_files(temporary)
    replaced_existing = target.exists()
    try:
        shutil.copyfile(candidate, temporary)
        if sha256_file(temporary) != expected_sha256:
            raise LocalKnowledgeError("copied candidate SHA-256 changed before installation")
        validate_local_package(temporary, expected_kind=kind)
        if replaced_existing:
            os.replace(target, backup)
        try:
            os.replace(temporary, target)
        except Exception:
            if replaced_existing and backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
    finally:
        _cleanup_database_files(temporary)
    installed = validate_local_package(target, expected_kind=kind)
    return {
        **installed,
        "status": "installed",
        "kind": kind,
        "target": str(target),
        "backup": str(backup) if replaced_existing else "",
    }


def _load_package_payload(database: Path, name: str) -> Any:
    with connect_readonly(database) as connection:
        row = connection.execute(
            "SELECT payload_hash,payload_gzip FROM local_knowledge_payload WHERE payload_name=?",
            (name,),
        ).fetchone()
    if not row:
        return None
    payload = _decode_payload(row["payload_gzip"])
    if _payload_hash(payload) != row["payload_hash"]:
        raise LocalKnowledgeError(f"local payload hash mismatch: {name}")
    return payload


def _assert_project_not_pinned_to_other_release(
    database: Path,
    project_code: str,
    expected: Mapping[str, tuple[str, str]],
) -> None:
    with connect(database) as connection:
        apply_migrations(connection)
        project = connection.execute(
            "SELECT project_id FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if not project:
            raise ValueError(f"project not found: {project_code}")
        rows = connection.execute(
            """
            SELECT source_type,source_id,content_hash FROM shared_knowledge_snapshot
            WHERE project_id=? AND snapshot_status='current'
            """,
            (project["project_id"],),
        ).fetchall()
    current = {str(row["source_type"]): (str(row["source_id"]), str(row["content_hash"])) for row in rows}
    conflicts = {
        source_type: {"current": current[source_type], "requested": requested}
        for source_type, requested in expected.items()
        if source_type in current and current[source_type] != requested
    }
    if conflicts:
        raise LocalKnowledgeError(
            "project is pinned to a different knowledge release; create a new project or run an explicit reviewed rebase",
        )


def _policy_clause_items(payload: dict[str, Any]) -> list[tuple[str, str, str, dict[str, Any]]]:
    items = []
    for policy in payload.get("policies", []):
        if policy.get("verification_status") != "verified":
            continue
        policy_id = policy.get("policy_id") or stable_id(
            "POLICY", policy.get("title"), policy.get("document_no"), policy.get("issuer")
        )
        for clause in policy.get("clauses", []):
            if clause.get("verification_status", policy.get("verification_status")) != "verified":
                continue
            original = str(clause.get("original_text") or "")
            clause_id = clause.get("clause_id") or stable_id(
                "CLAUSE", policy_id, clause.get("article_path", ""), original
            )
            item = {**clause, "clause_id": clause_id, "policy_id": policy_id, "text_hash": sha256_text(original)}
            items.append(("policy_clause", clause_id, item["text_hash"], item))
    return items


def _document_standard_items(payload: dict[str, Any]) -> list[tuple[str, str, str, dict[str, Any]]]:
    items = []
    for source in payload.get("standards", []):
        item = dict(source)
        item_id = item.get("standard_id") or stable_id(
            "DOCSTD",
            item.get("document_type"),
            item.get("jurisdiction_code"),
            item.get("title"),
            item.get("version"),
        )
        item["standard_id"] = item_id
        item_hash = sha256_text(dump_json(item))
        items.append(("document_standard", item_id, item_hash, item))
    return items


def sync_to_project(
    config: dict[str, Any],
    *,
    project_database: Path,
    project_code: str,
) -> dict[str, Any]:
    project_database = project_database.expanduser().resolve()
    standard_status = validate_local_package(config["packages"]["standard"]["path"], expected_kind="standard")
    policy_status = validate_local_package(config["packages"]["policy"]["path"], expected_kind="policy")
    standard_pack = _load_package_payload(config["packages"]["standard"]["path"], "standard_pack")
    catalog = _load_package_payload(config["packages"]["policy"]["path"], "policy_catalog")
    policies = _load_package_payload(config["packages"]["policy"]["path"], "verified_policies")
    standards = _load_package_payload(config["packages"]["policy"]["path"], "document_standards")
    if not standard_pack:
        raise LocalKnowledgeError("standard local package is missing standard_pack payload")
    standard_content_hash = _payload_hash(standard_pack)
    expected: dict[str, tuple[str, str]] = {
        "knowledge_package": (str(standard_pack["package_id"]), standard_content_hash)
    }
    if catalog:
        catalog = normalize_catalog(catalog)
        expected["policy_catalog"] = (str(catalog["catalog_id"]), str(catalog["content_hash"]))
    if policies:
        expected["policy_release"] = (
            stable_id("POLICYRELEASE", _payload_hash(policies)),
            _payload_hash(policies),
        )
    if standards:
        expected["document_standard"] = (
            stable_id("DOCSTDRELEASE", _payload_hash(standards)),
            _payload_hash(standards),
        )
    _assert_project_not_pinned_to_other_release(project_database, project_code, expected)

    standard_import = import_pack(project_database, standard_pack)
    standard_items = [
        (
            "corpus_block",
            block["block_id"],
            str(block.get("text_hash") or sha256_text(str(block.get("clean_text") or ""))),
            {**block, "text_hash": str(block.get("text_hash") or sha256_text(str(block.get("clean_text") or "")))},
        )
        for block in standard_pack["corpus"]["blocks"]
    ] + [
        (
            "product_capability",
            capability["capability_id"],
            sha256_text(dump_json(capability)),
            capability,
        )
        for capability in standard_pack["capabilities"]
    ]
    standard_snapshot = record_snapshot(
        project_database,
        project_code,
        source_type="knowledge_package",
        source_id=standard_pack["package_id"],
        content_hash=standard_content_hash,
        server_schema="local_shared_sqlite",
        items=standard_items,
        metadata={
            "title": standard_pack.get("title", ""),
            "blocks": len(standard_pack["corpus"]["blocks"]),
            "capabilities": len(standard_pack["capabilities"]),
            "source_corpus_type": standard_pack["corpus"].get("source_corpus_type", "standard_solution"),
            "review_summary": standard_pack.get("review_summary", {}),
            "transport": "local_shared_sqlite",
            "release_id": standard_status["release"]["release_id"],
        },
        permission_scope=standard_pack["permission_scope"],
        profile_name="local-split-packages",
    )

    results: dict[str, Any] = {
        "standard": {**standard_import, "snapshot_id": standard_snapshot},
        "policy": {},
    }
    catalog_ids: list[str] = []
    if catalog:
        catalog_import = import_catalog(project_database, catalog)
        catalog_snapshot = record_snapshot(
            project_database,
            project_code,
            source_type="policy_catalog",
            source_id=catalog["catalog_id"],
            content_hash=catalog["content_hash"],
            server_schema="local_shared_sqlite",
            items=[
                ("policy_catalog_entry", row["catalog_entry_id"], row["row_hash"], row)
                for row in catalog["records"]
            ],
            metadata={
                "title": catalog["title"],
                "records": len(catalog["records"]),
                "candidate_only": True,
                "transport": "local_shared_sqlite",
                "release_id": policy_status["release"]["release_id"],
            },
            permission_scope=catalog["permission_scope"],
            profile_name="local-split-packages",
        )
        results["policy"]["catalog"] = {**catalog_import, "snapshot_id": catalog_snapshot}
        catalog_ids.append(catalog["catalog_id"])
    if policies:
        policy_import = ingest_policies(project_database, policies)
        clause_items = _policy_clause_items(policies)
        if clause_items:
            policy_hash = _payload_hash(policies)
            policy_source = stable_id("POLICYRELEASE", policy_hash)
            policy_snapshot = record_snapshot(
                project_database,
                project_code,
                source_type="policy_release",
                source_id=policy_source,
                content_hash=policy_hash,
                server_schema="local_shared_sqlite",
                items=clause_items,
                metadata={
                    "policies": policy_import["policies"],
                    "clauses": policy_import["clauses"],
                    "transport": "local_shared_sqlite",
                    "release_id": policy_status["release"]["release_id"],
                },
                permission_scope="public_policy_reference",
                profile_name="local-split-packages",
            )
            results["policy"]["verified_policies"] = {
                **policy_import,
                "snapshot_id": policy_snapshot,
            }
    if standards:
        standard_basis_import = ingest_document_standards(project_database, standards)
        standard_items = _document_standard_items(standards)
        standard_hash = _payload_hash(standards)
        document_standard_snapshot = record_snapshot(
            project_database,
            project_code,
            source_type="document_standard",
            source_id=stable_id("DOCSTDRELEASE", standard_hash),
            content_hash=standard_hash,
            server_schema="local_shared_sqlite",
            items=standard_items,
            metadata={
                "standards": standard_basis_import["standards"],
                "transport": "local_shared_sqlite",
                "release_id": policy_status["release"]["release_id"],
            },
            permission_scope="public_standard_reference",
            profile_name="local-split-packages",
        )
        results["policy"]["document_standards"] = {
            **standard_basis_import,
            "snapshot_id": document_standard_snapshot,
        }
    validation = validate_snapshots(
        project_database,
        project_code,
        package_ids=[standard_pack["package_id"]],
        catalog_ids=catalog_ids,
        allow_stale=False,
    )
    return {
        "database": str(project_database),
        "project_code": project_code,
        "transport": "local_shared_sqlite",
        "remote_mcp_retained": True,
        "imports": results,
        "snapshot_validation": validation,
    }


def _write_output(value: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    standard = subparsers.add_parser("build-standard", help="Build standard-knowledge.sqlite")
    standard.add_argument("source_pack", type=Path)
    standard.add_argument("output_database", type=Path)
    standard.add_argument("--release-version", required=True)
    standard.add_argument("--output", type=Path)

    policy = subparsers.add_parser("build-policy", help="Build policy-knowledge.sqlite")
    policy.add_argument("output_database", type=Path)
    policy.add_argument("--release-version", required=True)
    policy.add_argument("--catalog", type=Path)
    policy.add_argument("--verified-policies", type=Path)
    policy.add_argument("--document-standards", type=Path)
    policy.add_argument("--output", type=Path)

    status = subparsers.add_parser("status", help="Validate configured local knowledge packages")
    status.add_argument("--config", type=Path)
    status.add_argument("--output", type=Path)

    query = subparsers.add_parser("query", help="Query one configured local package")
    query.add_argument("kind", choices=tuple(QUERY_DATABASE))
    query.add_argument("--config", type=Path)
    query.add_argument("--package-id", action="append", default=[])
    query.add_argument("--catalog-id", action="append", default=[])
    query.add_argument("--document-type", default="")
    query.add_argument("--project-type", default="")
    query.add_argument("--section-role", default="")
    query.add_argument("--module-code", default="")
    query.add_argument("--topic", default="")
    query.add_argument("--reuse-class", choices=("", "A", "B", "C", "D"), default="")
    query.add_argument("--permission-scope", default="")
    query.add_argument("--applicable-version", default="")
    query.add_argument("--tag", action="append", default=[])
    query.add_argument("--prerequisite", action="append", default=[])
    query.add_argument("--search", default="")
    query.add_argument("--limit", type=int, default=50)
    query.add_argument("--output", type=Path)

    install = subparsers.add_parser("install", help="Atomically install one verified package file")
    install.add_argument("kind", choices=PACKAGE_KINDS)
    install.add_argument("candidate", type=Path)
    install.add_argument("--expected-sha256", required=True)
    install.add_argument("--config", type=Path)
    install.add_argument("--output", type=Path)

    sync = subparsers.add_parser("sync-project", help="Pin configured packages into one project snapshot")
    sync.add_argument("project_database", type=Path)
    sync.add_argument("project_code")
    sync.add_argument("--config", type=Path)
    sync.add_argument("--output", type=Path)

    args = parser.parse_args()
    if args.command == "build-standard":
        result = build_standard_package(
            args.source_pack,
            args.output_database,
            release_version=args.release_version,
        )
    elif args.command == "build-policy":
        result = build_policy_package(
            args.output_database,
            release_version=args.release_version,
            catalog_path=args.catalog,
            verified_policies_path=args.verified_policies,
            document_standards_path=args.document_standards,
        )
    else:
        config = load_config(args.config)
        if args.command == "status":
            result = package_status(config)
        elif args.command == "query":
            result = query_packages(
                config,
                kind=args.kind,
                package_ids=args.package_id,
                catalog_ids=args.catalog_id,
                document_type=args.document_type,
                project_type=args.project_type,
                section_role=args.section_role,
                module_code=args.module_code,
                topic=args.topic,
                reuse_class=args.reuse_class,
                permission_scope=args.permission_scope,
                applicable_version=args.applicable_version,
                tags=args.tag,
                prerequisites=args.prerequisite,
                search=args.search,
                limit=args.limit,
            )
        elif args.command == "install":
            result = install_local_package(
                config,
                kind=args.kind,
                candidate=args.candidate,
                expected_sha256=args.expected_sha256,
            )
        else:
            result = sync_to_project(
                config,
                project_database=args.project_database,
                project_code=args.project_code,
            )
    _write_output(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
