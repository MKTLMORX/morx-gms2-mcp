"""实战反馈的通用回归场景；不启动 Runner、不操作桌面或真实项目。"""
import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from morx_gms2_mcp import schemas, win_mouse
from morx_gms2_mcp.runtime import atomic_json, key_codes
from morx_gms2_mcp.storage import digest, encode, parse_yy
import tests.test_core as core
import tests.test_runtime as fixtures


class ResourceFeedbackTests(core.ProjectFixture):
    def test_empty_group_preview_remove_conflict_and_restore(self):
        self.project.ensure_group('art/unused')
        info = self.project.validate()
        before = self.project.yyp.read_bytes()
        preview = self.project.remove_empty_group('art/unused', info['project_sha256'])
        self.assertTrue(preview['empty'])
        self.assertTrue(preview['sort_references'])
        self.assertEqual(before, self.project.yyp.read_bytes())
        result = self.project.remove_empty_group('art/unused', info['project_sha256'], False)
        self.assertFalse(any(f['folderPath'] == 'folders/art/unused.yy'
                             for f in self.project.metadata()['Folders']))
        with self.assertRaises(ValueError):
            self.project.remove_empty_group('art', info['project_sha256'], False)
        self.project.restore(result['transaction'], False)
        self.assertEqual(self.project.yyp.read_bytes(), before)

    def test_nonempty_group_and_parent_rejected_without_deleting_resources(self):
        self.project.create('scripts', 'scr_demo', group='code/used')
        for group in ('code', 'code/used'):
            stamp = self.project.validate()['project_sha256']
            self.assertFalse(self.project.remove_empty_group(group, stamp)['empty'])
            with self.assertRaises(ValueError):
                self.project.remove_empty_group(group, stamp, False)
        self.assertTrue(self.project.read_code('scr_demo'))

    def sprite(self):
        path = self.root / 'input.png'
        Image.new('RGBA', (8, 8), (1, 2, 3, 255)).save(path)
        self.project.import_sprite('spr_demo', [str(path), str(path)], origin_x=2, origin_y=3)
        return path

    def test_replace_images_preserves_identity_metadata_timing_and_rollback(self):
        path = self.sprite()
        info = self.project.inspect_sprite('spr_demo')
        directory = self.root / 'sprites/spr_demo'
        before = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
        Image.new('RGBA', (8, 8), (200, 20, 40, 255)).save(path)
        arguments = ('spr_demo', [str(path)] * 2, info['sha256'], info['image_sha256'])
        self.project.replace_sprite_images(*arguments)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        result = self.project.replace_sprite_images(*arguments, dry_run=False)
        self.assertEqual(info['sha256'], self.project.inspect_sprite('spr_demo')['sha256'])
        for p in before:
            self.assertEqual(p.read_bytes(), path.read_bytes() if p.suffix == '.png' else before[p])
        with self.assertRaises(ValueError):
            self.project.replace_sprite_images(*arguments, dry_run=False)
        self.project.restore(result['transaction'], False)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_replace_rejects_size_count_stale_description_and_multilayer(self):
        path = self.sprite()
        info = self.project.inspect_sprite('spr_demo')
        with self.assertRaises(ValueError):
            self.project.replace_sprite_images('spr_demo', [str(path)], info['sha256'], info['image_sha256'], False)
        Image.new('RGBA', (4, 4)).save(path)
        with self.assertRaises(ValueError):
            self.project.replace_sprite_images('spr_demo', [str(path)] * 2, info['sha256'], info['image_sha256'], False)
        self.project.configure_sprite('spr_demo', speed=3, dry_run=False)
        with self.assertRaises(ValueError):
            self.project.replace_sprite_images('spr_demo', [str(path)] * 2, info['sha256'], info['image_sha256'], False)


