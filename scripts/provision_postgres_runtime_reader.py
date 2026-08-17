#!/usr/bin/env python3
"""Plan or explicitly apply a least-privilege PostgreSQL runtime reader role."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from knowledge_doctor import REQUIRED_RUNTIME_VIEWS
from postgres_knowledge_db import (
    DEFAULT_PASSWORD_ENV,
    add_connection_arguments,
    connect as connect_postgres,
    require_psycopg,
    validate_schema,
)


DEFAULT_ADMIN_PASSWORD_ENV = "MEDICAL_FEASIBILITY_ADMIN_DB_PASSWORD"
DEFAULT_READER_PASSWORD_ENV = DEFAULT_PASSWORD_ENV
DEFAULT_READER_ROLE = "medical_report_reader"
ROLE_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
PASSWORD_GUC = "medical_report_runtime.reader_password"


def validate_role(role: str) -> str:
    if not ROLE_PATTERN.fullmatch(role):
        raise ValueError(f"invalid PostgreSQL role name: {role}")
    return role


def relation_names() -> list[str]:
    return ["schema_migration", *REQUIRED_RUNTIME_VIEWS]


def set_role_password(
    cursor: Any,
    sql: Any,
    *,
    reader_role: str,
    reader_password: str,
) -> None:
    """Set a role password without rendering it into client-side SQL text.

    PostgreSQL role DDL does not accept an extended-query ``$1`` placeholder
    in the PASSWORD grammar position.  Store the bound value in a
    transaction-local custom setting, then let a server-side DO block build
    the DDL.  The setting is cleared immediately after use.  If PostgreSQL
    returns dynamic-SQL context on failure, redact the secret before the
    exception leaves this helper.
    """
    cursor.execute("SELECT set_config(%s,%s,true)", (PASSWORD_GUC, reader_password))
    try:
        cursor.execute(
            sql.SQL(
                """
                DO $medical_report_reader_password$
                BEGIN
                  EXECUTE format(
                    'ALTER ROLE %I PASSWORD %L',
                    {},
                    current_setting({})
                  );
                END
                $medical_report_reader_password$
                """
            ).format(sql.Literal(reader_role), sql.Literal(PASSWORD_GUC))
        )
    except Exception as exc:
        raise RuntimeError(redacted_error(exc, [reader_password])) from None
    else:
        cursor.execute("SELECT set_config(%s,%s,true)", (PASSWORD_GUC, ""))


def build_plan(
    *,
    database: str,
    schema: str,
    reader_role: str,
    admin_password_env: str,
    reader_password_env: str,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    validate_schema(schema)
    validate_role(reader_role)
    env = environ if environ is not None else os.environ
    return {
        "schema_version": "1.0",
        "status": "planned",
        "mode": "dry_run",
        "database": database,
        "schema": schema,
        "reader_role": reader_role,
        "admin_password_env": admin_password_env,
        "admin_password_env_present": bool(env.get(admin_password_env)),
        "reader_password_env": reader_password_env,
        "reader_password_env_present": bool(env.get(reader_password_env)),
        "relations": relation_names(),
        "operations": [
            "verify_current_database_and_admin_role_capability",
            "create_or_harden_dedicated_login_role",
            "reject_existing_role_memberships",
            "set_default_transaction_read_only",
            "revoke_schema_table_and_sequence_write_privileges",
            "grant_database_connect_and_schema_usage",
            "grant_select_on_schema_migration_and_runtime_views_only",
            "verify_effective_select_only_privileges",
        ],
        "password_policy": (
            "Passwords are read only from the named environment variables and are never "
            "written to SQL, JSON, logs, command arguments, or project configuration."
        ),
        "apply_guard": "Requires both --apply and an exact --confirm-database match.",
    }


def provision_runtime_reader(
    connection: Any,
    *,
    database: str,
    schema: str,
    reader_role: str,
    reader_password: str,
) -> dict[str, Any]:
    """Apply and verify the minimum runtime grants in one transaction."""
    validate_schema(schema)
    validate_role(reader_role)
    if not reader_password:
        raise ValueError("reader password must not be empty")
    _, sql, _ = require_psycopg()
    relations = relation_names()
    created = False
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_user,current_database(),role.rolsuper,role.rolcreaterole,
                       pg_get_userbyid(database_record.datdba),
                       (SELECT pg_get_userbyid(namespace.nspowner)
                        FROM pg_namespace AS namespace WHERE namespace.nspname=%s)
                FROM pg_roles AS role
                JOIN pg_database AS database_record
                  ON database_record.datname=current_database()
                WHERE role.rolname=current_user
                """,
                (schema,),
            )
            admin = cursor.fetchone()
            if not admin:
                raise RuntimeError("unable to resolve current PostgreSQL administrator")
            (
                admin_user,
                current_database,
                is_superuser,
                can_create_role,
                database_owner,
                schema_owner,
            ) = admin
            if current_database != database:
                raise RuntimeError(
                    f"connected database {current_database!r} does not match requested {database!r}"
                )
            if not (is_superuser or can_create_role):
                raise PermissionError(
                    f"administrator {admin_user!r} lacks SUPERUSER or CREATEROLE"
                )
            if reader_role in {admin_user, database_owner, schema_owner}:
                raise RuntimeError(
                    "reader role must be distinct from the current administrator, "
                    "database owner, and schema owner"
                )

            cursor.execute(
                """
                SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,
                       rolbypassrls,rolinherit,rolcanlogin
                FROM pg_roles WHERE rolname=%s
                """,
                (reader_role,),
            )
            existing = cursor.fetchone()
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM pg_auth_members AS membership
                JOIN pg_roles AS member ON member.oid=membership.member
                WHERE member.rolname=%s
                """,
                (reader_role,),
            )
            membership_count = int(cursor.fetchone()[0])
            if membership_count:
                raise RuntimeError(
                    f"reader role {reader_role!r} has {membership_count} role memberships; "
                    "remove them explicitly before applying least-privilege grants"
                )
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM pg_class AS object
                JOIN pg_roles AS owner ON owner.oid=object.relowner
                WHERE owner.rolname=%s
                """,
                (reader_role,),
            )
            owned_object_count = int(cursor.fetchone()[0])
            if owned_object_count:
                raise RuntimeError(
                    f"reader role {reader_role!r} owns {owned_object_count} database objects; "
                    "reassign ownership explicitly before applying runtime grants"
                )

            role_identifier = sql.Identifier(reader_role)
            if existing is None:
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
                    ).format(role_identifier)
                )
                created = True
            else:
                cursor.execute(
                    sql.SQL(
                        "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
                    ).format(role_identifier)
                )
            set_role_password(
                cursor,
                sql,
                reader_role=reader_role,
                reader_password=reader_password,
            )
            cursor.execute(
                sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(
                    role_identifier
                )
            )

            schema_identifier = sql.Identifier(schema)
            database_identifier = sql.Identifier(database)
            cursor.execute(
                sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(
                    schema_identifier, role_identifier
                )
            )
            cursor.execute(
                sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM {}").format(
                    schema_identifier, role_identifier
                )
            )
            cursor.execute(
                sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(
                    schema_identifier, role_identifier
                )
            )
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    database_identifier, role_identifier
                )
            )
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    schema_identifier, role_identifier
                )
            )
            relation_identifiers = [
                sql.Identifier(schema, relation) for relation in relations
            ]
            cursor.execute(
                sql.SQL("GRANT SELECT ON {} TO {}").format(
                    sql.SQL(",").join(relation_identifiers), role_identifier
                )
            )

            cursor.execute(
                """
                SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,
                       rolbypassrls,rolinherit,rolcanlogin
                FROM pg_roles WHERE rolname=%s
                """,
                (reader_role,),
            )
            verified_role = cursor.fetchone()
            if not verified_role:
                raise RuntimeError("reader role disappeared during verification")
            (
                _,
                role_superuser,
                role_createdb,
                role_createrole,
                role_replication,
                role_bypass_rls,
                role_inherit,
                role_can_login,
            ) = verified_role
            if any(
                (
                    role_superuser,
                    role_createdb,
                    role_createrole,
                    role_replication,
                    role_bypass_rls,
                    role_inherit,
                )
            ) or not role_can_login:
                raise RuntimeError("reader role attributes failed least-privilege verification")

            privilege_summary: list[dict[str, Any]] = []
            for relation in relations:
                qualified = f"{schema}.{relation}"
                cursor.execute(
                    """
                    SELECT
                      has_table_privilege(%s,%s,'SELECT'),
                      has_table_privilege(%s,%s,'INSERT'),
                      has_table_privilege(%s,%s,'UPDATE'),
                      has_table_privilege(%s,%s,'DELETE'),
                      has_table_privilege(%s,%s,'TRUNCATE')
                    """,
                    (
                        reader_role,
                        qualified,
                        reader_role,
                        qualified,
                        reader_role,
                        qualified,
                        reader_role,
                        qualified,
                        reader_role,
                        qualified,
                    ),
                )
                select_ok, insert_ok, update_ok, delete_ok, truncate_ok = cursor.fetchone()
                write_ok = bool(insert_ok or update_ok or delete_ok or truncate_ok)
                privilege_summary.append(
                    {"relation": relation, "select": bool(select_ok), "write": write_ok}
                )
            invalid = [
                item for item in privilege_summary if not item["select"] or item["write"]
            ]
            if invalid:
                raise RuntimeError(
                    "reader role effective privilege verification failed for: "
                    + ", ".join(item["relation"] for item in invalid)
                )
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema=%s AND table_type='BASE TABLE'
                ORDER BY table_name
                """,
                (schema,),
            )
            base_tables = [row[0] for row in cursor.fetchall()]
            writable_base_tables: list[str] = []
            for relation in base_tables:
                qualified = f"{schema}.{relation}"
                cursor.execute(
                    """
                    SELECT
                      has_table_privilege(%s,%s,'INSERT'),
                      has_table_privilege(%s,%s,'UPDATE'),
                      has_table_privilege(%s,%s,'DELETE'),
                      has_table_privilege(%s,%s,'TRUNCATE')
                    """,
                    (
                        reader_role,
                        qualified,
                        reader_role,
                        qualified,
                        reader_role,
                        qualified,
                        reader_role,
                        qualified,
                    ),
                )
                if any(cursor.fetchone()):
                    writable_base_tables.append(relation)
            if writable_base_tables:
                raise RuntimeError(
                    "reader role retains effective write privileges on base tables: "
                    + ", ".join(writable_base_tables)
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {
        "status": "applied",
        "database": database,
        "schema": schema,
        "reader_role": reader_role,
        "role_created": created,
        "role_hardened": True,
        "default_transaction_read_only": True,
        "role_membership_count": 0,
        "owned_object_count": 0,
        "relations": relations,
        "relation_count": len(relations),
        "base_table_count_checked": len(base_tables),
        "verification": "select_only_passed",
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def redacted_error(exc: Exception, secrets: list[str]) -> str:
    message = str(exc)
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_connection_arguments(parser)
    parser.set_defaults(password_env=DEFAULT_ADMIN_PASSWORD_ENV)
    parser.add_argument("--reader-role", default=DEFAULT_READER_ROLE)
    parser.add_argument("--reader-password-env", default=DEFAULT_READER_PASSWORD_ENV)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the plan. Without this flag the script is a connection-free dry run.",
    )
    parser.add_argument(
        "--confirm-database",
        help="Exact database name required together with --apply.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    plan = build_plan(
        database=args.database,
        schema=args.schema,
        reader_role=args.reader_role,
        admin_password_env=args.password_env,
        reader_password_env=args.reader_password_env,
    )
    result: dict[str, Any] = plan
    exit_code = 0
    if args.apply:
        admin_password = os.environ.get(args.password_env, "")
        reader_password = os.environ.get(args.reader_password_env, "")
        if args.confirm_database != args.database:
            result = {
                **plan,
                "status": "failed",
                "failure_class": "apply_guard",
                "message": "--confirm-database must exactly match --database",
            }
            exit_code = 2
        elif not admin_password or not reader_password:
            result = {
                **plan,
                "status": "failed",
                "failure_class": "password_environment",
                "message": (
                    "Both the administrator and reader password environment variables "
                    "must be present before --apply."
                ),
            }
            exit_code = 2
        else:
            try:
                with connect_postgres(args) as connection:
                    applied = provision_runtime_reader(
                        connection,
                        database=args.database,
                        schema=args.schema,
                        reader_role=args.reader_role,
                        reader_password=reader_password,
                    )
                result = {**plan, **applied, "mode": "apply"}
            except Exception as exc:
                result = {
                    **plan,
                    "status": "failed",
                    "mode": "apply",
                    "failure_class": "provisioning",
                    "message": redacted_error(exc, [admin_password, reader_password]),
                }
                exit_code = 3
    if args.output:
        write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
