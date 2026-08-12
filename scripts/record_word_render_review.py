#!/usr/bin/env python3
"""Record an explicit, hash-bound human review of rendered Word pages."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from knowledge_db import now_iso, sha256_file


def record_review(
    docx: Path,
    render_dir: Path,
    *,
    reviewed_by: str,
    result: str,
    checked_all_pages: bool,
) -> dict:
    docx = docx.resolve()
    render_dir = render_dir.resolve()
    if not docx.is_file():
        raise FileNotFoundError(docx)
    if result not in {"pass", "fail"}:
        raise ValueError("result must be pass or fail")
    reviewer = reviewed_by.strip()
    if not reviewer:
        raise ValueError("reviewed_by must not be empty")
    pages = sorted(render_dir.glob("page-*.png"))
    if not pages:
        raise RuntimeError("render_dir must contain page-*.png files")
    if result == "pass" and not checked_all_pages:
        raise RuntimeError("pass requires --checked-all-pages")
    try:
        path_base = Path(os.path.commonpath((str(docx.parent), str(render_dir))))
    except ValueError:
        path_base = None

    def portable_path(path: Path) -> str:
        if path_base is not None:
            try:
                return path.relative_to(path_base).as_posix()
            except ValueError:
                pass
        return path.name

    return {
        "schema_version": "1.0",
        "review_type": "word_render_human_review",
        "result": result,
        "reviewed_by": reviewer,
        "reviewed_at": now_iso(),
        "checked_all_pages": checked_all_pages,
        "docx": portable_path(docx),
        "docx_sha256": sha256_file(docx),
        "render_dir": portable_path(render_dir),
        "page_count": len(pages),
        "pages": [
            {
                "path": portable_path(page),
                "sha256": sha256_file(page),
                "size_bytes": page.stat().st_size,
            }
            for page in pages
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", type=Path)
    parser.add_argument("render_dir", type=Path)
    parser.add_argument("--reviewed-by", required=True)
    parser.add_argument("--result", choices=("pass", "fail"), required=True)
    parser.add_argument("--checked-all-pages", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    review = record_review(
        args.docx,
        args.render_dir,
        reviewed_by=args.reviewed_by,
        result=args.result,
        checked_all_pages=args.checked_all_pages,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(review, ensure_ascii=False, indent=2))
    return 0 if review["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
