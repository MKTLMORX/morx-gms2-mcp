"""隔离任务必须交付本次已验证产物，失败、取消或冲突均不能覆盖旧包。"""
import os
from pathlib import Path
import secrets
import time
import unittest
from unittest.mock import patch

from morx_gms2_mcp import android_artifacts, android_signing, android_toolchain, schemas
from morx_gms2_mcp.packages import PackageManager
from tests import test_android_toolchain as fixtures


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ToolchainTests('test_native_runtime_requires_real_ndk_even_for_vm')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.project, self.host = self.fixture.project, self.fixture.host
        self.build_root, self.export_root = self.host / 'jobs', self.host / 'packages'
        android_toolchain.configure(self.project, {'build_root': str(self.build_root), 'export_root': str(self.export_root)}, dry_run=False)
        self.key = self.host / 'existing.p12'; self.key.write_bytes(b'owned-test-key')
        android_signing.configure(self.project, 'release', str(self.key), 'application', 'MORX_PACKAGE_TEST', dry_run=False)
        self.manager = PackageManager(self.project, self.fixture.options)
        self.addCleanup(self.manager.close)

    def request(self, **extra):
        return self.manager.build(str(self.export_root), signing_profile='release', allow_downloads=True, **extra)

    def command(self, argv, **kwargs):
        self.assertIn('-Dorg.gradle.project.android.builder.sdkDownload=false', kwargs['env']['GRADLE_OPTS'])
        copied = Path(kwargs['cwd']) / 'project'
        self.assertTrue((copied / self.project.yyp.name).is_file())
        self.assertFalse((copied / '.codex').exists())
        self.assertFalse(any(copied.rglob('*.p12')))
        candidate = Path(next(arg[4:] for arg in argv if arg.startswith('/tf=')))
        candidate.write_bytes(b'new candidate')
        kwargs['on_line']('Final Compile finished')
        return {'exit_code': 0, 'output': ''}

    def artifact(self, path, *args, **kwargs):
        return {'path': str(path), 'sha256': android_artifacts.checksum(path), 'bytes': path.stat().st_size,
                'artifact_type': 'apk', 'package_name': 'org.example.probe', 'abis': ['arm64-v8a'],
                'versionName': '1.0.0', 'versionCode': 1000000, 'min_sdk': 23, 'target_sdk': 35,
                'signature': {'verified': True}, 'format_verified': True}

    def execute(self, native=None, validator=None, **extra):
        with patch.dict(os.environ, {'MORX_PACKAGE_TEST': secrets.token_hex(16)}), patch(
                'morx_gms2_mcp.packages.android_signing.igor_password', return_value='test-encoded-value'), patch(
                'morx_gms2_mcp.packages.run_command', side_effect=native or self.command), patch(
                'morx_gms2_mcp.packages.android_artifacts.inspect', side_effect=validator or self.artifact):
            started = self.request(dry_run=False, **extra)
            self.assertTrue(started['submitted'], started)
            self.manager.jobs[started['job_id']]['thread'].join(8)
            self.assertFalse(self.manager.jobs[started['job_id']]['thread'].is_alive())
            return self.manager.status(started['job_id'])

    def test_preview_and_success_preserve_source_and_final_deliverable(self):
        source = {file.relative_to(self.project.root).as_posix(): file.read_bytes() for file in self.project.root.rglob('*') if file.is_file()}
        with patch.dict(os.environ, {'MORX_PACKAGE_TEST': secrets.token_hex(16)}):
            preview = self.request()
        self.assertTrue(preview['ready'], preview)
        self.assertFalse(self.build_root.exists())
        result = self.execute()
        self.assertEqual(result['state'], 'succeeded', result)
        self.assertTrue(result['cleaned'])
        self.assertRegex(result['source_manifest_sha256'], r'^[0-9a-f]{64}$')
        artifact = Path(result['artifact']['path'])
        self.assertEqual(artifact.read_bytes(), b'new candidate')
        self.manager.close()
        self.assertTrue(artifact.is_file())
        self.assertEqual(source, {file.relative_to(self.project.root).as_posix(): file.read_bytes()
                                 for file in self.project.root.rglob('*') if file.is_file()})

    def test_zero_exit_without_candidate_does_not_reuse_existing_package(self):
        self.export_root.mkdir(); target = self.export_root / 'test.apk'; target.write_bytes(b'old delivery')
        old_sha = android_artifacts.checksum(target)
        result = self.execute(native=lambda *args, **kwargs: {'exit_code': 0, 'output': ''}, expected_output_sha256=old_sha)
        self.assertEqual(result['failure_code'], 'output_missing')
        self.assertEqual(target.read_bytes(), b'old delivery')
        self.assertTrue(result['cleaned'])

    def test_validation_failure_and_wrong_identity_never_deliver(self):
        def bad_identity(path, *args, **kwargs):
            value = self.artifact(path); value['package_name'] = 'org.other.application'; return value
        for validator in (lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('signature invalid')), bad_identity):
            result = self.execute(validator=validator)
            self.assertEqual(result['state'], 'failed')
            self.assertFalse((self.export_root / 'test.apk').exists())
            self.assertTrue(result['cleaned'])

    def test_target_change_during_build_is_preserved(self):
        self.export_root.mkdir(); target = self.export_root / 'test.apk'; target.write_bytes(b'previous')
        def replace(argv, **kwargs):
            result = self.command(argv, **kwargs); target.write_bytes(b'external update'); return result
        result = self.execute(native=replace, expected_output_sha256=android_artifacts.checksum(target))
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(target.read_bytes(), b'external update')

    def test_queued_package_blocks_an_external_save_before_starting_compiler(self):
        self.project.create('scripts', 'scr_probe', 'function scr_probe() { return 1; }')
        relative = 'scripts/scr_probe/scr_probe.gml'
        path = self.project.path(relative)
        external = path.read_bytes().replace(b'return 1', b'return 2')
        with patch.dict(os.environ, {'MORX_PACKAGE_TEST': secrets.token_hex(16)}), patch(
                'morx_gms2_mcp.packages.run_command') as compiler:
            self.manager.gate.acquire()
            try:
                job = self.request(dry_run=False)
                self.assertTrue(job['submitted'])
                path.write_bytes(external)
            finally:
                self.manager.gate.release()
            self.manager.jobs[job['job_id']]['thread'].join(8)
            compiler.assert_not_called()
        status = self.manager.status(job['job_id'])
        self.assertEqual(status['failure_code'], 'source_conflict')
        self.assertEqual(status['error']['conflicts'][0]['path'], relative)
        self.assertTrue(status['cleaned'])
        self.assertEqual(path.read_bytes(), external)

    def test_cancel_is_async_and_cleans_only_its_snapshot(self):
        def waiting(argv, **kwargs):
            while not kwargs['cancel'].is_set(): time.sleep(.01)
            raise InterruptedError('cancelled')
        with patch.dict(os.environ, {'MORX_PACKAGE_TEST': secrets.token_hex(16)}), patch(
                'morx_gms2_mcp.packages.android_signing.igor_password', return_value='test-encoded-value'), patch(
                'morx_gms2_mcp.packages.run_command', side_effect=waiting):
            result = self.request(dry_run=False)
            sid = result['job_id']
            self.assertFalse(self.manager.cancel(sid)['cancel_requested'])
            self.assertTrue(self.manager.cancel(sid, False)['cancel_requested'])
            self.manager.jobs[sid]['thread'].join(8)
        self.assertEqual(self.manager.status(sid)['state'], 'cancelled')
        self.assertTrue(self.manager.status(sid)['cleaned'])
        self.assertTrue(self.key.is_file())

    def test_download_consent_and_output_boundary_are_enforced_before_submission(self):
        with patch.dict(os.environ, {'MORX_PACKAGE_TEST': secrets.token_hex(16)}):
            result = self.manager.build(str(self.export_root), signing_profile='release', dry_run=False)
        self.assertEqual(result['state'], 'preflight_failed')
        self.assertTrue(any(row['code'] == 'downloads_not_authorized' for row in result['toolchain']['issues']))
        self.assertEqual(len(self.manager.jobs), 0)
        with self.assertRaises(ValueError):
            self.manager.build(str(self.fixture.sdk), signing_profile='release')

    def test_cleanup_failure_preserves_marker_and_can_be_retried_without_removing_delivery(self):
        with patch('morx_gms2_mcp.packages.shutil.rmtree', side_effect=PermissionError('owned test lock')):
            result = self.execute()
        self.assertEqual(result['state'], 'succeeded')
        self.assertFalse(result['cleaned'])
        job = self.manager.jobs[result['job_id']]
        self.assertTrue((job['directory'] / 'morx-build.json').is_file())
        self.assertTrue(self.manager.cleanup(result['job_id'])['dry_run'])
        result = self.manager.cleanup(result['job_id'], False)
        self.assertTrue(result['cleaned'])
        self.assertNotIn('cleanup_error', result)
        self.assertTrue(Path(result['artifact']['path']).is_file())

    def test_release_copy_strips_managed_bridge_without_editing_source(self):
        room_path = self.project.root / 'rooms/Room1/Room1.yy'
        from morx_gms2_mcp.storage import encode, parse_yy
        room_path.write_bytes(encode(schemas.room('Room1', 640, 360)))
        self.project.install_runtime_bridge(False)
        original_room = room_path.read_bytes()
        original_yyp = self.project.yyp.read_bytes()
        def native(argv, **kwargs):
            copied = Path(kwargs['cwd']) / 'project'
            self.assertFalse((copied / 'objects/sys_morx_runtime').exists())
            meta = parse_yy((copied / self.project.yyp.name).read_text(encoding='utf-8-sig'))
            self.assertNotIn('sys_morx_runtime', [row['id']['name'] for row in meta['resources']])
            room = parse_yy((copied / 'rooms/Room1/Room1.yy').read_text(encoding='utf-8-sig'))
            self.assertNotIn('MORX_Runtime', [row['name'] for row in room['layers']])
            return self.command(argv, **kwargs)
        result = self.execute(native=native)
        self.assertEqual(result['state'], 'succeeded', result)
        self.assertEqual(room_path.read_bytes(), original_room)
        self.assertEqual(self.project.yyp.read_bytes(), original_yyp)
        self.assertTrue(self.project.bridge_status()['compatible'])

    def test_staging_name_collision_does_not_delete_other_file(self):
        existing = []
        def native(argv, **kwargs):
            result = self.command(argv, **kwargs)
            self.export_root.mkdir(exist_ok=True)
            path = self.export_root / ('test.apk.morx-' + Path(kwargs['cwd']).name + '.tmp')
            path.write_bytes(b'external staged file'); existing.append(path)
            return result
        result = self.execute(native=native)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(existing[0].read_bytes(), b'external staged file')
        self.assertFalse((self.export_root / 'test.apk').exists())

    def test_queued_deadline_expires_without_starting_native_command(self):
        self.manager.gate.acquire()
        try:
            with patch.dict(os.environ, {'MORX_PACKAGE_TEST': secrets.token_hex(16)}), patch(
                    'morx_gms2_mcp.packages.run_command') as native:
                result = self.request(dry_run=False, max_seconds=1)
                self.manager.jobs[result['job_id']]['thread'].join(4)
                self.assertEqual(self.manager.status(result['job_id'])['state'], 'timed_out')
                self.assertTrue(self.manager.status(result['job_id'])['cleaned'])
                native.assert_not_called()
        finally:
            self.manager.gate.release()
