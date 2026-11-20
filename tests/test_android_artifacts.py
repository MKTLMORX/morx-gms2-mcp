"""最终包须通过格式、官方元数据与签名检查，篡改或缺失不能冒充成功。"""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from morx_gms2_mcp import android_artifacts as artifacts, android_bridge

BADGING = """package: name='org.example.probe' versionCode='2003004' versionName='2.3.4'
sdkVersion:'23'
targetSdkVersion:'35'
uses-permission: name='android.permission.INTERNET'
application-label:'中文标题'
launchable-activity: name='org.example.probe.RunnerActivity'
native-code: 'arm64-v8a'
"""
MANIFEST = '''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="org.example.probe"
android:versionCode="2003004" android:versionName="2.3.4"><uses-sdk android:minSdkVersion="23" android:targetSdkVersion="35"/>
<uses-permission android:name="android.permission.INTERNET"/><application android:debuggable="false"/></manifest>'''


def bridge_marker(protocol=2):
    return {'protocol':protocol,'bridge':'b'*32,'development_only':True,'transport':'adb_run_as',
            'network_port':None,'sandbox':'files/morx_mcp'}


class ArtifactTests(unittest.TestCase):
    def test_badging_sdk_labels_and_conflicting_aliases(self):
        modern = BADGING.replace("sdkVersion:'23'", "minSdkVersion:'23'")
        self.assertEqual(artifacts.parse_badging(modern)['min_sdk'], 23)
        with self.assertRaisesRegex(ValueError, '冲突'):
            artifacts.parse_badging(modern + "\nsdkVersion:'24'\n")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.chain = {'paths': {'sdk_root': {'path': str(self.root)}, 'jdk_root': {'path': str(self.root)},
                               'bundletool_path': {'path': str(self.root / 'bundletool.jar')}},
                      'components': {'java': str(self.root / 'java.exe'), 'keytool': str(self.root / 'keytool.exe')},
                      'dependencies': {'build_tools': {'effective_value': '35.0.0'}}}

    def package(self, suffix, extra=None):
        path = self.root / ('probe.' + suffix)
        with zipfile.ZipFile(path, 'w') as archive:
            if suffix == 'apk':
                archive.writestr('AndroidManifest.xml', b'owned-test-placeholder')
                archive.writestr('lib/arm64-v8a/libprobe.so', b'owned-test-placeholder')
            else:
                archive.writestr('BundleConfig.pb', b'owned-test-placeholder')
                archive.writestr('base/manifest/AndroidManifest.xml', b'owned-test-placeholder')
                archive.writestr('base/lib/arm64-v8a/libprobe.so', b'owned-test-placeholder')
            for name, value in (extra or {}).items():
                archive.writestr(name, value)
        return path

    def test_apk_metadata_signature_and_hash_are_all_required(self):
        path = self.package('apk')
        outputs = [{'exit_code': 0, 'output': BADGING},
                   {'exit_code': 0, 'output': 'Signer #1 certificate SHA-256 digest: ' + 'a'*64}]
        with patch('morx_gms2_mcp.android_artifacts.run_command', side_effect=outputs) as run:
            result = artifacts.inspect(path, self.chain)
        self.assertEqual(result['package_name'], 'org.example.probe')
        self.assertEqual(result['versionCode'], 2003004)
        self.assertEqual(result['abis'], ['arm64-v8a'])
        self.assertTrue(result['internet_permission'])
        self.assertEqual(result['signature']['certificate_sha256'], ['a'*64])
        self.assertEqual(result['sha256'], artifacts.checksum(path))
        self.assertIn('apksigner.jar', str(run.call_args_list[-1]))
        with self.assertRaises(ValueError):
            artifacts.inspect(path, self.chain, '0'*64)

    def test_signature_tool_failure_never_returns_a_successful_package(self):
        path = self.package('apk')
        for result in ({'exit_code': 1, 'output': 'invalid signature'}, {'exit_code': 0, 'output': ''}):
            with patch('morx_gms2_mcp.android_artifacts.run_command', side_effect=[{'exit_code': 0, 'output': BADGING}, result]):
                with self.assertRaises(ValueError):
                    artifacts.inspect(path, self.chain)

    def test_legacy_metadata_is_readable_but_not_runtime_compatible(self):
        for protocol in (1,99):
            path=self.package('apk',{'assets/'+android_bridge.MARKER_FILE:json.dumps(bridge_marker(protocol))})
            outputs=[{'exit_code':0,'output':BADGING},
                     {'exit_code':0,'output':'certificate SHA-256 digest: '+'a'*64}]
            with patch('morx_gms2_mcp.android_artifacts.run_command',side_effect=outputs) as run:
                result=artifacts.inspect(path,self.chain,require_current_bridge=False)
            self.assertEqual(result['development_bridge']['protocol'],protocol)
            self.assertEqual(result['development_bridge_compatibility'],
                             {'protocol':protocol,'supported_protocol':2,'runtime_supported':False})
            self.assertTrue(result['signature']['verified']);self.assertEqual(run.call_count,2)
            with patch('morx_gms2_mcp.android_artifacts.run_command') as run:
                with self.assertRaisesRegex(ValueError,'不支持.*协议'):artifacts.inspect(path,self.chain)
                run.assert_not_called()
            with self.assertRaisesRegex(ValueError,'哈希已改变'):
                artifacts.inspect(path,self.chain,'0'*64,require_current_bridge=False)

    def test_relaxed_protocol_does_not_relax_marker_structure_or_signature(self):
        markers=[bridge_marker(value) for value in (True,0,'1',2.0,None)]
        markers += [{**bridge_marker(1),'network_port':1234}, {**bridge_marker(1),'sandbox':'../outside'},
                    {**bridge_marker(1),'bridge':'invalid'}]
        for marker in markers:
            path=self.package('apk',{'assets/'+android_bridge.MARKER_FILE:json.dumps(marker)})
            with self.subTest(marker=marker),self.assertRaises(ValueError):
                artifacts.inspect(path,self.chain,require_current_bridge=False)
        path=self.package('apk',{'assets/'+android_bridge.MARKER_FILE:json.dumps(bridge_marker(1))})
        with patch('morx_gms2_mcp.android_artifacts.run_command',side_effect=[{'exit_code':0,'output':BADGING},
                {'exit_code':1,'output':'invalid signature'}]):
            with self.assertRaisesRegex(ValueError,'官方产物工具验证失败'):
                artifacts.inspect(path,self.chain,require_current_bridge=False)
        path=self.package('apk',{'assets/'+android_bridge.MARKER_FILE:json.dumps(bridge_marker(1)),
                                 'assets/private.jks':'must not ship'})
        with self.assertRaisesRegex(ValueError,'禁止交付'):
            artifacts.inspect(path,self.chain,require_current_bridge=False)

    def test_aab_uses_bundletool_and_jar_signature_and_is_not_directly_installable(self):
        path = self.package('aab')
        outputs = [{'exit_code': 0, 'output': ''}, {'exit_code': 0, 'output': MANIFEST},
                   {'exit_code': 0, 'output': 'jar verified.\nWarning: self-signed certificate'},
                   {'exit_code': 0, 'output': 'SHA256: ' + ':'.join(['AB']*32)}]
        with patch('morx_gms2_mcp.android_artifacts.run_command', side_effect=outputs) as run:
            result = artifacts.inspect(path, self.chain)
        self.assertFalse(result['installable_directly'])
        self.assertEqual(result['signature']['certificate_sha256'], ['ab'*32])
        self.assertEqual(result['metadata_source'], 'bundletool')
        self.assertNotIn('apksigner', str(run.call_args_list))

    def test_zip_private_material_traversal_and_missing_structure_are_rejected(self):
        for name in ('../outside', '/absolute', 'assets/private.jks', 'assets/local_settings.json'):
            path = self.package('apk', {name: 'must not ship'})
            with self.subTest(name=name), self.assertRaises(ValueError):
                artifacts.inspect(path, self.chain)
        path = self.root / 'bad.apk'; path.write_bytes(b'not-a-zip')
        with self.assertRaises(ValueError):
            artifacts.inspect(path, self.chain)
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('other.txt', 'no manifest')
        with self.assertRaises(ValueError):
            artifacts.inspect(path, self.chain)

    def test_mutation_during_validation_and_mismatched_abi_are_rejected(self):
        path = self.package('apk')
        def validate(argv, **kwargs):
            if 'apksigner.jar' in str(argv):
                path.write_bytes(b'changed')
                return {'exit_code': 0, 'output': 'certificate SHA-256 digest: ' + 'a'*64}
            return {'exit_code': 0, 'output': BADGING}
        with patch('morx_gms2_mcp.android_artifacts.run_command', side_effect=validate):
            with self.assertRaisesRegex(ValueError, '修改'):
                artifacts.inspect(path, self.chain)
        path = self.package('apk')
        with patch('morx_gms2_mcp.android_artifacts.run_command', side_effect=[
                {'exit_code': 0, 'output': BADGING.replace('arm64-v8a', 'x86_64')},
                {'exit_code': 0, 'output': 'certificate SHA-256 digest: ' + 'a'*64}]):
            with self.assertRaisesRegex(ValueError, 'ABI'):
                artifacts.inspect(path, self.chain)
