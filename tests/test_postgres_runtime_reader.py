from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from provision_postgres_runtime_reader import (  # noqa: E402
    PASSWORD_GUC,
    build_plan,
    provision_runtime_reader,
    redacted_error,
    relation_names,
    validate_role,
)


class FakeCursor:
    def __init__(self, responses: list[object], fetchall_responses: list[list[object]]) -> None:
        self.responses = list(responses)
        self.fetchall_responses = list(fetchall_responses)
        self.executions: list[tuple[object, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query, params=None) -> None:
        self.executions.append((query, params))

    def fetchone(self):
        if not self.responses:
            raise AssertionError("unexpected fetchone")
        return self.responses.pop(0)

    def fetchall(self):
        if not self.fetchall_responses:
            raise AssertionError("unexpected fetchall")
        return self.fetchall_responses.pop(0)


class PasswordDdlFailingCursor(FakeCursor):
    def execute(self, query, params=None) -> None:
        super().execute(query, params)
        if "DO $medical_report_reader_password$" in str(query):
            raise RuntimeError("server context leaked reader-secret")


class FakeConnection:
    def __init__(
        self,
        responses: list[object],
        fetchall_responses: list[list[object]] | None = None,
    ) -> None:
        self.cursor_instance = FakeCursor(responses, fetchall_responses or [])
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class PasswordDdlFailingConnection(FakeConnection):
    def __init__(self, responses: list[object]) -> None:
        super().__init__(responses)
        self.cursor_instance = PasswordDdlFailingCursor(responses, [])


class PostgresRuntimeReaderTests(unittest.TestCase):
    def test_dry_run_plan_is_secret_free_and_complete(self) -> None:
        plan = build_plan(
            database="hrr_feedback",
            schema="medical_report_kb",
            reader_role="medical_report_reader",
            admin_password_env="ADMIN_SECRET_ENV",
            reader_password_env="READER_SECRET_ENV",
            environ={"ADMIN_SECRET_ENV": "admin-secret", "READER_SECRET_ENV": "reader-secret"},
        )
        rendered = json.dumps(plan, ensure_ascii=False)
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["relations"], relation_names())
        self.assertEqual(len(plan["relations"]), 10)
        self.assertNotIn("admin-secret", rendered)
        self.assertNotIn("reader-secret", rendered)
        self.assertTrue(plan["admin_password_env_present"])
        self.assertTrue(plan["reader_password_env_present"])

    def test_rejects_unsafe_role_names(self) -> None:
        for value in ("reader;DROP ROLE x", "UpperCase", "", "9reader"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_role(value)

    def test_apply_creates_and_verifies_select_only_role_without_embedding_password(self) -> None:
        role_attributes = (
            "medical_report_reader",
            False,
            False,
            False,
            False,
            False,
            False,
            True,
        )
        responses: list[object] = [
            ("admin", "hrr_feedback", True, True, "admin", "admin"),
            None,
            (0,),
            (0,),
            role_attributes,
            *[(True, False, False, False, False) for _ in relation_names()],
            (False, False, False, False),
            (False, False, False, False),
        ]
        connection = FakeConnection(
            responses,
            fetchall_responses=[[('knowledge_package',), ('corpus_block',)]],
        )
        secret = "reader-password-must-never-be-rendered"

        result = provision_runtime_reader(
            connection,
            database="hrr_feedback",
            schema="medical_report_kb",
            reader_role="medical_report_reader",
            reader_password=secret,
        )

        self.assertEqual(result["verification"], "select_only_passed")
        self.assertTrue(result["role_created"])
        self.assertEqual(result["relation_count"], 10)
        self.assertEqual(result["base_table_count_checked"], 2)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertNotIn(secret, json.dumps(result, ensure_ascii=False))
        query_text = "\n".join(str(query) for query, _ in connection.cursor_instance.executions)
        self.assertNotIn(secret, query_text)
        self.assertTrue(
            any(
                params == (PASSWORD_GUC, secret)
                for _, params in connection.cursor_instance.executions
            )
        )
        self.assertTrue(
            any(
                params == (PASSWORD_GUC, "")
                for _, params in connection.cursor_instance.executions
            )
        )
        self.assertFalse(
            any(
                "PASSWORD %s" in str(query)
                for query, _ in connection.cursor_instance.executions
            )
        )

    def test_existing_memberships_fail_closed_and_rollback(self) -> None:
        responses: list[object] = [
            ("admin", "hrr_feedback", True, True, "admin", "admin"),
            ("medical_report_reader", False, False, False, False, False, False, True),
            (1,),
        ]
        connection = FakeConnection(responses)
        with self.assertRaisesRegex(RuntimeError, "role memberships"):
            provision_runtime_reader(
                connection,
                database="hrr_feedback",
                schema="medical_report_kb",
                reader_role="medical_report_reader",
                reader_password="reader-secret",
            )
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_password_ddl_failure_is_redacted_and_rolls_back(self) -> None:
        connection = PasswordDdlFailingConnection(
            [
                ("admin", "hrr_feedback", True, True, "admin", "admin"),
                None,
                (0,),
                (0,),
            ]
        )
        with self.assertRaises(RuntimeError) as captured:
            provision_runtime_reader(
                connection,
                database="hrr_feedback",
                schema="medical_report_kb",
                reader_role="medical_report_reader",
                reader_password="reader-secret",
            )
        self.assertNotIn("reader-secret", str(captured.exception))
        self.assertIn("[REDACTED]", str(captured.exception))
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_refuses_to_harden_current_admin_or_owner_role(self) -> None:
        for protected_role in ("admin", "database_owner", "schema_owner"):
            with self.subTest(protected_role=protected_role):
                connection = FakeConnection(
                    [
                        (
                            "admin",
                            "hrr_feedback",
                            True,
                            True,
                            "database_owner",
                            "schema_owner",
                        )
                    ]
                )
                with self.assertRaisesRegex(RuntimeError, "must be distinct"):
                    provision_runtime_reader(
                        connection,
                        database="hrr_feedback",
                        schema="medical_report_kb",
                        reader_role=protected_role,
                        reader_password="reader-secret",
                    )
                self.assertEqual(connection.rollbacks, 1)

    def test_error_redaction_covers_both_secrets(self) -> None:
        error = RuntimeError("admin-secret and reader-secret must not escape")
        rendered = redacted_error(error, ["admin-secret", "reader-secret"])
        self.assertEqual(rendered.count("[REDACTED]"), 2)
        self.assertNotIn("secret", rendered)


if __name__ == "__main__":
    unittest.main()
