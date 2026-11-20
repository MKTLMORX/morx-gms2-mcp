"""受管命令的有界输出、取消与超时；不接受 shell 命令字符串。"""
import codecs
import os
import re
import subprocess
import threading
import time


class _ProcessTree:
    """Windows Job 跟随本次进程的后代，即使启动器先退出也能收尾。"""
    def __init__(self, process):
        self.handle = None
        if os.name != 'nt':
            return
        import ctypes
        from ctypes import wintypes
        class BasicLimits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t),
                        ('active', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                        ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', BasicLimits), ('io', IoCounters), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        handle = self.api.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handle = handle
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (not self.api.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                or not self.api.AssignProcessToJobObject(handle, int(process._handle))):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def stop_process(process, descendants=True):
    if process.poll() is not None:
        return
    if os.name == 'nt' and descendants:
        taskkill = os.path.join(os.environ.get('SystemRoot', 'C:/Windows'), 'System32/taskkill.exe')
        subprocess.run([taskkill, '/PID', str(process.pid), '/T', '/F'], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        process.terminate()
    try:
        process.wait(5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(5)


def run_command(arguments, timeout=30, env=None, cancel=None, on_line=None, on_started=None,
                capture_limit=1024 * 1024, private_values=(), cwd=None, manage_tree=True):
    if not isinstance(arguments, list) or not arguments or any(not isinstance(arg, str) or '\0' in arg for arg in arguments):
        raise ValueError('命令须为明确的参数列表，不能使用shell字符串')
    if cancel is not None and cancel.is_set():
        raise InterruptedError('任务已取消')
    process = subprocess.Popen(arguments, shell=False, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        tree = _ProcessTree(process) if manage_tree else None
    except Exception:
        stop_process(process)
        process.stdout.close()
        raise
    output = bytearray()
    overflow = threading.Event()
    reader_errors = []
    def emit(line):
        if on_line is None:
            return
        for value in private_values:
            if value:
                line = line.replace(value, '[private value omitted]')
        line = re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '[account identifier omitted]', line)
        if re.search(r'password|passwd|token|credential|licen[cs]e|authorization|access.?key', line, re.I):
            line = '[authorization failure]' if re.search(r'(?i)licen[cs]e.*(?:expired|invalid|not valid|not found|missing)|not licen[cs]ed|login required', line) else '[credential-related output omitted]'
        on_line(line[:16384])
    def collect():
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        line = ''
        dropping = False
        try:
            while True:
                chunk = process.stdout.read1(8192)
                if capture_limit:
                    remaining = capture_limit - len(output)
                    output.extend(chunk[:max(0, remaining)])
                    if len(chunk) > remaining:
                        overflow.set()
                text = decoder.decode(chunk, final=not chunk)
                parts = text.split('\n')
                for index, part in enumerate(parts):
                    if not dropping:
                        line += part
                    if index < len(parts) - 1 or not chunk:
                        if dropping:
                            emit('[overlong output line omitted]')
                        elif line:
                            emit(line.rstrip('\r'))
                        line = ''
                        dropping = False
                    elif len(line) > 16384:
                        # 不吐出可能含未完成口令的长行片段；整行只留标记。
                        line = ''
                        dropping = True
                if not chunk:
                    break
        except Exception as exc:
            reader_errors.append(type(exc).__name__)
        finally:
            process.stdout.close()
    thread = threading.Thread(target=collect, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    try:
        if on_started:
            on_started(process)
        while process.poll() is None:
            if cancel is not None and cancel.is_set():
                raise InterruptedError('任务已取消')
            if time.monotonic() >= deadline:
                raise TimeoutError('受管命令超过本任务截止时间')
            if overflow.is_set():
                raise ValueError('命令输出超过大小限制')
            time.sleep(.025)
        # Igor/gradlew退出时，Gradle守护进程可能仍持有同一个stdout句柄。
        # 关闭本任务Job，不按名称清理系统中其他Java/Gradle进程。
        if tree: tree.close()
        thread.join(5)
        if thread.is_alive() or reader_errors:
            raise ValueError('受管命令输出未完整关闭')
        if overflow.is_set():
            raise ValueError('命令输出超过大小限制')
        text = bytes(output).decode('utf-8-sig', errors='replace')
        for value in private_values:
            if value:
                text = text.replace(value, '[private value omitted]')
        return {'exit_code': process.returncode, 'output': text}
    finally:
        try:
            if tree: tree.close()
            if process.poll() is None:
                stop_process(process, descendants=manage_tree)
        finally:
            thread.join(5)
