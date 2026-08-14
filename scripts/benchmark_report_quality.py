#!/usr/bin/env python3
"""Compare an initial DOCX draft with a human benchmark and project plan."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from audit_delivery_artifact import NS, W_NS, document_metrics
from knowledge_db import apply_migrations, connect, sha256_file


def template_skeleton(text: str) -> str:
    value = re.sub(r"\s+", "", text)
    value = re.sub(r"针对[^，。；：]{2,45}[，：]", "针对<主题>，", value)
    value = re.sub(r"本节关于[^，。；：]{2,45}的论证", "本节关于<主题>的论证", value)
    value = re.sub(
        r"(包括|覆盖|涉及|关联|承载)[^。；]{15,260}?等\d+项建设内容",
        r"\1<范围清单>",
        value,
    )
    value = re.sub(r"从建设方式看，[^。]{20,320}", "从建设方式看，<建设方式>", value)
    value = re.sub(r"《[^》]{4,80}》", "《文件》", value)
    value = re.sub(r"\d+(?:\.\d+)?%?", "<数值>", value)
    return value.casefold()


def basis_group(heading_stack: dict[int, str]) -> str:
    headings = list(heading_stack.values())
    if any("政策" in value and "依据" in value for value in headings):
        return "policy"
    if any(
        any(term in value for term in ("标准", "规范")) and "依据" in value
        for value in headings
    ):
        return "standard"
    return ""


def docx_content_profile(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as package:
        document_root = ET.fromstring(package.read("word/document.xml"))
        styles_root = ET.fromstring(package.read("word/styles.xml"))
    style_names = {}
    for style in styles_root.findall("w:style", NS):
        style_id = style.get(f"{{{W_NS}}}styleId", "")
        name = style.find("w:name", NS)
        style_names[style_id] = name.get(f"{{{W_NS}}}val", style_id) if name is not None else style_id

    current_chapter = "前置部分"
    chapter_chars: dict[str, int] = defaultdict(int)
    body_paragraphs: list[str] = []
    placeholders = Counter()
    template_residue = Counter()
    policy_titles: set[str] = set()
    policy_basis_titles: set[str] = set()
    standard_basis_titles: set[str] = set()
    heading_stack: dict[int, str] = {}
    for paragraph in document_root.findall(".//w:p", NS):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", NS)).strip()
        if not text:
            continue
        style = paragraph.find("./w:pPr/w:pStyle", NS)
        style_id = style.get(f"{{{W_NS}}}val", "") if style is not None else ""
        style_name = style_names.get(style_id, style_id)
        heading = re.fullmatch(r"(?:Heading|标题)\s*([1-7])", style_name, flags=re.I)
        if heading:
            level = int(heading.group(1))
            heading_stack[level] = text
            heading_stack = {key: value for key, value in heading_stack.items() if key <= level}
            if level == 1:
                current_chapter = text
            titles = set(re.findall(r"《([^》]{4,80})》", text))
            current_basis_group = basis_group(heading_stack)
            if current_basis_group == "policy":
                policy_basis_titles.update(titles)
            if current_basis_group == "standard":
                standard_basis_titles.update(titles)
            continue
        compact = re.sub(r"\s+", "", text)
        for marker in (
            "项目数据库",
            "公司能力",
            "标准方案可复用的机制包括",
            "管理与实施机制",
            "当前登记的",
            "本节结合",
            "可评审初稿",
            "工作稿按",
            "候选功能",
        ):
            template_residue[marker] += text.count(marker)
        chapter_chars[current_chapter] += len(compact)
        if len(compact) >= 80:
            body_paragraphs.append(compact)
        for marker in ("待补充", "待确认", "冲突", "分析建议"):
            placeholders[marker] += text.count(f"【{marker}")
        titles = set(re.findall(r"《([^》]{4,80})》", text))
        policy_titles.update(titles)
        current_basis_group = basis_group(heading_stack)
        if current_basis_group == "policy":
            policy_basis_titles.update(titles)
        if current_basis_group == "standard":
            standard_basis_titles.update(titles)

    normalized = [
        re.sub(r"[，。；：、“”‘’（）()\d\s]+", "", paragraph).casefold()
        for paragraph in body_paragraphs
    ]
    normalized_counts = Counter(normalized)
    duplicate_count = sum(count - 1 for count in normalized_counts.values() if count > 1)
    duplicate_examples = []
    seen_examples = set()
    for paragraph, key in zip(body_paragraphs, normalized):
        if normalized_counts[key] <= 1 or key in seen_examples:
            continue
        seen_examples.add(key)
        duplicate_examples.append(
            {"count": normalized_counts[key], "text_sample": paragraph[:180]}
        )
    duplicate_examples.sort(key=lambda item: (-item["count"], item["text_sample"]))
    skeletons = [template_skeleton(paragraph) for paragraph in body_paragraphs]
    skeleton_counts = Counter(skeletons)
    skeleton_duplicate_count = sum(count - 1 for count in skeleton_counts.values() if count > 1)
    skeleton_examples = []
    seen_skeletons = set()
    for paragraph, key in zip(body_paragraphs, skeletons):
        if skeleton_counts[key] <= 1 or key in seen_skeletons:
            continue
        seen_skeletons.add(key)
        skeleton_examples.append(
            {"count": skeleton_counts[key], "text_sample": paragraph[:180]}
        )
    skeleton_examples.sort(key=lambda item: (-item["count"], item["text_sample"]))
    return {
        "chapter_chars": dict(chapter_chars),
        "body_paragraphs": len(body_paragraphs),
        "duplicate_substantive_paragraphs": duplicate_count,
        "duplicate_ratio": round(duplicate_count / max(1, len(body_paragraphs)), 4),
        "duplicate_examples": duplicate_examples[:20],
        "template_skeleton_duplicate_paragraphs": skeleton_duplicate_count,
        "template_skeleton_ratio": round(
            skeleton_duplicate_count / max(1, len(body_paragraphs)), 4
        ),
        "template_skeleton_examples": skeleton_examples[:20],
        "placeholders": dict(placeholders),
        "template_residue": dict(template_residue),
        "policy_title_mentions": len(policy_titles),
        "policy_titles": sorted(policy_titles),
        "policy_basis_title_mentions": len(policy_basis_titles),
        "policy_basis_titles": sorted(policy_basis_titles),
        "standard_basis_title_mentions": len(standard_basis_titles),
        "standard_basis_titles": sorted(standard_basis_titles),
    }


def project_expectations(database: Path, project_code: str) -> dict[str, Any]:
    with connect(database.resolve()) as conn:
        apply_migrations(conn)
        project = conn.execute(
            "SELECT project_id FROM project WHERE project_code=?", (project_code,)
        ).fetchone()
        if project is None:
            raise RuntimeError(f"project_code {project_code} is not initialized")
        project_id = project["project_id"]
        required_chars = conn.execute(
            """
            SELECT COALESCE(SUM(length_min),0) FROM section_composition_plan
            WHERE project_id=? AND applicability_status<>'not_applicable'
            """,
            (project_id,),
        ).fetchone()[0]
        outline_nodes = [
            dict(row)
            for row in conn.execute(
                """
                SELECT n.heading_level,n.title,n.chapter_code,n.status
                FROM section_outline_node n
                JOIN section_composition_plan p ON p.plan_id=n.plan_id
                WHERE p.project_id=? AND p.applicability_status<>'not_applicable'
                ORDER BY p.chapter_code,n.ordinal
                """,
                (project_id,),
            )
        ]
        confirmed_policies = [
            row["title"]
            for row in conn.execute(
                """
                SELECT DISTINCT d.title
                FROM project_policy_match m
                JOIN policy_document d ON d.policy_id=m.policy_id
                JOIN policy_match_run r ON r.match_run_id=m.match_run_id
                WHERE r.project_id=? AND m.decision_status='user_confirmed'
                """,
                (project_id,),
            )
        ]
    return {
        "required_chars": int(required_chars or 0),
        "outline_nodes": outline_nodes,
        "confirmed_policy_titles": confirmed_policies,
    }


def evaluate(
    candidate: Path,
    *,
    reference: Path | None = None,
    database: Path | None = None,
    project_code: str = "",
    minimum_reference_ratio: float = 0.35,
) -> dict[str, Any]:
    candidate = candidate.resolve()
    candidate_metrics = document_metrics(candidate)
    candidate_profile = docx_content_profile(candidate)
    reference_metrics = document_metrics(reference.resolve()) if reference else None
    reference_profile = docx_content_profile(reference.resolve()) if reference else None
    expectations = (
        project_expectations(database, project_code)
        if database is not None and project_code
        else {"required_chars": 0, "outline_nodes": [], "confirmed_policy_titles": []}
    )
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def add(target: list[dict[str, str]], code: str, description: str) -> None:
        target.append({"code": code, "description": description})

    if any(candidate_metrics["markdown_residue"].values()):
        add(blockers, "markdown_residue", "Word 正文仍含 Markdown 标记。")
    required_chars = expectations["required_chars"]
    if reference_metrics:
        required_chars = max(
            required_chars,
            int(reference_metrics["visible_body_chars"] * minimum_reference_ratio),
        )
    if candidate_metrics["visible_body_chars"] < required_chars:
        add(
            blockers,
            "benchmark_body_depth",
            f"有效正文 {candidate_metrics['visible_body_chars']} 字，低于基准下限 {required_chars} 字。",
        )
    required_paragraphs = max(2, expectations["required_chars"] // 500)
    if reference_metrics:
        required_paragraphs = max(
            required_paragraphs,
            int(reference_metrics["substantive_paragraphs"] * 0.30),
        )
    if candidate_metrics["substantive_paragraphs"] < required_paragraphs:
        add(
            blockers,
            "benchmark_argument_depth",
            f"实质论证段 {candidate_metrics['substantive_paragraphs']} 个，低于基准下限 {required_paragraphs} 个。",
        )
    if candidate_metrics["average_substantive_chars"] < 110:
        add(warnings, "thin_paragraphs", "实质段平均长度不足，需补充机制、流程、数据和验收论证。")
    if candidate_profile["duplicate_ratio"] > 0.08:
        add(
            blockers,
            "repetitive_prose",
            f"实质段完全重复率 {candidate_profile['duplicate_ratio']:.1%}，超过 8%。",
        )
    reference_skeleton_ratio = (
        reference_profile["template_skeleton_ratio"] if reference_profile else 0.0
    )
    allowed_skeleton_ratio = max(0.04, reference_skeleton_ratio * 4)
    if candidate_profile["template_skeleton_ratio"] > allowed_skeleton_ratio:
        add(
            blockers,
            "template_skeleton_repetition",
            (
                f"改写型模板骨架重复率 {candidate_profile['template_skeleton_ratio']:.1%}，"
                f"超过基准允许值 {allowed_skeleton_ratio:.1%}。"
            ),
        )
    hard_template_residue = sum(
        candidate_profile["template_residue"].get(marker, 0)
        for marker in ("项目数据库", "公司能力", "标准方案可复用的机制包括", "管理与实施机制")
    )
    soft_template_residue = sum(
        max(0, candidate_profile["template_residue"].get(marker, 0) - limit)
        for marker, limit in {
            "当前登记的": 2,
            "本节结合": 2,
            "可评审初稿": 2,
            "工作稿按": 1,
            "候选功能": 3,
        }.items()
    )
    if hard_template_residue or soft_template_residue:
        residues = {
            key: value
            for key, value in candidate_profile["template_residue"].items()
            if value
        }
        add(
            blockers,
            "template_prose_residue",
            "正文仍有批量生成痕迹："
            + "、".join(f"{key}×{value}" for key, value in sorted(residues.items())),
        )

    expected_outline = expectations["outline_nodes"]
    if expected_outline:
        actual_titles = {
            (item["level"], re.sub(r"^\d+(?:\.\d+){3,6}\s+", "", item["text"]).strip())
            for item in candidate_metrics["heading_titles"]
        }
        missing = [
            node for node in expected_outline
            if (int(node["heading_level"]), node["title"].strip()) not in actual_titles
        ]
        if missing:
            add(
                blockers,
                "scope_capability_outline_coverage",
                "建设清单—能力—功能目录未完整承载："
                + "、".join(f"{node['chapter_code']} {node['title']}" for node in missing[:12]),
            )
    elif reference_metrics:
        reference_deep = sum(reference_metrics["heading_levels"][str(level)] for level in range(4, 8))
        candidate_deep = sum(candidate_metrics["heading_levels"][str(level)] for level in range(4, 8))
        if candidate_deep < max(12, int(reference_deep * 0.25)):
            add(blockers, "shallow_heading_tree", "四至七级功能标题明显少于真人参照稿。")

    confirmed_policy_titles = expectations["confirmed_policy_titles"]
    if confirmed_policy_titles:
        missing_policy_titles = [
            title for title in confirmed_policy_titles if title not in candidate_profile["policy_titles"]
        ]
        if missing_policy_titles:
            add(
                blockers,
                "confirmed_policy_not_carried",
                "已确认政策未进入正文：" + "、".join(missing_policy_titles[:8]),
            )
    elif reference_profile:
        required_policy_mentions = min(8, max(3, reference_profile["policy_title_mentions"] // 3))
        if candidate_profile["policy_title_mentions"] < required_policy_mentions:
            add(
                warnings,
                "policy_basis_thin",
                f"正文仅识别到 {candidate_profile['policy_title_mentions']} 项政策/标准名称，建议至少 {required_policy_mentions} 项。",
            )
    if reference_profile:
        reference_basis_count = (
            reference_profile["policy_basis_title_mentions"]
            + reference_profile["standard_basis_title_mentions"]
        )
        required_basis_count = min(20, max(10, reference_basis_count // 4))
        candidate_basis_count = (
            candidate_profile["policy_basis_title_mentions"]
            + candidate_profile["standard_basis_title_mentions"]
        )
        if candidate_basis_count < required_basis_count:
            add(
                blockers,
                "basis_catalog_thin",
                f"编制依据章节仅列示 {candidate_basis_count} 项政策/标准，低于真人基准下限 {required_basis_count} 项。",
            )
        if candidate_profile["policy_basis_title_mentions"] < 4:
            add(blockers, "policy_basis_group_thin", "政策法规依据少于 4 项。")
        if candidate_profile["standard_basis_title_mentions"] < 4:
            add(blockers, "standard_basis_group_thin", "标准规范与评价依据少于 4 项。")

    if reference_metrics:
        required_tables = min(12, max(6, reference_metrics["tables"] // 3))
        if candidate_metrics["tables"] < required_tables:
            add(warnings, "insufficient_tables", f"有效表格少于基准建议值 {required_tables} 个。")

    score = max(0, 100 - 15 * len(blockers) - 5 * len(warnings))
    public_candidate_metrics = {
        key: value for key, value in candidate_metrics.items() if key != "heading_titles"
    }
    public_candidate_metrics["heading_title_count"] = len(candidate_metrics["heading_titles"])
    public_candidate_metrics["heading_title_sample"] = candidate_metrics["heading_titles"][:30]
    public_reference_metrics = None
    if reference_metrics is not None:
        public_reference_metrics = {
            key: value for key, value in reference_metrics.items() if key != "heading_titles"
        }
        public_reference_metrics["heading_title_count"] = len(reference_metrics["heading_titles"])
        public_reference_metrics["heading_title_sample"] = reference_metrics["heading_titles"][:30]
    return {
        "schema_version": "1.0",
        "candidate": str(candidate),
        "candidate_sha256": sha256_file(candidate),
        "reference": str(reference.resolve()) if reference else "",
        "database": str(database.resolve()) if database else "",
        "project_code": project_code,
        "status": "failed" if blockers else "warning" if warnings else "passed",
        "score": score,
        "required_body_chars": required_chars,
        "required_substantive_paragraphs": required_paragraphs,
        "candidate_metrics": public_candidate_metrics,
        "candidate_profile": candidate_profile,
        "reference_metrics": public_reference_metrics,
        "expectations": {
            "planned_body_chars": expectations["required_chars"],
            "outline_node_count": len(expected_outline),
            "confirmed_policy_count": len(confirmed_policy_titles),
        },
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "blockers": blockers,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--project-code", default="")
    parser.add_argument("--minimum-reference-ratio", type=float, default=0.35)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(
        args.candidate,
        reference=args.reference,
        database=args.database,
        project_code=args.project_code,
        minimum_reference_ratio=args.minimum_reference_ratio,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
