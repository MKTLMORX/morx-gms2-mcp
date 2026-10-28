"""阻塞准备阶段，确认会话可查询、取消且不会重复或延迟启动。"""
import threading
import time
import unittest
from unittest.mock import patch
from tests import test_runtime as fixtures


class PreparationTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.RuntimeTests('test_stop_preview_and_owned_process_only')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.runtime = fixture.runtime
        self.project = fixture.project

    def test_slow_validation_returns_session_and_cancellation_prevents_launch(self):
        entered, release = threading.Event(), threading.Event()
        def validate():
            entered.set()
            release.wait(5)
            return {'ok': True}
        with patch.object(self.project, 'validate', side_effect=validate), \
                patch('morx_gms2_mcp.runtime.subprocess.Popen') as launch:
            start = time.monotonic()
            result = self.runtime.run(False, compile_only=True)
            self.assertLess(time.monotonic() - start, 1)
            self.assertTrue(entered.wait(2))
            try:
                status = self.runtime.status()
                self.assertEqual(status['session_id'], result['session_id'])
                self.assertEqual(status['state'], 'preparing')
                self.assertEqual(status['preparation']['phase'], 'validating')
                self.assertFalse(status['compiled'])
                with self.assertRaises(ValueError):
                    self.runtime.run(False, compile_only=True)
                stopped = self.runtime.stop(result['session_id'], False)
                self.assertFalse(stopped['stopped'])
                self.assertEqual(stopped['reason'], 'preparation_in_progress')
                with self.assertRaises(ValueError):
                    self.runtime.restart(result['session_id'], False)
            finally:
                release.set()
                self.assertTrue(self.runtime.session['preparation_done'].wait(5))
            launch.assert_not_called()
            self.assertEqual(self.runtime.status()['build_status'], 'cancelled')
            self.assertTrue(self.runtime.stop(result['session_id'], False)['cleaned'])

    def test_cancel_during_copy_keeps_directory_until_worker_stops(self):
        entered, release = threading.Event(), threading.Event()
        original = self.runtime._copy_project
        def copy(destination, name, key, session):
            (destination / 'partial').write_bytes(b'partial')
            entered.set()
            release.wait(5)
            return original(destination, name, key, session)
        with patch.object(self.runtime, '_copy_project', side_effect=copy), \
                patch('morx_gms2_mcp.runtime.subprocess.Popen') as launch:
            result = self.runtime.run(False, compile_only=True)
            self.assertTrue(entered.wait(2))
            try:
                self.assertEqual(self.runtime.status()['preparation']['phase'], 'copying')
                stopped = self.runtime.stop(result['session_id'], False)
                self.assertFalse(stopped['cleaned'])
                self.assertTrue(self.runtime.session['directory'].exists())
            finally:
                release.set()
                self.assertTrue(self.runtime.session['preparation_done'].wait(5))
            launch.assert_not_called()
            self.assertTrue(self.runtime.stop(result['session_id'], False)['cleaned'])

    def test_prepare_failures_are_queryable_and_require_stop(self):
        for stage in ('validate', 'copy', 'launch'):
            with self.subTest(stage=stage):
                target = self.project if stage == 'validate' else self.runtime
                method = 'validate' if stage == 'validate' else '_copy_project'
                process = fixtures.FakeProcess()
                with patch('morx_gms2_mcp.runtime.subprocess.Popen',
                           side_effect=OSError('launch failed') if stage == 'launch' else None,
                           return_value=process), \
                        patch.object(target, method, side_effect=ValueError('prepare failed')) if stage != 'launch' else \
                        patch.object(self.runtime, '_log', wraps=self.runtime._log):
                    result = self.runtime.run(False, compile_only=True)
                    self.assertTrue(self.runtime.session['preparation_done'].wait(5))
                status = self.runtime.status()
                self.assertEqual(status['state'], 'failed')
                self.assertEqual(status['build_status'], 'failed')
                self.assertFalse(status['compiled'])
                self.assertIn('message', status['preparation']['preparation_error'])
                with self.assertRaises(ValueError):
                    self.runtime.run(False, compile_only=True)
                self.assertTrue(self.runtime.stop(result['session_id'], False)['cleaned'])

    def test_success_reports_copy_progress_and_launches_once(self):
        with patch('morx_gms2_mcp.runtime.subprocess.Popen', return_value=fixtures.FakeProcess()) as launch:
            result = self.runtime.run(False, compile_only=True)
            self.assertTrue(self.runtime.session['preparation_done'].wait(5))
            launch.assert_called_once()
        status = self.runtime.status()
        self.assertEqual(status['preparation']['phase'], 'launched')
        self.assertGreater(status['preparation']['copied_files'], 0)
        self.assertGreater(status['preparation']['copied_bytes'], 0)
        self.assertTrue(self.runtime.stop(result['session_id'], False)['cleaned'])
