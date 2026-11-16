import asyncio
import os
import unittest
from unittest.mock import patch

os.environ['VPS_MCP_API_URL'] = 'http://127.0.0.1:8000'
os.environ['VPS_MCP_READ_TOKEN'] = 'mcp-test-token-that-is-long-enough-for-tests-123'

from mcp import Client
from virtuewise_mcp.server import ControlPlaneClient, ControlPlaneError, mcp

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
