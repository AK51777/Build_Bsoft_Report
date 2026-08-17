#!/usr/bin/env python3
"""Build or reuse the current versioned report-outline candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from report_outline import build_outline_candidate, write_outline_outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_code")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    args = parser.parse_args()
    result = build_outline_candidate(args.database, args.project_code)
    write_outline_outputs(result, json_path=args.output_json, markdown_path=args.output_md)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
