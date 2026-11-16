import asyncio
import os
import unittest
from unittest.mock import patch

os.environ['VPS_MCP_API_URL'] = 'http://127.0.0.1:8000'
os.environ['VPS_MCP_READ_TOKEN'] = 'mcp-test-token-that-is-long-enough-for-tests-123'

from mcp import Client
from starlette.testclient import TestClient
from virtuewise_mcp.server import ControlPlaneClient, ControlPlaneError, create_http_app, mcp

class MCPServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_registers_only_six_read_only_tools(self):
        async with Client(mcp, raise_exceptions=True) as client:
            tools = await client.list_tools()
        names = {tool.name for tool in tools.tools}
        self.assertEqual(names, {
            'virtuewise_health', 'list_nodes', 'get_node_status',
            'list_virtual_machines', 'get_vm_details', 'get_resource_usage',
        })

    async def test_tool_calls_use_control_plane_client(self):
        with patch.object(ControlPlaneClient, 'get', return_value={'items': []}) as get:
            async with Client(mcp, raise_exceptions=True) as client:
                result = await client.call_tool('list_nodes', {})
            get.assert_called_once_with('/api/integrations/mcp/nodes')
            self.assertFalse(result.is_error)

    def test_streamable_http_requires_the_scoped_bearer_token(self):
        token = 'scoped-http-token-that-is-at-least-32-chars'
        app = create_http_app(token=token, allowed_hosts=['testserver'])
        with TestClient(app) as client:
            denied = client.post('/mcp', json={})
            self.assertEqual(denied.status_code, 401)
            denied = client.post('/mcp', headers={'Authorization': 'Bearer ' + 'z' * 40}, json={})
            self.assertEqual(denied.status_code, 401)
            accepted_by_auth = client.post('/mcp', headers={
                'Authorization': 'Bearer ' + token,
                'Accept': 'application/json, text/event-stream',
                'Content-Type': 'application/json',
            }, json={
                'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                           'clientInfo': {'name': 'test-client', 'version': '1.0.0'}},
            })
            self.assertEqual(accepted_by_auth.status_code, 200, accepted_by_auth.text)
            self.assertIn('result', accepted_by_auth.json())
            self.assertEqual(client.get('/unrelated').status_code, 404)

    def test_streamable_http_binds_loopback_only_and_validates_config(self):
        with self.assertRaisesRegex(ValueError, 'loopback'):
            create_http_app(token='x' * 40, host='0.0.0.0')
        with self.assertRaisesRegex(ValueError, '32 characters'):
            create_http_app(token='short')
        with self.assertRaisesRegex(ValueError, 'path'):
            create_http_app(token='x' * 40, path='/mcp/../admin')

    def test_rejects_remote_plain_http(self):
        with self.assertRaises(ValueError):
            ControlPlaneClient('http://control.example.com', 'x' * 40)

    def test_rejects_short_credentials(self):
        with self.assertRaises(ValueError):
            ControlPlaneClient('http://127.0.0.1:8000', 'short')

    def test_client_restricts_paths_to_dedicated_read_api(self):
        client = ControlPlaneClient('http://127.0.0.1:8000', 'x' * 40)
        with self.assertRaises(ValueError):
            client.get('/api/users')
        with self.assertRaises(ValueError):
            client.get('/api/integrations/mcp/../users')

if __name__ == '__main__':
    unittest.main()
