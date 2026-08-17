#!/usr/bin/env python3
"""Confirm the current report-outline candidate and bind it to its source signature."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge_db import load_json
from report_outline import confirm_outline, write_outline_outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("outline_version_id", help="The exact candidate version that was reviewed.")
    parser.add_argument("--confirmed-by", required=True)
    parser.add_argument("--confirmed-at")
    parser.add_argument("--note", default="")
    parser.add_argument(
        "--candidate-json",
        type=Path,
        help="Optional reviewed candidate JSON; only titles may change.",
    )
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    args = parser.parse_args()
    result = confirm_outline(
        args.database,
        args.project_code,
        outline_version_id=args.outline_version_id,
        confirmed_by=args.confirmed_by,
        confirmed_at=args.confirmed_at,
        confirmation_note=args.note,
        edited_payload=load_json(args.candidate_json) if args.candidate_json else None,
    )
    write_outline_outputs(result, json_path=args.output_json, markdown_path=args.output_md)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
