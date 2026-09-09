"""Build only the bound construction list and contents, using the shared Word preset."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

from build_report_docx import build_docx, plain_inline_text, resolve_format_authority
from construction_alignment import render_scope_fragment, render_solution_fragment, validate_manifest
from knowledge_db import sha256_file
from lint_docx_format import lint_docx
from construction_word import audit_word, fidelity, PRESET_ID


def audit_heading_paths(docx: Path, manifest: dict, semantic_styles: dict | None = None) -> dict:
    """Compare actual Word ancestry with manifest ancestry, not merely level continuity."""
    styles = semantic_styles or {}
    names = {styles.get(f"heading_{level}", f"Heading {level}"): level for level in range(1, 8)}
    actual: list[list[str]] = []
    stack: list[str] = []
    in_contents = False
    for paragraph in Document(docx).paragraphs:
        level = names.get(paragraph.style.name)
        if not level:
            continue
        properties = [paragraph._p.pPr]
        style = paragraph.style
        while style is not None:
            properties.append(style.element.find(qn("w:pPr")))
            style = style.base_style
        outline = next((props.find(qn("w:outlineLvl")) for props in properties
                        if props is not None and props.find(qn("w:outlineLvl")) is not None), None)
        if outline is None or outline.get(qn("w:val")) != str(level - 1):
            raise ValueError("Word outline level differs from its semantic heading level")
        if level == 1:
            in_contents = paragraph.text.strip() in {"建设内容", "第2章 建设内容"}
            stack = []
            continue
        if not in_contents:
            continue
        depth = level - 1
        if depth > len(stack) + 1:
            raise ValueError("Word heading level skipped an ancestor")
        stack = stack[:depth - 1] + [paragraph.text.strip()]
        actual.append(list(stack))

    expected: list[list[str]] = []
    previous: list[str] = []
    for item in manifest["application_software_solution"]["items"]:
        parents = item.get("display_parent_path", [])
        common = 0
        while common < min(len(previous), len(parents)) and previous[common] == parents[common]:
            common += 1
        expected.extend(parents[:index + 1] for index in range(common, len(parents)))
        module = parents + [item["original_name"]]
        expected.append(module)
        relative_before: list[str] = []
        section_before = ""
        for fragment in item.get("fragments", []):
            relative = fragment["relative_heading_path"]
            common = 0
            while common < min(len(relative_before), len(relative)) and relative_before[common] == relative[common]:
                common += 1
            section = str(fragment.get("source_section_id") or "")
            if relative and section and section != section_before and relative == relative_before:
                common = len(relative) - 1
            expected.extend(module + relative[:index + 1] for index in range(common, len(relative)))
            relative_before, section_before = relative, section
        previous = parents
    if "heading_tree" in manifest:
        expected = []
        paths = {"construction_content": []}
        for node in manifest["heading_tree"]["nodes"]:
            if node["kind"] == "heading":
                paths[node["node_id"]] = paths[node["parent_id"]] + [node["title"]]
                expected.append(paths[node["node_id"]])
    expected = [[plain_inline_text(value) for value in path] for path in expected]
    if actual != expected:
        raise ValueError("Word heading ancestry differs from the confirmed construction manifest")
    return {"passed": True, "checked_heading_count": len(expected)}


def build_construction_docx(database: Path, manifest: dict, output: Path,
                            project_name: str, *, format_config: Path | None = None) -> dict:
    validation = validate_manifest(database, manifest)
    if not validation.get("valid") or manifest.get("preview_only") or manifest.get("status") == "blocked":
        raise ValueError("construction assembly validation must pass before Word generation")
    if not project_name.strip():
        raise ValueError("project_name is required")
    if any(fragment.get("asset_manifest") or fragment.get("content_format", "plain_text") != "plain_text"
           for item in manifest["application_software_solution"]["items"]
           for fragment in item.get("fragments", [])):
        raise ValueError("rich standard content requires an asset-preserving Word renderer")
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown = (render_scope_fragment(manifest, 1, "第1章 建设清单") + "\n\n"
                + render_solution_fragment(manifest, 1, "第2章 建设内容"))
    summary = build_docx(markdown, output, project_name, mode="construction",
                         format_config=format_config, document_title="建设清单与建设内容",
                         database=database, project_code=manifest["project_code"], construction_manifest=manifest)
    _, authority = resolve_format_authority(None, format_config)
    headings = audit_heading_paths(output, manifest, authority.get("semantic_styles"))
    content_audit = audit_word(output, manifest, authority.get("semantic_styles"))
    if not content_audit["valid"]:
        raise ValueError(f"Word source content audit failed at token {content_audit['first_mismatch']}")
    style_path = authority.get("_resolved_evidence", {}).get("style_contract_path")
    lint = lint_docx(output, Path(style_path) if style_path else None)
    structure_pass = not any(summary.get("markdown_residue", {}).values()) and not int(
        lint.get("summary", {}).get("blocking_count", 0))
    limitation = any(fragment.get("content_format", "plain_text") == "plain_text"
                     for item in manifest["application_software_solution"]["items"]
                     for fragment in item.get("fragments", []))
    result = {
        "document_type": "construction_only", "output_docx": str(output.resolve()),
        "docx_sha256": sha256_file(output), "assembly_manifest_id": manifest["manifest_id"],
        "assembly_manifest_hash": manifest["manifest_hash"], "validation": validation,
        "build_summary": summary, "heading_ancestry_audit": headings, "format_lint": lint,
        "content_audit": content_audit, "fidelity": fidelity(manifest), "preset_id": PRESET_ID,
        "structural_validation_passed": structure_pass, "visual_render_review": "not_run",
        "delivery_ready": False, "plain_text_source_limitation": limitation,
        "next_action": "渲染并检查Word；通过后交付DOCX。不得仅返回中间Markdown或声称已保留源Word全部版式。",
    }
    output.with_suffix(".construction-build.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_docx", type=Path)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--format-config", type=Path)
    args = parser.parse_args()
    result = build_construction_docx(args.database, json.loads(args.manifest.read_text(encoding="utf-8-sig")),
                                     args.output_docx, args.project_name, format_config=args.format_config)
    print(json.dumps({key: result[key] for key in ("output_docx", "structural_validation_passed", "visual_render_review")}, ensure_ascii=False))
    return 0 if result["structural_validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
