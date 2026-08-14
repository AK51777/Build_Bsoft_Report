#!/usr/bin/env python3
"""Shared PostgreSQL helpers for the server-side knowledge repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


DEFAULT_SCHEMA = "medical_report_kb"
DEFAULT_PASSWORD_ENV = "MEDICAL_FEASIBILITY_DB_PASSWORD"
SCHEMA_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")
LEGACY_EQUIVALENT_MIGRATION_HASHES = {
    "002_shared_knowledge_policy_schema": {
        "canonical_hash": "2b0015882d48b9ce2977e7ed079b8eae7d97b31a29d8ef889a8adbc00913b86a",
        "accepted_hashes": {
            "1e64be3aff8afc3156d303f6d86e6757fa44fc50ae00421447e948fcaa549e1e",
        },
    }
}


def require_psycopg():
    try:
        import psycopg
        from psycopg import sql
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL knowledge mode requires psycopg; install requirements-postgres.txt"
        ) from exc
    return psycopg, sql, Jsonb


def migration_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "knowledge-base" / "postgres"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_schema(schema: str) -> str:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"invalid PostgreSQL schema name: {schema}")
    return schema


def migration_hash_is_accepted(version: str, applied_hash: str, current_hash: str) -> bool:
    if applied_hash == current_hash:
        return True
    compatibility = LEGACY_EQUIVALENT_MIGRATION_HASHES.get(version)
    if not compatibility or current_hash != compatibility["canonical_hash"]:
        return False
    return applied_hash in compatibility["accepted_hashes"]


def add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5432, type=int)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password-env", default=DEFAULT_PASSWORD_ENV)
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--connect-timeout", default=10, type=int)


def connection_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    password = os.environ.get(args.password_env)
    if not password:
        raise RuntimeError(
            f"database password is missing from environment variable {args.password_env}"
        )
    validate_schema(args.schema)
    return {
        "host": args.host,
        "port": args.port,
        "dbname": args.database,
        "user": args.user,
        "password": password,
        "connect_timeout": args.connect_timeout,
        "application_name": "build-medical-it-feasibility-report",
    }


def connect(args: argparse.Namespace):
    psycopg, _, _ = require_psycopg()
    return psycopg.connect(**connection_kwargs(args))


def jsonb(value: Any):
    _, _, Jsonb = require_psycopg()
    return Jsonb(value)


def apply_migrations(connection, migrations: Path | None = None, schema: str = DEFAULT_SCHEMA) -> list[str]:
    validate_schema(schema)
    _, sql, _ = require_psycopg()
    migrations = migrations or migration_dir()
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        cursor.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {}.schema_migration ("
                "version TEXT PRIMARY KEY, file_hash TEXT NOT NULL, "
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
            ).format(sql.Identifier(schema))
        )
        cursor.execute(
            sql.SQL("SELECT version,file_hash FROM {}.schema_migration").format(
                sql.Identifier(schema)
            )
        )
        applied = dict(cursor.fetchall())
    connection.commit()

    completed: list[str] = []
    for sql_path in sorted(migrations.glob("*.sql")):
        version = sql_path.stem
        migration_sql = sql_path.read_text(encoding="utf-8")
        file_hash = sha256_text(migration_sql)
        if version in applied:
            if not migration_hash_is_accepted(version, applied[version], file_hash):
                raise RuntimeError(f"applied PostgreSQL migration changed: {version}")
            continue
        with connection.cursor() as cursor:
            cursor.execute(migration_sql)
            cursor.execute(
                sql.SQL(
                    "INSERT INTO {}.schema_migration(version,file_hash) VALUES (%s,%s)"
                ).format(sql.Identifier(schema)),
                (version, file_hash),
            )
        connection.commit()
        completed.append(version)
    return completed
