#!/usr/bin/env python3
"""Diagnose a shared medical-report knowledge profile without exposing its password."""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path
from typing import Any, Callable

from knowledge_profile import (
    KnowledgeConfigurationError,
    public_settings,
    redact_text,
    resolve_knowledge_settings,
)
from postgres_knowledge_db import connect as connect_postgres, migration_dir, validate_schema


REQUIRED_RUNTIME_VIEWS = (
    "runtime_knowledge_package",
    "runtime_package_source",
    "runtime_corpus_document",
    "runtime_corpus_block",
    "runtime_product_capability",
    "runtime_capability_block",
    "runtime_policy_catalog",
    "runtime_policy_catalog_entry",
    "runtime_policy_clause",
)
EXIT_CODES = {
    "passed": 0,
    "configuration": 10,
    "password": 11,
    "network": 12,
    "connection": 13,
    "schema": 14,
    "permission": 15,
    "content": 16,
}


def _namespace(settings: dict[str, Any]) -> argparse.Namespace:
    return argparse.Namespace(
        host=settings["host"],
        port=settings["port"],
        database=settings["database"],
        user=settings["user"],
        password_env=settings["password_env"],
        schema=settings["schema"],
        connect_timeout=settings["connect_timeout"],
    )


def _fetch_dicts(cursor) -> list[dict[str, Any]]:
    columns = [column.name for column in cursor.description]
    return [
        {key: _json_value(value) for key, value in zip(columns, row)}
        for row in cursor.fetchall()
    ]


