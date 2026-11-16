import unittest
from fastapi.testclient import TestClient
from app.main import app


class DeveloperPortalTests(unittest.TestCase):
    def setUp(self): self.client = TestClient(app)

    def test_developer_portal_is_served(self):
        response = self.client.get("/developers")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Build and install a provider", response.text)
        self.assertIn("Connect with MCP", response.text)

    def test_dashboard_includes_admin_mcp_token_management(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("mcp-token-form", response.text)
        self.assertIn("mcp-token-copy", response.text)
        self.assertIn("node-enroll-form", response.text)
        self.assertIn("node-enroll-copy", response.text)
        self.assertIn("node-management-content", response.text)

    def test_portal_assets_are_served(self):
        for path in ("/assets/developers.css", "/assets/developers.js"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertGreater(len(response.content), 100)


if __name__ == "__main__": unittest.main()
