import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app import main

class MCPReadApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        main.DATABASE_PATH = Path(self.temp.name) / 'mcp-read-test.db'
        main.ADMIN_PASSWORD = 'mcp-api-admin-password-long'
        main.SESSION_SECRET = 'mcp-api-session-secret-long-enough-123456789'
        main.MCP_READ_TOKEN = 'mcp-test-read-token-which-is-long-enough-123456'

    def tearDown(self):
        self.temp.cleanup()

    def test_read_routes_require_separate_token_and_reject_writes(self):
        with TestClient(main.app) as client:
            self.assertEqual(client.get('/api/integrations/mcp/nodes').status_code, 401)
            headers = {'Authorization': 'Bearer ' + main.MCP_READ_TOKEN}
            nodes = client.get('/api/integrations/mcp/nodes', headers=headers)
            self.assertEqual(nodes.status_code, 200, nodes.text)
            self.assertEqual(nodes.json(), {'items': []})
            self.assertEqual(client.post('/api/integrations/mcp/nodes', headers=headers).status_code, 405)
            self.assertEqual(client.get('/api/integrations/mcp/nodes', headers={'Authorization': 'Bearer wrong-token'}).status_code, 401)

    def test_mcp_vm_inventory_is_proxied_through_agent(self):
        main.AGENT_URL = 'http://127.0.0.1:8765'
        main.AGENT_TOKEN = 'test-agent-token'
        async def fake_agent(method, path, payload=None):
            self.assertEqual((method, path), ('GET', '/v1/vms'))
            return {'items': [{'name': 'vm-one', 'state': 'off'}]}
        with patch.object(main, 'agent_request', new=AsyncMock(side_effect=fake_agent)):
            with TestClient(main.app) as client:
                headers = {'Authorization': 'Bearer ' + main.MCP_READ_TOKEN}
                inventory = client.get('/api/integrations/mcp/vms', headers=headers)
                self.assertEqual(inventory.status_code, 200, inventory.text)
                detail = client.get('/api/integrations/mcp/vms/vm-one', headers=headers)
                self.assertEqual(detail.json()['name'], 'vm-one')
                missing = client.get('/api/integrations/mcp/vms/unknown', headers=headers)
                self.assertEqual(missing.status_code, 404)

if __name__ == '__main__':
    unittest.main()
