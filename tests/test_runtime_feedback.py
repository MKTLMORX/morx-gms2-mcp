"""实战反馈回归：退出证据、日志阶段、精简列表、格式控制和独立 Runner。"""
import asyncio
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from morx_gms2_mcp.runtime import MAX_SESSION_SECONDS, atomic_json
from morx_gms2_mcp.sessions import RuntimePool
from morx_gms2_mcp.server import build_server
from tests import test_runtime as fixtures


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RuntimeTests('test_stop_preview_and_owned_process_only')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.rt = self.fixture.runtime
        self.project = self.fixture.project

    def launch(self):
        self.fixture.launch()
        return self.fixture.session_id

    def test_timeout_reports_deadline_and_retains_errors_after_cleanup(self):
        sid = self.launch()
        status = self.rt.status()
        self.assertEqual(status['lifecycle']['max_session_seconds'], MAX_SESSION_SECONDS)
        self.assertTrue(status['lifecycle']['includes_preparation'])
        self.assertGreater(status['lifecycle']['remaining_seconds'], 890)
        self.assertIn('+00:00', status['lifecycle']['expires_at'])
        atomic_json(self.rt._file('errors.json'), {'session': sid,
                    'errors': [{'stage': 'unhandled', 'message': 'specific failure', 'frame': 12}]})
        self.rt._expire(sid)
        self.assertFalse(self.rt.session['directory'].exists())
        status = self.rt.status()
        self.assertEqual(status['stop_reason'], 'session_timeout')
        self.assertEqual(status['stop_source'], 'timer')
        self.assertEqual(status['error_count'], 1)
        self.assertEqual(status['last_runtime_error']['message'], 'specific failure')
        self.assertEqual(self.rt.errors(sid)['total'], 1)
        self.rt.stop(sid, False)
        self.assertEqual(self.rt.status()['stop_source'], 'timer')

    def test_exit_zero_with_unhandled_error_is_reported_without_cleanup(self):
        sid = self.launch()
        atomic_json(self.rt._file('errors.json'), {'session': sid,
                    'errors': [{'stage': 'unhandled', 'message': 'GML exception'}]})
        self.fixture.process.code = 0
        status = self.rt.status()
        self.assertEqual(status['state'], 'exited')
        self.assertEqual(status['stop_reason'], 'runtime_exception')
        self.assertEqual(status['stop_source'], 'process')
        self.assertTrue(self.rt.session['save_directory'].exists())

    def test_shutdown_and_explicit_stop_have_distinct_sources(self):
        sid = self.launch()
        self.rt.close()
        self.assertEqual(self.rt.status()['stop_reason'], 'service_shutdown')
        self.assertEqual(self.rt.status()['stop_source'], 'service')
        self.assertEqual(self.rt.stop(sid, False)['stop_reason'], 'service_shutdown')

    def test_old_deadline_does_not_stop_restarted_session(self):
        sid = self.launch()
        old = self.rt.session['deadline']
        with patch('morx_gms2_mcp.runtime.subprocess.Popen', return_value=fixtures.FakeProcess()):
            self.rt.restart(sid, False)
        self.rt._expire(sid, deadline=old)
        self.assertFalse(self.rt.session['stopped'])
        self.assertNotEqual(self.rt.session['deadline'], old)
        self.assertNotIn('termination', self.rt.session)
        self.assertEqual(self.rt.status()['lifecycle']['previous_termination']['reason'], 'restart_game')

    def test_runner_logs_do_not_inflate_compiler_diagnostics(self):
        self.launch()
        self.rt._log('script.gml at line 4 : error: bad syntax')
        self.rt._log('Final Compile finished')
        self.rt._log('"C:/runtime/Runner.exe" -game "C:/package/game.win"')
        self.rt._log('Socket(0): WebSocket connection failed (error code 12175)')
        self.fixture.ready()
        status = self.rt.status()
        self.assertTrue(status['compiled'])
        self.assertEqual(status['compile_error_count'], 1)
        self.assertEqual(status['runtime_log_error_count'], 1)
        self.assertNotIn('WebSocket', str(status['build_diagnostics']))
        self.assertEqual(self.rt.lines[-1]['stage'], 'runtime')

    def test_list_defaults_to_summary_and_can_hide_clean_history(self):
        sid = self.launch()
        self.rt.session['snapshot'] = {'by_root': {'folder': 'large snapshot' * 1000}}
        pool = RuntimePool(self.project, self.fixture.options)
        pool.sessions[sid] = self.rt
        brief, full = pool.listing(), pool.listing(summary=False)
        self.assertNotIn('preparation', brief['sessions'][0])
        self.assertLess(len(json.dumps(brief)) * 3, len(json.dumps(full)))
        self.rt.stop(sid, False)
        self.assertEqual(pool.listing(include_history=False)['sessions'], [])
        self.assertEqual(pool.listing()['sessions'][0]['stop_reason'], 'stop_game')
        with self.assertRaises(ValueError):
            pool.listing(summary='false')

    def test_code_format_is_explicit_previewed_hash_guarded_and_reversible(self):
        self.project.create('scripts', 'scr_format', '// original\n')
        path = self.project.path('scripts/scr_format/scr_format.gml')
        for original in (b'// original\n', b'\xef\xbb\xbf// original\r\n'):
            path.write_bytes(original)
            sha = self.project.read_code('scr_format')['sha256']
            preview = self.project.update_code('scr_format', '// original\r\n', sha, line_ending='crlf')
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(preview['formatting']['after']['line_ending'], 'crlf')
            changed = self.project.update_code('scr_format', '// changed\n', sha,
                                               dry_run=False, line_ending='lf')
            self.assertEqual(path.read_bytes(), (b'\xef\xbb\xbf' if original.startswith(b'\xef') else b'') + b'// changed\n')
            with self.assertRaises(ValueError):
                self.project.update_code('scr_format', '// stale', sha, dry_run=False, line_ending='crlf')
            self.project.restore(changed['transaction'], False)
            self.assertEqual(path.read_bytes(), original)
        with self.assertRaises(ValueError):
            self.project.update_code('scr_format', '', sha, line_ending='windows')

    def test_edit_does_not_stop_or_refresh_isolated_runtime(self):
        self.project.create('scripts', 'scr_edit_live', 'return 1;')
        sid = self.launch()
        copied = self.rt.session['directory'] / 'project/scripts/scr_edit_live/scr_edit_live.gml'
        sha = self.project.read_code('scr_edit_live')['sha256']
        self.project.update_code('scr_edit_live', 'return 2;', sha, dry_run=False)
        self.assertEqual(copied.read_text(), 'return 1;')
        self.assertIsNone(self.fixture.process.poll())
        self.assertFalse(self.rt.session['stopped'])

    def test_assertion_errors_name_bad_fields_and_example_is_usable(self):
        with self.assertRaisesRegex(ValueError, 'equals.*允许参数.*expected'):
            self.rt.tests.normalize([{'action': 'assert', 'equals': 0}])
        server = build_server(self.fixture.root)
        async def check():
            response = await server.call_tool('server_info', {})
            blocks = response[0] if isinstance(response, tuple) else response
            info = json.loads(blocks[0].text)
            steps = self.rt.tests.normalize(info['game_test_example']['steps'])
            self.assertEqual(steps[0]['path'], ['globals', 'score'])
            self.assertEqual(steps[1]['action'], 'capture')
        asyncio.run(check())

    def direct(self, builder_code=0, missing=False):
        self.project.install_runtime_bridge(False)
        runner = Path(self.fixture.options.runtime_root) / 'windows/x64/Runner.exe'
        runner.parent.mkdir(parents=True)
        runner.write_bytes(b'official runner fixture')
        builder, game = fixtures.FakeProcess(), fixtures.FakeProcess()
        builder.code = builder_code
        builder.pid, game.pid = 900001, 900002
        def spawn(command, **kwargs):
            if isinstance(command, list):
                self.assertEqual(command[-1], 'Compile')
                sid = self.rt.session['id']
                artifact = self.rt.session['directory'] / 'output' / ('MORX_Run_' + sid) / ('MORX_Run_' + sid + '.win')
                if not missing:
                    artifact.parent.mkdir(parents=True)
                    artifact.write_bytes(b'valid game package')
                return builder
            self.assertIn(' -game "', command)
            self.assertTrue(command.endswith('.win"'))
            self.assertFalse(kwargs['shell'])
            return game
        with patch('morx_gms2_mcp.runtime.subprocess.Popen', side_effect=spawn) as mock:
            result = self.rt.run(False, launch_mode='direct')
            self.assertTrue(self.rt.session['preparation_done'].wait(5))
        return result['session_id'], builder, game, mock.call_count

    def test_direct_runner_releases_builder_and_restarts_without_recompile(self):
        sid, builder, game, calls = self.direct()
        self.assertEqual(calls, 2)
        self.assertEqual(builder.poll(), 0)
        self.assertIsNone(game.poll())
        status = self.rt.status()
        self.assertTrue(status['compiled'])
        self.assertEqual(status['build_exit_code'], 0)
        self.assertEqual(status['build_pid'], builder.pid)
        self.assertEqual(status['runner_pid'], game.pid)
        save = self.rt.session['save_directory'] / 'save.txt'
        save.write_text('isolated save')
        with patch('morx_gms2_mcp.runtime.subprocess.Popen', return_value=fixtures.FakeProcess()) as mock:
            result = self.rt.restart(sid, False)
            self.assertFalse(result['rebuilds_snapshot'])
            self.assertEqual(mock.call_count, 1)
            self.assertIsInstance(mock.call_args.args[0], str)
        self.assertEqual(save.read_text(), 'isolated save')
        self.assertTrue(self.rt.stop(sid, False)['cleaned'])

    def test_direct_failed_compile_does_not_launch_runner(self):
        sid, builder, game, calls = self.direct(builder_code=1)
        self.assertEqual(calls, 1)
        self.assertEqual(self.rt.status()['build_status'], 'failed')
        self.rt.stop(sid, False)

    def test_direct_missing_package_does_not_launch_runner(self):
        sid, _, _, calls = self.direct(missing=True)
        self.assertEqual(calls, 1)
        status = self.rt.status()
        self.assertEqual(status['state'], 'failed')
        self.assertIn('产物', status['preparation']['preparation_error']['message'])
        self.assertTrue(self.rt.stop(sid, False)['cleaned'])

    def test_direct_tampered_artifact_cannot_restart_and_remains_cleanable(self):
        sid, _, _, _ = self.direct()
        _, path = self.rt._runner_command(self.rt.session, Path(self.fixture.options.runtime_root))
        path.write_bytes(b'modified')
        with patch('morx_gms2_mcp.runtime.subprocess.Popen') as mock:
            with self.assertRaisesRegex(ValueError, '产物已改变'):
                self.rt.restart(sid, False)
            mock.assert_not_called()
        self.assertTrue(self.rt.session['cleanup_failed'])
        self.assertTrue(self.rt.stop(sid, False)['cleaned'])
