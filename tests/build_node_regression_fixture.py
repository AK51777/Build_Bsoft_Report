#!/usr/bin/env python3
"""Build synthetic fact/policy inputs for the Node confirmation regression."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from export_fact_confirmation_data import export as export_facts  # noqa: E402
from ingest_policies import ingest as ingest_policies  # noqa: E402
from ingest_project_facts import ingest as ingest_project_facts  # noqa: E402
from match_project_policies import match as match_project_policies  # noqa: E402
from test_p0_p1 import policy_payload, project_payload  # noqa: E402


def write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    database = output_dir / "node-regression.sqlite"
    ingest_project_facts(database, project_payload())
    ingest_policies(database, policy_payload())
    policy_selection = match_project_policies(
        database,
        "TEST-540400",
        {"electronic_medical_record", "interoperability"},
        None,
        None,
    )
    fact_json = output_dir / "fact-confirmation-data.json"
    policy_json = output_dir / "policy-selection.json"
    write_json(fact_json, export_facts(database, "TEST-540400"))
    write_json(policy_json, policy_selection)
    print(
        json.dumps(
            {
                "database": str(database),
                "project_code": "TEST-540400",
                "fact_json": str(fact_json),
                "policy_json": str(policy_json),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
