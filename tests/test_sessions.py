"""两个假进程的独立路由、排队取消、故障清理；真实双 Runner 另做实战。"""
import asyncio
import json
import threading
import time
import unittest
from unittest.mock import patch

from morx_gms2_mcp.sessions import RuntimePool
from morx_gms2_mcp.server import build_server
from tests import test_runtime as fixtures


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RuntimeTests('test_stop_preview_and_owned_process_only')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.pool = RuntimePool(self.fixture.project, self.fixture.options)
        self.addCleanup(self.pool.close)
        self.fixture.project.install_runtime_bridge(False)

    def wait(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(condition())

    def test_two_sessions_queue_then_route_stop_and_stale_id(self):
        first, second = fixtures.FakeProcess(), fixtures.FakeProcess()
        first.pid, second.pid = 900001, 900002
        with patch('morx_gms2_mcp.runtime.subprocess.Popen', side_effect=[first, second]) as launch:
            a = self.pool.run(False, compile_only=True)['session_id']
            self.wait(lambda: self.pool.target(a).session['preparation_done'].is_set())
            with self.assertRaises(ValueError):
                self.pool.run(False, compile_only=True)
            b = self.pool.run(False, compile_only=True, new_session=True)['session_id']
            self.wait(lambda: self.pool.status(session_id=b)['preparation']['phase'] == 'queued')
            self.assertEqual(launch.call_count, 1)
            with self.assertRaisesRegex(ValueError, '必须明确'):
                self.pool.status()
            self.assertEqual(self.pool.status(session_id=a)['build_pid'], 900001)
            first.code = 0
            self.wait(lambda: self.pool.target(b).session['preparation_done'].is_set())
            self.assertEqual(launch.call_count, 2)
            ra, rb = self.pool.target(a), self.pool.target(b)
            self.assertNotEqual(ra.session['directory'], rb.session['directory'])
            self.assertNotEqual(ra.session['save_directory'], rb.session['save_directory'])
            ra._log('only A'); rb._log('only B')
            self.assertNotIn('only B', str(self.pool.output(a)))
            with patch.object(ra, 'input', return_value={'session_id': a}) as ia, patch.object(rb, 'input') as ib:
                self.assertEqual(self.pool.input(a, ['a'])['session_id'], a)
                ia.assert_called_once(); ib.assert_not_called()
            self.assertTrue(self.pool.stop(a, False)['cleaned'])
            self.assertTrue(rb.session['directory'].exists())
            self.assertIsNone(second.code)
            with self.assertRaises(ValueError):
                self.pool.request(a, 'state')
            self.assertEqual(self.pool.status()['session_id'], b)
            self.assertEqual(len(self.pool.listing()['sessions']), 2)

    def test_queued_cancellation_does_not_touch_first_session(self):
        with patch('morx_gms2_mcp.runtime.subprocess.Popen', return_value=fixtures.FakeProcess()) as launch:
            a = self.pool.run(False, compile_only=True)['session_id']
            self.wait(lambda: self.pool.target(a).session['preparation_done'].is_set())
            b = self.pool.run(False, compile_only=True, new_session=True)['session_id']
            self.wait(lambda: self.pool.target(b).session['phase'] == 'queued')
            self.assertTrue(self.pool.stop(b, False)['cleaned'])
            self.assertTrue(self.pool.target(a).session['directory'].exists())
            self.assertFalse(self.pool.target(a).session['cancel'].is_set())
            launch.assert_called_once()

    def test_readiness_releases_slot_while_runners_stay_alive_and_restart_queues(self):
        processes = [fixtures.FakeProcess() for _ in range(3)]
        with patch('morx_gms2_mcp.runtime.subprocess.Popen', side_effect=processes):
            a = self.pool.run(False)['session_id']
            self.wait(lambda: self.pool.target(a).session['preparation_done'].is_set())
            ra = self.pool.target(a)
            with patch.object(ra, '_ready', return_value=True):
                b = self.pool.run(False, new_session=True)['session_id']
                self.wait(lambda: self.pool.target(b).session['preparation_done'].is_set())
            self.assertIsNone(processes[0].poll())
            self.assertIsNone(processes[1].poll())
            marker = ra.session['save_directory'] / 'probe.txt'
            marker.write_text('A save')
            restarted = self.pool.restart(a, False)
            self.assertEqual(restarted['state'], 'preparing')
            self.wait(lambda: ra.session['phase'] == 'queued')
            self.assertEqual(marker.read_text(), 'A save')
            with patch.object(self.pool.target(b), '_ready', return_value=True):
                self.wait(lambda: ra.session['preparation_done'].is_set())
            self.assertIs(ra.process, processes[2])
            self.assertEqual(marker.read_text(), 'A save')

    def test_preparation_failure_does_not_stall_other_session(self):
        with patch('morx_gms2_mcp.runtime.subprocess.Popen', side_effect=[OSError('failure'), fixtures.FakeProcess()]):
            a = self.pool.run(False, compile_only=True)['session_id']
            self.wait(lambda: self.pool.target(a).session['preparation_done'].is_set())
            b = self.pool.run(False, compile_only=True, new_session=True)['session_id']
            self.wait(lambda: self.pool.target(b).session['preparation_done'].is_set())
            self.assertEqual(self.pool.status(session_id=a)['state'], 'failed')
            self.assertEqual(self.pool.status(session_id=b)['preparation']['phase'], 'launched')
            self.assertTrue(self.pool.stop(a, False)['cleaned'])
            self.assertTrue(self.pool.target(b).session['directory'].exists())

    def test_independent_test_records_and_unknown_targets(self):
        with patch('morx_gms2_mcp.runtime.subprocess.Popen', return_value=fixtures.FakeProcess()):
            a = self.pool.run(False, compile_only=True)['session_id']
            b = self.pool.run(False, compile_only=True, new_session=True)['session_id']
            for sid, tid in [(a, 'a'*32), (b, 'b'*32)]:
                self.pool.target(sid).tests.record = {'test_id': tid, 'session_id': sid, 'status': 'passed', 'images': []}
            self.assertEqual(self.pool.test_result('a'*32)['session_id'], a)
            self.assertEqual(self.pool.test_result('b'*32)['session_id'], b)
            with self.assertRaises(ValueError):
                self.pool.test_result('missing')
            with self.assertRaises(ValueError):
                self.pool.output('missing')

    def test_mcp_snapshot_session_and_networking_interfaces(self):
        server = build_server(self.fixture.root, writable=True, runtime_options=self.fixture.options)
        async def call(name, args):
            result = await server.call_tool(name, args)
            return json.loads((result[0] if isinstance(result, tuple) else result)[0].text)
        async def check():
            info = await call('server_info', {})
            self.assertIn('AsyncNetworking', info['events'])
            self.assertIn('先按工具名发现', info['usage'])
            self.assertLessEqual(len(server.instructions), 80)
            self.assertIn('server_info', server.instructions)
            for tool in await server.list_tools():
                self.assertNotIn('先调用 server_info 确认绑定项目', tool.description)
            preview = await call('inspect_runtime_snapshot', {'limit': 0})
            self.assertGreater(preview['file_count'], 0)
            config = await call('configure_runtime_snapshot', {})
            self.assertTrue(config['dry_run'])
            self.assertEqual((await call('list_game_sessions', {}))['sessions'], [])
            self.assertTrue((await call('compile_game', {'new_session': True}))['dry_run'])
        asyncio.run(check())

    def test_mcp_waiting_for_a_does_not_block_b_status_or_stop(self):
        # 在真正的 FastMCP 调度层挂起 A；若同步阻塞事件循环，B 会等到释放后才执行。
        entered, release = threading.Event(), threading.Event()
        markers = []
        def request(sid, *args, **kwargs):
            entered.set()
            if not release.wait(3):
                markers.append('blocked_event_loop')
            raise ValueError('simulated uncertain response')
        def status(*args, **kwargs):
            markers.append('B status')
            return {'session_id':'b','state':'running','compiled':True,'exit_code':None}
        def stop(sid, *args):
            markers.append('B stop')
            return {'session_id':sid,'stopped':True,'cleaned':True}
        with patch('morx_gms2_mcp.server.RuntimePool', return_value=self.pool):
            server=build_server(self.fixture.root,runtime_options=self.fixture.options)
        async def verify():
            first=asyncio.create_task(server.call_tool('capture_game',{'session_id':'a'}))
            try:
                for _ in range(100):
                    if entered.is_set():break
                    await asyncio.sleep(.01)
                self.assertTrue(entered.is_set())
                await asyncio.wait_for(server.call_tool('runtime_status',{'session_id':'b'}),1)
                await asyncio.wait_for(server.call_tool('stop_game',{'session_id':'b','dry_run':False}),1)
                self.assertNotIn('blocked_event_loop',markers)
                self.assertEqual(markers,['B status','B stop'])
            finally:
                release.set()
                try:await first
                except Exception:pass
        with patch.object(self.pool,'request',side_effect=request),patch.object(self.pool,'status',side_effect=status),patch.object(self.pool,'stop',side_effect=stop):
            asyncio.run(verify())
