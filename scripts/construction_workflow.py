"""Prepare one review, confirm once, then resume source-bound construction Word generation."""
from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import re
import sys
import subprocess
from pathlib import Path

from construction_alignment import (capture_scope_snapshot, match_scope, apply_decisions, assemble,
                                    validate_manifest, _reviewed_parent_paths)
from construction_model import derive_scope, traceability, fingerprint, duplicate_mappings
from construction_word import generate_word, audit_word, fidelity
from extract_xlsx_scope import build_payload, detect_columns
from ingest_scope_items_sqlite import ingest_scope_payload
from knowledge_db import connect, connect_readonly, apply_migrations, upsert_project, now_iso, sha256_file
from knowledge_snapshot import validate_snapshots
from local_knowledge_packages import load_config, package_status, sync_to_project

VERSION = "construction-workflow-v1"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def source_payload(path):
    path = Path(path).resolve()
    suffix = path.suffix.lower()
    if suffix == ".json":
        return read(path)
    if suffix == ".xlsx":
        return build_payload(path, requested_sheets=None, header_row=None, max_rows=5000)
    tables = []
    if suffix in {".csv", ".tsv"}:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            tables = [list(csv.reader(stream, delimiter="\t" if suffix == ".tsv" else ","))]
    elif suffix in {".md", ".txt"}:
        from build_report_docx import parse_table_row
        table = []
        for line in path.read_text(encoding="utf-8-sig").splitlines() + [""]:
            if line.strip().startswith("|"):
                cells = parse_table_row(line)
                if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    table.append(cells)
            elif table:
                tables.append(table); table = []
    elif suffix == ".docx":
        from docx import Document
        tables = [[[cell.text for cell in row.cells] for row in table.rows] for table in Document(path).tables]
    else:
        raise ValueError("supported scope formats: xlsx, csv, tsv, docx tables, md tables, extracted JSON")
    sheets = []
    for index, table in enumerate(tables, 1):
        if not table or len(table) < 2:
            continue
        headers = table[0]
        if len(headers) != len(set(headers)) or not all(headers):
            raise ValueError("scope headers must be unique and nonempty")
        columns = detect_columns(headers)
        if not columns.get("original_name"):
            raise ValueError("scope table needs a system/module name column; preserve ambiguous columns in extracted JSON")
        if any(len(row) != len(headers) for row in table[1:]):
            raise ValueError("scope row width differs from header; resolve before normalization")
        sheets.append({"name": f"清单{index}", "headers": headers, "detected_columns": columns,
                       "rows": [{"_source_row": n, **dict(zip(headers, values))} for n, values in enumerate(table[1:], 2)]})
    if not sheets:
        raise ValueError("no construction scope tables found")
    return {"source": {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size},
            "sheet_count": len(sheets), "sheets": sheets}


def runtime_hash(stage="word"):
    names = ["construction_model.py", "construction_alignment.py"] if stage == "assembly" else [
             "construction_word.py", "build_construction_docx.py", "build_report_docx.py", "lint_docx_format.py"]
    return fingerprint({n: sha256_file(Path(__file__).with_name(n)) for n in names})


def paths(root):
    root = Path(root).resolve()
    return root, root / "运行数据", root / "运行数据/construction-state.json"


