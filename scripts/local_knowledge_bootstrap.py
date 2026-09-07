"""Configure split packages once and prepare pinned project knowledge without a token."""

from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path

from knowledge_db import connect_readonly, dump_json, sha256_file
from knowledge_snapshot import validate_snapshots
from local_knowledge_packages import (
    CONFIG_ENV, DEFAULT_CONFIG_RELATIVE, LocalKnowledgeError, load_config,
    package_status, sync_to_project, validate_local_package,
)


def discover_local_config(explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        return explicit.expanduser().resolve()
    if os.environ.get(CONFIG_ENV):
        return Path(os.environ[CONFIG_ENV]).expanduser().resolve()
    default = Path.home() / DEFAULT_CONFIG_RELATIVE
    return default if default.is_file() else None


def configure_packages(directory: Path, *, standard_sha256: str, policy_sha256: str,
                       config_path: Path | None = None) -> dict:
    """Reference received files; never overwrite an existing different configuration."""
    directory = directory.expanduser().resolve()
    target = (config_path or Path.home() / DEFAULT_CONFIG_RELATIVE).expanduser().resolve()
    packages = {}
    for kind, digest, age in (("standard", standard_sha256, 45), ("policy", policy_sha256, 8)):
        path = directory / f"{kind}-knowledge.sqlite"
        if len(digest) != 64 or sha256_file(path) != digest.lower():
            raise LocalKnowledgeError(f"{kind} package does not match the trusted release digest")
        validate_local_package(path, expected_kind=kind)
        packages[kind] = {"path": str(path), "required": True, "max_age_days": age}
    payload = {"schema_version": "1.0", "packages": packages}
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8-sig"))
        if existing != payload:
            raise FileExistsError("local knowledge configuration already exists; review it before replacing")
        return {"status": "unchanged", "config_path": str(target)}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(6)}.tmp")
    try:
        temporary.write_text(dump_json(payload) + "\n", encoding="utf-8")
        # An atomic, no-clobber publication: a concurrently created config wins.
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"status": "configured", "config_path": str(target), "remote_mcp_retained": True}


def prepare_project_knowledge(config_path: Path, database: Path, project_code: str,
                              *, selection: dict | None = None) -> dict:
    config = load_config(config_path)
    status = package_status(config)
    if status["status"] == "blocked":
        raise LocalKnowledgeError("configured local knowledge is missing or invalid; repair it before generation")
    selection = selection or {}
    with connect_readonly(database) as conn:
        existing = conn.execute(
            """SELECT 1 FROM shared_knowledge_snapshot s
               JOIN project p ON p.project_id=s.project_id
               WHERE p.project_code=? AND s.snapshot_status IN ('current','stale') LIMIT 1""",
            (project_code,),
        ).fetchone()
    if existing:
        manifest = validate_snapshots(
            database, project_code, package_ids=selection.get("package_ids", []),
            catalog_ids=selection.get("catalog_ids", []),
            permission_scopes=selection.get("permission_scopes", {}), allow_stale=False,
        )
        action = "kept_project_snapshot"
    else:
        for kind, key in (("standard", "package_ids"), ("policy", "catalog_ids")):
            available = set(status["packages"][kind]["release"]["source_ids"])
            if set(selection.get(key, [])) - available:
                raise LocalKnowledgeError(f"selected {key} are not present in the configured local package")
        synced = sync_to_project(config, project_database=database, project_code=project_code,
                                 validation_selection=selection)
        manifest = synced["snapshot_validation"]
        action = "synced_local_packages"
    return {"action": action, "mode": "snapshot_required", "manifest": manifest, "packages": status}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_directory", type=Path)
    parser.add_argument("--standard-sha256", required=True)
    parser.add_argument("--policy-sha256", required=True)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    result = configure_packages(args.package_directory, standard_sha256=args.standard_sha256,
                                policy_sha256=args.policy_sha256, config_path=args.config)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
