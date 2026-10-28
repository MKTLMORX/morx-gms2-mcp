"""运行桥接边界、隔离副本和协议测试；不启动图形程序。"""
import asyncio
import io
import json
import os
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
            self.assertTrue(self.runtime.session['preparation_done'].wait(5))
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
        self.runtime.input(self.session_id, ['right'], frames=3, dry_run=False,
                           mode='state')
        with self.assertRaises(ValueError):
            self.runtime.input(self.session_id, ['right'], mode='physical')

    def test_input_message_mode_posts_and_releases_keys(self):
        self.launch()
        self.ready()
        window = type('FakeWindow', (), {'handle': 1, 'pid': 2,
                                         'title': 'MORX_Run_x',
                                         'width': 640, 'height': 360})()
        with patch('morx_gms2_mcp.win_mouse.find_game_window', return_value=window), \
             patch('morx_gms2_mcp.win_keyboard.key_down') as down, \
             patch('morx_gms2_mcp.win_keyboard.key_up') as up, \
             patch.object(self.runtime, 'wait',
                          return_value={'data': {'frames': 3}, 'frame': 7}) as wait:
            result = self.runtime.input(self.session_id, ['e', 'right'], frames=3,
                                        dry_run=False)
        down.assert_called_once_with(window, [69, 39])
        up.assert_called_once_with(window, [69, 39])
        wait.assert_called_once_with(self.session_id, 3)
        self.assertEqual(result['mode'], 'message')
        self.assertEqual(result['frame'], 7)

    def test_input_message_mode_releases_keys_after_wait_failure(self):
        self.launch()
        self.ready()
        window = type('FakeWindow', (), {'handle': 1, 'pid': 2,
                                         'title': 'MORX_Run_x',
                                         'width': 640, 'height': 360})()
        with patch('morx_gms2_mcp.win_mouse.find_game_window', return_value=window), \
             patch('morx_gms2_mcp.win_keyboard.key_down') as down, \
             patch('morx_gms2_mcp.win_keyboard.key_up') as up, \
             patch.object(self.runtime, 'wait', side_effect=ValueError('timeout')):
            with self.assertRaisesRegex(ValueError, 'timeout'):
                self.runtime.input(self.session_id, ['e'], frames=3, dry_run=False)
        down.assert_called_once()
        up.assert_called_once()

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
        server = config['mcpServers']['morx-gms2-mcp']
        args = server['args']
        self.assertIn('--allow-run', args)
        self.assertIn('--runtime-root', args)
        self.assertIn('--user-directory', args)
        self.assertEqual(server['timeout'], 180000)
        self.assertNotIn('must never', json.dumps(config))
        codex = client_config(self.root, 'codex', True, self.options)
        self.assertIn('tool_timeout_sec = 180', codex)
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

    def test_atomic_json_retries_transient_replace_error(self):
        path = self.root / 'retry.json'
        real_replace = os.replace
        attempts = []

        def flaky(source, target):
            attempts.append(target)
            if len(attempts) < 3:
                raise PermissionError(13, 'file is in use', str(target))
            return real_replace(source, target)

        with patch('morx_gms2_mcp.storage.os.replace', side_effect=flaky):
            atomic_json(path, {'ok': True})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(bounded_json(path), {'ok': True})
        self.assertFalse(any(path.parent.glob('retry.json.*.tmp')))

    def test_atomic_json_reports_persistent_replace_error(self):
        path = self.root / 'locked.json'
        with patch('morx_gms2_mcp.storage.os.replace',
                   side_effect=PermissionError(13, 'file is in use')):
            with self.assertRaises(PermissionError):
                atomic_json(path, {'ok': True})
        self.assertFalse(path.exists())
        self.assertFalse(any(path.parent.glob('locked.json.*.tmp')))

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

    def sequence_responder(self, commands):
        seen = []
        def worker():
            deadline = time.monotonic() + 10
            for command in commands:
                while time.monotonic() < deadline:
                    try:
                        request = bounded_json(self.runtime._file('request.json'))
                    except (OSError, ValueError):
                        time.sleep(.005)
                        continue
                    if request.get('command') != command:
                        time.sleep(.005)
                        continue
                    seen.append(command)
                    if command == 'capture':
                        Image.new('RGB', (64, 64), (10, 20, 30)).save(
                            self.runtime._file('capture_' + request['request'] + '.png'))
                    atomic_json(self.runtime._file('response.json'), {'session': self.session_id,
                        'request': request['request'], 'ok': True, 'data': {'value': 42}, 'frame': 7})
                    break
                else:
                    raise AssertionError('No bridge request: ' + command)
        thread = threading.Thread(target=worker)
        thread.start()
        self.addCleanup(thread.join, 10)
        return seen

    def test_mouse_preview_validation_and_injection(self):
        self.launch()
        self.ready()
        window = type('FakeWindow', (), {'handle': 1, 'pid': 2, 'title': 'MORX_Run_x',
                                         'width': 640, 'height': 360})()
        with patch('morx_gms2_mcp.win_mouse.find_game_window', return_value=window), \
             patch('morx_gms2_mcp.win_mouse.send_mouse_message',
                   return_value={'screen_x': 30, 'screen_y': 40}) as message, \
             patch('morx_gms2_mcp.win_mouse.send_mouse',
                   return_value={'screen_x': 10, 'screen_y': 20}) as physical, \
             patch.object(self.runtime, 'wait', return_value={'data': {}, 'frame': 1}), \
             patch.object(self.runtime, 'request', side_effect=lambda sid, cmd, args:
                          {'data': {'trace_id': args['trace_id']}}), \
             patch.object(self.runtime, 'inspect', return_value={'data': {}, 'frame': 1}):
            preview = self.runtime.mouse(self.session_id, 'click', 100, 200)
            self.assertTrue(preview['dry_run'])
            self.assertEqual(preview['mode'], 'message')
            message.assert_not_called()
            physical.assert_not_called()
            result = self.runtime.mouse(self.session_id, 'click', 100, 200, dry_run=False, settle_frames=0)
            self.assertEqual(message.call_args.args, (window, 'click', 100, 200, 'left', 0, 50))
            self.assertEqual(message.call_count, 1)
            self.assertTrue(callable(message.call_args.kwargs['on_dispatch']))
            physical.assert_not_called()
            self.assertEqual(result['cursor'], {'screen_x': 30, 'screen_y': 40})
            self.assertEqual(result['window'], {'width': 640, 'height': 360})
            result = self.runtime.mouse(self.session_id, 'click', 100, 200,
                                        mode='physical', dry_run=False)
            physical.assert_called_once_with(window, 'click', 100, 200, 'left', 0, 50)
            self.assertEqual(result['cursor'], {'screen_x': 10, 'screen_y': 20})
            for args, kwargs in [(('move', 640, 0), {}), (('move', -1, 0), {}),
                                 (('wheel',), {'delta': 0}), (('click', 1, 1), {'button': 'x'}),
                                 (('click', 1, 1), {'hold_ms': 0}), (('move',), {}),
                                 (('move', 1, 1), {'mode': 'other'})]:
                with self.assertRaises(ValueError):
                    self.runtime.mouse(self.session_id, *args, dry_run=False, **kwargs)
            with self.assertRaises(ValueError):
                self.runtime.mouse('another', 'move', 1, 1, dry_run=False)

    def test_trigger_event_preview_and_dispatch(self):
        self.launch()
        self.ready()
        preview = self.runtime.trigger_event(self.session_id, 'obj_probe', 'KeyPress', key='e')
        self.assertTrue(preview['dry_run'])
        self.assertEqual(preview['event_type'], 9)
        self.assertEqual(preview['event_number'], 69)
        self.assertFalse(self.runtime._file('request.json').exists())
        seen = self.sequence_responder(['event'])
        result = self.runtime.trigger_event(self.session_id, 'obj_probe', 'KeyPress',
                                            key='e', dry_run=False)
        self.assertTrue(result['ok'])
        self.assertEqual(seen, ['event'])
        preview = self.runtime.trigger_event(self.session_id, 'obj_probe', 'Collision',
                                             collision_object='obj_other')
        self.assertEqual(preview['event_type'], 4)
        self.assertEqual(preview['collision_object'], 'obj_other')
        for kwargs in ({'event': 'Draw'}, {'event': 'Mouse', 'number': 99},
                       {'event': 'Keyboard', 'key': 'f1'}, {'event': 'Collision'},
                       {'event': 'Alarm', 'number': 12}):
            with self.assertRaises(ValueError):
                self.runtime.trigger_event(self.session_id, 'obj_probe',
                                           dry_run=False, **kwargs)
        with self.assertRaises(ValueError):
            self.runtime.trigger_event('another', 'obj_probe', 'KeyPress', key='e',
                                       dry_run=False)

    def test_sequence_preview_validation_and_execution(self):
        self.launch()
        self.ready()
        steps = [{'action': 'input', 'keys': ['right'], 'frames': 3, 'mode': 'state'},
                 {'action': 'event', 'object_name': 'obj_probe', 'event': 'KeyPress',
                  'key': 'e'},
                 {'action': 'wait', 'frames': 2},
                 {'action': 'state', 'object_name': '', 'variables': ['value']},
                 {'action': 'capture'}]
        preview = self.runtime.run_sequence(self.session_id, steps)
        self.assertTrue(preview['dry_run'])
        self.assertEqual(preview['total_frames'], 5)
        self.assertEqual(preview['captures'], 1)
        self.assertFalse(self.runtime._file('request.json').exists())
        seen = self.sequence_responder(['input', 'event', 'wait', 'state', 'capture'])
        result = self.runtime.run_sequence(self.session_id, steps, dry_run=False)
        self.assertTrue(result['ok'])
        self.assertEqual(seen, ['input', 'event', 'wait', 'state', 'capture'])
        self.assertEqual(len(result['results']), 5)
        self.assertEqual(len(result['pictures']), 1)
        self.assertEqual(result['results'][-1]['image_index'], 0)
        for bad in ([], [{'action': 'wait', 'frames': 0}], [{'action': 'bogus'}],
                    [{'action': 'capture'}] * 5,
                    [{'action': 'wait', 'frames': 600}] * 4,
                    [{'action': 'input', 'keys': ['win']}],
                    [{'action': 'input', 'keys': ['right'], 'mode': 'physical'}],
                    [{'action': 'event', 'object_name': 'obj_probe', 'event': 'Draw'}]):
            with self.assertRaises(ValueError):
                self.runtime.run_sequence(self.session_id, bad)

    def test_runtime_errors_are_session_bound_and_redacted(self):
        self.launch()
        self.runtime.redactions = ['C:\\Data\\Example']
        atomic_json(self.runtime._file('errors.json'), {'session': 'old',
            'errors': [{'message': 'stale'}]})
        self.assertEqual(self.runtime.errors(self.session_id)['count'], 0)
        atomic_json(self.runtime._file('errors.json'), {'session': self.session_id, 'errors': [
            {'frame': 12, 'line': 7, 'stage': 'unhandled',
             'message': 'boom at C:\\Data\\Example\\a.gml', 'long_message': 'long message',
             'script': 'scr_probe', 'stacktrace': ['C:\\Data\\Example\\a.gml:7', 'scr_probe:1']}]})
        result = self.runtime.errors(self.session_id)
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['errors'][0]['index'], 1)
        self.assertEqual(result['errors'][0]['line'], 7)
        self.assertNotIn('C:\\Data\\Example', json.dumps(result))
        self.assertIn('[local-path]', json.dumps(result))
        self.assertEqual(self.runtime.status()['error_count'], 1)
        self.assertEqual(self.runtime.errors(self.session_id, after=1)['count'], 0)
        with self.assertRaises(ValueError):
            self.runtime.errors('another-session')

    def test_malformed_or_oversized_errors_do_not_break_status(self):
        self.launch()
        atomic_json(self.runtime._file('errors.json'), {'session': self.session_id, 'errors': 'bad'})
        self.assertEqual(self.runtime.status()['error_count'], 0)
        self.runtime._file('errors.json').write_bytes(b'x' * (2 * 1024 * 1024 + 1))
        self.assertEqual(self.runtime.status()['error_count'], 0)

    def test_stop_returns_errors_before_cleanup(self):
        self.launch()
        atomic_json(self.runtime._file('errors.json'), {'session': self.session_id,
            'errors': [{'stage': 'unhandled', 'message': 'boom'}]})
        result = self.runtime.stop(self.session_id, dry_run=False)
        self.assertEqual(result['error_count'], 1)
        self.assertEqual(result['errors'][0]['message'], 'boom')
        self.assertFalse(self.runtime._file('errors.json').exists())

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

    def test_inspect_globals_and_instance_arguments(self):
        with patch.object(self.runtime, 'request', return_value={'data': {}}) as request:
            self.runtime.inspect('session', scope='globals',
                                 variables=['score', 'inventory'])
        self.assertEqual(request.call_args.args[2], {
            'object_name': '', 'variables': ['score', 'inventory'],
            'limit': 1, 'scope': 'globals', 'max_depth': 3})
        for value in (100002, '100002', '@ref instance(100002)', 'instance(100002)'):
            with patch.object(self.runtime, 'request', return_value={'data': {}}) as request:
                self.runtime.inspect('session', object_name='obj_player', instance_id=value)
            self.assertEqual(request.call_args.args[2]['instance_id'], 100002)
            self.assertEqual(request.call_args.args[2]['scope'], 'instances')
        for arguments in ({'scope': 'globals'},
                          {'scope': 'globals', 'variables': ['score'], 'object_name': 'obj_x'},
                          {'scope': 'globals', 'variables': ['score'], 'instance_id': 1},
                          {'scope': 'other'}, {'instance_id': 0}):
            with self.assertRaises(ValueError):
                self.runtime.inspect('session', **arguments)

    def test_sequence_state_depth_reaches_bridge_request(self):
        self.launch()
        self.ready()
        for depth in (1, 8):
            with patch.object(self.runtime, 'request', return_value={'ok': True}) as request:
                self.runtime.run_sequence(self.session_id, [
                    {'action': 'state', 'scope': 'globals', 'variables': ['inventory'],
                     'max_depth': depth}], dry_run=False)
            self.assertEqual(request.call_args.args[1], 'state')
            self.assertEqual(request.call_args.args[2]['max_depth'], depth)
        for depth in (0, 9, True, 1.5):
            with patch.object(self.runtime, 'request') as request:
                with self.assertRaises(ValueError):
                    self.runtime.run_sequence(self.session_id,
                        [{'action': 'state', 'max_depth': depth}], dry_run=False)
                request.assert_not_called()

    def test_stop_reports_partial_cleanup_and_allows_retry(self):
        import shutil
        self.launch()
        directory = self.runtime.session['directory']
        savedir = self.runtime.session['save_directory']
        remove = shutil.rmtree
        def fail_save(path):
            if Path(path) == savedir:
                raise PermissionError('test lock')
            return remove(path)
        with patch('morx_gms2_mcp.runtime.shutil.rmtree', side_effect=fail_save):
            result = self.runtime.stop(self.session_id, False)
        self.assertFalse(result['cleaned'])
        self.assertTrue(result['stopped'])
        self.assertFalse(directory.exists())
        self.assertTrue(savedir.exists())
        self.assertEqual(result['cleanup'][1]['error_type'], 'PermissionError')
        with self.assertRaisesRegex(ValueError, '再次 stop_game'):
            self.runtime.run(compile_only=True)
        result = self.runtime.stop(self.session_id, False)
        self.assertTrue(result['cleaned'])
        self.assertFalse(savedir.exists())
        self.assertTrue(self.runtime.stop(self.session_id, False)['cleaned'])

    def test_cleanup_confirms_absence_and_rejects_unsafe_path(self):
        self.launch()
        with patch('morx_gms2_mcp.runtime.shutil.rmtree', return_value=None):
            result = self.runtime.stop(self.session_id, False)
        self.assertFalse(result['cleaned'])
        self.assertEqual([i['reason'] for i in result['cleanup']], ['directory_remains'] * 2)
        foreign = self.root / 'foreign'
        foreign.mkdir()
        bad = {**self.runtime.session, 'directory': foreign}
        result = self.runtime._cleanup(bad)
        self.assertFalse(result['cleaned'])
        self.assertEqual(result['cleanup'][0]['reason'], 'unsafe_path')
        self.assertTrue(foreign.exists())

    def test_compressed_map_passes_compile_preflight(self):
        png = self.root / 'tile.png'
        Image.new('RGBA', (16, 16), (1, 2, 3, 255)).save(png)
        self.project.import_sprite('spr_tile', [str(png)])
        self.project.create_tileset('ts_tile', 'spr_tile', 8, 8)
        self.project.create_tile_layer('Room1', 'Tiles', 'ts_tile', width=2, height=2)
        room = self.project.read('rooms/Room1/Room1.yy')
        next(layer for layer in room['layers'] if layer['name'] == 'Tiles')['tiles'] = {
            'SerialiseWidth': 2, 'SerialiseHeight': 2, 'TileDataFormat': 1,
            'TileCompressedData': [-4, 1]}
        self.project.path('rooms/Room1/Room1.yy').write_bytes(encode(room))
        with patch('morx_gms2_mcp.runtime.subprocess.Popen') as launch:
            self.assertTrue(self.runtime.run(compile_only=True)['dry_run'])
            launch.assert_not_called()

    def test_sequence_state_supports_globals_and_instance_id(self):
        step = self.runtime._sequence_step({
            'action': 'state', 'scope': 'globals', 'variables': ['score']})
        self.assertEqual(step, {'action': 'state', 'object_name': '',
            'variables': ['score'], 'limit': 1, 'scope': 'globals'})
        step = self.runtime._sequence_step({
            'action': 'state', 'object_name': 'obj_player', 'instance_id': 100002})
        self.assertEqual(step['instance_id'], 100002)
        with self.assertRaises(ValueError):
            self.runtime._sequence_step({
                'action': 'state', 'scope': 'globals', 'variables': ['score'],
                'object_name': 'obj_player'})

    def test_set_variables_preview_validation_and_dispatch(self):
        self.launch()
        self.ready()
        preview = self.runtime.set_variables(self.session_id, {'hp': 5, 'state': {'a': [1, True]}},
                                             object_name='obj_probe', create_missing=True)
        self.assertTrue(preview['dry_run'])
        self.assertEqual(preview['scope'], 'instances')
        self.assertFalse(self.runtime._file('request.json').exists())
        globals_preview = self.runtime.set_variables(self.session_id, {'score': 7},
                                                     scope='globals')
        self.assertEqual(globals_preview['limit'], 1)
        with patch.object(self.runtime, 'request', return_value={'data': {'count': 1}}) as request:
            result = self.runtime.set_variables(self.session_id, {'hp': 5},
                                                object_name='obj_probe', dry_run=False)
        self.assertEqual(result['data']['count'], 1)
        args = request.call_args.args[2]
        self.assertEqual(args['values'], {'hp': 5})
        self.assertEqual(args['object_name'], 'obj_probe')
        self.assertFalse(args['create_missing'])
        for arguments in (
                {'values': {'hp': 1}},
                {'values': {}},
                {'values': {'hp': None}, 'object_name': 'obj_probe'},
                {'values': {'hp': float('nan')}, 'object_name': 'obj_probe'},
                {'values': {'a' * 65: 1}, 'object_name': 'obj_probe'},
                {'values': {'v': [[[[[1]]]]]}, 'object_name': 'obj_probe'},
                {'values': {'v': 'x' * 4097}, 'object_name': 'obj_probe'},
                {'values': {'hp': 1}, 'scope': 'globals', 'object_name': 'obj_probe'},
                {'values': {'hp': 1}, 'object_name': 'sys_morx_runtime'},
                {'values': {'hp': 1}, 'object_name': 'obj_probe', 'instance_id': 0},
                {'values': {'hp': 1}, 'object_name': 'obj_probe', 'create_missing': 'yes'}):
            with self.assertRaises(ValueError):
                self.runtime.set_variables(self.session_id, dry_run=False, **arguments)

    def test_spawn_and_destroy_preview_validation_and_dispatch(self):
        self.launch()
        self.ready()
        preview = self.runtime.spawn(self.session_id, 'obj_probe', 10, 20,
                                     layer_name='Instances')
        self.assertTrue(preview['dry_run'])
        self.assertEqual(preview['layer_name'], 'Instances')
        self.assertFalse(self.runtime._file('request.json').exists())
        with patch.object(self.runtime, 'request', return_value={'data': {'instance': {}}}) as request:
            self.runtime.spawn(self.session_id, 'obj_probe', 1, 2, depth=-5, dry_run=False)
        self.assertEqual(request.call_args.args[1], 'spawn')
        self.assertEqual(request.call_args.args[2]['depth'], -5)
        preview = self.runtime.destroy(self.session_id, 'obj_probe', limit=3)
        self.assertEqual(preview['limit'], 3)
        preview = self.runtime.destroy(self.session_id, instance_id='@ref instance(100002)')
        self.assertEqual(preview['instance_id'], 100002)
        with patch.object(self.runtime, 'request', return_value={'data': {'count': 1}}) as request:
            self.runtime.destroy(self.session_id, 'obj_probe', dry_run=False)
        self.assertEqual(request.call_args.args[1], 'destroy')
        for arguments in (
                {'object_name': 'bad name'},
                {'object_name': 'obj_probe', 'x': float('inf')},
                {'object_name': 'obj_probe', 'layer_name': 1},
                {'object_name': 'obj_probe', 'depth': True},
                {'object_name': 'sys_morx_runtime'}):
            with self.assertRaises(ValueError):
                self.runtime.spawn(self.session_id, dry_run=False, **arguments)
        for arguments in ({}, {'object_name': 'obj_probe', 'limit': 0},
                          {'object_name': 'sys_morx_runtime'},
                          {'instance_id': 'bad'}):
            with self.assertRaises(ValueError):
                self.runtime.destroy(self.session_id, dry_run=False, **arguments)

    def test_sequence_set_spawn_destroy(self):
        self.launch()
        self.ready()
        steps = [
            {'action': 'set', 'values': {'hp': 1}, 'object_name': 'obj_probe'},
            {'action': 'spawn', 'object_name': 'obj_probe', 'x': 1, 'y': 2},
            {'action': 'destroy', 'object_name': 'obj_probe', 'limit': 2},
        ]
        preview = self.runtime.run_sequence(self.session_id, steps)
        self.assertTrue(preview['dry_run'])
        self.assertEqual([step['action'] for step in preview['steps']],
                         ['set', 'spawn', 'destroy'])
        self.assertEqual(preview['steps'][0]['values'], {'hp': 1})
        for bad in ([{'action': 'set', 'values': {'hp': 1}}],
                    [{'action': 'spawn', 'object_name': 'sys_morx_runtime'}],
                    [{'action': 'destroy'}]):
            with self.assertRaises(ValueError):
                self.runtime.run_sequence(self.session_id, bad)
        seen = self.sequence_responder(['set', 'spawn', 'destroy'])
        result = self.runtime.run_sequence(self.session_id, steps, dry_run=False)
        self.assertTrue(result['ok'])
        self.assertEqual(seen, ['set', 'spawn', 'destroy'])
        self.assertEqual([item['action'] for item in result['results']],
                         ['set', 'spawn', 'destroy'])

    def test_bridge_serializes_globals_arrays_and_structs_with_bounds(self):
        bridge = ''.join(sources('a' * 32).values())
        for marker in ('variable_global_exists', 'variable_global_get',
                       'variable_struct_get_names', '_budget.left', '_depth >= depth_limit',
                       'min(array_length_value,32)', 'min(struct_length_value,32)',
                       '{left:64}', 'variable_global_set', 'variable_instance_set',
                       'variable_clone', 'instance_create_layer', 'instance_create_depth',
                       'instance_destroy',
                       'mcp_collect_targets', 'case "set"', 'case "spawn"', 'case "destroy"'):
            self.assertIn(marker, bridge)
        self.assertIn('scope == "globals"', bridge)

    def test_run_copy_is_outside_project_and_stop_cleans_up(self):
        result = self.launch()
        snapshot = Path(result['snapshot_project'])
        self.assertTrue(snapshot.is_relative_to(Path(self.options.sandbox_root).resolve()))
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
            self.assertTrue(self.runtime.session['preparation_done'].wait(5))
        self.assertNotEqual(result['session_id'], self.session_id)

    def test_redaction_is_case_insensitive_and_covers_sandbox(self):
        self.runtime.redactions = [str(self.options.sandbox_root), 'C:\\Data\\Example']
        self.assertEqual(self.runtime._redact('c:\\data\\example\\secret'), '[local-path]\\secret')
        self.runtime._log('PATH=' + str(self.options.sandbox_root).upper())
        self.assertIn('[local-path]', self.runtime.lines[-1]['text'])

    def test_mcp_image_response_and_tool_annotations(self):
        self.launch()
        self.ready()
        from morx_gms2_mcp.sessions import RuntimePool
        pool = RuntimePool(self.runtime.project, self.options)
        pool.sessions[self.session_id] = self.runtime
        with patch('morx_gms2_mcp.server.RuntimePool', return_value=pool):
            server = build_server(self.root, runtime_options=self.options)
        async def verify():
            tools = await server.list_tools()
            self.assertEqual(len(tools), 71)
            for tool in tools:
                if not tool.annotations.readOnlyHint:
                    self.assertTrue(tool.inputSchema['properties']['dry_run']['default'])
            self.respond(command='capture')
            result = await server.call_tool('capture_game', {'session_id': self.session_id})
            blocks = result[0] if isinstance(result, tuple) else result
            self.assertEqual([item.type for item in blocks], ['text', 'image'])
            self.assertEqual(blocks[1].mimeType, 'image/png')
        asyncio.run(verify())
