from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from remote_readonly_knowledge_mcp import (  # noqa: E402
    AuthConfig,
    CredentialStore,
    DatabaseConfig,
    MCPHTTPServer,
    ReadonlyKnowledgeMCP,
    ServerConfig,
)


class FakeRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.page_calls: list[dict[str, object]] = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["kind"] == "status":
            rows = [
                {
                    "packages": 1,
                    "corpus_blocks": 12,
                    "capabilities": 3,
                    "policy_catalog_entries": 4,
                    "verified_policy_clauses": 2,
                }
            ]
        else:
            rows = [
                {
                    "package_id": "PACK-TEST",
                    "title": "脱敏标准知识包",
                    "content_hash": "hash-test",
                }
            ]
        return {
            "kind": kwargs["kind"],
            "evidence_status": "published_runtime_view",
            "count": len(rows),
            "rows": rows,
        }

    def page(self, **kwargs):
        self.page_calls.append(kwargs)
        rows = [
            {"package_id": "PACK-TEST", "block_id": "BLOCK-1"},
            {"package_id": "PACK-TEST", "block_id": "BLOCK-2"},
        ]
        return {
            "entity": kwargs["entity"],
            "asset_id": kwargs["asset_id"],
            "offset": kwargs["offset"],
            "limit": kwargs["limit"],
            "count": len(rows),
            "rows": rows,
            "next_offset": kwargs["offset"] + len(rows),
        }


class RemoteReadonlyKnowledgeMCPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        self.hash_secret_env = "TEST_MEDICAL_REPORT_MCP_HASH_SECRET"
        self.previous_hash_secret = os.environ.get(self.hash_secret_env)
        os.environ[self.hash_secret_env] = "test-only-hash-secret"
        auth = AuthConfig(
            credential_store=self.root / "credentials.json",
            hash_secret_env=self.hash_secret_env,
            access_token_days=30,
        )
        self.config = ServerConfig(
            host="127.0.0.1",
            port=0,
            mcp_path="/mcp",
            auth=auth,
            max_query_limit=25,
            max_request_bytes=1024 * 1024,
            database=DatabaseConfig(
                host="127.0.0.1",
                port=5432,
                database="test",
                user="reader",
                password_env="TEST_DATABASE_PASSWORD",
            ),
        )
        self.credentials = CredentialStore(auth)
        issued = self.credentials.issue_activation_code(label="test-user", valid_hours=1)
        self.access_token = self.credentials.activate(issued["activation_code"])["access_token"]
        self.repository = FakeRepository()
        self.app = ReadonlyKnowledgeMCP(self.config, self.repository)
        self.server = MCPHTTPServer(self.config, self.app, self.credentials)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()
        if self.previous_hash_secret is None:
            os.environ.pop(self.hash_secret_env, None)
        else:
            os.environ[self.hash_secret_env] = self.previous_hash_secret

    def request(self, payload: dict, *, token: str | None = "default"):
        return self.post("/mcp", payload, token=token)

    def post(self, path: str, payload: dict, *, token: str | None = None):
        headers = {"Content-Type": "application/json"}
        if token == "default":
            token = self.access_token
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_tool_contract_contains_only_read_only_tools(self) -> None:
        status, response = self.request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )
        self.assertEqual(status, 200)
        tools = response["result"]["tools"]
        self.assertEqual(
            {tool["name"] for tool in tools},
            {"knowledge_service_status", "knowledge_query", "knowledge_page"},
        )
        for tool in tools:
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertFalse(tool["annotations"]["destructiveHint"])

    def test_unauthorized_request_is_rejected_before_repository_access(self) -> None:
        payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(payload, token=None)
        self.assertEqual(caught.exception.code, 401)
        self.assertIn("Bearer", caught.exception.headers["WWW-Authenticate"])
        self.assertEqual(self.repository.calls, [])

    def test_activation_code_is_one_time_and_access_token_is_not_stored_plaintext(self) -> None:
        issued = self.credentials.issue_activation_code(label="new-user", valid_hours=1)
        status, activated = self.post(
            "/activate",
            {"activation_code": issued["activation_code"]},
        )
        self.assertEqual(status, 200)
        self.assertTrue(activated["access_token"].startswith("mrk_live_"))

        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/activate", {"activation_code": issued["activation_code"]})
        self.assertEqual(caught.exception.code, 401)

        stored = self.config.auth.credential_store.read_text(encoding="utf-8")
        self.assertNotIn(issued["activation_code"], stored)
        self.assertNotIn(activated["access_token"], stored)

    def test_initialize_and_query_published_knowledge(self) -> None:
        _, initialized = self.request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        self.assertEqual(initialized["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("只", initialized["result"]["instructions"])

        _, response = self.request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "knowledge_query",
                    "arguments": {"kind": "package", "search": "标准", "limit": 10},
                },
            }
        )
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["rows"][0]["package_id"], "PACK-TEST")
        self.assertEqual(self.repository.calls[-1]["kind"], "package")
        self.assertEqual(self.repository.calls[-1]["limit"], 10)

    def test_pages_published_snapshot_with_deterministic_cursor(self) -> None:
        _, response = self.request(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "knowledge_page",
                    "arguments": {
                        "entity": "corpus-block",
                        "asset_id": "PACK-TEST",
                        "offset": 20,
                        "limit": 2,
                    },
                },
            }
        )
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["next_offset"], 22)
        self.assertEqual(
            self.repository.page_calls[-1],
            {
                "entity": "corpus-block",
                "asset_id": "PACK-TEST",
                "offset": 20,
                "limit": 2,
            },
        )

    def test_query_limit_is_enforced_before_repository_access(self) -> None:
        _, response = self.request(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "knowledge_query",
                    "arguments": {"kind": "corpus", "limit": 26},
                },
            }
        )
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertIn("between 1 and 25", result["structuredContent"]["message"])
        self.assertEqual(self.repository.calls, [])

    def test_config_rejects_inline_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "remote-mcp.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "token": "must-not-be-inline",
                        "server": {"host": "127.0.0.1", "port": 8080},
                        "auth": {
                            "credential_store": str(self.root / "credentials.json"),
                            "hash_secret_env": "HASH_SECRET_ENV",
                        },
                        "database": {
                            "database": "knowledge",
                            "user": "reader",
                            "password_env": "DB_PASSWORD_ENV",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "inline secrets are forbidden"):
                ServerConfig.load(path)


if __name__ == "__main__":
    unittest.main()
