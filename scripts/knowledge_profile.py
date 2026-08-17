#!/usr/bin/env python3
"""Resolve user-level shared-knowledge profiles without exposing secrets."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


CONFIG_ENV = "MEDICAL_FEASIBILITY_KB_CONFIG"
DEFAULT_PASSWORD_ENV = "MEDICAL_FEASIBILITY_DB_PASSWORD"
DEFAULT_CONFIG_RELATIVE = Path(".codex") / "config" / "medical-report-kb.json"
KNOWLEDGE_MODES = {"server_required", "snapshot_required", "offline_pack", "disabled"}
DEFAULT_PERMISSION_SCOPES = {
    "knowledge_package": ["internal_company_reuse"],
    "policy_catalog": ["internal_company_reference"],
    "policy_release": ["public_policy_reference"],
}


class KnowledgeConfigurationError(ValueError):
    """Raised when knowledge configuration cannot be resolved safely."""

    def __init__(self, reason: str, message: str, *, details: Any = None):
        super().__init__(message)
        self.reason = reason
        self.details = details


def default_config_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / DEFAULT_CONFIG_RELATIVE


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise KnowledgeConfigurationError(
            "knowledge_config_not_found", f"knowledge configuration not found: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise KnowledgeConfigurationError(
            "knowledge_config_invalid_json",
            f"knowledge configuration is not valid JSON: {path}",
        ) from exc
    if not isinstance(payload, dict):
        raise KnowledgeConfigurationError(
            "knowledge_config_invalid", "knowledge configuration root must be an object"
        )
    return payload


def discover_config_path(
    explicit_path: Path | str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> tuple[Path, str]:
    env = environ if environ is not None else os.environ
    if explicit_path:
        return Path(explicit_path).expanduser().resolve(), "command_line"
    env_path = str(env.get(CONFIG_ENV, "")).strip()
    if env_path:
        return Path(env_path).expanduser().resolve(), "environment"
    return default_config_path(home).resolve(), "user_default"


def _as_string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise KnowledgeConfigurationError(
            "knowledge_config_invalid", f"{field} must be an array of strings"
        )
    return [item.strip() for item in value if item.strip()]


def _permission_scopes(value: Any) -> dict[str, list[str]]:
    result = {key: list(items) for key, items in DEFAULT_PERMISSION_SCOPES.items()}
    if value is None:
        return result
    if isinstance(value, list):
        shared = _as_string_list(value, "permission_scope")
        return {key: list(shared) for key in result}
    if not isinstance(value, dict):
        raise KnowledgeConfigurationError(
            "knowledge_config_invalid",
            "permission_scopes must be an object or an array of strings",
        )
    for key in result:
        if key in value:
            result[key] = _as_string_list(value[key], f"permission_scopes.{key}")
    return result


def _legacy_profile(knowledge: dict[str, Any]) -> dict[str, Any] | None:
    server = knowledge.get("server")
    if not isinstance(server, dict):
        return None
    if not any(str(server.get(key, "")).strip() for key in ("database", "user", "host")):
        return None
    return {
        **server,
        "mode": knowledge.get(
            "mode", "server_required" if server.get("enabled") else "disabled"
        ),
        "package_ids": knowledge.get("package_ids", server.get("package_ids", [])),
        "catalog_ids": knowledge.get("catalog_ids", server.get("catalog_ids", [])),
        "policy_topics": knowledge.get("policy_topics", server.get("policy_topics", [])),
        "allow_stale_cache": knowledge.get(
            "allow_stale_cache", server.get("allow_stale_cache", False)
        ),
    }


def resolve_knowledge_settings(
    project_config: dict[str, Any],
    *,
    explicit_config_path: Path | str | None = None,
    explicit_profile: str | None = None,
    explicit_mode: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """Resolve path first, then profile; project selection overrides profile selection."""

    knowledge = project_config.get("knowledge") or {}
    if not isinstance(knowledge, dict):
        raise KnowledgeConfigurationError(
            "knowledge_config_invalid", "project knowledge configuration must be an object"
        )
    requested_mode = str(explicit_mode or knowledge.get("mode") or "server_required").strip()
    if requested_mode not in KNOWLEDGE_MODES:
        raise KnowledgeConfigurationError(
            "knowledge_mode_invalid",
            f"unsupported knowledge mode: {requested_mode}",
            details={"supported_modes": sorted(KNOWLEDGE_MODES)},
        )

    path, path_source = discover_config_path(
        explicit_config_path, environ=environ, home=home
    )
    user_payload: dict[str, Any] = {}
    profile: dict[str, Any] | None = None
    profile_name = str(explicit_profile or knowledge.get("profile") or "").strip()
    if path.is_file():
        user_payload = _read_json(path)
        profiles = user_payload.get("profiles") or {}
        if not isinstance(profiles, dict):
            raise KnowledgeConfigurationError(
                "knowledge_config_invalid", "profiles must be an object"
            )
        profile_name = profile_name or str(user_payload.get("default_profile") or "").strip()
        if profile_name:
            candidate = profiles.get(profile_name)
            if not isinstance(candidate, dict):
                raise KnowledgeConfigurationError(
                    "knowledge_profile_not_found",
                    f"knowledge profile not found: {profile_name}",
                    details={"available_profiles": sorted(profiles)},
                )
            profile = dict(candidate)
    legacy = _legacy_profile(knowledge)
    if profile is None and legacy is not None:
        profile = legacy
        profile_name = profile_name or "legacy_inline"
        path_source = "project_legacy_inline"

    if requested_mode in {"server_required"} and profile is None:
        raise KnowledgeConfigurationError(
            "knowledge_profile_missing",
            f"knowledge mode {requested_mode} requires a resolvable user-level profile",
            details={"expected_config_path": str(path), "profile": profile_name or "default"},
        )

    effective = dict(profile or {})
    profile_mode = str(effective.get("mode") or requested_mode).strip()
    mode = str(explicit_mode or knowledge.get("mode") or profile_mode).strip()
    if mode not in KNOWLEDGE_MODES:
        raise KnowledgeConfigurationError(
            "knowledge_mode_invalid", f"unsupported knowledge mode: {mode}"
        )

    def selected_list(key: str) -> list[str]:
        project_value = knowledge.get(key)
        if isinstance(project_value, list) and project_value:
            return _as_string_list(project_value, f"knowledge.{key}")
        return _as_string_list(effective.get(key, []), f"profiles.{profile_name}.{key}")

    selection = dict(effective.get("selection") or {})
    project_selection = knowledge.get("selection") or {}
    if isinstance(project_selection, dict):
        selection.update({key: value for key, value in project_selection.items() if value not in (None, "", [])})
    project = project_config.get("project") or {}
    selection.setdefault("document_type", project.get("document_type", ""))
    selection.setdefault("project_type", project.get("project_type", ""))
    selection.setdefault("jurisdiction_code", project.get("jurisdiction_code", ""))

    settings = {
        "mode": mode,
        "profile_name": profile_name,
        "config_path": str(path),
        "config_source": path_source,
        "host": effective.get("host", "127.0.0.1"),
        "port": int(effective.get("port", 5432)),
        "database": str(effective.get("database", "")).strip(),
        "user": str(effective.get("user", "")).strip(),
        "schema": str(effective.get("schema", "medical_report_kb")).strip(),
        "password_env": str(effective.get("password_env", DEFAULT_PASSWORD_ENV)).strip(),
        "connect_timeout": int(effective.get("connect_timeout", 10)),
        "package_ids": selected_list("package_ids"),
        "catalog_ids": selected_list("catalog_ids"),
        "policy_topics": selected_list("policy_topics"),
        "permission_scopes": _permission_scopes(
            knowledge.get("permission_scopes", effective.get("permission_scopes"))
        ),
        "allow_stale_cache": bool(
            knowledge.get("allow_stale_cache", effective.get("allow_stale_cache", False))
        ),
        "selection": selection,
    }
    if mode == "server_required":
        missing = [key for key in ("database", "user") if not settings[key]]
        if missing:
            raise KnowledgeConfigurationError(
                "knowledge_profile_incomplete",
                f"knowledge profile is missing required fields: {missing}",
                details={"profile": profile_name, "missing": missing},
            )
        if not settings["password_env"]:
            raise KnowledgeConfigurationError(
                "knowledge_profile_incomplete", "password_env must not be empty"
            )
    return settings


def public_settings(settings: dict[str, Any], *, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return diagnostic-safe settings; never include a password value."""

    env = environ if environ is not None else os.environ
    password_env = str(settings.get("password_env", ""))
    allowed = {
        "mode", "profile_name", "config_path", "config_source", "host", "port",
        "database", "user", "schema", "password_env", "connect_timeout", "package_ids",
        "catalog_ids", "policy_topics", "permission_scopes", "allow_stale_cache", "selection",
    }
    result = {key: settings.get(key) for key in allowed}
    result["password_env_present"] = bool(password_env and env.get(password_env))
    return result


def redact_text(value: Any, settings: dict[str, Any] | None = None, *, environ: Mapping[str, str] | None = None) -> str:
    text = str(value)
    if settings:
        env = environ if environ is not None else os.environ
        password_env = str(settings.get("password_env", ""))
        secret = env.get(password_env, "") if password_env else ""
        if secret:
            text = text.replace(secret, "***")
    return text
