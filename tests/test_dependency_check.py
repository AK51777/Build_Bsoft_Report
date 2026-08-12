from __future__ import annotations

import sys
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from check_dependencies import check_dependencies  # noqa: E402


class DependencyCheckTests(unittest.TestCase):
    def test_core_check_applies_all_migrations_without_external_services(self) -> None:
        result = check_dependencies()
        self.assertTrue(result["core"]["ready"])
        self.assertTrue(result["core"]["migrations_apply"])
        self.assertIn("004_scope_baseline_traceability", result["core"]["applied_migrations"])
        self.assertFalse(result["optional"]["postgres_required"])
        self.assertFalse(result["optional"]["rag_required"])
        self.assertFalse(result["word"]["visual_delivery_ready"])


if __name__ == "__main__":
    unittest.main()
