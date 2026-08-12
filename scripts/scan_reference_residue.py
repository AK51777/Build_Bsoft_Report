#!/usr/bin/env python3
"""Scan text and Office Open XML files for forbidden reference-project terms."""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".csv",
    ".tsv",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".yaml",
    ".yml",
}

OFFICE_PART_PREFIXES = {
    ".docx": (
        "word/document.xml",
        "word/header",
        "word/footer",
        "word/comments",
        "word/footnotes",
        "word/endnotes",
        "docProps/core.xml",
        "docProps/custom.xml",
    ),
    ".xlsx": (
        "xl/sharedStrings.xml",
        "xl/worksheets/",
        "xl/comments",
        "docProps/core.xml",
        "docProps/custom.xml",
    ),
    ".pptx": (
        "ppt/slides/",
        "ppt/notesSlides/",
        "ppt/comments/",
        "docProps/core.xml",
        "docProps/custom.xml",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan drafts and Office files for forbidden names, amounts, or terms."
    )
    parser.add_argument("paths", nargs="+", help="Files or directories to scan.")
    parser.add_argument(
        "--term",
        action="append",
        default=[],
        help="Forbidden literal term. Repeat for multiple terms.",
    )
    parser.add_argument(
        "--terms-file",
        action="append",
        default=[],
        help="UTF-8 text file with one term per line, or a JSON string array.",
    )
    parser.add_argument("--output", required=True, help="Output JSON report.")
    parser.add_argument(
        "--regex",
        action="store_true",
        help="Interpret terms as regular expressions instead of literals.",
    )
    parser.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Use case-sensitive matching.",
    )
    parser.add_argument(
        "--max-matches",
        type=int,
        default=500,
        help="Maximum match records retained across all files.",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=45,
        help="Characters retained before and after a match.",
    )
    parser.add_argument(
        "--no-fail",
        action="store_true",
        help="Return exit code 0 even when residue is found.",
    )
    return parser.parse_args()


def load_terms(raw_terms: list[str], term_files: list[str]) -> list[str]:
    terms = [term.strip() for term in raw_terms if term.strip()]
    for raw_path in term_files:
        path = Path(raw_path)
        content = path.read_text(encoding="utf-8-sig")
        if path.suffix.lower() == ".json":
            data = json.loads(content)
            if not isinstance(data, list) or not all(
                isinstance(item, str) for item in data
            ):
                raise ValueError(f"Terms JSON must be a string array: {path}")
            terms.extend(item.strip() for item in data if item.strip())
        else:
            terms.extend(
                line.strip()
                for line in content.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
    return list(dict.fromkeys(terms))


def iter_files(paths: list[str]) -> Iterable[Path]:
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_file():
            key = str(path.resolve()).casefold()
            if key not in seen:
                seen.add(key)
                yield path
        elif path.is_dir():
            for file_path in sorted(
                (item for item in path.rglob("*") if item.is_file()),
                key=lambda item: str(item).casefold(),
            ):
                if any(part in {".git", "__pycache__", "node_modules"} for part in file_path.parts):
                    continue
                key = str(file_path.resolve()).casefold()
                if key not in seen:
                    seen.add(key)
                    yield file_path


def xml_text(raw_xml: bytes) -> str:
    try:
        root = ET.fromstring(raw_xml)
        return " ".join(text.strip() for text in root.itertext() if text.strip())
    except ET.ParseError:
        return raw_xml.decode("utf-8", errors="replace")


def office_parts(path: Path) -> list[tuple[str, str]]:
    suffix = path.suffix.lower()
    prefixes = OFFICE_PART_PREFIXES.get(suffix)
    if not prefixes:
        return []
    parts: list[tuple[str, str]] = []
    with zipfile.ZipFile(path) as package:
        for name in package.namelist():
            if not name.endswith(".xml"):
                continue
            if not any(name == prefix or name.startswith(prefix) for prefix in prefixes):
                continue
            parts.append((name, xml_text(package.read(name))))
    return parts


def readable_parts(path: Path) -> list[tuple[str, str]]:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return [("file", path.read_text(encoding="utf-8-sig", errors="replace"))]
    if suffix in OFFICE_PART_PREFIXES:
        return office_parts(path)
    return []


def compile_patterns(
    terms: list[str], regex_mode: bool, case_sensitive: bool
) -> list[tuple[str, re.Pattern[str]]]:
    flags = 0 if case_sensitive else re.IGNORECASE
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for term in terms:
        expression = term if regex_mode else re.escape(term)
        patterns.append((term, re.compile(expression, flags=flags)))
    return patterns


def normalize_context(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def scan_text(
    text: str,
    patterns: list[tuple[str, re.Pattern[str]]],
    file_path: Path,
    part: str,
    context: int,
    remaining: int,
) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    for term, pattern in patterns:
        for match in pattern.finditer(text):
            start = max(0, match.start() - context)
            end = min(len(text), match.end() + context)
            matches.append(
                {
                    "file": str(file_path.resolve()),
                    "part": part,
                    "term": term,
                    "matched_text": match.group(0),
                    "character_offset": match.start(),
                    "context": normalize_context(text[start:end]),
                }
            )
            if len(matches) >= remaining:
                return matches
    return matches


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).resolve()
    excluded_paths = {output_path}
    excluded_paths.update(Path(path).resolve() for path in args.terms_file)
    try:
        terms = load_terms(args.term, args.terms_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Failed to load terms: {exc}", file=sys.stderr)
        return 1
    if not terms:
        print("At least one --term or --terms-file is required.", file=sys.stderr)
        return 1

    try:
        patterns = compile_patterns(terms, args.regex, args.case_sensitive)
    except re.error as exc:
        print(f"Invalid regular expression: {exc}", file=sys.stderr)
        return 1

    findings: list[dict[str, object]] = []
    scanned_files: list[str] = []
    skipped_files: list[dict[str, str]] = []
    for path in iter_files(args.paths):
        if len(findings) >= args.max_matches:
            break
        if path.resolve() in excluded_paths:
            continue
        try:
            parts = readable_parts(path)
        except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
            skipped_files.append({"file": str(path.resolve()), "reason": str(exc)})
            continue
        if not parts:
            skipped_files.append(
                {"file": str(path.resolve()), "reason": "Unsupported file type"}
            )
            continue
        scanned_files.append(str(path.resolve()))
        for part, text in parts:
            remaining = args.max_matches - len(findings)
            if remaining <= 0:
                break
            findings.extend(
                scan_text(
                    text,
                    patterns,
                    path,
                    part,
                    args.context,
                    remaining,
                )
            )

    counts: dict[str, int] = {term: 0 for term in terms}
    for finding in findings:
        counts[str(finding["term"])] += 1
    payload = {
        "terms": terms,
        "regex_mode": args.regex,
        "case_sensitive": args.case_sensitive,
        "scanned_file_count": len(scanned_files),
        "skipped_file_count": len(skipped_files),
        "match_count": len(findings),
        "truncated": len(findings) >= args.max_matches,
        "counts_by_term": counts,
        "matches": findings,
        "skipped_files": skipped_files,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Scanned {len(scanned_files)} files; found {len(findings)} matches. "
        f"Report: {output_path}"
    )
    if findings and not args.no_fail:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
