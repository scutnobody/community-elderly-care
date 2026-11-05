"""Operational safety tests use only disposable directories and real SQLite/HTTP."""
import importlib
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tests.support import ApiCase


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ops = importlib.import_module('tools.operations')

    def tearDown(self):
        self.tmp.cleanup()

    def source(self):
        source = self.root / 'source'
        source.mkdir()
        with closing(sqlite3.connect(source / 'elderly_care.db')) as db:
            db.execute('CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)')
            db.execute("INSERT INTO sample VALUES(1, 'retained')")
            db.commit()
        (source / 'uploads' / 'attachments').mkdir(parents=True)
        (source / 'uploads' / 'attachments' / 'photo.png').write_bytes(b'attachment-content')
        return source

    def test_offline_backup_roundtrip_retains_database_and_attachment(self):
        source = self.source()
        with self.assertRaises(ValueError):
            self.ops.backup(source, self.root / 'backup')
        archive = self.ops.backup(source, self.root / 'backup', server_stopped=True)
        self.ops.restore(archive, self.root / 'restored', server_stopped=True)
        with closing(sqlite3.connect(self.root / 'restored' / 'elderly_care.db')) as db:
            self.assertEqual(db.execute('SELECT value FROM sample').fetchone()[0], 'retained')
        self.assertEqual((self.root / 'restored/uploads/attachments/photo.png').read_bytes(), b'attachment-content')

    def test_tampering_blocks_restore_before_target_changes(self):
        archive = self.ops.backup(self.source(), self.root / 'backup', server_stopped=True)
        (archive / 'uploads/attachments/photo.png').write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            self.ops.restore(archive, self.root / 'restored', server_stopped=True)
        self.assertFalse((self.root / 'restored').exists())

    def test_missing_registered_attachment_invalidates_backup(self):
        source = self.source()
        with closing(sqlite3.connect(source / 'elderly_care.db')) as db:
            db.execute('CREATE TABLE attachments(stored_name TEXT)')
            db.execute("INSERT INTO attachments VALUES('missing.png')")
            db.commit()
        with self.assertRaises(ValueError):
            self.ops.backup(source, self.root / 'backup', server_stopped=True)

    def test_replace_requires_consent_and_keeps_complete_current_directory(self):
        archive = self.ops.backup(self.source(), self.root / 'backup', server_stopped=True)
        target = self.root / 'target'
        target.mkdir()
        (target / 'important.txt').write_text('current data', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.ops.restore(archive, target, server_stopped=True)
        retained = self.ops.restore(archive, target, server_stopped=True, replace=True)
        self.assertEqual((retained / 'important.txt').read_text(encoding='utf-8'), 'current data')

    def test_manifest_cannot_escape_snapshot_directory(self):
        archive = self.ops.backup(self.source(), self.root / 'backup', server_stopped=True)
        manifest = json.loads((archive / 'manifest.json').read_text(encoding='utf-8'))
        manifest['files']['../outside'] = {'sha256': '0' * 64, 'size': 0}
        (archive / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.ops.verify(archive)

    def test_generator_builds_requested_volume_and_refuses_existing_path(self):
        generator = importlib.import_module('tools.generate_data')
        target = self.root / 'generated.db'
        generator.generate(target, elders=7, providers=3, orders=19, health_records=23, seed=42)
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM elderly_profiles').fetchone()[0], 7)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM service_providers').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM service_orders').fetchone()[0], 19)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM health_vitals').fetchone()[0], 23)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        with self.assertRaises(FileExistsError):
            generator.generate(target, elders=1, providers=1, orders=1, health_records=1)

    def test_generator_refuses_all_application_subdirectories(self):
        generator = importlib.import_module('tools.generate_data')
        original = generator.PROJECT
        generator.PROJECT = self.root / 'application'
        try:
            with self.assertRaises(ValueError):
                generator.generate(generator.PROJECT / 'static' / 'bad.db', elders=1, providers=1,
                    orders=1, health_records=1, template=original / 'elderly_care.db')
        finally:
            generator.PROJECT = original

    def test_local_http_refuses_redirect_before_followup_request(self):
        client = importlib.import_module('tools.local_http')
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header('Location', 'http://example.com/should-never-be-requested')
                self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with self.assertRaises(ValueError):
                client.call(f'http://127.0.0.1:{server.server_port}', 'GET', '/api/orders')
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_local_url_policy_rejects_remote_credentials_and_redirects(self):
        client = importlib.import_module('tools.local_http')
        for url in ['http://example.com:8080', 'http://127.0.0.1.evil.test', 'http://user:pass@localhost', 'file:///tmp/a', 'http://0.0.0.0:8080']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                client.local_base(url)
        self.assertEqual(client.local_base('http://127.0.0.1:8080/'), 'http://127.0.0.1:8080')

    def test_load_checker_reports_real_counts_failures_and_locks(self):
        load = importlib.import_module('tools.load_check')
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                self.reply(200, {'ok': True, 'token': 'test-token', 'user': {'role': 'admin'}})
            def do_GET(self):
                if self.headers.get('Authorization') != 'Bearer test-token':
                    return self.reply(403, {'ok': False})
                if self.path.startswith('/api/dashboard-stats'):
                    return self.reply(200, {'ok': True, 'stats': {'elder_count': 7, 'order_count': 19, 'provider_count': 3}})
                self.reply(503, {'ok': False, 'error': 'database is locked'})
            def reply(self, code, payload):
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            result = load.run(f'http://127.0.0.1:{server.server_port}', 'admin', 'password', 'admin', concurrency=2, requests=6, paths=['/api/orders'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
        self.assertEqual(result['requests'], 6)
        self.assertEqual(result['errors'], 6)
        self.assertEqual(result['lock_errors'], 6)
        self.assertEqual(result['data_volume']['order_count'], 19)
        self.assertGreater(result['duration_seconds'], 0)

    def test_smoke_refuses_mutating_requests_without_isolated_consent(self):
        smoke = importlib.import_module('api_smoke_test')
        with self.assertRaises(ValueError):
            smoke.run('http://127.0.0.1:9', confirm_isolated=False)


class SmokeWorkflowTests(ApiCase):
    def test_generated_database_with_zero_orders_can_initialize_without_demo_dependencies(self):
        generator = importlib.import_module('tools.generate_data')
        target = self.root / 'generated' / 'elderly_care.db'
        generator.generate(target, elders=1, providers=1, orders=0, health_records=0, template=self.server.DB_PATH)
        self.server.DB_PATH = target
        self.server.BASE_DIR = target.parent
        self.server.UPLOAD_DIR = target.parent / 'uploads' / 'exams'
        self.server.initialize_database()
        self.assertEqual(self.sql('SELECT COUNT(*) AS n FROM elderly_profiles')[0]['n'], 1)

    def test_smoke_finishes_role_authenticated_approved_record_pipeline(self):
        smoke = importlib.import_module('api_smoke_test')
        order_no = smoke.run(self.base, confirm_isolated=True)
        row = self.sql('SELECT status FROM service_orders WHERE order_no=?', (order_no,))[0]
        self.assertEqual(row['status'], 'COMPLETED')

    def test_smoke_reviews_controlled_test_credential_when_service_requires_it(self):
        with self.server.connect_db() as db:
            db.execute("UPDATE service_items SET required_certificate='冒烟测试专用资格' WHERE service_code='FW-003'")
        order_no = importlib.import_module('api_smoke_test').run(self.base, confirm_isolated=True)
        credential = self.sql("SELECT status,certificate_no FROM provider_credentials WHERE name='冒烟测试专用资格'")[0]
        self.assertEqual(credential['status'], 'APPROVED')
        self.assertTrue(credential['certificate_no'].startswith('TEST-SMOKE-'))
        self.assertEqual(self.sql('SELECT status FROM service_orders WHERE order_no=?', (order_no,))[0]['status'], 'COMPLETED')


if __name__ == '__main__':
    unittest.main()
