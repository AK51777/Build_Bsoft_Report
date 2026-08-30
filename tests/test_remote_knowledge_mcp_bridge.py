from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from remote_knowledge_mcp_bridge import (  # noqa: E402
    BridgeConfig,
    CredentialBridgeMCP,
)


class FakeHTTPResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class RemoteKnowledgeMCPBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        self.config = BridgeConfig(
            remote_url="https://knowledge.example.test/mcp",
            activation_url="https://knowledge.example.test/activate",
            credential_file=self.root / "credential.json",
            request_timeout=5,
            max_query_limit=25,
        )
        self.app = CredentialBridgeMCP(self.config)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def call(self, name: str, arguments: dict | None = None) -> dict:
        return self.app.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )["result"]

    def test_access_status_requires_activation_initially(self) -> None:
        result = self.call("knowledge_access_status")
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["status"], "activation_required")

    def test_activation_stores_token_locally_but_never_returns_it_to_model(self) -> None:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        access_token = "mrk_live_test-secret-token"
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeHTTPResponse(
                {
                    "access_token": access_token,
                    "token_type": "Bearer",
                    "expires_at": expires_at,
                }
            )

        with patch("remote_knowledge_mcp_bridge.urllib.request.urlopen", fake_urlopen):
            result = self.call(
                "activate_knowledge_access",
                {"activation_code": "MRK-AAAA-BBBB-CCCC"},
            )

        self.assertFalse(result["isError"])
        serialized_result = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(access_token, serialized_result)
        self.assertNotIn("MRK-AAAA-BBBB-CCCC", serialized_result)
        self.assertEqual(captured["url"], self.config.activation_url)
        self.assertEqual(captured["body"]["activation_code"], "MRK-AAAA-BBBB-CCCC")
        self.assertNotIn("Authorization", captured["headers"])
        stored = json.loads(self.config.credential_file.read_text(encoding="utf-8"))
        self.assertEqual(stored["access_token"], access_token)
        if sys.platform != "win32":
            self.assertEqual(stat.S_IMODE(self.config.credential_file.stat().st_mode), 0o600)

    def test_reopened_bridge_reuses_saved_bearer_token_for_read_only_query(self) -> None:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        access_token = "mrk_live_persisted-token"
        self.app.credentials.save(
            access_token=access_token,
            expires_at=expires_at,
            remote_url=self.config.remote_url,
        )
        reopened = CredentialBridgeMCP(self.config)
        captured = {}
        remote_result = {
            "content": [{"type": "text", "text": "published"}],
            "structuredContent": {"count": 1, "rows": [{"package_id": "PACK-1"}]},
            "isError": False,
        }

        def fake_urlopen(request, timeout):
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeHTTPResponse({"jsonrpc": "2.0", "id": "remote", "result": remote_result})

        with patch("remote_knowledge_mcp_bridge.urllib.request.urlopen", fake_urlopen):
            result = reopened.dispatch(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "knowledge_query",
                        "arguments": {"kind": "package", "limit": 10},
                    },
                }
            )["result"]

        self.assertEqual(captured["headers"]["Authorization"], f"Bearer {access_token}")
        self.assertEqual(captured["body"]["params"]["name"], "knowledge_query")
        self.assertEqual(result, remote_result)

    def test_tool_contract_marks_only_activation_as_non_read_only(self) -> None:
        listed = self.app.dispatch(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
        )
        tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
        self.assertFalse(tools["activate_knowledge_access"]["annotations"]["readOnlyHint"])
        for name in {
            "knowledge_access_status",
            "knowledge_service_status",
            "knowledge_query",
            "knowledge_page",
        }:
            self.assertTrue(tools[name]["annotations"]["readOnlyHint"])

    def test_snapshot_page_is_proxied_with_saved_credential(self) -> None:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        self.app.credentials.save(
            access_token="mrk_live_page-token",
            expires_at=expires_at,
            remote_url=self.config.remote_url,
        )
        captured = {}
        remote_result = {
            "content": [{"type": "text", "text": "page"}],
            "structuredContent": {"rows": [], "next_offset": None},
            "isError": False,
        }

        def fake_urlopen(request, timeout):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeHTTPResponse({"jsonrpc": "2.0", "id": "remote", "result": remote_result})

        with patch("remote_knowledge_mcp_bridge.urllib.request.urlopen", fake_urlopen):
            result = self.call(
                "knowledge_page",
                {"entity": "corpus-block", "asset_id": "PACK-1", "limit": 25},
            )

        self.assertEqual(captured["body"]["params"]["name"], "knowledge_page")
        self.assertEqual(result, remote_result)

    def test_bridge_config_requires_https_and_contains_no_credentials(self) -> None:
        config_path = self.root / "bridge.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "remote_url": "http://knowledge.example.test/mcp",
                    "access_token": "forbidden",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "credentials are forbidden"):
            BridgeConfig.load(config_path)

        config_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "remote_url": "http://knowledge.example.test/mcp",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "must use https"):
            BridgeConfig.load(config_path)


if __name__ == "__main__":
    unittest.main()