def ensure_knowledge(database, project_code, config_path=None, package_id=""):
    with connect(database) as conn:
        apply_migrations(conn)
        row = conn.execute("SELECT project_id FROM project WHERE project_code=?", (project_code,)).fetchone()
        if not row:
            upsert_project(conn, {"project_code": project_code, "official_name": project_code})
        conn.commit()
        snapshots = conn.execute("SELECT s.source_id FROM shared_knowledge_snapshot s JOIN project p ON p.project_id=s.project_id WHERE p.project_code=? AND s.source_type='knowledge_package' AND s.snapshot_status='current'", (project_code,)).fetchall()
    if snapshots:
        ids = [r["source_id"] for r in snapshots]
        if not package_id and len(ids) != 1:
            raise ValueError("select a unique standard knowledge package")
        package_id = package_id or ids[0]
        result = validate_snapshots(database, project_code, package_ids=[package_id], catalog_ids=[], allow_stale=False, source_types={"knowledge_package"})
        return package_id, {"source": "kept_project_snapshot", "validation": result}
    config = load_config(Path(config_path) if config_path else None, required_kinds=("standard",))
    status = package_status(config)
    if status["status"] == "blocked":
        raise ValueError("standard knowledge package missing or invalid")
    result = sync_to_project(config, project_database=database, project_code=project_code, construction_only=True)
    with connect_readonly(database) as conn:
        ids = [r[0] for r in conn.execute("SELECT s.source_id FROM shared_knowledge_snapshot s JOIN project p ON p.project_id=s.project_id WHERE p.project_code=? AND s.source_type='knowledge_package' AND s.snapshot_status='current'", (project_code,))]
    if len(ids) != 1 or (package_id and package_id != ids[0]):
        raise ValueError("requested standard package differs from synchronized package")
    return ids[0], {"source": "standard_local_package", "status": status, "sync": result}


def review_markdown(review):
    trace = review["traceability"]
    refs = {e["scope_row_id"]: e["display_ref"] for e in trace["mapping_edges"]}
    lines = ["# 请确认建设清单对照", "", "确认后自动生成 Word；只需在这一页核对。", "",
             f"原始清单 {trace['original_row_count']} 行，装配模块 {trace['assembly_item_count']} 项，标题行 {trace['heading_only_count']} 项。", "",
             "同意本页全部建议可回复：**全部按建议处理并生成Word**。有未给出可用建议的项目，请明确选择；不会自动确认。", "",
             "## 范围和目录处理", ""]
    if not any(r["role"] == "heading_only" or r["assembly_count"] != 1 for r in trace["original_rows"]):
        lines.append("原始行按一对一装配，目录归属见下表。")
    for raw in trace["original_rows"]:
        if raw["role"] == "heading_only" or raw["assembly_count"] != 1:
            lines.append(f"- 第{raw['original_ordinal']}项 {raw['title']}：" + ("仅作标题" if raw["role"] == "heading_only" else f"展开为{raw['assembly_count']}个模块"))
    lines.extend(["", "## 可能重复的映射", ""])
    for group in review["duplicate_mappings"]:
        lines.append("- " + "、".join(f"第{refs[i['scope_row_id']]}项 {i['original_name']}" for i in group["items"]) + "：" + group["message"])
    if not review["duplicate_mappings"]:
        lines.append("未发现首选子树重复。")
    lines.extend(["", "## 逐项建议", "", "|原始序号|清单项|建议父目录|建议对应模块|状态|", "|---|---|---|---|---|"])
    for item in sorted(review["items"], key=lambda i: (i["proposal"].get("decision") != "confirmed_gap", i["state"] == "auto_confirmed_exact", i["source_ordinal"])):
        proposal = item["proposal"]
        title = proposal.get("choice_label", "需逐项指定")
        state = "缺口【待补充】" if proposal.get("decision") == "confirmed_gap" else ("已精确匹配" if item["state"] == "auto_confirmed_exact" else "请确认")
        cells = [refs[item["scope_row_id"]], item["original_name"], " → ".join(item["parent_path"]), title, state]
        lines.append("|" + "|".join(str(c).replace("|", "\\|").replace("\n", " ") for c in cells) + "|")
    lines.extend(["", "## 来源保真范围", "", "文本与基础表格按标准块装配。原包若只有文本，不能保证原 Word 图片和复杂版式。"])
    if review.get("source_limitations", {}).get("dangling_image_reference_blocks", 0):
        lines.append(f"候选中有 {review['source_limitations']['dangling_image_reference_blocks']} 个块存在图示引用；请核对原包资产。")
    lines.extend(["", f"核对版本：`{review['review_id']}`；全部确认绑定本页目录、展开项、缺口和建议集合。", ""])
    return "\n".join(lines)


