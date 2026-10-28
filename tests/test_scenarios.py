"""场景结果来自断言和实际响应；不把工具投递成功视为游戏逻辑通过。"""
import json
import threading
import time
import unittest
import asyncio
from unittest.mock import patch
from tests import test_runtime as fixtures
from tests.test_core import ProjectFixture
from tests.test_audio import make_wav
from morx_gms2_mcp.storage import encode
from morx_gms2_mcp.scenarios import compare
from morx_gms2_mcp.server import build_server


class AudioGroupTests(ProjectFixture):
    def test_create_assign_conflict_and_restore(self):
        original = self.project.yyp.read_bytes()
        info = self.project.inspect_audio_groups()
        sha = info['project_sha256']
        self.assertTrue(self.project.create_audio_group('audio_effects', sha)['dry_run'])
        self.assertEqual(original, self.project.yyp.read_bytes())
        result = self.project.create_audio_group('audio_effects', sha, False)
        with self.assertRaises(ValueError):
            self.project.create_audio_group('other', sha, False)
        with self.assertRaises(ValueError):
            self.project.create_audio_group('AUDIO_EFFECTS', self.project.inspect_audio_groups()['project_sha256'])
        self.project.restore(result['transaction'], False)
        self.assertEqual(original, self.project.yyp.read_bytes())
        self.project.create_audio_group('audio_effects', sha, False)
        path = make_wav(self.root / 'sound.wav')
        self.project.import_sound('snd_effect', str(path), audio_group='audio_effects')
        groups = self.project.inspect_audio_groups()['audio_groups']
        self.assertEqual(groups[1]['members'][0]['name'], 'snd_effect')
        sound = self.project.inspect_sound('snd_effect')
        with self.assertRaises(ValueError):
            self.project.configure_sound('snd_effect', sound['sha256'], compression='streamed', dry_run=False)
        self.assertTrue(self.project.validate()['ok'])

    def test_invalid_and_readonly_do_not_write(self):
        from morx_gms2_mcp.resources import Project
        read = Project(self.root, writable=False)
        sha = read.inspect_audio_groups()['project_sha256']
        self.assertTrue(read.create_audio_group('audio_test', sha)['dry_run'])
        with self.assertRaises(ValueError):
            read.create_audio_group('audio_test', sha, False)
        for name in ('../bad', 'CON', 'bad space'):
            with self.assertRaises(ValueError):
                self.project.create_audio_group(name, sha, False)
        with self.assertRaisesRegex(ValueError, '冲突'):
            self.project.create_audio_group('Room1', sha, False)
        meta = self.project.metadata()
        meta['AudioGroups'].append({'name': 'AUDIOGROUP_DEFAULT'})
        self.project.yyp.write_bytes(encode(meta))
        with self.assertRaises(ValueError):
            self.project.inspect_audio_groups()


