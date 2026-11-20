"""用真实自建Python子进程验证输出边界、取消、超时及收尾，无桌面输入。"""
import os
import secrets
import sys
import threading
import unittest
from pathlib import Path
import tempfile
import time

from morx_gms2_mcp.android_process import run_command


class ProcessTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows Job 对后代进程及继承管道的收尾')
    def test_parent_exit_reaps_owned_descendant_and_closes_inherited_output(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'child-completed'
            child = 'import time;from pathlib import Path;time.sleep(2);Path(' + repr(str(marker)) + ').touch()'
            parent = ('import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",' + repr(child) + ']);'
                      'print("parent done",flush=True);time.sleep(.2)')
            result = run_command([sys.executable, '-c', parent], timeout=8)
            self.assertEqual(result['exit_code'], 0)
            self.assertIn('parent done', result['output'])
            time.sleep(2.1)
            self.assertFalse(marker.exists())

    def test_unicode_capture_and_argument_array(self):
        result = run_command([sys.executable, '-c', 'import sys;sys.stdout.buffer.write("中文 café".encode("utf-8"))'])
        self.assertEqual(result, {'exit_code': 0, 'output': '中文 café'})
        with self.assertRaises(ValueError):
            run_command('python -c bad')

    def test_sensitive_lines_private_values_and_overlong_tail_are_not_logged(self):
        private = secrets.token_hex(20)
        lines = []
        code = ('import os; value=os.environ["MORX_TEST_VALUE"];'
                'print("result "+value);print("password="+value);print("x"*20000+value)')
        result = run_command([sys.executable, '-c', code], env={**os.environ, 'MORX_TEST_VALUE': private},
                             on_line=lines.append, private_values=(private,))
        self.assertNotIn(private, str(lines))
        self.assertNotIn(private, result['output'])
        self.assertIn('[credential-related output omitted]', lines)
        self.assertIn('[overlong output line omitted]', lines)

    def test_timeout_and_cancel_stop_only_the_started_process(self):
        owned = []
        with self.assertRaises(TimeoutError):
            run_command([sys.executable, '-c', 'import time;time.sleep(30)'], timeout=.15, on_started=owned.append)
        self.assertIsNotNone(owned[-1].poll())
        cancel = threading.Event()
        timer = threading.Timer(.15, cancel.set); timer.start()
        try:
            with self.assertRaises(InterruptedError):
                run_command([sys.executable, '-c', 'import time;time.sleep(30)'], cancel=cancel, on_started=owned.append)
        finally:
            timer.join()
        self.assertIsNotNone(owned[-1].poll())

    def test_output_limit_and_streaming_mode_are_bounded(self):
        owned = []
        with self.assertRaisesRegex(ValueError, '输出超过'):
            run_command([sys.executable, '-c', 'print("x"*100000)'], capture_limit=1024, on_started=owned.append)
        self.assertIsNotNone(owned[-1].poll())
        count = []
        result = run_command([sys.executable, '-c', 'print("x"*100000)'], capture_limit=0, on_line=count.append)
        self.assertEqual(result['output'], '')
        self.assertEqual(count, ['[overlong output line omitted]'])