class RuntimeFeedbackTests(core.ProjectFixture):
    def setUp(self):
        self.fixture = fixtures.RuntimeTests('test_stop_preview_and_owned_process_only')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.runtime = self.fixture.runtime
        self.project = self.fixture.project
        self.root = self.fixture.root

    def launch(self):
        self.fixture.launch()
        self.session_id = self.fixture.session_id
        self.process = self.fixture.process
        self.fixture.ready()

    def test_runtime_log_preserves_utf8_across_read_boundaries(self):
        for text in ('x' * 4095 + '中文日志\n末行', 'x' * 4093 + '🎮中文\r\n结束'):
            process = fixtures.FakeProcess(text.encode('utf-8'))
            self.runtime.process = process
            self.runtime.lines.clear()
            self.runtime._collect(process)
            self.assertEqual([row['text'] for row in self.runtime.lines], text.splitlines())
            self.assertTrue(process.stdout.closed)

    def test_runtime_log_flushes_incomplete_utf8_at_eof(self):
        process = fixtures.FakeProcess(b'valid\nlast\xe4\xb8')
        self.runtime.process = process
        self.runtime._collect(process)
        self.assertEqual([row['text'] for row in self.runtime.lines], ['valid', 'last\ufffd'])
        self.assertTrue(process.stdout.closed)

    def test_runtime_log_keeps_filtering_after_split_utf8(self):
        process = fixtures.FakeProcess(('x' * 4095 + '中 password=example\nnext').encode('utf-8'))
        self.runtime.process = process
        self.runtime._collect(process)
        self.assertEqual([row['text'] for row in self.runtime.lines],
                         ['[credential-related output omitted]', 'next'])

    def test_runtime_log_eof_decode_respects_line_limit(self):
        process = fixtures.FakeProcess(b'x' * 20000 + b'\xe4\xb8')
        self.runtime.process = process
        self.runtime._collect(process)
        self.assertEqual(len(self.runtime.lines), 1)
        self.assertEqual(len(self.runtime.lines[0]['text']), 16384)
        self.assertTrue(self.runtime.lines[0]['text'].endswith('[line truncated]'))

    def test_long_runtime_line_keeps_error_prefix_and_marks_truncation(self):
        process = fixtures.FakeProcess(('ERROR: useful diagnostic ' + 'x' * 30000 + '\nnext').encode())
        self.runtime.process = process
        self.runtime._collect(process)
        line = self.runtime.lines[0]['text']
        self.assertTrue(line.startswith('ERROR: useful diagnostic'))
        self.assertIn('[line truncated]', line)
        self.assertLessEqual(len(line), 16384)
        self.assertTrue(self.runtime.build_diagnostics)
        self.assertEqual(self.runtime.lines[1]['text'], 'next')

    def test_long_runtime_line_preserves_credential_filter(self):
        samples = ['password=example ' + 'x' * 30000,
                   'x' * 30000 + ' password=example',
                   'x' * 4093 + 'password=example ' + 'x' * 30000]
        for text in samples:
            process = fixtures.FakeProcess((text + '\nnext').encode())
            self.runtime.process = process
            self.runtime.lines.clear()
            self.runtime._collect(process)
            self.assertEqual([row['text'] for row in self.runtime.lines],
                             ['[credential-related output omitted]', 'next'])

    def test_root_resource_parent_is_remapped_only_in_snapshot(self):
        path = self.root / 'rooms/Room1/Room1.yy'
        room = self.project.read('rooms/Room1/Room1.yy')
        room['parent'] = {'name': 'test', 'path': 'test.yyp'}
        path.write_bytes(encode(room))
        self.launch()
        snapshot = self.runtime.session['directory'] / 'project'
        room_copy = parse_yy((snapshot / 'rooms/Room1/Room1.yy').read_text(encoding='utf-8'))
        self.assertEqual(room_copy['parent'], {'name': 'MORX_Run_' + self.session_id,
                                              'path': 'MORX_Run_' + self.session_id + '.yyp'})
        self.assertEqual(self.project.read('rooms/Room1/Room1.yy')['parent']['path'], 'test.yyp')

    def test_compile_does_not_require_bridge_or_launch_runner(self):
        process = fixtures.FakeProcess()
        with patch('morx_gms2_mcp.runtime.subprocess.Popen', return_value=process) as launch:
            result = self.runtime.run(False, compile_only=True)
        self.assertEqual(launch.call_args.args[0][-2:], ['Windows', 'Compile'])
        self.assertNotIn('Run', launch.call_args.args[0])
        self.assertNotIn('sys_morx_runtime', [r['id']['name'] for r in self.project.metadata()['resources']])
        self.runtime._log('Final Compile finished')
        self.assertEqual(self.runtime.status()['build_status'], 'building')
        process.code = 1
        self.assertEqual(self.runtime.status()['build_status'], 'failed')
        self.assertFalse(self.runtime.status()['compiled'])
        process.code = 0
        self.assertTrue(self.runtime.status()['compiled'])
        self.assertFalse(self.runtime.status()['bridge_ready'])
        self.runtime.stop(result['session_id'], False)

    def test_runtime_errors_are_separate_from_compile_failure(self):
        self.launch()
        self.runtime._log('Object: obj_demo at line 2 : invalid identifier')
        self.runtime._log('Final Compile finished')
        self.process.code = 1
        status = self.runtime.status()
        self.assertEqual(status['error_count'], 0)
        self.assertEqual(status['compile_error_count'], 1)
        self.assertEqual(status['build_status'], 'failed')

    def test_transient_missing_heartbeat_and_staleness_diagnostics(self):
        self.launch()
        self.assertTrue(self.runtime.status()['bridge_ready'])
        path = self.runtime._file('ready.json')
        path.unlink()
        status = self.runtime.status()
        self.assertTrue(status['bridge_ready'])
        self.assertTrue(status['bridge_diagnostics']['cached'])
        self.runtime.heartbeat['mtime'] = time.time() - 11
        status = self.runtime.status()
        self.assertFalse(status['bridge_ready'])
        self.assertEqual(status['bridge_diagnostics']['reason'], 'heartbeat_stale')
        atomic_json(path, {'session': 'wrong', 'bridge': self.runtime.session['bridge']})
        self.assertEqual(self.runtime.status()['bridge_diagnostics']['reason'], 'heartbeat_identity_mismatch')

    def test_completed_temporary_response_recovers_without_resending(self):
        self.launch()
        self.runtime.pending = 'a' * 32
        self.runtime.pending_since = time.monotonic()
        atomic_json(self.runtime._file('response.json'), {'session': self.session_id, 'request': 'old', 'ok': True, 'data': {}})
        response = self.runtime._file('response.json.tmp')
        atomic_json(response, {'session': self.session_id, 'request': self.runtime.pending,
                               'ok': True, 'data': {'frame': 228}})
        with patch('morx_gms2_mcp.runtime.atomic_json') as send:
            status = self.runtime.status()
        send.assert_not_called()
        self.assertIsNone(self.runtime.pending)
        self.assertNotIn('pending_request', status)
        self.assertEqual(status['completed_request']['response_source'], 'completed_temporary_file')

    def test_incomplete_or_unrelated_temporary_response_keeps_pending(self):
        self.launch()
        self.runtime.pending = 'a' * 32
        path = self.runtime._file('response.json.tmp')
        path.write_text('{"session":', encoding='utf-8')
        self.assertIn('pending_request', self.runtime.status())
        atomic_json(path, {'session': 'other', 'request': self.runtime.pending, 'ok': True, 'data': {}})
        self.assertIn('pending_request', self.runtime.status())

    def test_pending_request_blocks_window_input_before_any_messages(self):
        self.launch()
        self.runtime.pending = 'unknown'
        with patch('morx_gms2_mcp.win_keyboard.key_down') as down, \
             patch('morx_gms2_mcp.win_mouse.send_mouse_message') as mouse:
            with self.assertRaises(ValueError):
                self.runtime.input(self.session_id, ['delete'], dry_run=False)
            with self.assertRaises(ValueError):
                self.runtime.mouse(self.session_id, 'click', 1, 1, dry_run=False)
        down.assert_not_called()
        mouse.assert_not_called()

    def test_navigation_keys_and_bounded_expansion(self):
        self.assertEqual(key_codes(['delete', 'home', 'end', 'pageup', 'pagedown', 'insert']), [46, 36, 35, 33, 34, 45])
        with patch.object(self.runtime, 'request', return_value={'data': {}}) as request:
            self.runtime.inspect('session', max_depth=8)
        self.assertEqual(request.call_args.args[2]['max_depth'], 8)
        for depth in (0, 9, True, '4'):
            with self.assertRaises(ValueError):
                self.runtime.inspect('session', max_depth=depth)

    def test_restart_preserves_save_but_stop_cleans_it(self):
        self.launch()
        save = self.runtime.session['save_directory'] / 'progress.json'
        save.write_text('{"level":2}', encoding='utf-8')
        self.assertTrue(self.runtime.restart(self.session_id)['dry_run'])
        new_process = fixtures.FakeProcess()
        with patch('morx_gms2_mcp.runtime.subprocess.Popen', return_value=new_process) as launch:
            result = self.runtime.restart(self.session_id, False)
        self.assertTrue(result['preserve_save'])
        self.assertEqual(launch.call_args.args[0][-1], 'Run')
        self.assertEqual(json.loads(save.read_text())['level'], 2)
        self.assertFalse(self.runtime._file('ready.json').exists())
        self.assertFalse(self.runtime.status()['bridge_ready'])
        self.runtime.stop(self.session_id, False)
        self.assertFalse(save.exists())

    def test_capture_export_has_fixed_boundary_and_no_overwrite(self):
        self.launch()
        for filename in ('../escape.png', 'C:/escape.png', 'a/b.png', 'bad.jpg'):
            with self.assertRaises(ValueError):
                self.runtime.save_capture(self.session_id, filename)
        self.assertTrue(self.runtime.save_capture(self.session_id, 'proof.png')['dry_run'])
        self.fixture.respond(command='capture')
        result = self.runtime.save_capture(self.session_id, 'proof.png', False)
        path = self.project.path(result['path'])
        with Image.open(path) as picture:
            self.assertEqual(picture.size, (1280, 720))
        with self.assertRaises(ValueError):
            self.runtime.save_capture(self.session_id, 'proof.png', False)

    def test_mouse_gui_conversion_settling_and_observation(self):
        self.launch()
        window = win_mouse.Window(1, 2, 'test', 1280, 720)
        engine = {'window_width': 1600, 'window_height': 900, 'gui_width': 1600,
                  'gui_height': 900, 'mouse': {'window_x': 343, 'window_y': 600}}
        with patch('morx_gms2_mcp.win_mouse.find_game_window', return_value=window), \
             patch('morx_gms2_mcp.win_mouse.send_mouse_message') as message, \
             patch.object(self.runtime, 'inspect', return_value={'data': engine, 'frame': 12}), \
             patch.object(self.runtime, 'request', side_effect=lambda sid, cmd, args:
                          {'data': {'trace_id': args['trace_id']}}) as trace, \
             patch.object(self.runtime, 'wait', return_value={'data': {}, 'frame': 12}) as wait:
            result = self.runtime.mouse(self.session_id, 'click', 343, 600, dry_run=False,
                                        coordinate_space='gui')
        self.assertEqual(message.call_args_list[0].args, (window, 'move', 274, 480))
        self.assertEqual(message.call_args_list[1].args[:4], (window, 'click', 274, 480))
        self.assertEqual([c.args[1] for c in wait.call_args_list], [2, 1])
        self.assertTrue(result['input_dispatched'])
        self.assertEqual(result['observed_mouse'], engine['mouse'])
        self.assertEqual([c.args[2]['phase'] for c in trace.call_args_list], ['begin', 'end'])
        self.assertEqual(result['mouse_trace']['trace_id'], result['mouse_trace']['engine']['trace_id'])
        self.assertEqual(result['mouse_trace']['game_effect'], 'unknown')

    def test_mouse_drag_keeps_dispatch_and_release_evidence_when_later_sample_moves(self):
        self.launch()
        window = win_mouse.Window(1, 2, 'test', 1280, 720)
        api = Mock()
        release = {'frame': 36, 'gui_x': 1120, 'gui_y': 450, 'released': True, 'down': False}
        def trace_request(sid, cmd, args):
            return {'data': {'trace_id': args['trace_id'], 'release': release}}
        with patch('morx_gms2_mcp.win_mouse.find_game_window', return_value=window), \
             patch('morx_gms2_mcp.win_mouse._windows', return_value=(api, None)), \
             patch('morx_gms2_mcp.win_mouse.time.sleep'), \
             patch.object(self.runtime, 'request', side_effect=trace_request), \
             patch.object(self.runtime, 'wait'), \
             patch.object(self.runtime, 'inspect', return_value={'frame': 40,
                          'data': {'mouse': {'gui_x': 1112, 'gui_y': 95}}}):
            result = self.runtime.mouse(self.session_id, 'drag', 790, 588, end_x=896, end_y=360,
                                        steps=18, duration_ms=450, dry_run=False)
        dispatch = result['mouse_trace']['dispatch']
        self.assertEqual(len(dispatch), 22)  # 预移动、初始移动、按下、18 次移动、释放
        self.assertEqual([d['order'] for d in dispatch], list(range(1, 23)))
        self.assertEqual(dispatch[-1]['message'], win_mouse.WM_LBUTTONUP)
        self.assertEqual((dispatch[-1]['client_x'], dispatch[-1]['client_y']), (896, 360))
        self.assertEqual(result['mouse_trace']['engine']['release'], release)
        self.assertEqual(result['observed_mouse']['gui_y'], 95)
        self.assertFalse(result['mouse_trace']['automatic_replay'])

    def test_partial_drag_returns_evidence_and_releases_without_replay(self):
        self.launch()
        window = win_mouse.Window(1, 2, 'test', 1280, 720)
        api = Mock()
        api.PostMessageW.side_effect = [1, 1, 0, 1]  # 初始移动、按下、移动失败、释放
        with patch('morx_gms2_mcp.win_mouse.find_game_window', return_value=window), \
             patch('morx_gms2_mcp.win_mouse._windows', return_value=(api, None)), \
             patch('morx_gms2_mcp.win_mouse.time.sleep'), \
             patch.object(self.runtime, 'request', side_effect=lambda sid, cmd, args:
                          {'data': {'trace_id': args['trace_id']}}), \
             patch.object(self.runtime, 'wait'), \
             patch.object(self.runtime, 'inspect', return_value={'data': {}}):
            result = self.runtime.mouse(self.session_id, 'drag', 10, 20, end_x=100, end_y=120,
                                        settle_frames=0, dry_run=False)
        self.assertTrue(result['input_dispatched'])
        self.assertFalse(result['dispatch_complete'])
        self.assertIn('dispatch_error', result)
        self.assertEqual(api.PostMessageW.call_count, 4)
        self.assertEqual(result['mouse_trace']['dispatch'][-1]['message'], win_mouse.WM_LBUTTONUP)

    def test_mouse_trace_failure_before_input_does_not_dispatch(self):
        self.launch()
        window = win_mouse.Window(1, 2, 'test', 1280, 720)
        with patch('morx_gms2_mcp.win_mouse.find_game_window', return_value=window), \
             patch('morx_gms2_mcp.win_mouse.send_mouse_message') as message, \
             patch.object(self.runtime, 'request', side_effect=ValueError('timeout')):
            with self.assertRaisesRegex(ValueError, 'timeout'):
                self.runtime.mouse(self.session_id, 'click', 20, 30, dry_run=False)
        message.assert_not_called()

    def test_concurrent_mouse_call_cannot_replace_active_trace_or_dispatch(self):
        self.launch()
        window = win_mouse.Window(1, 2, 'test', 1280, 720)
        started, resume = threading.Event(), threading.Event()
        def drag(*args, **kwargs):
            started.set()
            if not resume.wait(5):
                raise ValueError('test resume timeout')
            return {}
        with patch('morx_gms2_mcp.win_mouse.find_game_window', return_value=window), \
             patch('morx_gms2_mcp.win_mouse.drag_message', side_effect=drag) as dispatch, \
             patch.object(self.runtime, 'request', side_effect=lambda sid, cmd, args:
                          {'data': {'trace_id': args['trace_id']}}) as requests, \
             patch.object(self.runtime, 'wait'), \
             patch.object(self.runtime, 'inspect', return_value={'data': {}}), \
             ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.runtime.mouse, self.session_id, 'drag', 10, 20,
                                end_x=100, end_y=120, settle_frames=0, dry_run=False)
            try:
                self.assertTrue(started.wait(5))
                with self.assertRaisesRegex(ValueError, '上个鼠标动作'):
                    self.runtime.mouse(self.session_id, 'drag', 20, 30, end_x=200, end_y=300,
                                       dry_run=False)
                self.assertEqual(requests.call_count, 1)
            finally:
                resume.set()
            self.assertTrue(first.result(5)['dispatch_complete'])
        self.assertEqual(dispatch.call_count, 1)
        self.assertFalse(self.runtime.mouse_guard.locked())

    def test_mouse_trace_timeout_after_input_keeps_dispatch_without_replay(self):
        self.launch()
        window = win_mouse.Window(1, 2, 'test', 1280, 720)
        api = Mock()
        with patch('morx_gms2_mcp.win_mouse.find_game_window', return_value=window), \
             patch('morx_gms2_mcp.win_mouse._windows', return_value=(api, None)), \
             patch('morx_gms2_mcp.win_mouse.time.sleep'), \
             patch.object(self.runtime, 'request', side_effect=[{'data': {}}, ValueError('timeout')]), \
             patch.object(self.runtime, 'wait'):
            result = self.runtime.mouse(self.session_id, 'click', 20, 30, dry_run=False)
        self.assertTrue(result['dispatch_complete'])
        self.assertEqual(result['observation_error'], 'timeout')
        self.assertNotIn('engine', result['mouse_trace'])
        self.assertEqual(api.PostMessageW.call_count, 4)

    def test_capture_export_rejects_readonly_and_restart_failure_retains_save(self):
        self.launch()
        self.project.writable = False
        with self.assertRaises(ValueError):
            self.runtime.save_capture(self.session_id, 'proof.png', False)
        save = self.runtime.session['save_directory'] / 'progress.json'
        save.write_text('{}', encoding='utf-8')
        with patch('morx_gms2_mcp.runtime.subprocess.Popen', side_effect=OSError('test failure')):
            with self.assertRaises(OSError):
                self.runtime.restart(self.session_id, False)
        self.assertTrue(save.exists())
        self.runtime.stop(self.session_id, False)
        self.assertFalse(save.exists())

    def test_invalid_heartbeat_cannot_use_cached_readiness(self):
        self.launch()
        self.assertTrue(self.runtime.status()['bridge_ready'])
        self.runtime._file('ready.json').write_text('[]', encoding='utf-8')
        self.assertFalse(self.runtime.status()['bridge_ready'])
        self.assertEqual(self.runtime.status()['bridge_diagnostics']['reason'], 'heartbeat_invalid')


