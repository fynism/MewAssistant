import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient


class PlatformAvailabilityTests(unittest.TestCase):
    def test_service_status(self):
        from backend.app import app
        client = TestClient(app)
        with patch("backend.routers.platform.settings", SimpleNamespace(
                mcp_public_base_url="https://knowledge.example", mcp_external_enabled=True)):
            payload = client.get("/platform/services/knowledge").json()
            self.assertTrue(payload["externalAvailable"])
            self.assertEqual(payload["endpoint"], "https://knowledge.example/mcp/knowledge")
        with patch("backend.routers.platform.settings", SimpleNamespace(
                mcp_public_base_url="http://localhost:8000", mcp_external_enabled=True)):
            self.assertFalse(client.get("/platform/services/knowledge").json()["externalAvailable"])
        with patch("backend.routers.platform.settings", SimpleNamespace(
                mcp_public_base_url="https://knowledge.example", mcp_external_enabled=False)):
            self.assertIsNone(client.get("/platform/services/knowledge").json()["endpoint"])
        client.close()


if __name__ == "__main__":
    unittest.main()