def _json_value(value: Any) -> Any:
    """Normalize PostgreSQL driver values before they reach run manifests."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _check(code: str, status: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "status": status, "message": message, **details}


def diagnose_settings(
    settings: dict[str, Any],
    *,
    connector: Callable[[argparse.Namespace], Any] = connect_postgres,
    tcp_probe: Callable[[tuple[str, int], float], Any] | None = socket.create_connection,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = environ if environ is not None else os.environ
    checks: list[dict[str, Any]] = []
    public = public_settings(settings, environ=env)
    mode = settings["mode"]
    if mode != "server_required":
        checks.append(
            _check(
                "MODE",
                "pass",
                f"mode {mode} does not require a live PostgreSQL connection",
            )
        )
        return {
            "schema_version": "1.0",
            "status": "passed",
            "exit_code": EXIT_CODES["passed"],
            "profile": public,
            "checks": checks,
            "counts": {},
            "packages": [],
            "catalogs": [],
        }
    password_env = settings["password_env"]
    if not env.get(password_env):
        checks.append(
            _check(
                "PASSWORD_ENV",
                "fail",
                f"password environment variable is missing: {password_env}",
                required_action=f"Set {password_env} in the process environment; do not place the password in JSON.",
            )
        )
        return {
            "schema_version": "1.0",
            "status": "failed",
            "failure_class": "password",
            "exit_code": EXIT_CODES["password"],
            "profile": public,
            "checks": checks,
            "counts": {},
            "packages": [],
            "catalogs": [],
        }
    checks.append(_check("PASSWORD_ENV", "pass", f"password environment variable is present: {password_env}"))
    if tcp_probe is not None:
        try:
            probe = tcp_probe(
                (settings["host"], int(settings["port"])),
                float(settings["connect_timeout"]),
            )
            if hasattr(probe, "close"):
                probe.close()
            checks.append(_check("TCP", "pass", "host and port are reachable"))
        except OSError as exc:
            checks.append(
                _check(
                    "TCP",
                    "fail",
                    redact_text(exc, settings, environ=env),
                    required_action="Start the configured tunnel or restore network reachability; the doctor will not create a tunnel.",
                )
            )
            return {
                "schema_version": "1.0",
                "status": "failed",
                "failure_class": "network",
                "exit_code": EXIT_CODES["network"],
                "profile": public,
                "checks": checks,
                "counts": {},
                "packages": [],
                "catalogs": [],
            }
    schema = validate_schema(settings["schema"])
    try:
        connection = connector(_namespace(settings))
    except Exception as exc:
        checks.append(
            _check(
                "POSTGRES_CONNECTION",
                "fail",
                redact_text(exc, settings, environ=env),
                required_action="Verify database name, read-only user, password environment variable, TLS/tunnel and pg_hba rules.",
            )
        )
        return {
            "schema_version": "1.0",
            "status": "failed",
            "failure_class": "connection",
            "exit_code": EXIT_CODES["connection"],
            "profile": public,
            "checks": checks,
            "counts": {},
            "packages": [],
            "catalogs": [],
        }
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass(%s)", (f"{schema}.schema_migration",)
            )
            migration_table = cursor.fetchone()[0]
            expected_migrations = sorted(path.stem for path in migration_dir().glob("*.sql"))
            if migration_table:
                cursor.execute(f"SELECT version FROM {schema}.schema_migration ORDER BY version")
                applied_migrations = [row[0] for row in cursor.fetchall()]
            else:
                applied_migrations = []
            missing_migrations = sorted(set(expected_migrations) - set(applied_migrations))
            if missing_migrations:
                checks.append(
                    _check(
                        "SCHEMA_MIGRATIONS",
                        "fail",
                        "required PostgreSQL migrations are missing",
                        applied=applied_migrations,
                        missing=missing_migrations,
                        required_action="Ask a database administrator to apply the missing migrations with an administrative credential.",
                    )
                )
            else:
                checks.append(
                    _check(
                        "SCHEMA_MIGRATIONS",
                        "pass",
                        "all repository PostgreSQL migrations are recorded",
                        applied=applied_migrations,
                    )
                )
            cursor.execute(
                "SELECT table_name FROM information_schema.views WHERE table_schema=%s",
                (schema,),
            )
            existing_views = {row[0] for row in cursor.fetchall()}
            missing_views = sorted(set(REQUIRED_RUNTIME_VIEWS) - existing_views)
            if missing_views:
                checks.append(
                    _check(
                        "RUNTIME_VIEWS",
                        "fail",
                        "required published runtime views are missing",
                        missing=missing_views,
                        required_action="Ask a database administrator to apply the repository migrations; do not run migrations with the read-only account.",
                    )
                )
            else:
                checks.append(_check("RUNTIME_VIEWS", "pass", "all required published runtime views exist"))

            cursor.execute(
                """
                SELECT table_name,table_type
                FROM information_schema.tables
                WHERE table_schema=%s ORDER BY table_type,table_name
                """,
                (schema,),
            )
            schema_objects = cursor.fetchall()
            privilege_rows = []
            for table_name, table_type in schema_objects:
                qualified = f"{schema}.{table_name}"
                cursor.execute(
                    """
                    SELECT
                      has_table_privilege(current_user,%s,'SELECT'),
                      has_table_privilege(current_user,%s,'INSERT'),
                      has_table_privilege(current_user,%s,'UPDATE'),
                      has_table_privilege(current_user,%s,'DELETE'),
                      has_table_privilege(current_user,%s,'TRUNCATE')
                    """,
                    (qualified, qualified, qualified, qualified, qualified),
                )
                select_ok, insert_ok, update_ok, delete_ok, truncate_ok = cursor.fetchone()
                privilege_rows.append(
                    {
                        "table": table_name,
                        "table_type": table_type,
                        "select": bool(select_ok),
                        "write": bool(insert_ok or update_ok or delete_ok or truncate_ok),
                    }
                )
            missing_select = [
                name
                for name in REQUIRED_RUNTIME_VIEWS
                if not next((item["select"] for item in privilege_rows if item["table"] == name), False)
            ]
            write_grants = [
                {"table": item["table"]}
                for item in privilege_rows
                if item["table_type"] == "BASE TABLE" and item["write"]
            ]
            if write_grants or missing_select:
                checks.append(
                    _check(
                        "READ_ONLY_ACCOUNT",
                        "fail",
                        "runtime account is not a SELECT-only account for the published views",
                        write_grants=write_grants,
                        missing_runtime_select=missing_select,
                        required_action="Use a dedicated account with SELECT/USAGE only.",
                    )
                )
            else:
                checks.append(_check("READ_ONLY_ACCOUNT", "pass", "no direct write grants were found"))

            if missing_views:
                counts = {}
                packages = []
                catalogs = []
            else:
                cursor.execute(
                    f"""
                    SELECT
                      (SELECT COUNT(*) FROM {schema}.runtime_knowledge_package) AS packages,
                      (SELECT COUNT(*) FROM {schema}.runtime_corpus_block) AS corpus_blocks,
                      (SELECT COUNT(*) FROM {schema}.runtime_product_capability) AS capabilities,
                      (SELECT COUNT(*) FROM {schema}.runtime_policy_catalog) AS catalogs,
                      (SELECT COUNT(*) FROM {schema}.runtime_policy_catalog_entry) AS catalog_records,
                      (SELECT COUNT(*) FROM {schema}.runtime_policy_clause) AS policy_clauses
                    """
                )
                counts = _fetch_dicts(cursor)[0]
                cursor.execute(
                    f"""
                    SELECT p.package_id,p.title,p.schema_version,p.permission_scope,p.content_hash,
                           p.published_at,d.document_type,d.project_type,d.jurisdiction_code,d.version
                    FROM {schema}.runtime_knowledge_package p
                    LEFT JOIN {schema}.runtime_corpus_document d ON d.package_id=p.package_id
                    ORDER BY p.published_at DESC NULLS LAST,p.package_id
                    """
                )
                packages = _fetch_dicts(cursor)
                cursor.execute(
                    f"""
                    SELECT catalog_id,title,catalog_scope,permission_scope,record_count,
                           content_hash,published_at
                    FROM {schema}.runtime_policy_catalog
                    ORDER BY published_at DESC NULLS LAST,catalog_id
                    """
                )
                catalogs = _fetch_dicts(cursor)
                configured_package_ids = set(settings.get("package_ids") or [])
                configured_catalog_ids = set(settings.get("catalog_ids") or [])
                allowed_package_scopes = set(
                    (settings.get("permission_scopes") or {}).get("knowledge_package", [])
                )
                allowed_catalog_scopes = set(
                    (settings.get("permission_scopes") or {}).get("policy_catalog", [])
                )
                eligible_package_ids = {
                    str(item.get("package_id") or "")
                    for item in packages
                    if not allowed_package_scopes
                    or item.get("permission_scope") in allowed_package_scopes
                }
                eligible_catalog_ids = {
                    str(item.get("catalog_id") or "")
                    for item in catalogs
                    if not allowed_catalog_scopes
                    or item.get("permission_scope") in allowed_catalog_scopes
                }
                missing_scope_packages = sorted(
                    configured_package_ids - eligible_package_ids
                )
                missing_scope_catalogs = sorted(
                    configured_catalog_ids - eligible_catalog_ids
                )
                if (
                    missing_scope_packages
                    or missing_scope_catalogs
                    or not eligible_package_ids
                    or not eligible_catalog_ids
                ):
                    checks.append(
                        _check(
                            "PERMISSION_SCOPE",
                            "fail",
                            "published package or catalog permission scope does not satisfy the profile",
                            configured_package_ids=sorted(configured_package_ids),
                            configured_catalog_ids=sorted(configured_catalog_ids),
                            missing_or_disallowed_package_ids=missing_scope_packages,
                            missing_or_disallowed_catalog_ids=missing_scope_catalogs,
                            allowed_package_scopes=sorted(allowed_package_scopes),
                            allowed_catalog_scopes=sorted(allowed_catalog_scopes),
                            required_action="Select published IDs visible within the project's permission scope or ask an administrator to grant the correct read-only scope.",
                        )
                    )
                else:
                    checks.append(
                        _check(
                            "PERMISSION_SCOPE",
                            "pass",
                            "published package and catalog permission scopes satisfy the profile",
                        )
                    )
        if (
            not missing_views
            and counts.get("packages", 0) > 0
            and counts.get("corpus_blocks", 0) > 0
            and counts.get("capabilities", 0) > 0
            and counts.get("catalogs", 0) > 0
            and counts.get("catalog_records", 0) > 0
        ):
            checks.append(_check("PUBLISHED_CONTENT", "pass", "published knowledge content is visible", counts=counts))
        elif not missing_views:
            checks.append(
                _check(
                    "PUBLISHED_CONTENT",
                    "fail",
                    "published knowledge package, corpus blocks, capabilities, or policy catalog records are empty",
                    counts=counts,
                    required_action="Publish an approved non-empty knowledge package before running a server-required project.",
                )
            )
    finally:
        connection.close()

    failed_codes = {item["code"] for item in checks if item["status"] == "fail"}
    failure_class = ""
    if "RUNTIME_VIEWS" in failed_codes or "SCHEMA_MIGRATIONS" in failed_codes:
        failure_class = "schema"
    elif "READ_ONLY_ACCOUNT" in failed_codes or "PERMISSION_SCOPE" in failed_codes:
        failure_class = "permission"
    elif "PUBLISHED_CONTENT" in failed_codes:
        failure_class = "content"
    status = "failed" if failure_class else "passed"
    return {
        "schema_version": "1.0",
        "status": status,
        "failure_class": failure_class,
        "exit_code": EXIT_CODES[failure_class or "passed"],
        "profile": public,
        "checks": checks,
        "counts": counts,
        "packages": packages,
        "catalogs": catalogs,
    }


def load_project_config(project_root: Path | None) -> dict[str, Any]:
    if project_root is None:
        return {"project": {}, "knowledge": {"mode": "server_required"}}
    path = project_root.resolve() / "project-config.json"
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise KnowledgeConfigurationError(
            "project_config_not_found", f"project configuration not found: {path}"
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--profile")
    parser.add_argument("--mode", choices=("server_required", "snapshot_required", "offline_pack", "disabled"))
    parser.add_argument("--no-tcp", action="store_true", help="Skip the separate TCP reachability probe")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON (the default output format)")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        project_config = load_project_config(args.project_root)
        if args.profile:
            project_config.setdefault("knowledge", {})["profile"] = args.profile
        settings = resolve_knowledge_settings(
            project_config,
            explicit_config_path=args.config,
            explicit_profile=args.profile,
            explicit_mode=args.mode,
        )
        result = diagnose_settings(
            settings, tcp_probe=None if args.no_tcp else socket.create_connection
        )
    except KnowledgeConfigurationError as exc:
        result = {
            "schema_version": "1.0",
            "status": "failed",
            "failure_class": "configuration",
            "exit_code": EXIT_CODES["configuration"],
            "reason": exc.reason,
            "message": str(exc),
            "details": exc.details,
        }
    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
