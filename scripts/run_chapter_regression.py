#!/usr/bin/env python3
"""Run chapter, chapter-family, or full regression tests with bounded scope."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from chapter_rules import regression_targets, resolve_chapter_rule


SKILL_ROOT = Path(__file__).resolve().parent.parent


def build_command(chapter_code: str, tier: str) -> list[str]:
    if tier == "full":
        return [
            sys.executable,
            "-B",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_*.py",
        ]
    return [
        sys.executable,
        "-B",
        "-m",
        "unittest",
        *regression_targets(chapter_code, tier),
    ]


def run_regression(
    chapter_code: str,
    tier: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    contract = resolve_chapter_rule(chapter_code)
    command = build_command(chapter_code, tier)
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "chapter_code": chapter_code,
        "section_role": contract.get("section_role"),
        "regression_group": contract.get("regression_group"),
        "tier": tier,
        "command": command,
        "dry_run": dry_run,
    }
    if dry_run:
        result["status"] = "planned"
        result["exit_code"] = None
        return result
    completed = subprocess.run(command, cwd=SKILL_ROOT, check=False)
    result["exit_code"] = completed.returncode
    result["status"] = "passed" if completed.returncode == 0 else "failed"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chapter_code")
    parser.add_argument(
        "--tier", choices=("chapter", "family", "full"), default="chapter"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_regression(args.chapter_code, args.tier, dry_run=args.dry_run)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return int(result.get("exit_code") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