def prepare_review(root, input_path, project_code, *, database=None, project_name="", plan=None,
                   local_config=None, package_id="", render_after_build=True):
    root, data, state_path = paths(root)
    data.mkdir(parents=True, exist_ok=True)
    database = Path(database).resolve() if database else data / "knowledge.sqlite"
    if state_path.exists():
        prior = read(state_path)
        if prior["project_code"] != project_code or prior["database"] != str(database):
            raise ValueError("workbench is already bound to another project/database")
    package_id, knowledge = ensure_knowledge(database, project_code, local_config, package_id)
    original = source_payload(input_path)
    derived = derive_scope(original, plan)
    ingest_scope_payload(database, derived, project_code=project_code)
    capture = capture_scope_snapshot(database, project_code, derived)
    matched = match_scope(database, project_code, scope_snapshot_id=capture["scope_snapshot_id"], package_id=package_id)
    trace = traceability(derived, capture["scope_rows"])
    rows = {r["scope_row_id"]: r for r in capture["scope_rows"]}
    hierarchy = copy.deepcopy(matched["hierarchy_review"])
    items = copy.deepcopy(matched["items"])
    selected_fragments = []
    for item in items:
        cells = rows[item["scope_row_id"]]["display_cells"]
        item["parent_path"] = cells["_display_parent_path"]
        next(e for e in hierarchy["items"] if e["scope_row_id"] == item["scope_row_id"])["parent_path"] = item["parent_path"]
        options = item["candidates"]
        choice = cells.get("_suggested_choice", {})
        chosen = next((c for c in options if choice and all(c.get(k) == v for k, v in choice.items())), None)
        if choice and not chosen:
            raise ValueError("explicit suggested module did not match a current candidate; specify a valid capability/root")
        chosen = chosen or next((c for c in options if c["candidate_id"] == item.get("auto_selected_candidate_id")), options[0] if options else None)
        proposal = {"scope_row_id": item["scope_row_id"]}
        if not options:
            proposal.update(decision="confirmed_gap", choice_label="无可用标准内容，建议保留【待补充】")
        elif chosen and chosen["candidate_status"] in {"ready", "needs_review"} and chosen["root_heading_path"] and chosen["subtree_block_ids"] and chosen["name_score"] >= 0.65:
            proposal.update(decision="confirmed", candidate_id=chosen["candidate_id"], choice_label=chosen["product_name"] + " / " + chosen["module_name"])
            selected_fragments += chosen["subtree_block_ids"]
        item["proposal"] = proposal
    with connect_readonly(database) as conn:
        source_fragments = [dict(r) for block in set(selected_fragments) for r in conn.execute("SELECT clean_text,content_format FROM corpus_block WHERE block_id=?", (block,))]
    review = {"version": VERSION, "project_code": project_code, "match_run_id": matched["match_run_id"],
              "scope_snapshot_id": matched["scope_snapshot_id"], "scope_snapshot_hash": matched["scope_snapshot_hash"],
              "package_id": package_id, "package_content_hash": matched["package_content_hash"],
              "match_input_hash": matched["input_hash"], "traceability": trace, "hierarchy_review": hierarchy,
              "items": items, "duplicate_mappings": duplicate_mappings(items, candidates=True),
              "source_limitations": {"dangling_image_reference_blocks": sum(bool(re.search(r"如下图|见.{0,6}(?:示意图|流程图)", f["clean_text"])) for f in source_fragments)}}
    review["review_id"] = fingerprint(review)
    review_path = data / "reviews" / (review["review_id"] + ".json")
    write(review_path, review)
    main = root / "01-请确认建设清单对照.md"
    text = review_markdown(review)
    if not main.exists() or main.read_text(encoding="utf-8") != text:
        main.write_text(text, encoding="utf-8")
    if state_path.exists() and prior.get("review_id") == review["review_id"]:
        return get_status(root)
    state = {"version": VERSION, "workflow_scope": "construction_only", "document_type": "construction_plan",
             "output_formats": ["docx"], "status": "awaiting_confirmation", "project_code": project_code,
             "project_name": project_name or project_code, "database": str(database), "package_id": package_id,
             "review_id": review["review_id"], "review_path": str(review_path), "review_sha256": sha256_file(review_path),
             "primary_review": str(main), "input_path": str(Path(input_path).resolve()),
             "input_sha256": sha256_file(Path(input_path)), "knowledge": knowledge,
             "runtime_hash": runtime_hash(), "updated_at": now_iso()}
    state["render_after_build"] = bool(render_after_build)
    write(state_path, state)
    return get_status(root)


