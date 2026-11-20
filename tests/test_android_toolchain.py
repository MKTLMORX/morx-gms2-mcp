"""工具链发现不依赖 PATH；错误配置不回退掩盖，修改仅在项目私有配置。"""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from PIL import Image

from morx_gms2_mcp import android, android_toolchain as toolchain
from morx_gms2_mcp.android_devices import AndroidManager
from morx_gms2_mcp.runtime import RuntimeOptions
from morx_gms2_mcp.storage import encode
from morx_gms2_mcp.server import build_server
from tests.test_android import android_fixture
from tests.test_core import ProjectFixture
BUILD_PATH_ISSUE = toolchain.build_path_issue


class ToolchainTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        host_temp = tempfile.TemporaryDirectory()
        self.addCleanup(host_temp.cleanup)
        self.host = Path(host_temp.name)
        # 这些夹具使用假Igor并落在宿主临时目录；AGP路径策略在独立用例验证，不依赖CI用户名。
        policy = patch.object(toolchain, 'build_path_issue', return_value=None)
        policy.start(); self.addCleanup(policy.stop)
        self.sdk = self.host / 'sdk'
        self.jdk = self.host / 'jdk'
        self.ndk = self.host / 'ndk'
        self.runtime_root = self.host / 'runtime-2026.0.0.23'
        self.user = self.host / 'user'
        self.user.mkdir()
        (self.user / 'licence.plist').write_bytes(b'not read')
        for path in ('bin/igor/windows/x64/Igor.exe', 'android/runner/ProjectFiles/src/main/jniLibs/arm64-v8a/libyoyo.so'):
            dest = self.runtime_root / path; dest.parent.mkdir(parents=True, exist_ok=True); dest.touch()
        for path in ('platforms/android-35/android.jar', 'platform-tools/adb.exe', 'build-tools/35.0.0/aapt2.exe',
                     'build-tools/35.0.0/apksigner.bat', 'build-tools/35.0.0/zipalign.exe'):
            dest = self.sdk / path; dest.parent.mkdir(parents=True, exist_ok=True); dest.touch()
        (self.jdk / 'bin').mkdir(parents=True)
        for name in ('java.exe', 'keytool.exe'):
            (self.jdk / 'bin' / name).touch()
        (self.jdk / 'release').write_text('JAVA_VERSION="17.0.11"\n', encoding='utf-8')
        clang = self.ndk / 'toolchains/llvm/prebuilt/windows-x86_64/bin/clang++.exe'
        clang.parent.mkdir(parents=True); clang.touch()
        (self.ndk / 'ndk-build.cmd').touch()
        (self.ndk / 'source.properties').write_text('Pkg.Revision=27.0.12077973\n', encoding='utf-8')
        self.options = RuntimeOptions(True, str(self.runtime_root), str(self.user))
        data = android_fixture()
        data.update({android.PREFIX + key: value for key, value in
                     {'compile_sdk': '35', 'target_sdk': '35', 'minimum_sdk': '23', 'build_tools': '35.0.0'}.items()})
        (self.root / android.OPTIONS).parent.mkdir(parents=True)
        (self.root / android.OPTIONS).write_bytes(encode(data))
        (self.user / 'local_settings.json').write_text(json.dumps({
            toolchain.NATIVE_PATHS['sdk_root']: str(self.sdk), toolchain.NATIVE_PATHS['jdk_root']: str(self.jdk),
            toolchain.NATIVE_PATHS['ndk_root']: str(self.ndk),
            'machine.Platform Settings.Android.Keystore.keystore_password': 'private-value-not-returned'}), encoding='utf-8')

    def test_device_and_artifact_tools_do_not_depend_on_project_write_lock(self):
        tools = self.sdk / 'build-tools/35.0.0'
        (tools / 'lib').mkdir()
        (tools / 'lib/apksigner.jar').touch()
        (tools.parent / '36.0.0').mkdir()  # A partial installation must not win.
        manager = AndroidManager(self.project, self.options)
        with patch.object(self.project, 'lock') as lock, patch.object(android, 'inspect') as inspect, \
                patch('morx_gms2_mcp.android_devices.run_command', return_value={'exit_code': 0, 'output': ''}) as command:
            lock.__enter__.side_effect = ValueError('模拟其他进程占用工程')
            chain = manager._chain()
            self.assertEqual(chain['components']['installed_build_tools'], ['35.0.0'])
            manager._adb(['devices', '-l'])
            self.assertEqual(command.call_args.args[0], [str(self.sdk / 'platform-tools/adb.exe'), 'devices', '-l'])
            inspect.assert_not_called()
            lock.__enter__.assert_not_called()

    def test_native_runtime_requires_real_ndk_even_for_vm(self):
        result = toolchain.inspect(self.project, self.options)
        self.assertTrue(result['ready'], result)
        self.assertTrue(result['ndk_required'])
        self.assertEqual(result['paths']['sdk_root']['source'], 'ide_preference')
        self.assertEqual(result['components']['java_version'], '17.0.11')
        self.assertNotIn('private-value-not-returned', str(result))
        self.assertNotIn('not read', str(result))
        preferences = self.user / 'local_settings.json'
        data = json.loads(preferences.read_text(encoding='utf-8')); data.pop(toolchain.NATIVE_PATHS['ndk_root'])
        preferences.write_text(json.dumps(data), encoding='utf-8')
        self.assertFalse(toolchain.inspect(self.project, self.options)['ready'])
        yyc = toolchain.inspect(self.project, self.options, 'YYC')
        self.assertFalse(yyc['ready'])
        self.assertTrue(any(row.get('component') == 'ndk_root' for row in yyc['issues']))

    def test_windows_non_ascii_build_root_is_preflight_failure_without_moving_source(self):
        ascii_root = Path(self.host.anchor) / 'GameBuildsAndroid'
        unicode_root = Path(self.host.anchor) / 'GameBuilds_制作'
        self.assertIsNone(BUILD_PATH_ISSUE(str(ascii_root), windows=True))
        self.assertIsNone(BUILD_PATH_ISSUE(str(unicode_root), windows=False))
        original_yyp=self.project.yyp.read_bytes()
        toolchain.configure(self.project, {'build_root':str(unicode_root)}, dry_run=False)
        with patch.object(toolchain,'build_path_issue',side_effect=lambda path:BUILD_PATH_ISSUE(path,windows=True)):
            result=toolchain.inspect(self.project,self.options)
        self.assertFalse(result['ready'])
        issue=next(row for row in result['issues'] if row['code']=='non_ascii_build_root')
        self.assertFalse(issue['source_project_move_required'])
        self.assertFalse(issue['overridePathCheck_injected'])
        self.assertEqual(self.project.yyp.read_bytes(),original_yyp)
        self.assertFalse(unicode_root.exists())

    def test_explicit_bad_path_is_not_hidden_by_valid_ide_or_environment(self):
        missing = self.host / 'missing-sdk'
        self.project.path(toolchain.CONFIG).write_bytes(encode({'version': 1, 'sdk_root': str(missing)}))
        with patch.dict(os.environ, {'ANDROID_HOME': str(self.sdk)}):
            result = toolchain.inspect(self.project, self.options)
        self.assertEqual(result['paths']['sdk_root']['state'], 'path_not_found')
        self.assertFalse(result['ready'])

    def test_project_configuration_preview_conflict_restore_and_no_global_edit(self):
        prefs = (self.user / 'local_settings.json').read_bytes()
        paths = {'sdk_root': str(self.sdk), 'jdk_root': str(self.jdk), 'build_root': str(self.host / 'jobs')}
        preview = toolchain.configure(self.project, paths)
        self.assertTrue(preview['dry_run'])
        self.assertFalse(self.project.path(toolchain.CONFIG).exists())
        result = toolchain.configure(self.project, paths, dry_run=False)
        with self.assertRaises(ValueError):
            toolchain.configure(self.project, {'sdk_root': ''}, dry_run=False)
        self.project.restore(result['transaction'], False)
        self.assertFalse(self.project.path(toolchain.CONFIG).exists())
        self.assertEqual((self.user / 'local_settings.json').read_bytes(), prefs)
        self.assertFalse((self.host / 'jobs').exists())

    def test_android_studio_sdk_is_discovered_when_not_on_path(self):
        (self.user / 'local_settings.json').unlink()
        roaming = self.host / 'roaming'
        table = roaming / 'Google/AndroidStudio2024.1/options/jdk.table.xml'
        table.parent.mkdir(parents=True)
        table.write_text('<application><component><jdk><type value="Android SDK"/><homePath value="' +
                         self.sdk.as_posix() + '"/></jdk></component></application>', encoding='utf-8')
        with patch.dict(os.environ, {'APPDATA': str(roaming), 'ANDROID_HOME': '', 'ANDROID_SDK_ROOT': ''}):
            paths, _ = toolchain.discover(self.project, self.options)
        self.assertEqual(Path(paths['sdk_root']['path']), self.sdk)
        self.assertEqual(paths['sdk_root']['source'], 'android_studio_or_standard_location')

    def test_missing_platform_and_inherited_values_report_specific_causes(self):
        (self.sdk / 'platforms/android-35/android.jar').unlink()
        result = toolchain.inspect(self.project, self.options)
        self.assertTrue(any(row['code'] == 'requires_download' for row in result['issues']))
        data = self.project.read(android.OPTIONS); data[android.PREFIX + 'compile_sdk'] = ''
        (self.root / android.OPTIONS).write_bytes(encode(data))
        result = toolchain.inspect(self.project, self.options)
        self.assertTrue(any(row['code'] == 'inherited_dependency_unresolved' for row in result['issues']))
        self.assertFalse(toolchain.inspect(self.project, self.options, artifact_type='aab')['ready'])

    def test_incompatible_jdk_and_gradle_are_distinct_from_missing_paths(self):
        (self.jdk / 'release').write_text('JAVA_VERSION="11.0.2"\n', encoding='utf-8')
        data = self.project.read(android.OPTIONS); data[android.PREFIX + 'gradle_version'] = '8.9'
        (self.root / android.OPTIONS).write_bytes(encode(data))
        result = toolchain.inspect(self.project, self.options)
        incompatible = {row['component'] for row in result['issues'] if row['code'] == 'version_incompatible'}
        self.assertEqual(incompatible, {'jdk', 'gradle'})

    def test_mcp_configuration_tools_preserve_readonly_and_transaction_contract(self):
        Image.new('RGBA', (48, 48), (40, 120, 180, 255)).save(self.root / 'source.png')
        async def verify():
            server = build_server(self.root, writable=True, runtime_options=self.options)
            readonly = build_server(self.root, writable=False, runtime_options=self.options)
            async def call(target, name, arguments, error=False):
                result = await target.call_tool(name, arguments)
                if hasattr(result, 'isError'):
                    self.assertEqual(result.isError, error)
                    blocks = result.content
                else:
                    self.assertFalse(error)
                    blocks = result[0] if isinstance(result, tuple) else result
                return json.loads(blocks[0].text)
            info = await call(server, 'inspect_android_export', {})
            operations = [
                ('configure_android_export', {'changes': {'display_name': '协议测试'}, 'expected_sha256': info['sha256']}),
                ('import_android_icons', {'source_path': 'source.png', 'expected_sha256': info['sha256']}),
                ('configure_android_toolchain', {'paths': {'sdk_root': str(self.sdk)}})]
            for name, arguments in operations:
                self.assertTrue((await call(readonly, name, arguments))['dry_run'])
                await call(readonly, name, {**arguments, 'dry_run': False}, True)
                result = await call(server, name, {**arguments, 'dry_run': False})
                await call(server, 'restore_backup', {'transaction_id': result['transaction'], 'dry_run': False})
            result = await call(server, 'inspect_android_toolchain', {})
            self.assertTrue(result['ready'])
            self.assertNotIn('private-value-not-returned', str(result))
        asyncio.run(verify())
