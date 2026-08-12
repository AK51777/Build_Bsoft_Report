#!/usr/bin/env python3
"""Shared SQLite helpers for the feasibility-report knowledge base."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


STATUS_MAP = {
    "【已确认】": "confirmed",
    "【材料明确】": "material_explicit",
    "【待确认】": "pending_confirmation",
    "【待补充】": "pending_supplement",
    "【冲突】": "conflict",
    "【分析建议】": "analysis_recommendation",
    "【仅作参考】": "reference_only",
    "【不适用】": "not_applicable",
}


class ManagedConnection(sqlite3.Connection):
    """Commit/rollback and close when used as a context manager.

    ``sqlite3.Connection`` commits or rolls back in ``with`` blocks but does
    not close the file handle.  That behaviour leaves temporary databases
    locked on Windows, so the shared helper makes the lifecycle explicit.
    """

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    token = uuid.uuid5(uuid.NAMESPACE_URL, raw).hex[:16]
    return f"{prefix}-{token}"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, factory=ManagedConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migration_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "knowledge-base" / "migrations"


def apply_migrations(conn: sqlite3.Connection, migrations: Path | None = None) -> list[str]:
    migrations = migrations or migration_dir()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kb_schema_migration (
          version TEXT PRIMARY KEY,
          file_hash TEXT NOT NULL,
          applied_at TEXT NOT NULL
        )
        """
    )
    applied = {
        row["version"]: row["file_hash"]
        for row in conn.execute("SELECT version, file_hash FROM kb_schema_migration")
    }
    completed: list[str] = []
    for sql_path in sorted(migrations.glob("*.sql")):
        version = sql_path.stem
        sql = sql_path.read_text(encoding="utf-8")
        file_hash = sha256_text(sql)
        if version in applied:
            if applied[version] != file_hash:
                raise RuntimeError(f"已应用迁移内容发生变化：{version}")
            continue
        try:
            conn.executescript(sql)
        except sqlite3.OperationalError as exc:
            if "optional" in version and "fts5" in str(exc).lower():
                completed.append(f"{version}:skipped-no-fts5")
                conn.execute(
                    "INSERT INTO kb_schema_migration(version,file_hash,applied_at) VALUES(?,?,?)",
                    (version, file_hash, now_iso()),
                )
                conn.commit()
                continue
            raise
        conn.execute(
            "INSERT INTO kb_schema_migration(version,file_hash,applied_at) VALUES(?,?,?)",
            (version, file_hash, now_iso()),
        )
        conn.commit()
        completed.append(version)
    return completed


def upsert_project(conn: sqlite3.Connection, project: dict[str, Any]) -> str:
    project_code = project["project_code"]
    project_id = project.get("project_id") or stable_id("PROJECT", project_code)
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO project (
          project_id, project_code, official_name, document_type, owner_name,
          jurisdiction_code, jurisdiction_name, project_type, scope_authority,
          acceptance_targets_json, status, baseline_version, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(project_id) DO UPDATE SET
          project_code=excluded.project_code,
          official_name=excluded.official_name,
          document_type=excluded.document_type,
          owner_name=excluded.owner_name,
          jurisdiction_code=excluded.jurisdiction_code,
          jurisdiction_name=excluded.jurisdiction_name,
          project_type=excluded.project_type,
          scope_authority=excluded.scope_authority,
          acceptance_targets_json=excluded.acceptance_targets_json,
          status=excluded.status,
          baseline_version=excluded.baseline_version,
          updated_at=excluded.updated_at
        """,
        (
            project_id,
            project_code,
            project["official_name"],
            project.get("document_type", "feasibility_study"),
            project.get("owner_name", ""),
            project.get("jurisdiction_code", ""),
            project.get("jurisdiction_name", ""),
            project.get("project_type", "hospital_informationization"),
            project.get("scope_authority", ""),
            dump_json(project.get("acceptance_targets", [])),
            project.get("status", "active"),
            project.get("baseline_version", "working"),
            timestamp,
            timestamp,
        ),
    )
    return project_id


def fetch_all(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]