def verify_review(state):
    review = read(state["review_path"])
    if sha256_file(Path(state["review_path"])) != state["review_sha256"] or fingerprint({k:v for k,v in review.items() if k != "review_id"}) != state["review_id"]:
        raise ValueError("review package changed; prepare a new review")
    if sha256_file(Path(state["input_path"])) != state["input_sha256"]:
        raise ValueError("original input changed; prepare a new review")
    current = match_scope(Path(state["database"]), state["project_code"], package_id=state["package_id"])
    if current["match_run_id"] != review["match_run_id"] or current["input_hash"] != review["match_input_hash"]:
        raise ValueError("scope, standard content or matching rules changed; prepare a new review")
    return review


def confirm_and_generate(root, confirmation):
    root, data, state_path = paths(root)
    state = read(state_path)
    review = verify_review(state)
    if confirmation.get("review_id") != review["review_id"] or not str(confirmation.get("reviewed_by") or "").strip() or not str(confirmation.get("user_reply") or "").strip():
        raise ValueError("confirmation must bind review_id, reviewed_by and the actual user_reply")
    if confirmation.get("accept_all") is not True:
        raise ValueError("for changed mappings/parents prepare an updated review or pass explicit overrides with accept_all for remaining displayed proposals")
    overrides = confirmation.get("overrides", [])
    override_map = {e["scope_row_id"]: e for e in overrides}
    if len(override_map) != len(overrides) or set(override_map) - {i["scope_row_id"] for i in review["items"]}:
        raise ValueError("unknown or repeated confirmation override")
    decisions, hierarchy = [], copy.deepcopy(review["hierarchy_review"])
    received = now_iso()
    hierarchy.update(status="confirmed", reviewed_by=confirmation["reviewed_by"], reviewed_at=received)
    for item in review["items"]:
        proposal = copy.deepcopy(item["proposal"])
        override = override_map.get(item["scope_row_id"], {})
        if override:
            selection_fields = ("candidate_id", "capability_id", "root_heading_path")
            if "candidate_id" in override or "capability_id" in override:
                # A new selector must not inherit a competing selector or old root.
                for field in selection_fields:
                    proposal.pop(field, None)
                if "decision" not in override:
                    proposal["decision"] = "confirmed"
            proposal.update({k: v for k, v in override.items() if k != "parent_path"})
            if proposal.get("decision") == "confirmed_gap":
                for field in selection_fields:
                    proposal.pop(field, None)
            if "parent_path" in override:
                next(e for e in hierarchy["items"] if e["scope_row_id"] == item["scope_row_id"])["parent_path"] = override["parent_path"]
        proposal.pop("choice_label", None)
        if proposal.get("decision") not in {"confirmed", "confirmed_gap"}:
            raise ValueError(f"scope item {item['source_ordinal']} still requires an explicit candidate/root choice")
        decisions.append(proposal)
    key = fingerprint({"review_id": review["review_id"], "decisions": decisions, "parents": hierarchy["items"], "reviewed_by": confirmation["reviewed_by"]})
    receipt = data / "confirmations" / (key + ".json")
    if receipt.exists():
        record = read(receipt)
        hierarchy = record["hierarchy_review"]
    else:
        record = {"confirmation": confirmation, "received_at": received, "hierarchy_review": hierarchy, "decisions": decisions}
        write(receipt, record)
    with connect_readonly(Path(state["database"])) as conn:
        rows = [dict(r) for r in conn.execute("SELECT csr.*,psi.original_name FROM construction_scope_row csr JOIN project_scope_item psi ON psi.scope_id=csr.scope_id WHERE scope_snapshot_id=?", (review["scope_snapshot_id"],))]
    _reviewed_parent_paths(hierarchy, review, rows)
    applied = apply_decisions(Path(state["database"]), {"match_run_id": review["match_run_id"], "reviewed_by": confirmation["reviewed_by"], "reviewed_at": record["received_at"], "decisions": decisions})
    state.update(status="assembling", confirmation_path=str(receipt), confirmation_sha256=sha256_file(receipt),
                 decision_result=applied, updated_at=now_iso())
    write(state_path, state)
    return resume(root)


