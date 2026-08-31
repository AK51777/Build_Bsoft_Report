from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
MIGRATIONS = SKILL_ROOT / "assets" / "knowledge-base" / "migrations"
sys.path.insert(0, str(SCRIPTS))

from knowledge_db import apply_migrations, connect, dump_json, now_iso, upsert_project  # noqa: E402


class SnapshotForeignKeyMigrationTests(unittest.TestCase):
    def foreign_key_target(self, connection) -> str:
        rows = connection.execute(
            "PRAGMA foreign_key_list(shared_knowledge_snapshot_item)"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        return str(rows[0][2])

    def test_fresh_database_uses_final_snapshot_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            database = Path(tempdir) / "fresh.sqlite"
            with connect(database) as connection:
                completed = apply_migrations(connection)
                self.assertIn("018_repair_snapshot_item_foreign_key", completed)
                self.assertEqual(
                    self.foreign_key_target(connection),
                    "shared_knowledge_snapshot",
                )
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_legacy_broken_foreign_key_is_repaired_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            legacy_migrations = root / "legacy-migrations"
            legacy_migrations.mkdir()
            for migration in sorted(MIGRATIONS.glob("*.sql")):
                if migration.name.startswith("018_"):
                    continue
                shutil.copy2(migration, legacy_migrations / migration.name)

            database = root / "legacy.sqlite"
            with connect(database) as connection:
                apply_migrations(connection, legacy_migrations)
                self.assertIn(
                    self.foreign_key_target(connection),
                    {"shared_knowledge_snapshot", "shared_knowledge_snapshot_v2"},
                )
                project_id = upsert_project(
                    connection,
                    {"project_code": "LEGACY-FK", "official_name": "兼容迁移测试项目"},
                )
                connection.commit()
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    """
                    INSERT INTO shared_knowledge_snapshot (
                      snapshot_id,project_id,source_type,source_id,content_hash,
                      server_schema,snapshot_status,fetched_at,metadata_json
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "SNAPSHOT-LEGACY",
                        project_id,
                        "knowledge_package",
                        "PACK-LEGACY",
                        "a" * 64,
                        "medical_report_kb",
                        "current",
                        now_iso(),
                        dump_json({"sync_completed": True}),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO shared_knowledge_snapshot_item (
                      snapshot_id,item_type,item_id,item_hash,payload_json
                    ) VALUES (?,?,?,?,?)
                    """,
                    (
                        "SNAPSHOT-LEGACY",
                        "corpus_block",
                        "BLOCK-LEGACY",
                        "b" * 64,
                        dump_json({"block_id": "BLOCK-LEGACY"}),
                    ),
                )
                connection.commit()
                connection.execute("PRAGMA foreign_keys = ON")

                completed = apply_migrations(connection)

                self.assertIn("018_repair_snapshot_item_foreign_key", completed)
                self.assertEqual(
                    self.foreign_key_target(connection),
                    "shared_knowledge_snapshot",
                )
                stored = connection.execute(
                    """
                    SELECT item_id,item_hash,payload_json
                    FROM shared_knowledge_snapshot_item
                    WHERE snapshot_id='SNAPSHOT-LEGACY'
                    """
                ).fetchone()
                self.assertEqual(stored["item_id"], "BLOCK-LEGACY")
                self.assertEqual(stored["item_hash"], "b" * 64)
                self.assertEqual(
                    stored["payload_json"],
                    dump_json({"block_id": "BLOCK-LEGACY"}),
                )
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

                connection.execute(
                    "DELETE FROM shared_knowledge_snapshot WHERE snapshot_id='SNAPSHOT-LEGACY'"
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM shared_knowledge_snapshot_item"
                    ).fetchone()[0],
                    0,
                )


if __name__ == "__main__":
    unittest.main()
