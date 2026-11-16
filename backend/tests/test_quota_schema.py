import sqlite3, tempfile, unittest
from contextlib import closing
from pathlib import Path
from app.tenancy import initialize_tenancy

class QuotaSchemaTests(unittest.TestCase):
    def test_migrations_add_resource_allocations_and_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'test.db'
            initialize_tenancy(db, 'a-secure-test-password-long-enough')
            with closing(sqlite3.connect(db)) as conn:
                user=conn.execute('SELECT cpu_quota_cores,memory_quota_mb,storage_quota_gb FROM users WHERE username=?',('admin',)).fetchone()
                self.assertEqual(user,(8,16384,200))
                columns={r[1] for r in conn.execute('PRAGMA table_info(vm_ownership)')}
                self.assertTrue({'cpu_count','memory_mb','disk_gb','node_id','network_name'}.issubset(columns))
                tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertIn('provisioning_jobs',tables)
                job_columns={r[1] for r in conn.execute('PRAGMA table_info(provisioning_jobs)')}
                self.assertTrue({'reconciliation_status','reconciled_by','reconciled_at','reconciliation_note','idempotency_key','request_hash','worker_lease_owner','worker_lease_expires_at'}.issubset(job_columns))
            initialize_tenancy(db, 'a-secure-test-password-long-enough')

if __name__=='__main__': unittest.main()
