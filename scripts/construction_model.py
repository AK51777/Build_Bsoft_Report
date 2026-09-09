"""Pure construction-only provenance and heading contracts; no database writes."""
from __future__ import annotations

import copy
import re
import unicodedata
from collections import defaultdict

from knowledge_db import dump_json, sha256_text, stable_id


MODEL_VERSION = "construction-model-v1"


def fingerprint(value):
    return sha256_text(dump_json(value))


def same_title(a, b):
    return unicodedata.normalize("NFKC", str(a)).strip() == unicodedata.normalize("NFKC", str(b)).strip()


def compact_path(values):
    result = []
    for value in values:
        title = str(value).strip()
        if title and (not result or not same_title(result[-1], title)):
            result.append(title)
    return result


def derive_scope(original, plan=None):
    """Keep every original row; expansion/heading roles must be shown in review."""
    original = copy.deepcopy(original)
    plan = plan or []
    changes = {int(entry["original_ordinal"]): entry for entry in plan}
    if len(changes) != len(plan):
        raise ValueError("duplicate original_ordinal in structure plan")
    if not original.get("source", {}).get("sha256"):
        raise ValueError("original scope requires a source hash")
    rows, ledger = [], []
    ordinal = 0
    for sheet in original.get("sheets", []):
        columns = sheet.get("detected_columns", {})
        for raw in sheet.get("rows", []):
            ordinal += 1
            change = changes.get(ordinal, {})
            names = [str(raw.get(h) or "").strip() for h in columns.get("original_name", [])]
            names = [n for n in names if n]
            domains = [str(raw.get(h) or "").strip() for h in columns.get("domain", [])]
            parents = compact_path(change.get("parent_path", domains + names[:-1]))
            row_id = stable_id("ORIGINALROW", original["source"]["sha256"], sheet.get("name"), raw.get("_source_row"), ordinal, fingerprint(raw))
            role = change.get("role", "module")
            if role not in {"module", "heading_only"}:
                raise ValueError(f"unsupported row role: {role}")
            title = str(change.get("title") or (names[-1] if names else "")).strip()
            if not title:
                raise ValueError(f"original row {ordinal} needs a name or an explicit heading title")
            modules = change.get("modules", [{"name": title}]) if role == "module" else []
            if role == "module" and not modules:
                raise ValueError("module row cannot silently disappear")
            entry = {"original_row_id": row_id, "original_ordinal": ordinal,
                     "worksheet_name": sheet.get("name", ""), "source_row": raw.get("_source_row"),
                     "display_hash": fingerprint(raw), "role": role, "title": title,
                     "parent_path": parents, "assembly_count": len(modules)}
            ledger.append(entry)
            for index, module in enumerate(modules, 1):
                if not isinstance(module, dict) or not str(module.get("name") or "").strip():
                    raise ValueError("expanded module requires name")
                path = compact_path(module.get("parent_path", parents))
                rows.append({"_source_row": len(rows) + 2, "原始序号": str(ordinal),
                             "所属路径": " / ".join(path), "模块名称": str(module["name"]).strip(),
                             "_display_parent_path": path, "_original_row_id": row_id,
                             "_original_ordinal": ordinal, "_expansion_index": index,
                             "_display_ref": f"{ordinal}.{index}" if len(modules) > 1 else str(ordinal),
                             "_suggested_choice": copy.deepcopy(module.get("choice", {}))})
    if set(changes) - set(range(1, ordinal + 1)):
        raise ValueError("structure plan references an unknown original row")
    if not rows:
        raise ValueError("scope contains no assembly modules")
    return {"source": copy.deepcopy(original["source"]), "sheet_count": 1,
            "sheets": [{"name": "装配模块", "headers": ["原始序号", "所属路径", "模块名称"],
                        "detected_columns": {"original_name": ["模块名称"], "domain": ["所属路径"]}, "rows": rows}],
            "original_display_payload": original, "original_rows": ledger,
            "structure_plan": copy.deepcopy(plan), "model_version": MODEL_VERSION}


def traceability(payload, captured_rows):
    original = payload.get("original_display_payload", payload)
    ledger = copy.deepcopy(payload.get("original_rows", []))
    if ledger:
        rebuilt = derive_scope(original, payload.get("structure_plan", []))
        if rebuilt != payload:
            raise ValueError("derived scope does not match original rows and structure plan")
    edges = []
    for row in captured_rows:
        cells = row.get("display_cells") or row.get("display_cells_json", {})
        if isinstance(cells, str):
            import json
            cells = json.loads(cells)
        original_id = cells.get("_original_row_id", row["scope_row_id"])
        ordinal = cells.get("_original_ordinal", row["source_ordinal"])
        if not payload.get("original_rows"):
            ledger.append({"original_row_id": original_id, "original_ordinal": ordinal,
                           "role": "module", "assembly_count": 1, "title": "",
                           "parent_path": [], "display_hash": fingerprint(cells)})
        edges.append({"original_row_id": original_id, "original_ordinal": ordinal,
                      "scope_row_id": row["scope_row_id"], "assembly_order": row["source_ordinal"],
                      "display_ref": cells.get("_display_ref", str(ordinal)),
                      "expansion_index": cells.get("_expansion_index", 1)})
    by_id = {r["original_row_id"]: r for r in ledger}
    counts = defaultdict(int)
    for edge in edges:
        if edge["original_row_id"] not in by_id:
            raise ValueError("assembly item has no original row")
        counts[edge["original_row_id"]] += 1
    if any(counts[r["original_row_id"]] != r["assembly_count"] for r in ledger):
        raise ValueError("original row coverage or expansion count mismatch")
    return {"original_display_hash": fingerprint(original), "original_rows": ledger,
            "mapping_edges": edges, "original_row_count": len(ledger), "assembly_item_count": len(edges),
            "heading_only_count": sum(r["role"] == "heading_only" for r in ledger)}