def resume(root):
    root, data, state_path = paths(root)
    state = read(state_path)
    if not state.get("confirmation_path"):
        return get_status(root)
    review = verify_review(state)
    receipt = Path(state["confirmation_path"])
    if sha256_file(receipt) != state["confirmation_sha256"]:
        raise ValueError("confirmation receipt changed")
    confirmed = read(receipt)
    database = Path(state["database"])
    try:
        manifest_path = data / "construction-assembly-manifest.json"
        manifest = read(manifest_path) if manifest_path.exists() else None
        if not manifest or manifest.get("hierarchy_review") != confirmed["hierarchy_review"] or not validate_manifest(database, manifest)["valid"] or state.get("assembly_runtime_hash") != runtime_hash("assembly"):
            manifest, _ = assemble(database, state["project_code"], review["match_run_id"], hierarchy_review=confirmed["hierarchy_review"])
            write(manifest_path, manifest)
        validation = validate_manifest(database, manifest)
        write(data / "construction-validation.json", validation)
        if not validation["valid"]:
            raise ValueError("assembly validation blocked")
        state.update(status="validating", manifest_path=str(manifest_path), manifest_hash=manifest["manifest_hash"], assembly_runtime_hash=runtime_hash("assembly"))
        write(state_path, state)
        output = root / "02-交付/建设清单与建设内容.docx"
        build_key = fingerprint({"manifest": manifest["manifest_hash"], "runtime": runtime_hash(), "project_name": state["project_name"]})
        reusable = output.exists() and state.get("word_build_key") == build_key and state.get("docx_sha256") == sha256_file(output)
        if reusable:
            reusable = audit_word(output, manifest)["valid"]
        if not reusable:
            state.update(status="generating_word")
            write(state_path, state)
            result = generate_word(database, manifest, output, state["project_name"])
            if not result["structural_validation_passed"]:
                raise ValueError("Word structural checks failed")
            state.pop("render_review", None)
        state.update(status="docx_created_render_pending", docx_path=str(output), docx_sha256=sha256_file(output),
                     word_build_key=build_key, fidelity=fidelity(manifest), updated_at=now_iso(), error=None)
        write(state_path, state)
        if state.get("render_after_build", True):
            from construction_render import render
            try:
                result = render(root)
                result.pop("render", None)
                return result
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, ImportError) as exc:
                state = read(state_path)
                state.update(status="docx_created_render_pending", error={"type": type(exc).__name__, "message": str(exc)})
                write(state_path, state)
                return get_status(root)
        return get_status(root)
    except Exception as exc:
        state.update(status="blocked", error={"type": type(exc).__name__, "message": str(exc)}, updated_at=now_iso())
        write(state_path, state)
        raise


