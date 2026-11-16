import asyncio
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from app import main


class ProvisioningWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        main.DATABASE_PATH = Path(self.temp.name) / 'worker-test.db'
        main.AGENT_URL = 'http://127.0.0.1:8765'
        main.AGENT_TOKEN = 'test-agent-token'
        main.ADMIN_PASSWORD = 'worker-test-admin-password-long'
        main.SESSION_SECRET = 'worker-test-session-secret-long-enough-123456789'

    def tearDown(self):
        self.temp.cleanup()

    def _login(self, client):
        response = client.post('/api/auth/login', json={'username': 'admin', 'password': main.ADMIN_PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        return {'X-CSRF-Token': response.json()['csrf_token']}

    def test_queued_job_is_processed_and_persisted(self):
        async def fake_agent_request(method, path, payload=None):
            self.assertEqual((method, path), ('POST', '/v1/vms'))
            return {'name': payload['name'], 'state': 'off'}

        with patch.object(main, 'agent_request', new=AsyncMock(side_effect=fake_agent_request)):
            with TestClient(main.app) as client:
                headers = self._login(client)
                response = client.post('/api/vms', headers=headers, json={
                    'name': 'queued-vm', 'iso_path': 'C:\\ISO\\ubuntu.iso',
                    'memory_mb': 2048, 'disk_gb': 20, 'cpu_count': 2,
                })
                self.assertEqual(response.status_code, 202, response.text)
                job_id = response.json()['provisioning_job_id']
                deadline = time.time() + 5
                job = None
                while time.time() < deadline:
                    job = client.get(f'/api/jobs/{job_id}', headers=headers).json()
                    if job['status'] in ('succeeded', 'failed'):
                        break
                    time.sleep(0.05)
                self.assertEqual(job['status'], 'succeeded', job)
                self.assertEqual(job['attempts'], 1)
                self.assertEqual(job['result']['name'], 'queued-vm')

    def test_two_worker_consumers_dispatch_the_same_queued_job_only_once(self):
        main.initialize_database()
        with closing(sqlite3.connect(main.DATABASE_PATH)) as db:
            user_id = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
            db.execute("INSERT INTO vm_ownership(vm_name,user_id,cpu_count,memory_mb,disk_gb) VALUES('concurrent-vm',?,1,512,8)", (user_id,))
            db.execute("INSERT INTO provisioning_jobs(id,user_id,vm_name,status,request_json) VALUES('concurrent-job',?,'concurrent-vm','queued',?)",
                       (user_id, '{"name":"concurrent-vm","cpu_count":1,"memory_mb":512,"disk_gb":8}'))
            db.commit()
        calls = []

        async def fake_agent_request(method, path, payload=None):
            calls.append(payload["name"])
            await asyncio.sleep(0.2)
            return {"name": payload["name"], "state": "off"}

        async def run_workers():
            workers = [asyncio.create_task(main.provisioning_worker()) for _ in range(2)]
            await asyncio.sleep(0.4)
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        with patch.object(main, "agent_request", new=AsyncMock(side_effect=fake_agent_request)):
            asyncio.run(run_workers())
        self.assertEqual(calls, ["concurrent-vm"])
        with closing(sqlite3.connect(main.DATABASE_PATH)) as db:
            job = db.execute("SELECT status,attempts,worker_lease_owner,worker_lease_expires_at FROM provisioning_jobs WHERE id='concurrent-job'").fetchone()
        self.assertEqual(job[:2], ("succeeded", 1))
        self.assertIsNone(job[2])
        self.assertIsNone(job[3])

    def test_startup_marks_ambiguous_running_jobs_for_reconciliation(self):
        main.initialize_database()
        with closing(sqlite3.connect(main.DATABASE_PATH)) as db:
            user_id = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
            db.execute("INSERT INTO vm_ownership(vm_name,user_id,cpu_count,memory_mb,disk_gb) VALUES('stranded-vm',?,1,512,8)", (user_id,))
            db.execute("INSERT INTO provisioning_jobs(id,user_id,vm_name,status,request_json,attempts,worker_lease_expires_at) VALUES('stranded-job',?,'stranded-vm','running','{}',1,?)", (user_id, int(time.time())-1))
            db.commit()
        with TestClient(main.app) as client:
            headers = self._login(client)
            job = client.get('/api/jobs/stranded-job', headers=headers).json()
            self.assertEqual(job['status'], 'failed')
            self.assertIn('worker lease expired', job['error'])
            with closing(sqlite3.connect(main.DATABASE_PATH)) as db:
                self.assertIsNotNone(db.execute("SELECT 1 FROM vm_ownership WHERE vm_name='stranded-vm'").fetchone())


if __name__ == '__main__':
    unittest.main()