def duplicate_mappings(items, *, candidates=False):
    groups = defaultdict(list)
    for item in items:
        choice = item
        if candidates:
            options = item.get("candidates", [])
            choice = next((c for c in options if c["candidate_id"] == item.get("auto_selected_candidate_id")), options[0] if options else {})
        root = choice.get("root_heading_path", [])
        blocks = choice.get("subtree_block_ids", choice.get("block_ids", []))
        if not root or not blocks:
            continue
        # Exact subtree identity also catches two capability aliases of the same body.
        key = fingerprint({"root": root, "blocks": blocks})
        groups[key].append({"scope_row_id": item["scope_row_id"],
                            "source_ordinal": item["source_ordinal"],
                            "original_name": item["original_name"],
                            "capability_id": choice.get("capability_id", "")})
    return [{"subtree_hash": key, "items": values, "action": "keep_separate",
             "message": "多项映射到同一标准子树；默认分别保留，合并需明确确认。"}
            for key, values in groups.items() if len(values) > 1]


def build_heading_tree(items, provenance=None):
    """Ordered semantic nodes: one owner for grouping, module and source headings."""
    nodes, stack = [], []

    def heading_path(path, owner="", force_leaf=False):
        path = compact_path(path)
        if len(path) > 6:
            raise ValueError("heading_depth_exceeded: review parent paths; do not flatten child headings")
        common = 0
        while common < min(len(stack), len(path)) and same_title(stack[common][0], path[common]):
            common += 1
        if force_leaf and common == len(path) and path:
            common -= 1
        del stack[common:]
        for title in path[common:]:
            node_id = stable_id("HEADING", len(nodes), [s[0] for s in stack], title, owner)
            node = {"kind": "heading", "node_id": node_id,
                    "parent_id": stack[-1][1] if stack else "construction_content",
                    "level": len(stack) + 2, "title": title, "scope_row_id": owner}
            nodes.append(node)
            stack.append((title, node_id))

    item_map = {item["scope_row_id"]: item for item in items}
    sequence = []
    if provenance:
        for raw in provenance["original_rows"]:
            if raw["role"] == "heading_only":
                sequence.append(("heading", raw))
            else:
                sequence.extend(("item", item_map[e["scope_row_id"]]) for e in provenance["mapping_edges"]
                                if e["original_row_id"] == raw["original_row_id"] and e["scope_row_id"] in item_map)
    else:
        sequence = [("item", i) for i in items]
    for kind, item in sequence:
        if kind == "heading":
            heading_path(item["parent_path"] + [item["title"]])
            continue
        owner = item["scope_row_id"]
        base = compact_path(item.get("display_parent_path", []) + [item["original_name"]])
        # A second module never disappears merely because its label equals the previous one.
        heading_path(base, owner, force_leaf=True)
        if item["status"] != "verbatim":
            nodes.append({"kind": "placeholder", "parent_id": stack[-1][1], "scope_row_id": owner,
                          "text": "【待补充】" if item["status"] == "pending_supplement" else "【待确认】"})
        last_relative, last_section = None, None
        for fragment in item.get("fragments", []):
            relative = fragment["relative_heading_path"]
            section = fragment.get("source_section_id")
            heading_path(base + relative, owner, force_leaf=bool(relative and relative == last_relative and section and section != last_section))
            nodes.append({"kind": "fragment", "parent_id": stack[-1][1], "scope_row_id": owner,
                          "block_id": fragment["block_id"], "text_hash": fragment["text_hash"]})
            last_relative, last_section = relative, section
    return {"version": MODEL_VERSION, "nodes": nodes, "tree_hash": fingerprint(nodes)}


def render_tree(manifest, chapter_level=2, title="应用软件建设方案"):
    fragments = {(i["scope_row_id"], f["block_id"]): f for i in manifest["application_software_solution"]["items"] for f in i["fragments"]}
    lines = ["#" * chapter_level + " " + title, ""]
    for node in manifest["heading_tree"]["nodes"]:
        if node["kind"] == "heading":
            if node["level"] + chapter_level - 1 > 7:
                raise ValueError("heading_depth_exceeded: review parent paths; do not flatten child headings")
            lines.extend(["#" * (node["level"] + chapter_level - 1) + " " + node["title"], ""])
        elif node["kind"] == "placeholder":
            lines.extend([node["text"], ""])
        else:
            lines.extend([fragments[(node["scope_row_id"], node["block_id"])]["clean_text"], ""])
    return "\n".join(lines)
