#!/usr/bin/env python3
"""Summarize a format-lint JSON report into an actionable Markdown review."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


LABELS = {
    "leading_whitespace": "段首空格/Tab伪缩进",
    "direct_paragraph_indent": "段落直接缩进",
    "direct_paragraph_spacing": "段落直接间距",
    "direct_tab_stops": "直接制表位",
    "heading_like_without_heading_style": "疑似标题未绑定标题样式",
    "heading_run_direct_formatting": "标题内直接字符格式",
    "numbering_level_mismatch": "标题编号层级不一致",
    "unknown_style_id": "未知样式ID",
    "unstyled_paragraph": "无可识别默认样式",
}


def summarize(payload: dict) -> str:
    summary = payload["summary"]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for issue in payload.get("issues", []):
        grouped[issue["issue_type"]].append(issue)
    lines = [
        "# Word格式与缩进核验摘要",
        "",
        f"- 文件：`{payload['file_path']}`",
        f"- 文件哈希：`{payload['file_hash']}`",
        f"- 非空段落：{summary['nonempty_paragraph_count']}；发现问题记录：{summary['issue_count']}。",
        f"- 高风险：{summary['severity_counts'].get('high', 0)}；中风险：{summary['severity_counts'].get('medium', 0)}；低风险：{summary['severity_counts'].get('low', 0)}。",
        "- 本轮只检测，不修改源文件；数量表示段落级偏差记录，不等于需要逐条手工修复的次数。",
        "",
        "## 类型统计与处理优先级",
        "",
        "| 优先级 | 类型 | 数量 | 处理建议 |",
        "|---|---|---:|---|",
    ]
    priority = [
        ("P0", "heading_like_without_heading_style", "人工确认真实标题后绑定标题样式和outlineLvl。"),
        ("P0", "direct_tab_stops", "核对目录、列表和表格白名单；其余清除直接制表位。"),
        ("P0", "leading_whitespace", "清除用于模拟缩进的Tab、半角和全角空格。"),
        ("P1", "heading_run_direct_formatting", "将字体字号收敛到标题样式，保留明确例外。"),
        ("P1", "direct_paragraph_indent", "按正文、列表、表格和题注分别归一到样式契约。"),
        ("P2", "direct_paragraph_spacing", "在完成语义分类后统一段前、段后和行距。"),
    ]
    for level, issue_type, action in priority:
        lines.append(f"| {level} | {LABELS[issue_type]} | {len(grouped.get(issue_type, []))} | {action} |")
    lines.extend(["", "## 高风险样例", "", "| 段落 | 类型 | 样式 | 文本摘录 |", "|---:|---|---|---|"])
    high_examples = [issue for issue in payload.get("issues", []) if issue["severity"] == "high"][:20]
    for issue in high_examples:
        excerpt = issue["text_excerpt"].replace("|", "｜").replace("\n", " ")
        lines.append(f"| {issue['paragraph_index']} | {LABELS.get(issue['issue_type'], issue['issue_type'])} | {issue['style_id']} | {excerpt} |")
    if not high_examples:
        lines.append("| - | 无 | - | - |")
    lines.extend(
        [
            "",
            "## 建议修复路径",
            "",
            "1. 复制Word候选稿并冻结当前哈希，禁止直接改原文件。",
            "2. 先处理疑似标题、自动编号和制表位，避免目录和层级在批量修改后失真。",
            "3. 再清理段首空白并按语义角色绑定正文、列表、表格、题注等样式。",
            "4. 最后统一字体、字号、缩进、段前段后和行距；保留经确认的例外清单。",
            "5. 每批修复后重新打开、更新目录、渲染并复跑格式检查。",
            "",
            "## 当前结论",
            "",
            "该文档可以作为格式画像候选，但存在较多直接格式和伪缩进，尚不适合直接作为可批量修改的稳定样式模板。正式冻结前需要用户确认，并完成复制稿上的样式归一和视觉复核。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(summarize(payload), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "issues": payload["summary"]["issue_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
