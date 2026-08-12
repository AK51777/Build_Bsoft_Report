#!/usr/bin/env python3
"""Inventory project source files without modifying them."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    "node_modules",
    ".codex_tmp",
}

FILE_TYPES = {
    ".docx": "DOCX",
    ".doc": "DOC",
    ".xlsx": "XLSX",
    ".xls": "XLS",
    ".pdf": "PDF",
    ".md": "MD",
    ".txt": "TXT",
    ".csv": "CSV",
    ".tsv": "TSV",
    ".pptx": "PPTX",
    ".ppt": "PPT",
    ".json": "JSON",
    ".xml": "XML",
}

CSV_FIELDS = [
    "source_id",
    "file_name",
    "file_type",
    "extension",
    "absolute_path",
    "input_root",
    "relative_path",
    "size_bytes",
    "modified_time",
    "sha256",
    "is_temporary",
    "status",
    "error",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a deterministic inventory of project source files."
    )
    parser.add_argument("paths", nargs="+", help="Files or directories to inventory.")
    parser.add_argument(
        "--output",
        required=True,
        help="Output .json or .csv file. Existing output is replaced.",
    )
    parser.add_argument(
        "--existing",
        help="Existing JSON/CSV inventory whose source IDs should be reused.",
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="Directory name to skip. Repeat for multiple names.",
    )
    parser.add_argument(
        "--exclude-suffix",
        action="append",
        default=[],
        help="File suffix to skip, such as .tmp. Repeat as needed.",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Do not recurse into input directories.",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden files and directories.",
    )
    return parser.parse_args()


def is_hidden(path: Path) -> bool:
    if path.name.startswith("."):
        return True
    if os.name == "nt":
        try:
            attrs = path.stat().st_file_attributes
            return bool(attrs & 2)
        except (AttributeError, OSError):
            return False
    return False


def iter_files(
    input_path: Path,
    recursive: bool,
    include_hidden: bool,
    excluded_dirs: set[str],
    excluded_suffixes: set[str],
) -> Iterable[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() not in excluded_suffixes:
            yield input_path
        return
    if not input_path.is_dir():
        return

    if recursive:
        for root, dirs, files in os.walk(input_path):
            root_path = Path(root)
            dirs[:] = [
                name
                for name in dirs
                if name not in excluded_dirs
                and (include_hidden or not is_hidden(root_path / name))
            ]
            for name in files:
                path = root_path / name
                if not include_hidden and is_hidden(path):
                    continue
                if path.suffix.lower() in excluded_suffixes:
                    continue
                yield path
    else:
        for path in input_path.iterdir():
            if not path.is_file():
                continue
            if not include_hidden and is_hidden(path):
                continue
            if path.suffix.lower() in excluded_suffixes:
                continue
            yield path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_existing(path: Path | None) -> tuple[dict[str, str], dict[str, str], int]:
    by_path: dict[str, str] = {}
    by_hash: dict[str, str] = {}
    max_id = 0
    if path is None:
        return by_path, by_hash, max_id
    if not path.exists():
        raise FileNotFoundError(f"Existing inventory not found: {path}")

    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            records = list(csv.DictReader(stream))
    else:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        records = data.get("files", data) if isinstance(data, dict) else data

    for record in records:
        source_id = str(record.get("source_id", "")).strip()
        if not source_id:
            continue
        absolute_path = str(record.get("absolute_path", "")).strip()
        file_hash = str(record.get("sha256", "")).strip()
        if absolute_path:
            by_path[os.path.normcase(os.path.abspath(absolute_path))] = source_id
        if file_hash:
            by_hash[file_hash] = source_id
        try:
            max_id = max(max_id, int(source_id.rsplit("-", 1)[-1]))
        except ValueError:
            pass
    return by_path, by_hash, max_id


def assign_source_id(
    absolute_path: str,
    file_hash: str,
    by_path: dict[str, str],
    by_hash: dict[str, str],
    next_id: int,
) -> tuple[str, int]:
    path_key = os.path.normcase(os.path.abspath(absolute_path))
    if path_key in by_path:
        return by_path[path_key], next_id
    if file_hash and file_hash in by_hash:
        return by_hash[file_hash], next_id
    source_id = f"SRC-{next_id:03d}"
    return source_id, next_id + 1


def inventory_file(path: Path, input_root: Path) -> dict[str, object]:
    resolved = path.resolve()
    relative_path = (
        str(resolved.relative_to(input_root.resolve()))
        if input_root.is_dir()
        else resolved.name
    )
    record: dict[str, object] = {
        "source_id": "",
        "file_name": resolved.name,
        "file_type": FILE_TYPES.get(resolved.suffix.lower(), "OTHER"),
        "extension": resolved.suffix.lower(),
        "absolute_path": str(resolved),
        "input_root": str(input_root.resolve()),
        "relative_path": relative_path,
        "size_bytes": "",
        "modified_time": "",
        "sha256": "",
        "is_temporary": resolved.name.startswith("~$"),
        "status": "ok",
        "error": "",
    }
    try:
        stat = resolved.stat()
        record["size_bytes"] = stat.st_size
        record["modified_time"] = datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat()
        record["sha256"] = sha256_file(resolved)
    except (OSError, PermissionError) as exc:
        record["status"] = "error"
        record["error"] = str(exc)
    return record


def write_output(path: Path, records: list[dict[str, object]], inputs: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)
        return

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_paths": inputs,
        "file_count": len(records),
        "error_count": sum(1 for record in records if record["status"] != "ok"),
        "files": records,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def build_inventory(
    paths: list[Path],
    *,
    existing_path: Path | None = None,
    output_path: Path | None = None,
    recursive: bool = True,
    include_hidden: bool = False,
    excluded_dirs: set[str] | None = None,
    excluded_suffixes: set[str] | None = None,
) -> dict[str, object]:
    excluded_dirs = DEFAULT_EXCLUDED_DIRS | (excluded_dirs or set())
    excluded_suffixes = {
        suffix.lower() for suffix in (excluded_suffixes or set())
    }
    by_path, by_hash, next_id = load_existing(existing_path)
    next_id += 1
    seen: set[str] = set()
    records: list[dict[str, object]] = []

    for input_root in paths:
        if not input_root.exists():
            records.append(
                {
                    "source_id": "",
                    "file_name": input_root.name,
                    "file_type": "MISSING",
                    "extension": input_root.suffix.lower(),
                    "absolute_path": str(input_root.absolute()),
                    "input_root": str(input_root.absolute()),
                    "relative_path": "",
                    "size_bytes": "",
                    "modified_time": "",
                    "sha256": "",
                    "is_temporary": False,
                    "status": "error",
                    "error": "Input path does not exist",
                }
            )
            continue
        for file_path in iter_files(
            input_root,
            recursive=recursive,
            include_hidden=include_hidden,
            excluded_dirs=excluded_dirs,
            excluded_suffixes=excluded_suffixes,
        ):
            resolved_key = os.path.normcase(str(file_path.resolve()))
            if output_path and resolved_key == os.path.normcase(str(output_path.resolve())):
                continue
            if resolved_key in seen:
                continue
            seen.add(resolved_key)
            records.append(inventory_file(file_path, input_root))

    records.sort(key=lambda item: str(item["absolute_path"]).casefold())
    for record in records:
        if record["status"] != "ok":
            continue
        source_id, next_id = assign_source_id(
            str(record["absolute_path"]),
            str(record["sha256"]),
            by_path,
            by_hash,
            next_id,
        )
        record["source_id"] = source_id
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_paths": [str(path.absolute()) for path in paths],
        "file_count": len(records),
        "error_count": sum(1 for record in records if record["status"] != "ok"),
        "files": records,
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).resolve()
    existing_path = Path(args.existing).resolve() if args.existing else None
    excluded_dirs = DEFAULT_EXCLUDED_DIRS | set(args.exclude_dir)
    excluded_suffixes = {suffix.lower() for suffix in args.exclude_suffix}

    result = build_inventory(
        [Path(path) for path in args.paths],
        existing_path=existing_path,
        output_path=output_path,
        recursive=not args.no_recursive,
        include_hidden=args.include_hidden,
        excluded_dirs=excluded_dirs,
        excluded_suffixes=excluded_suffixes,
    )
    records = result["files"]
    write_output(output_path, records, result["input_paths"])
    print(
        f"Wrote {len(records)} records to {output_path} "
        f"({sum(1 for record in records if record['status'] != 'ok')} errors)."
    )
    return 1 if any(record["status"] != "ok" for record in records) else 0


if __name__ == "__main__":
    sys.exit(main())
