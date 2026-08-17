from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from init_project_workbench import initialize_project  # noqa: E402


class ProjectInitializerTests(unittest.TestCase):
    def test_initializes_complete_local_workbench(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workbench = Path(tmp) / "project"
            result = initialize_project(
                workbench,
                project_code="TEST-NEW-001",
                official_name="测试医院信息化建设项目",
                owner_name="测试医院",
                jurisdiction_code="100000",
                jurisdiction_name="测试地区",
                scope_authority="测试建设清单",
                acceptance_targets=["电子病历五级"],
            )

            self.assertEqual(result["project_code"], "TEST-NEW-001")
            for relative in (
                "00-项目任务书.md",
                "10-章节任务包/CHxx-章节任务包.md",
                "11-正文工作稿",
                "14-交付稿",
                "原始资料",
                "数据包/清洗文本",
                "数据包/结构化数据",
                "数据包/数据库/knowledge.sqlite",
                "project-config.json",
                "project-manifest.json",
            ):
                self.assertTrue((workbench / relative).exists(), relative)

            task_book = (workbench / "00-项目任务书.md").read_text(encoding="utf-8")
            self.assertIn("TEST-NEW-001", task_book)
            self.assertIn("测试医院信息化建设项目", task_book)
            self.assertIn("电子病历五级", task_book)

            config = json.loads(
                (workbench / "project-config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(config["workflow"]["current_stage"], "0")
            self.assertEqual(config["paths"]["database"], "数据包/数据库/knowledge.sqlite")
            self.assertEqual(config["knowledge"]["mode"], "server_required")
            self.assertEqual(config["knowledge"]["profile"], "default")
            self.assertEqual(config["knowledge"]["catalog_ids"], [])
            self.assertNotIn("server", config["knowledge"])
            self.assertNotIn("password", json.dumps(config, ensure_ascii=False).casefold())

            conn = sqlite3.connect(workbench / config["paths"]["database"])
            try:
                project = conn.execute(
                    "SELECT project_code,official_name FROM project"
                ).fetchone()
                self.assertEqual(project, ("TEST-NEW-001", "测试医院信息化建设项目"))
                self.assertGreater(
                    conn.execute(
                        "SELECT COUNT(*) FROM kb_schema_migration"
                    ).fetchone()[0],
                    0,
                )
            finally:
                conn.close()

    def test_repeated_initialization_preserves_user_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workbench = Path(tmp) / "project"
            first = initialize_project(workbench, project_code="TEST-IDEMPOTENT")
            initialized_at = json.loads(
                (workbench / "project-manifest.json").read_text(encoding="utf-8")
            )["initialized_at"]
            task_book = workbench / "00-项目任务书.md"
            task_book.write_text("用户已编辑，不得覆盖。\n", encoding="utf-8")

            second = initialize_project(workbench, project_code="TEST-IDEMPOTENT")

            self.assertEqual(task_book.read_text(encoding="utf-8"), "用户已编辑，不得覆盖。\n")
            self.assertIn("00-项目任务书.md", second["preserved_files"])
            self.assertFalse(second["database_changed"])
            self.assertEqual(first["workbench"], second["workbench"])
            self.assertEqual(
                json.loads(
                    (workbench / "project-manifest.json").read_text(encoding="utf-8")
                )["initialized_at"],
                initialized_at,
            )

    def test_rejects_project_code_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workbench = Path(tmp) / "project"
            initialize_project(workbench, project_code="TEST-A")
            with self.assertRaises(RuntimeError):
                initialize_project(workbench, project_code="TEST-B")

    def test_normalizes_supported_chinese_type_aliases_and_rejects_unknown_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workbench = Path(tmp) / "project"
            initialize_project(
                workbench,
                project_code="TEST-TYPE-ALIASES",
                document_type="可行性研究报告",
                project_type="医疗信息化",
            )
            config = json.loads((workbench / "project-config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["project"]["document_type"], "feasibility_study")
            self.assertEqual(
                config["project"]["project_type"], "hospital_informationization"
            )

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "unsupported project_type"):
                initialize_project(
                    Path(tmp) / "project",
                    project_code="TEST-TYPE-UNKNOWN",
                    project_type="未支持的项目类型",
                )

    def test_legacy_server_config_migrates_without_overwriting_existing_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workbench = Path(tmp) / "project"
            initialize_project(workbench, project_code="TEST-LEGACY")
            config_path = workbench / "project-config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["knowledge"] = {
                "server": {
                    "enabled": True,
                    "host": "legacy-host",
                    "port": 6543,
                    "database": "legacy-db",
                    "user": "legacy-reader",
                    "package_ids": ["PACK-LEGACY"],
                    "allow_stale_cache": True,
                },
                "mapping_threshold": 0.91,
            }
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

            initialize_project(workbench, project_code="TEST-LEGACY")
            migrated = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["knowledge"]["mode"], "server_required")
            self.assertEqual(migrated["knowledge"]["profile"], "")
            self.assertEqual(migrated["knowledge"]["server"]["host"], "legacy-host")
            self.assertEqual(migrated["knowledge"]["server"]["port"], 6543)
            self.assertEqual(migrated["knowledge"]["server"]["catalog_ids"], [])
            self.assertEqual(migrated["knowledge"]["mapping_threshold"], 0.91)


if __name__ == "__main__":
    unittest.main()
