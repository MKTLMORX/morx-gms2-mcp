"""签名配置只保留引用，子进程口令不进argv，已有签名不能隐式轮换。"""
import json
import os
from pathlib import Path
import plistlib
import secrets
import subprocess
import tempfile
from unittest.mock import patch

from morx_gms2_mcp import android_signing as signing
from morx_gms2_mcp import android_toolchain as toolchain
from morx_gms2_mcp.runtime import RuntimeOptions
from morx_gms2_mcp.storage import FileConflict, encode
from tests.test_core import ProjectFixture


class SigningTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.host = Path(temporary.name)
        jdk = self.host / 'jdk'
        (jdk / 'bin').mkdir(parents=True)
        (jdk / 'bin/keytool.exe').touch()
        self.project.path(toolchain.CONFIG).write_bytes(encode({'version': 1, 'jdk_root': str(jdk)}))
        self.options = RuntimeOptions(True)
        self.keystore = self.host / 'existing.p12'
        self.keystore.write_bytes(b'owned-test-keystore')
        self.secret = secrets.token_hex(20)

    def add_profile(self):
        return signing.configure(self.project, 'release', str(self.keystore), 'application',
                                 'MORX_TEST_STORE', dry_run=False)

    def test_profile_reference_preview_conflict_and_exact_restore(self):
        before = self.keystore.read_bytes()
        preview = signing.configure(self.project, 'release', str(self.keystore), 'application', 'MORX_TEST_STORE')
        self.assertTrue(preview['dry_run'])
        self.assertFalse(self.project.path(signing.REGISTRY).exists())
        result = self.add_profile()
        data, _ = signing.registry(self.project)
        self.assertEqual(data['profiles']['release']['key_password_env'], 'MORX_TEST_STORE')
        self.assertNotIn(self.secret, self.project.path(signing.REGISTRY).read_text(encoding='utf-8'))
        with self.assertRaises(ValueError):
            signing.configure(self.project, 'release', str(self.keystore), 'application', 'MORX_TEST_STORE', dry_run=False)
        self.project.restore(result['transaction'], False)
        self.assertFalse(self.project.path(signing.REGISTRY).exists())
        self.assertEqual(self.keystore.read_bytes(), before)

    def test_existing_alias_change_is_not_silent_key_rotation(self):
        self.add_profile()
        checksum = signing.inspect(self.project, self.options)['configuration_sha256']
        with self.assertRaisesRegex(ValueError, '轮换'):
            signing.configure(self.project, 'release', str(self.keystore), 'different', 'MORX_TEST_STORE',
                              expected_sha256=checksum, dry_run=False)
        nested = self.root / 'private.p12'; nested.write_bytes(b'test')
        with self.assertRaises(ValueError):
            signing.configure(self.project, 'other', str(nested), 'application', 'MORX_TEST_STORE')

    def test_certificate_check_does_not_return_password_or_certificate_identity(self):
        self.add_profile()
        certificate = 'AA:'*31 + 'AA'
        output = ('Owner: CN=private certificate identity\nSHA256: ' + certificate).encode('utf-8')
        with patch.dict(os.environ, {'MORX_TEST_STORE': self.secret}), patch(
                'morx_gms2_mcp.android_signing.subprocess.run', return_value=subprocess.CompletedProcess([], 0, output)) as run:
            report = signing.inspect(self.project, self.options, 'release', True)
            argv = run.call_args.args[0]
            self.assertNotIn(self.secret, str(argv))
            self.assertIn('-storepass:env', argv)
            self.assertEqual(run.call_args.kwargs['env']['MORX_ANDROID_STORE_PASS'], self.secret)
        self.assertTrue(report['profiles'][0]['alias_verified'])
        self.assertEqual(report['profiles'][0]['certificate_sha256'], 'aa'*32)
        self.assertNotIn(self.secret, str(report))
        self.assertNotIn('private certificate identity', str(report))

    def test_missing_environment_and_keytool_failure_do_not_expose_diagnostics(self):
        self.add_profile()
        with patch.dict(os.environ, {'MORX_TEST_STORE': ''}):
            result = signing.inspect(self.project, self.options, 'release', True)
        self.assertFalse(result['profiles'][0]['password_available'])
        with patch.dict(os.environ, {'MORX_TEST_STORE': self.secret}), patch(
                'morx_gms2_mcp.android_signing.subprocess.run', return_value=subprocess.CompletedProcess([], 1, self.secret.encode())):
            result = signing.inspect(self.project, self.options, 'release', True)
        self.assertNotIn(self.secret, str(result))
        self.assertFalse(result['profiles'][0]['alias_verified'])

    def test_development_preview_and_creation_never_overwrite_existing_key(self):
        def generate(argv, **kwargs):
            self.assertNotIn(kwargs['env']['MORX_ANDROID_STORE_PASS'], str(argv))
            destination = Path(argv[argv.index('-keystore') + 1])
            destination.write_bytes(b'generated-test-keystore')
            return subprocess.CompletedProcess(argv, 0, b'')
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.host)}), patch(
                'morx_gms2_mcp.android_signing._dpapi', return_value=b'protected-test-value'), patch(
                'morx_gms2_mcp.android_signing.subprocess.run', side_effect=generate) as run:
            self.assertTrue(signing.create_development(self.project, self.options)['dry_run'])
            run.assert_not_called()
            result = signing.create_development(self.project, self.options, dry_run=False)
            self.assertTrue(result['created'])
            report = signing.inspect(self.project, self.options)
            self.assertNotIn('protected-test-value', str(report))
            checksum = report['configuration_sha256']
            again = signing.create_development(self.project, self.options, checksum, False)
            self.assertFalse(again['created'])
            self.assertEqual(run.call_count, 1)
        self.assertEqual(self.keystore.read_bytes(), b'owned-test-keystore')

    def test_signing_settings_encoding_requires_local_license_without_returning_it(self):
        license_file = self.host / 'owned-test.plist'
        license_file.write_bytes(plistlib.dumps({'unrelated': 'must not be disclosed'}))
        with self.assertRaises(ValueError) as error:
            signing.igor_password(self.secret, license_file)
        self.assertNotIn('must not be disclosed', str(error.exception))
        license_file.write_bytes(plistlib.dumps({'email': 'fixture@example.com'}))
        encoded = signing.igor_password(self.secret, license_file)
        self.assertNotIn(self.secret, encoded)
        self.assertNotIn('fixture@example.com', encoded)
        license_file.write_bytes(plistlib.dumps({'Licensee': {'Email': 'fixture@example.com', 'Id': 'not returned'}}))
        self.assertEqual(signing.igor_password(self.secret, license_file), encoded)

    def test_development_registry_readback_conflict_preserves_new_key(self):
        def generate(argv, **kwargs):
            Path(argv[argv.index('-keystore') + 1]).write_bytes(b'generated-test-keystore')
            return subprocess.CompletedProcess(argv, 0, b'')
        external = encode({'version': 1, 'profiles': {}})
        write = self.project._write
        def concurrent_save(relative, data):
            write(relative, data)
            if relative == signing.REGISTRY:
                self.project.path(relative).write_bytes(external)
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.host)}), patch(
                'morx_gms2_mcp.android_signing._dpapi', return_value=b'protected-test-value'), patch(
                'morx_gms2_mcp.android_signing.subprocess.run', side_effect=generate), patch.object(
                self.project, '_write', side_effect=concurrent_save):
            with self.assertRaises(FileConflict) as conflict:
                signing.create_development(self.project, self.options, dry_run=False)
        self.assertEqual(self.project.path(signing.REGISTRY).read_bytes(), external)
        txid = conflict.exception.details['transaction']
        saved = json.loads((self.project.journal / txid / 'after' / signing.REGISTRY).read_bytes())
        profile = saved['profiles']['development']
        self.assertTrue(Path(profile['keystore']).is_file())
        self.assertTrue(Path(profile['secret_file']).is_file())
