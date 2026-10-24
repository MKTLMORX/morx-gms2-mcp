"""区分可恢复文件重试与命令失败；不启动图形程序。"""
import asyncio
import copy
import json
import unittest
from unittest.mock import patch

from morx_gms2_mcp.runtime import atomic_json, bridge_io_summary
from morx_gms2_mcp.server import build_server
import tests.test_runtime as fixtures


def diagnostic(pending=0, retries=8, recoveries=2, stalled=0):
    return {'pending_writes': pending, 'retry_attempts': retries, 'retry_recoveries': recoveries,
            'stalled_writes': stalled, 'sample_frame': 8000,
            'files': [{'file': 'response.json', 'state': 'retrying' if pending else 'recovered',
                       'pending': bool(pending), 'pending_age_ms': 700 if pending else 0,
                       'retry_attempts': retries, 'current_retry_attempts': 3 if pending else 0,
                       'recovery_count': recoveries, 'last_recovery_attempts': 5,
                       'first_failure_frame': 4700, 'last_failure_frame': 7990,
                       'last_recovery_frame': 7991, 'last_failure_stage': 'replace_destination',
                       'request': 'b' * 32, 'last_failure_request': 'a' * 32,
                       'last_recovery_request': 'a' * 32}]}


class SummaryTests(unittest.TestCase):
    def test_retry_total_is_not_reported_as_final_failure(self):
        result = bridge_io_summary(diagnostic())
        self.assertEqual(result['state'], 'recovered')
        self.assertEqual(result['pending_writes'], 0)
        self.assertEqual(result['retry_attempts'], 8)
        self.assertEqual(result['retry_recoveries'], 2)
        self.assertEqual(result['write_failures_semantics'], 'retry_attempts_not_final_failures')
        self.assertEqual(result['files'][0]['last_recovery_frame'], 7991)
        self.assertEqual(result['files'][0]['last_recovery_request'], 'a' * 32)

    def test_pending_and_stalled_remain_distinct_from_recovered(self):
        self.assertEqual(bridge_io_summary(diagnostic(pending=1))['state'], 'retrying')
        self.assertEqual(bridge_io_summary(diagnostic(pending=1, stalled=1))['state'], 'stalled')
        self.assertEqual(bridge_io_summary(diagnostic(retries=0, recoveries=0))['state'], 'healthy')

    def test_old_counter_is_explicitly_limited_without_inventing_recoveries(self):
        result = bridge_io_summary({'pending_writes': 0, 'write_failures': 8})
        self.assertEqual(result['retry_attempts'], 8)
        self.assertIsNone(result['retry_recoveries'])
        self.assertEqual(result['state'], 'unknown')
        self.assertFalse(result['detail_available'])

    def test_missing_or_invalid_data_cannot_look_healthy(self):
        self.assertEqual(bridge_io_summary(None)['state'], 'unavailable')
        for value in (True, -1, 1.5, float('nan'), float('inf'), 10**500, [], {}):
            data = diagnostic()
            data['retry_attempts'] = value
            self.assertFalse(bridge_io_summary(data)['available'])
        self.assertFalse(bridge_io_summary(diagnostic(pending=0, stalled=1))['available'])
        self.assertFalse(bridge_io_summary(diagnostic(retries=1, recoveries=2))['available'])

    def test_file_rows_are_bounded_and_do_not_expose_arbitrary_paths_or_text(self):
        data = diagnostic()
        bad = copy.deepcopy(data['files'][0])
        bad.update(file='../private.json', request='secret', state='run command', last_failure_stage='private path')
        data['files'] = [bad, {'file': []}, data['files'][0]] * 20
        result = bridge_io_summary(data)
        self.assertEqual(len(result['files']), 1)
        self.assertEqual(result['files'][0]['file'], 'response.json')
        self.assertNotIn('private', json.dumps(result))
        data['files'] = [{'file': 'ready.json', 'state': [], 'last_failure_stage': {}}]
        result = bridge_io_summary(data)
        self.assertEqual(result['files'][0]['state'], 'unknown')


class RuntimeDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RuntimeTests('test_stop_preview_and_owned_process_only')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.runtime = self.fixture.runtime
        self.project = self.fixture.project

    def launch(self):
        self.fixture.launch()
        self.fixture.ready()
        self.session_id = self.fixture.session_id

    def heartbeat(self, io):
        atomic_json(self.runtime._file('ready.json'), {'session': self.session_id,
                    'bridge': self.runtime.session['bridge'], 'frame': 8000, 'bridge_io': io})

    def test_status_reads_heartbeat_diagnostics_without_reissuing_a_request(self):
        self.launch()
        self.heartbeat(diagnostic())
        with patch('morx_gms2_mcp.runtime.atomic_json') as send:
            result = self.runtime.status()
        send.assert_not_called()
        self.assertEqual(result['error_count'], 0)
        self.assertEqual(result['bridge_io']['state'], 'recovered')
        self.assertTrue(result['bridge_io']['current'])
        self.assertIsNotNone(result['bridge_io']['sample_age_seconds'])

    def test_stale_heartbeat_reports_sample_as_historical(self):
        self.launch()
        self.heartbeat(diagnostic())
        self.runtime.status()
        self.runtime._file('ready.json').unlink()
        self.runtime.heartbeat['mtime'] -= 11
        result = self.runtime.status()
        self.assertFalse(result['bridge_ready'])
        self.assertFalse(result['bridge_io']['current'])
        self.assertGreater(result['bridge_io']['sample_age_seconds'], 10)

    def test_state_and_stop_preserve_diagnostics_without_creating_runtime_errors(self):
        self.launch()
        self.heartbeat(diagnostic())
        self.fixture.respond(command='state', data={'bridge_io': diagnostic(), 'instances': []})
        result = self.runtime.inspect(self.session_id, variables=[])
        self.assertEqual(result['data']['bridge_io']['retry_recoveries'], 2)
        stopped = self.runtime.stop(self.session_id, False)
        self.assertEqual(stopped['error_count'], 0)
        self.assertEqual(stopped['bridge_io']['state'], 'recovered')
        self.assertFalse(stopped['bridge_io']['current'])

    def test_bridge_upgrade_preflight_is_readonly_and_repair_is_explicit(self):
        self.assertFalse(self.project.bridge_status()['installed'])
        self.project.install_runtime_bridge(False)
        self.assertTrue(self.project.bridge_status()['compatible'])
        path = self.project.root / 'objects/sys_morx_runtime/Create_0.gml'
        path.write_bytes(path.read_bytes() + b'\n// old managed bridge\n')
        before = path.read_bytes()
        state = self.project.bridge_status()
        self.assertFalse(state['compatible'])
        self.assertEqual(state['action'], 'install_runtime_bridge')
        self.assertEqual(path.read_bytes(), before)
        self.project.install_runtime_bridge()
        self.assertEqual(path.read_bytes(), before)
        self.project.install_runtime_bridge(False)
        self.assertTrue(self.project.bridge_status()['compatible'])

    def test_server_info_exposes_bridge_sync_before_run(self):
        server = build_server(self.project.root)
        async def check():
            result = await server.call_tool('server_info', {})
            blocks = result[0] if isinstance(result, tuple) else result
            info = json.loads(blocks[0].text)
            self.assertEqual(info['runtime_bridge']['action'], 'install_runtime_bridge')
            self.assertFalse(info['runtime_bridge']['installed'])
        asyncio.run(check())