class ComparisonTests(unittest.TestCase):
    def test_missing_truncated_and_bool_are_not_numeric_success(self):
        for value in ({'type': 'missing'}, {'type': 'truncated'}, {'type': 'complex'}):
            self.assertFalse(compare({'x': value}, ['x'], 'ne', 1)['passed'])
        self.assertFalse(compare({'x': True}, ['x'], 'eq', 1)['passed'])
        self.assertTrue(compare({}, ['x'], 'exists', False)['passed'])
        data = {'x': {'type': 'struct', 'values': {}, 'truncated': True}}
        self.assertFalse(compare(data, ['x', 'absent'], 'exists', False)['passed'])
        self.assertFalse(compare(data, ['x', 'values', 'absent'], 'exists', False)['passed'])
        self.assertTrue(compare(data, ['x'], 'exists', True)['passed'])
        self.assertFalse(compare({'x': 'a'*1024}, ['x'], 'eq', 'a'*1024)['passed'])
        self.assertTrue(compare({'globals': {'type': 'missing', 'error': 'text', 'score': 2}},
                                ['globals', 'score'], 'eq', 2)['passed'])
        self.assertTrue(compare({'globals': {'type': 'missing'}}, ['globals', 'type'], 'eq', 'missing')['passed'])

    def test_exact_integer_and_nested_response(self):
        value = {'type': 'int64', 'value': '9223372036854775807'}
        result = compare({'items': [value]}, ['items', 0], 'eq', value)
        self.assertTrue(result['passed'])
        self.assertEqual(result['actual'], value)
        self.assertFalse(compare({'x': 2}, ['x'], 'gte', 3)['passed'])


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RuntimeTests('test_stop_preview_and_owned_process_only')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.launch()
        self.fixture.ready()
        self.rt = self.fixture.runtime
        self.sid = self.fixture.session_id
        self.engine = self.rt.tests

    def assertion(self, action='assert', expected=2):
        return {'action': action, 'observe': {'action': 'state', 'scope': 'globals', 'variables': ['score']},
                'path': ['globals', 'score'], 'operator': 'eq', 'expected': expected, 'poll_ms': 50,
                'timeout_seconds': 1}

    def finish(self, test_id):
        self.engine.worker.join(5)
        self.assertFalse(self.engine.worker.is_alive())
        return self.engine.result(test_id)

    def test_preflight_all_steps_before_any_input(self):
        with patch.object(self.rt, 'request') as request:
            with self.assertRaises(ValueError):
                self.engine.start(self.sid, [{'action': 'input', 'keys': ['a']}, {'action': 'eval', 'code': 'x'}], dry_run=False)
            request.assert_not_called()
            preview = self.engine.start(self.sid, [self.assertion()])
            self.assertTrue(preview['dry_run'])
            self.assertIsNone(self.engine.record)
        for step in ({'action': 'mouse', 'mouse_action': 'click', 'mode': 'physical', 'x': 1, 'y': 1},
                     {'action': 'mouse', 'mouse_action': 'drag', 'x': 1, 'y': 1},
                     {**self.assertion(), 'path': 'global.score'}):
            with self.assertRaises(ValueError):
                self.engine.start(self.sid, [step])

    def test_wait_until_only_requeries_and_passes(self):
        calls = []
        def response(session, command, args=None, timeout=15):
            calls.append(command)
            return {'data': {'globals': {'score': 2 if len(calls) >= 3 else 0}}, 'frame': len(calls)}
        with patch.object(self.rt, 'request', side_effect=response):
            job = self.engine.start(self.sid, [self.assertion('wait_until')], dry_run=False)
            result = self.finish(job['test_id'])
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(calls, ['state'] * 3)
        self.assertEqual(result['results'][0]['result']['attempts'], 3)

    def test_failure_stops_actions_collects_and_exports_evidence(self):
        commands = []
        def response(session, command, args=None, timeout=15):
            commands.append(command)
            return {'data': {'globals': {'score': 0}}, 'image': b'fixture-png', 'frame': 8}
        with patch.object(self.rt, 'request', side_effect=response), patch.object(self.rt, 'input') as action:
            job = self.engine.start(self.sid, [self.assertion(), {'action': 'input', 'keys': ['a']}], dry_run=False)
            result = self.finish(job['test_id'])
            action.assert_not_called()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['failed_step'], 1)
        self.assertEqual(result['image_count'], 1)
        self.assertTrue(result['source_snapshot_sha256'])
        self.assertTrue(self.engine.save(job['test_id'])['dry_run'])
        saved = self.engine.save(job['test_id'], False)
        report = self.fixture.project.path(saved['path'] + '/report.json')
        self.assertEqual(json.loads(report.read_text(encoding='utf-8'))['status'], 'failed')
        with self.assertRaises(ValueError):
            self.engine.save(job['test_id'], False)
        self.rt.stop(self.sid, False)
        self.assertTrue(report.is_file())

    def test_transport_error_is_not_retried_or_recaptured(self):
        def response(*args, **kwargs):
            self.rt.pending = 'ambiguous'
            self.rt.pending_since = time.monotonic()
            raise ValueError('request timeout')
        with patch.object(self.rt, 'request', side_effect=response) as request:
            job = self.engine.start(self.sid, [self.assertion('wait_until')], dry_run=False)
            result = self.finish(job['test_id'])
        request.assert_called_once()
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['image_count'], 0)
        self.assertIn('capture_skipped', result['evidence'])
        self.rt.pending = None

    def test_continue_assertions_retains_failure(self):
        with patch.object(self.rt, 'request', return_value={'data': {'globals': {'score': 2}}, 'image': b'x'}):
            job = self.engine.start(self.sid, [self.assertion(expected=1), self.assertion()], False, dry_run=False)
            result = self.finish(job['test_id'])
        self.assertEqual(len(result['results']), 2)
        self.assertEqual(result['status'], 'failed')

    def test_timeout_and_cancel_are_distinct(self):
        with patch.object(self.rt, 'request', return_value={'data': {'globals': {'score': 0}}, 'image': b'x'}):
            job = self.engine.start(self.sid, [self.assertion('wait_until')], dry_run=False)
            result = self.finish(job['test_id'])
            self.assertEqual(result['status'], 'timed_out')
            job = self.engine.start(self.sid, [self.assertion('wait_until')], dry_run=False)
            self.engine.cancel.set()
            self.assertEqual(self.finish(job['test_id'])['status'], 'cancelled')

    def test_audio_protocol_and_mouse_sequence_dispatch(self):
        spec = {'sound_names': ['snd_music'], 'voices': [{'label': 'music', 'variable': 'music_handle'}]}
        with patch.object(self.rt, 'request', return_value={'data': {'sounds': {}}, 'frame': 1}) as request:
            self.rt.inspect_audio(self.sid, **spec)
            self.assertEqual(request.call_args.args[1], 'audio')
            self.assertEqual(request.call_args.args[2]['voices'][0]['scope'], 'globals')
        with patch.object(self.rt, 'mouse', return_value={'dispatch_complete': True}) as mouse:
            result = self.rt.run_sequence(self.sid, [{'action': 'mouse', 'mouse_action': 'click', 'x': 10, 'y': 20}], False)
            self.assertTrue(result['ok'])
            self.assertEqual(mouse.call_args.args[1], 'click')
            self.assertTrue(result['results'][0]['data']['dispatch_complete'])
        for voices in ([{}], [{'label': 'x', 'variable': 'x.y'}], [{'label': 'x', 'variable': 'x', 'scope': 'other'}]):
            with self.assertRaises(ValueError):
                self.rt._audio_args([], voices)

    def test_active_test_blocks_interleaving_and_stop_cancels(self):
        entered, release = threading.Event(), threading.Event()
        def slow(record, step, deadline):
            entered.set()
            release.wait(3)
            return {'passed': True}
        with patch.object(self.engine, '_step', side_effect=slow):
            job = self.engine.start(self.sid, [self.assertion()], dry_run=False)
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaisesRegex(ValueError, '交叉'):
                    self.rt.input(self.sid, ['a'], dry_run=False)
                with self.assertRaises(ValueError):
                    self.engine.start(self.sid, [self.assertion()], dry_run=False)
                self.rt.stop(self.sid, False)
            finally:
                release.set()
            self.assertEqual(self.finish(job['test_id'])['status'], 'cancelled')

    def test_mcp_test_audio_status_and_save_handlers(self):
        from morx_gms2_mcp.sessions import RuntimePool
        pool = RuntimePool(self.rt.project, self.fixture.options)
        pool.sessions[self.sid] = self.rt
        with patch('morx_gms2_mcp.server.RuntimePool', return_value=pool):
            server = build_server(self.fixture.root, writable=True, runtime_options=self.fixture.options)
        async def invoke(name, args):
            result = await server.call_tool(name, args)
            blocks = result[0] if isinstance(result, tuple) else result
            return json.loads(blocks[0].text)
        async def run():
            with patch.object(self.rt, 'request', return_value={'data': {'globals': {'score': 2}}, 'frame': 3}):
                job = await invoke('run_game_test', {'session_id': self.sid, 'steps': [self.assertion()], 'dry_run': False})
                await asyncio.to_thread(self.engine.worker.join, 5)
                result = await invoke('game_test_status', {'test_id': job['test_id']})
                self.assertTrue(result['passed'])
                saved = await invoke('save_game_test_report', {'test_id': job['test_id'], 'dry_run': False})
                self.assertIn('report.json', saved['files'])
                audio = await invoke('inspect_game_audio', {'session_id': self.sid, 'sound_names': ['snd_music']})
                self.assertEqual(audio['frame'], 3)
        asyncio.run(run())

    def test_multiple_capture_indices_and_readonly_export(self):
        def sequence(*args, **kwargs):
            return {'ok': True, 'results': [{'image_index': 0}], 'pictures': [b'png']}
        with patch.object(self.rt, 'run_sequence', side_effect=sequence), \
                patch.object(self.rt, 'request', return_value={'data': {'globals': {'score': 2}}}):
            job = self.engine.start(self.sid, [{'action': 'capture'}, {'action': 'capture'}, self.assertion()], dry_run=False)
            result = self.finish(job['test_id'])
        self.assertEqual([r['result']['image_index'] for r in result['results'][:2]], [0, 1])
        self.fixture.project.writable = False
        self.assertTrue(self.engine.save(job['test_id'])['dry_run'])
        with self.assertRaises(ValueError):
            self.engine.save(job['test_id'], False)

    def test_audio_assertion_uses_runtime_data(self):
        step = {'action': 'assert', 'observe': {'action': 'audio', 'sound_names': ['snd_music']},
                'path': ['groups', 'audio_music', 'loaded'], 'expected': True}
        with patch.object(self.rt, 'request', return_value={'data': {'groups': {'audio_music': {'loaded': True}}}}) as query:
            job = self.engine.start(self.sid, [step], dry_run=False)
            result = self.finish(job['test_id'])
        self.assertTrue(result['passed'])
        self.assertEqual(query.call_args.args[1], 'audio')
