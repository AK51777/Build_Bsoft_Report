#!/usr/bin/env python3
"""Audit rendered report pages and build contact sheets for visual review."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat


def page_number(path: Path) -> int:
    match = re.search(r"(\d+)$", path.stem)
    return int(match.group(1)) if match else 0


def page_metrics(path: Path) -> dict:
    with Image.open(path) as source:
        image = source.convert("RGB")
    width, height = image.size
    white = Image.new("RGB", image.size, "white")
    difference = ImageChops.difference(image, white).convert("L")
    bbox = difference.point(lambda value: 255 if value > 12 else 0).getbbox()
    dark = difference.point(lambda value: 255 if value > 40 else 0)
    dark_ratio = ImageStat.Stat(dark).mean[0] / 255
    edge = max(3, round(min(width, height) * 0.008))
    edge_pixels = [
        difference.crop((0, 0, width, edge)),
        difference.crop((0, height - edge, width, height)),
        difference.crop((0, 0, edge, height)),
        difference.crop((width - edge, 0, width, height)),
    ]
    edge_touch = any(crop.point(lambda value: 255 if value > 20 else 0).getbbox() for crop in edge_pixels)
    return {
        "page": page_number(path),
        "file": str(path.resolve()),
        "width": width,
        "height": height,
        "content_bbox": list(bbox) if bbox else None,
        "dark_ratio": round(dark_ratio, 5),
        "blank": bbox is None or dark_ratio < 0.00015,
        "dense": dark_ratio > 0.22,
        "edge_touch": bool(edge_touch),
    }


def contact_sheet(
    paths: list[Path],
    output: Path,
    *,
    columns: int,
    thumb_width: int,
    thumb_height: int,
) -> None:
    rows = (len(paths) + columns - 1) // columns
    label_height = 18
    sheet = Image.new(
        "RGB",
        (columns * thumb_width, rows * (thumb_height + label_height)),
        "#d9d9d9",
    )
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        with Image.open(path) as source:
            image = source.convert("RGB")
            image.thumbnail((thumb_width - 4, thumb_height - 4))
        column = index % columns
        row = index // columns
        left = column * thumb_width + (thumb_width - image.width) // 2
        top = row * (thumb_height + label_height) + 2
        sheet.paste(image, (left, top))
        draw.text(
            (column * thumb_width + 4, row * (thumb_height + label_height) + thumb_height),
            f"p.{page_number(path)}",
            fill="black",
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=90)


def audit(rendered_dir: Path, contact_dir: Path) -> dict:
    pages = sorted(rendered_dir.glob("*.png"), key=page_number)
    if not pages:
        raise RuntimeError(f"no PNG pages found in {rendered_dir}")
    metrics = [page_metrics(path) for path in pages]
    contact_dir.mkdir(parents=True, exist_ok=True)
    overview = contact_dir / "overview-all-pages.jpg"
    contact_sheet(pages, overview, columns=14, thumb_width=80, thumb_height=113)
    detailed = []
    batch_size = 20
    for start in range(0, len(pages), batch_size):
        batch = pages[start : start + batch_size]
        output = contact_dir / f"pages-{page_number(batch[0]):04d}-{page_number(batch[-1]):04d}.jpg"
        contact_sheet(batch, output, columns=5, thumb_width=220, thumb_height=311)
        detailed.append(str(output.resolve()))
    return {
        "schema_version": "1.0",
        "rendered_dir": str(rendered_dir.resolve()),
        "page_count": len(pages),
        "portrait_page_count": sum(item["height"] > item["width"] for item in metrics),
        "landscape_page_count": sum(item["width"] > item["height"] for item in metrics),
        "blank_pages": [item["page"] for item in metrics if item["blank"]],
        "dense_pages": [item["page"] for item in metrics if item["dense"]],
        "edge_touch_pages": [item["page"] for item in metrics if item["edge_touch"]],
        "overview_contact_sheet": str(overview.resolve()),
        "detailed_contact_sheets": detailed,
        "status": "failed" if any(item["edge_touch"] for item in metrics) else "warning" if any(item["blank"] or item["dense"] for item in metrics) else "passed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rendered_dir", type=Path)
    parser.add_argument("contact_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.rendered_dir.resolve(), args.contact_dir.resolve())
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