def record_render_review(root, record):
    root, data, state_path = paths(root)
    state = read(state_path)
    if not state.get("docx_path") or record.get("docx_sha256") != sha256_file(Path(state["docx_path"])):
        raise ValueError("render review must bind the current Word hash")
    if record.get("result") != "pass" or record.get("checked_all_pages") is not True or record.get("reviewer_type") not in {"ai_visual", "human"} or not record.get("reviewed_by"):
        raise ValueError("actual full-page visual review and reviewer type are required")
    binding = read(data / "render-binding.json")
    if binding["docx_sha256"] != record["docx_sha256"] or binding["page_count"] != record.get("page_count") or binding["pages"] != record.get("pages") or sha256_file(Path(binding["pdf_path"])) != binding["pdf_sha256"]:
        raise ValueError("visual review must cover exactly the current renderer's complete page set")
    pages = record.get("pages", [])
    if not pages or record.get("page_count") != len(pages) or [p.get("page_number") for p in pages] != list(range(1,len(pages)+1)):
        raise ValueError("render review requires ordered complete page coverage")
    for page in pages:
        path = Path(page["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file() or sha256_file(path) != page["sha256"]:
            raise ValueError("rendered page missing, changed, or outside workbench")
    manifest = read(state["manifest_path"])
    if not validate_manifest(Path(state["database"]), manifest)["valid"] or not audit_word(Path(state["docx_path"]), manifest)["valid"]:
        raise ValueError("current Word/manifest no longer passes source audit")
    record = {**record, "recorded_at": now_iso(), "manifest_hash": state["manifest_hash"]}
    target = data / "render-review.json"
    write(target, record)
    state.update(render_review={"path": str(target), "sha256": sha256_file(target)}, docx_sha256=record["docx_sha256"])
    write(state_path, state)
    return get_status(root)


def get_status(root):
    root, data, state_path = paths(root)
    state = read(state_path)
    ready = False
    if state.get("render_review") and Path(state["input_path"]).is_file() and sha256_file(Path(state["input_path"])) == state["input_sha256"] and state.get("assembly_runtime_hash") == runtime_hash("assembly") and state.get("word_build_key") == fingerprint({"manifest": state.get("manifest_hash"), "runtime": runtime_hash(), "project_name": state["project_name"]}):
        evidence = state["render_review"]
        if Path(evidence["path"]).is_file() and sha256_file(Path(evidence["path"])) == evidence["sha256"]:
            record = read(evidence["path"])
            ready = (Path(state["docx_path"]).is_file() and record["docx_sha256"] == sha256_file(Path(state["docx_path"]))
                     and all(Path(p["path"]).is_file() and sha256_file(Path(p["path"])) == p["sha256"] for p in record["pages"]))
            if ready:
                manifest = read(state["manifest_path"])
                ready = validate_manifest(Path(state["database"]), manifest)["valid"]
    status = state["status"]
    if ready:
        status = "ready_with_gaps" if any(i["status"] == "pending_supplement" for i in read(state["manifest_path"])["application_software_solution"]["items"]) else "ready"
    return {"status": status, "review_id": state["review_id"], "primary_review": state["primary_review"],
            "docx_path": state.get("docx_path"), "delivery_ready": ready, "error": state.get("error"),
            "next_action": "交付Word并说明已确认缺口。" if ready else ("确认核对页后自动生成Word。" if status == "awaiting_confirmation" else "继续完成当前Word的目录更新、渲染及逐页视觉检查；提交绑定哈希的检查记录。")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-review")
    prepare.add_argument("root", type=Path); prepare.add_argument("input", type=Path)
    prepare.add_argument("--project-code", required=True); prepare.add_argument("--project-name", default="")
    prepare.add_argument("--database", type=Path); prepare.add_argument("--local-config", type=Path)
    prepare.add_argument("--package-id", default=""); prepare.add_argument("--plan", type=Path)
    confirm = sub.add_parser("confirm-and-generate")
    confirm.add_argument("root", type=Path); confirm.add_argument("confirmation", type=Path)
    for name in ("resume", "status", "render"):
        sub.add_parser(name).add_argument("root", type=Path)
    review = sub.add_parser("record-render-review")
    review.add_argument("root", type=Path); review.add_argument("review", type=Path)
    args = parser.parse_args()
    if args.command == "prepare-review":
        result = prepare_review(args.root, args.input, args.project_code, database=args.database, project_name=args.project_name,
                                plan=read(args.plan) if args.plan else None, local_config=args.local_config, package_id=args.package_id)
    elif args.command == "confirm-and-generate":
        result = confirm_and_generate(args.root, read(args.confirmation))
    elif args.command == "resume":
        result = resume(args.root)
    elif args.command == "status":
        result = get_status(args.root)
    elif args.command == "render":
        from construction_render import render
        result = render(args.root)
    else:
        result = record_render_review(args.root, read(args.review))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