class MouseFeedbackTests(core.ProjectFixture):
    def test_drag_duration_does_not_accumulate_coarse_sleep_rounding(self):
        window = win_mouse.Window(1, 2, 'test', 1280, 720)
        api = Mock()
        clock = [0.0]
        def coarse_sleep(delay):
            clock[0] += delay + .015
        with patch('morx_gms2_mcp.win_mouse._windows', return_value=(api, None)), \
             patch('morx_gms2_mcp.win_mouse.time.monotonic', side_effect=lambda: clock[0]), \
             patch('morx_gms2_mcp.win_mouse.time.sleep', side_effect=coarse_sleep):
            win_mouse.drag_message(window, 10, 20, 100, 120, duration_ms=50, steps=60)
        self.assertGreaterEqual(clock[0], .05)
        self.assertLessEqual(clock[0], .066)
        self.assertEqual(api.PostMessageW.call_count, 63)
        self.assertEqual(api.PostMessageW.call_args.args[1], win_mouse.WM_LBUTTONUP)

    def test_125_percent_mapping_and_gui_mapping(self):
        window = win_mouse.Window(1, 2, 'test', 1280, 720)
        window.physical_width, window.physical_height = 1600, 900
        metrics = win_mouse.coordinate_metrics(window, {'window_width': 1600, 'window_height': 900,
                                                       'gui_width': 640, 'gui_height': 360})
        self.assertEqual(win_mouse.convert_point(343, 600, 'window', metrics), (274, 480))
        self.assertEqual(win_mouse.convert_point(343, 600, 'physical', metrics), (274, 480))
        self.assertEqual(win_mouse.convert_point(320, 180, 'gui', metrics), (640, 360))
        self.assertEqual(win_mouse.convert_point(1279, 719, 'client', metrics), (1279, 719))
        with self.assertRaises(ValueError):
            win_mouse.convert_point(640, 0, 'gui', metrics)

    def test_drag_has_held_moves_and_always_releases(self):
        window = win_mouse.Window(1, 2, 'test', 1280, 720)
        api = Mock()
        with patch('morx_gms2_mcp.win_mouse._windows', return_value=(api, None)), \
             patch('morx_gms2_mcp.win_mouse.time.sleep'):
            win_mouse.drag_message(window, 10, 20, 100, 120, steps=3)
        calls = api.PostMessageW.call_args_list
        self.assertEqual(calls[1].args[1], win_mouse.WM_LBUTTONDOWN)
        self.assertEqual(calls[-1].args[1], win_mouse.WM_LBUTTONUP)
        self.assertTrue(all(c.args[2] == 1 for c in calls[2:-1]))
        api.reset_mock()
        with patch('morx_gms2_mcp.win_mouse._windows', return_value=(api, None)), \
             patch('morx_gms2_mcp.win_mouse.time.sleep', side_effect=ValueError('interrupted')):
            with self.assertRaises(ValueError):
                win_mouse.drag_message(window, 10, 20, 100, 120)
        self.assertEqual(api.PostMessageW.call_args_list[-1].args[1], win_mouse.WM_LBUTTONUP)
