"""运行桥接边界、隔离副本和协议测试；不启动图形程序。"""
import asyncio
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import patch

from PIL import Image
from morx_gms2_mcp import schemas
from morx_gms2_mcp.cli import client_config
from morx_gms2_mcp.runtime import Runtime, RuntimeOptions, atomic_json, bounded_json, key_codes
from morx_gms2_mcp.runtime_bridge import LAYER, OBJECT, sources
from morx_gms2_mcp.server import build_server
from morx_gms2_mcp.storage import encode
from tests.test_core import ProjectFixture
from tests.test_display import windows_fixture


class FakeProcess:
    def __init__(self, text=b'build\n'):
        self.stdout = io.BufferedReader(io.BytesIO(text))
        self.code = None
        self.pid = 999999

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        self.code = 0
        return self.code


class RuntimeTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        self.external = tempfile.TemporaryDirectory()
        self.addCleanup(self.external.cleanup)
        env = Path(self.external.name)
        runtime_root = env / 'runtime'
        igor = runtime_root / 'bin/igor/windows/x64/Igor.exe'
        igor.parent.mkdir(parents=True)
        igor.write_bytes(b'not executable')
        user = env / 'user'
        user.mkdir()
        (user / 'licence.plist').write_text('must never be read or copied')
        sandbox = env / 'sandbox'
        sandbox.mkdir()
        self.options = RuntimeOptions(True, str(runtime_root), str(user), str(sandbox))
        (self.root / 'rooms/Room1/Room1.yy').write_bytes(encode(schemas.room('Room1', 800, 450)))
        for platform, data in [('windows', windows_fixture()), ('main', {'option_gameguid': 'test'})]:
            path = self.root / f'options/{platform}/options_{platform}.yy'
            path.parent.mkdir(parents=True)
            path.write_bytes(encode(data))
        self.runtime = Runtime(self.project, self.options)
        self.addCleanup(self.runtime.close)

    def launch(self):
        self.project.install_runtime_bridge(dry_run=False)
        self.process = FakeProcess()
        with patch('morx_gms2_mcp.runtime.subprocess.Popen', return_value=self.process) as launch:
            result = self.runtime.run(dry_run=False)
        self.assertFalse(launch.call_args.kwargs['shell'])
        self.assertIn('Windows', launch.call_args.args[0])
        self.assertFalse(any(str(arg).startswith('/of=') for arg in launch.call_args.args[0]))
        self.session_id = result['session_id']
        return result

    def ready(self):
        atomic_json(self.runtime._file('ready.json'), {'session': self.session_id,
            'bridge': self.runtime.session['bridge'], 'frame': 1})

    def respond(self, data=None, command=None, wrong_first=False):
        def worker():
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    request = bounded_json(self.runtime._file('request.json'))
                except (OSError, ValueError):
                    time.sleep(.005)
                    continue
                if command is not None:
                    self.assertEqual(request['command'], command)
                if wrong_first:
                    atomic_json(self.runtime._file('response.json'), {'session': 'old',
                        'request': request['request'], 'ok': True, 'data': {'bad': True}})
                    time.sleep(.04)
                if request['command'] == 'capture':
                    Image.new('RGB', (1920, 1080), (10, 20, 30)).save(
                        self.runtime._file('capture_' + request['request'] + '.png'))
                atomic_json(self.runtime._file('response.json'), {'session': self.session_id,
                    'request': request['request'], 'ok': True, 'data': data or {'value': 42}, 'frame': 7})
                return
            raise AssertionError('No bridge request')
        thread = threading.Thread(target=worker)
        thread.start()
        self.addCleanup(thread.join, 3)
        return thread

    def test_bridge_preview_install_and_exact_rollback(self):
        before = self.project.yyp.read_bytes()
        original_room = (self.root / 'rooms/Room1/Room1.yy').read_bytes()
        preview = self.project.install_runtime_bridge()
        self.assertTrue(preview['dry_run'])
        self.assertFalse((self.root / 'objects').exists())
        result = self.project.install_runtime_bridge(dry_run=False)
        self.assertTrue(self.project.validate()['ok'])
        self.assertTrue(self.project.read('objects/' + OBJECT + '/' + OBJECT + '.yy')['persistent'])
        self.assertEqual(self.project.install_runtime_bridge(dry_run=False)['changed'], [])
        self.project.restore(result['transaction'], dry_run=False)
        self.assertEqual(self.project.yyp.read_bytes(), before)
        self.assertEqual((self.root / 'rooms/Room1/Room1.yy').read_bytes(), original_room)
        self.assertFalse((self.root / 'objects').exists())

    def test_modified_bridge_or_inherited_room_rejected(self):
        path = self.root / 'rooms/Room1/Room1.yy'
        room = self.project.read('rooms/Room1/Room1.yy')
        room['parentRoom'] = {'name': 'Parent', 'path': 'rooms/Parent/Parent.yy'}
        path.write_bytes(encode(room))
        with self.assertRaises(ValueError):
            self.project.install_runtime_bridge(dry_run=False)
        room['parentRoom'] = None
        path.write_bytes(encode(room))
        self.project.install_runtime_bridge(dry_run=False)
        create = self.root / f'objects/{OBJECT}/Create_0.gml'
        create.write_text('// external edit')
        with self.assertRaises(ValueError):
            self.project.install_runtime_bridge(dry_run=False)

    def test_disabled_runtime_and_no_implicit_install_or_launch(self):
        disabled = Runtime(self.project)
        with patch('morx_gms2_mcp.runtime.subprocess.Popen') as launch:
            with self.assertRaises(ValueError):
                disabled.run()
            with self.assertRaises(ValueError):
                self.runtime.run()
            launch.assert_not_called()
        self.assertFalse((self.root / 'objects').exists())

    def test_run_preview_makes_no_copy_and_needs_valid_configuration(self):
        self.project.install_runtime_bridge(dry_run=False)
        with patch('morx_gms2_mcp.runtime.subprocess.Popen') as launch:
            self.assertTrue(self.runtime.run()['dry_run'])
            launch.assert_not_called()
        self.assertFalse((self.root / '.codex/morx-runtime').exists())
        self.assertIsNone(self.runtime.session)

    def test_isolated_copy_save_directory_and_no_license_copy(self):
        self.project.install_runtime_bridge(dry_run=False)
        before = self.project.yyp.read_bytes()
        original_options = (self.root / 'options/windows/options_windows.yy').read_bytes()
        result = self.launch()
        copy = Path(result['snapshot_project'])
        self.assertNotEqual(copy, self.project.yyp)
        self.assertEqual(self.project.yyp.read_bytes(), before)
        self.assertEqual((self.root / 'options/windows/options_windows.yy').read_bytes(), original_options)
        self.assertFalse((copy.parent / '.codex').exists())
        self.assertFalse(list(copy.parent.rglob('licence.plist')))
        self.assertEqual(json.loads(copy.read_text())['name'], 'MORX_Run_' + result['session_id'])
        self.assertEqual(Path(result['save_directory']).name, copy.stem)
        with self.assertRaises(ValueError):
            self.runtime.run(dry_run=False)

    def test_runtime_ready_is_session_bound_and_process_aware(self):
        self.launch()
        self.assertFalse(self.runtime.status()['bridge_ready'])
        atomic_json(self.runtime._file('ready.json'), {'session': 'old', 'bridge': self.runtime.session['bridge']})
        self.assertFalse(self.runtime.status()['bridge_ready'])
        self.ready()
        self.assertTrue(self.runtime.status()['bridge_ready'])
        self.process.code = 1
        self.assertEqual(self.runtime.status()['state'], 'exited')

    def test_output_is_bounded_cursor_based_and_redacted(self):
        self.launch()
        self.runtime.redactions = ['PRIVATE_LOCAL_PATH']
        self.runtime._log('token=do-not-return')
        self.runtime._log('PRIVATE_LOCAL_PATH/file.gml: error')
        result = self.runtime.output(self.session_id)
        text = json.dumps(result)
        self.assertNotIn('do-not-return', text)
        self.assertNotIn('PRIVATE_LOCAL_PATH', text)
        cursor = result['next_cursor']
        self.assertEqual(self.runtime.output(self.session_id, after=cursor)['lines'], [])
        for i in range(1100):
            self.runtime._log(str(i))
        self.assertTrue(self.runtime.output(self.session_id)['truncated'])
        self.assertEqual(len(self.runtime.output(self.session_id, limit=200)['lines']), 200)

    def test_wrong_session_responses_are_not_accepted(self):
        self.launch()
        self.ready()
        self.respond(command='state', wrong_first=True)
        result = self.runtime.inspect(self.session_id)
        self.assertEqual(result['data'], {'value': 42})
        self.assertEqual(result['frame'], 7)
        with self.assertRaises(ValueError):
            self.runtime.inspect('another-session')

    def test_timeout_preserves_pending_until_late_response_confirmed(self):
        self.launch()
        self.ready()
        with self.assertRaisesRegex(ValueError, '超时'):
            self.runtime.request(self.session_id, 'wait', {'frames': 1}, timeout=.03)
        pending = self.runtime.pending
        with self.assertRaisesRegex(ValueError, '上个请求'):
            self.runtime.request(self.session_id, 'wait')
        atomic_json(self.runtime._file('response.json'), {'session': self.session_id, 'request': pending,
                    'ok': True, 'data': {'frames': 1}})
        status = self.runtime.status()
        self.assertIn('completed_request', status)
        self.assertIsNone(self.runtime.pending)

    def test_capture_returns_real_png_preview_and_rejects_response_paths(self):
        self.launch()
        self.ready()
        self.respond(data={'path': '../../unrelated.png'}, command='capture')
        result = self.runtime.request(self.session_id, 'capture')
        with Image.open(io.BytesIO(result['image'])) as picture:
            self.assertEqual(picture.size, (1280, 720))
            self.assertEqual(picture.getpixel((1, 1)), (10, 20, 30))

    def test_input_preview_validation_and_wait_arguments(self):
        self.launch()
        self.ready()
        self.assertTrue(self.runtime.input(self.session_id, ['right'], frames=30)['dry_run'])
        self.assertFalse(self.runtime._file('request.json').exists())
        self.assertEqual(key_codes(['a', 'space', 'right']), [65, 32, 39])
        for keys in ([], ['win'], ['ctrl', 'a'], ['a', 'A'], ['left'] * 9):
            with self.assertRaises(ValueError):
                key_codes(keys)
        with self.assertRaises(ValueError):
            self.runtime.wait(self.session_id, 0)
        with self.assertRaises(ValueError):
            self.runtime.inspect(self.session_id, variables=['hp; game_end()'])
        self.respond(command='input')
        self.runtime.input(self.session_id, ['right'], frames=3, dry_run=False)

    def test_stop_preview_and_owned_process_only(self):
        self.launch()
        self.assertTrue(self.runtime.stop(self.session_id)['dry_run'])
        self.assertIsNone(self.process.poll())
        with self.assertRaises(ValueError):
            self.runtime.stop('unrelated', dry_run=False)
        self.assertTrue(self.runtime.stop(self.session_id, dry_run=False)['stopped'])
        self.assertEqual(self.process.poll(), 0)
        self.assertFalse(self.runtime._file('session.json').exists())

    def test_code_edit_hash_preview_encoding_readonly_and_rollback(self):
        self.project.create('scripts', 'scr_edit', '// start')
        path = self.root / 'scripts/scr_edit/scr_edit.gml'
        path.write_bytes(b'\xef\xbb\xbf// start\r\n')
        original = path.read_bytes()
        info = self.project.read_code('scr_edit')
        self.project.update_code('scr_edit', '// changed\n', info['sha256'])
        self.assertEqual(path.read_bytes(), original)
        result = self.project.update_code('scr_edit', '// changed\n', info['sha256'], dry_run=False)
        self.assertEqual(path.read_bytes(), b'\xef\xbb\xbf// changed\r\n')
        with self.assertRaises(ValueError):
            self.project.update_code('scr_edit', '// stale', info['sha256'], dry_run=False)
        self.project.restore(result['transaction'], dry_run=False)
        self.assertEqual(path.read_bytes(), original)
        self.project.writable = False
        with self.assertRaises(ValueError):
            self.project.update_code('scr_edit', '// denied', info['sha256'], dry_run=False)

    def test_object_code_and_bridge_code_protection(self):
        self.project.create('objects', 'obj_edit')
        self.project.add_event('obj_edit', 'Step', 'x++;')
        info = self.project.read_code('obj_edit', 'Step')
        self.project.update_code('obj_edit', 'x+=2;', info['sha256'], 'Step', dry_run=False)
        self.assertEqual(self.project.read_code('obj_edit', 'Step')['code'], 'x+=2;')
        with self.assertRaises(ValueError):
            self.project.read_code('obj_edit', 'Create')
        self.project.install_runtime_bridge(dry_run=False)
        info = self.project.read_code(OBJECT, 'Create')
        with self.assertRaises(ValueError):
            self.project.update_code(OBJECT, '', info['sha256'], 'Create', dry_run=False)

    def test_runtime_flags_propagate_without_license_contents(self):
        config = json.loads(client_config(self.root, 'claude', True, self.options))
        args = config['mcpServers']['morx-gms2-mcp']['args']
        self.assertIn('--allow-run', args)
        self.assertIn('--runtime-root', args)
        self.assertIn('--user-directory', args)
        self.assertNotIn('must never', json.dumps(config))
        codex = client_config(self.root, 'codex', True, self.options)
        self.assertIn('PYTHONIOENCODING = "utf-8"', codex)
        self.assertIn('PYTHONUTF8 = "1"', codex)

    def test_control_files_are_single_line_json(self):
        atomic_json(self.root / 'probe.json', {'session': 'x', 'nested': {'a': [1, 2]}})
        raw = (self.root / 'probe.json').read_text(encoding='utf-8')
        self.assertEqual(raw.count('\n'), 1)
        self.assertEqual(json.loads(raw), {'session': 'x', 'nested': {'a': [1, 2]}})

    def test_runtime_files_accept_nested_temp_paths(self):
        path = self.root / 'nested' / 'deeper' / 'file.json'
        path.parent.mkdir(parents=True)
        atomic_json(path, {'ok': True})
        self.assertEqual(bounded_json(path), {'ok': True})

    def test_bridge_repair_after_line_ending_change_and_start_room_move(self):
        self.project.install_runtime_bridge(dry_run=False)
        create = self.root / f'objects/{OBJECT}/Create_0.gml'
        create.write_bytes(b'\xef\xbb\xbf' + create.read_bytes().replace(b'\n', b'\r\n'))
        self.assertTrue(self.project.install_runtime_bridge(dry_run=False)['already_installed'])
        self.project.bridge_key()
        self.project.create_room('Room2', 320, 180)
        self.project.set_start_room('Room2')
        repaired = self.project.install_runtime_bridge(dry_run=False)
        self.assertTrue(repaired['repaired'])
        room2 = self.project.read('rooms/Room2/Room2.yy')
        self.assertIn(LAYER, [layer['name'] for layer in room2['layers']])
        room1 = self.project.read('rooms/Room1/Room1.yy')
        self.assertNotIn(LAYER, [layer['name'] for layer in room1['layers']])
        self.project.bridge_key()

    def test_unmanaged_bridge_name_fails_cleanly(self):
        self.project.create('objects', OBJECT)
        with self.assertRaisesRegex(ValueError, '普通物体'):
            self.project.install_runtime_bridge(dry_run=False)

    def test_event_number_must_be_integer(self):
        self.project.create('objects', 'obj_events')
        for number in (True, 1.0):
            with self.assertRaises(ValueError):
                self.project.add_event('obj_events', 'Alarm', '//', number)

    def test_response_errors_clear_pending(self):
        self.launch()
        self.runtime.pending = 'abc'
        atomic_json(self.runtime._file('response.json'), {'session': self.session_id,
            'request': 'abc', 'ok': 'yes', 'data': {}})
        status = self.runtime.status()
        self.assertIn('response_error', status)
        self.assertIsNone(self.runtime.pending)
        self.runtime.pending = 'def'
        atomic_json(self.runtime._file('response.json'), {'session': self.session_id,
            'request': 'def', 'ok': True, 'data': {'blob': 'x' * (5 * 1024 * 1024)}})
        status = self.runtime.status()
        self.assertIn('response_error', status)
        self.assertIsNone(self.runtime.pending)

    def test_inspect_reports_bridge_error_as_failure(self):
        self.launch()
        self.ready()
        self.respond(data={'error': 'Object not found in running build'}, command='state')
        with self.assertRaisesRegex(ValueError, 'Object not found'):
            self.runtime.inspect(self.session_id, object_name='obj_missing')

    def test_run_copy_is_outside_project_and_stop_cleans_up(self):
        result = self.launch()
        snapshot = Path(result['snapshot_project'])
        self.assertTrue(snapshot.is_relative_to(Path(self.options.sandbox_root)))
        self.assertFalse(snapshot.is_relative_to(self.root))
        build_directory = Path(self.runtime.session['directory'])
        save_directory = Path(self.runtime.session['save_directory'])
        self.assertTrue(build_directory.is_dir())
        self.assertTrue(save_directory.is_dir())
        self.runtime.stop(self.session_id, dry_run=False)
        self.assertFalse(build_directory.exists())
        self.assertFalse(save_directory.exists())

    def test_new_run_requires_stop_after_process_exit(self):
        self.launch()
        self.process.code = 1
        with self.assertRaisesRegex(ValueError, '先 stop_game'):
            self.runtime.run(dry_run=False)
        self.runtime.stop(self.session_id, dry_run=False)
        self.process = FakeProcess()
        with patch('morx_gms2_mcp.runtime.subprocess.Popen', return_value=self.process):
            result = self.runtime.run(dry_run=False)
        self.assertNotEqual(result['session_id'], self.session_id)

    def test_redaction_is_case_insensitive_and_covers_sandbox(self):
        self.runtime.redactions = [str(self.options.sandbox_root), 'C:\\Data\\Example']
        self.assertEqual(self.runtime._redact('c:\\data\\example\\secret'), '[local-path]\\secret')
        self.runtime._log('PATH=' + str(self.options.sandbox_root).upper())
        self.assertIn('[local-path]', self.runtime.lines[-1]['text'])

    def test_mcp_image_response_and_tool_annotations(self):
        self.launch()
        self.ready()
        with patch('morx_gms2_mcp.server.Runtime', return_value=self.runtime):
            server = build_server(self.root, runtime_options=self.options)
        async def verify():
            tools = await server.list_tools()
            self.assertEqual(len(tools), 35)
            for tool in tools:
                if not tool.annotations.readOnlyHint:
                    self.assertTrue(tool.inputSchema['properties']['dry_run']['default'])
            self.respond(command='capture')
            result = await server.call_tool('capture_game', {'session_id': self.session_id})
            blocks = result[0] if isinstance(result, tuple) else result
            self.assertEqual([item.type for item in blocks], ['text', 'image'])
            self.assertEqual(blocks[1].mimeType, 'image/png')
        asyncio.run(verify())
