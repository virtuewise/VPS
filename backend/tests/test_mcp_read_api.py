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

    def _admin_headers(self, client):
        login = client.post('/api/auth/login', json={
            'username': 'admin', 'password': main.ADMIN_PASSWORD
        })
        self.assertEqual(login.status_code, 200, login.text)
        return {'X-CSRF-Token': login.json()['csrf_token']}

    def test_customer_mcp_tokens_are_scoped_hashed_expiring_and_revocable(self):
        with TestClient(main.app) as client:
            admin_headers = self._admin_headers(client)
            created_user = client.post('/api/users', headers=admin_headers, json={
                'username': 'mcp-customer', 'password': 'mcp-customer-password-123',
                'role': 'user', 'vm_quota': 5
            })
            self.assertEqual(created_user.status_code, 200, created_user.text)
            user_id = created_user.json()['id']
            issued = client.post('/api/mcp/tokens', headers=admin_headers, json={
                'user_id': user_id, 'label': 'Customer read-only',
                'scopes': ['health.read', 'vms.read'], 'expires_in_days': 7
            })
            self.assertEqual(issued.status_code, 200, issued.text)
            token = issued.json()['token']
            token_id = issued.json()['id']
            self.assertEqual(set(issued.json()['scopes']), {'health.read', 'vms.read'})
            with closing(sqlite3.connect(main.DATABASE_PATH)) as db:
                stored = db.execute('SELECT token_hash FROM mcp_api_tokens WHERE id=?', (token_id,)).fetchone()[0]
                db.execute('INSERT INTO vm_ownership(vm_name,user_id) VALUES(?,?)', ('owned-vm', user_id))
                db.commit()
            import hashlib
            self.assertEqual(stored, hashlib.sha256(token.encode()).hexdigest())
            self.assertNotEqual(stored, token)

            main.AGENT_URL = 'http://127.0.0.1:8765'
            main.AGENT_TOKEN = 'test-agent-token'
            async def fake_agent(method, path, payload=None):
                self.assertEqual((method, path), ('GET', '/v1/vms'))
                return {'items': [{'name': 'owned-vm'}, {'name': 'someone-elses-vm'}]}
            with patch.object(main, 'MCP_READ_TOKEN', ''), patch.object(main, 'agent_request', new=AsyncMock(side_effect=fake_agent)):
                headers = {'Authorization': 'Bearer ' + token}
                inventory = client.get('/api/integrations/mcp/vms', headers=headers)
                self.assertEqual(inventory.status_code, 200, inventory.text)
                self.assertEqual([vm['name'] for vm in inventory.json()['items']], ['owned-vm'])
                detail = client.get('/api/integrations/mcp/vms/owned-vm', headers=headers)
                self.assertEqual(detail.status_code, 200, detail.text)
                hidden = client.get('/api/integrations/mcp/vms/someone-elses-vm', headers=headers)
                self.assertEqual(hidden.status_code, 404)
                self.assertEqual(client.get('/api/integrations/mcp/nodes', headers=headers).status_code, 403)
                self.assertEqual(client.get('/api/integrations/mcp/resources', headers=headers).status_code, 403)
                self.assertEqual(client.get('/api/integrations/mcp/health', headers=headers).json(), {'control_plane': 'ok'})

            listed = client.get('/api/mcp/tokens')
            self.assertEqual(listed.status_code, 200, listed.text)
            self.assertNotIn('token_hash', str(listed.json()))
            revoked = client.post(f'/api/mcp/tokens/{token_id}/revoke', headers=admin_headers)
            self.assertEqual(revoked.status_code, 200, revoked.text)
            self.assertEqual(client.get('/api/integrations/mcp/vms', headers={'Authorization': 'Bearer ' + token}).status_code, 401)

    def test_customer_cannot_be_granted_infrastructure_wide_mcp_scopes(self):
        with TestClient(main.app) as client:
            admin_headers = self._admin_headers(client)
            user = client.post('/api/users', headers=admin_headers, json={
                'username': 'scope-customer', 'password': 'scope-customer-password-123',
                'role': 'user', 'vm_quota': 2
            })
            self.assertEqual(user.status_code, 200, user.text)
            forbidden = client.post('/api/mcp/tokens', headers=admin_headers, json={
                'user_id': user.json()['id'], 'label': 'Too broad',
                'scopes': ['nodes.read'], 'expires_in_days': 30
            })
            self.assertEqual(forbidden.status_code, 422, forbidden.text)
            self.assertEqual(client.post('/api/mcp/tokens', json={
                'user_id': user.json()['id'], 'label': 'No admin session',
                'scopes': ['vms.read']
            }).status_code, 403)

    def test_scoped_mcp_tokens_expire(self):
        with TestClient(main.app) as client:
            admin_headers = self._admin_headers(client)
            user = client.post('/api/users', headers=admin_headers, json={
                'username': 'expiry-customer', 'password': 'expiry-customer-password-123',
                'role': 'user', 'vm_quota': 2
            })
            issued = client.post('/api/mcp/tokens', headers=admin_headers, json={
                'user_id': user.json()['id'], 'label': 'Expiring token',
                'scopes': ['vms.read'], 'expires_in_days': 1
            })
            self.assertEqual(issued.status_code, 200, issued.text)
            with closing(sqlite3.connect(main.DATABASE_PATH)) as db:
                db.execute('UPDATE mcp_api_tokens SET expires_at=0 WHERE id=?', (issued.json()['id'],))
                db.commit()
            denied = client.get('/api/integrations/mcp/vms', headers={'Authorization': 'Bearer ' + issued.json()['token']})
            self.assertEqual(denied.status_code, 401)

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
