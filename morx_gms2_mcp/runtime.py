"""固定项目的隔离构建、受管理进程与游戏内文件桥接。无桌面抓取或外部监听。"""
from collections import deque
from dataclasses import dataclass
import codecs
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
import time
from uuid import uuid4

from PIL import Image
from .display import integer
from .resources import name_check, trigger_spec, replace_refs
from .storage import compact_encode, encode, parse_yy, replace_retry

CREDENTIAL_OUTPUT = re.compile(
    r'(?i)licen[cs]e|access.?key|api.?key|secret|token|password|authorization|authentication')


@dataclass(frozen=True)
class RuntimeOptions:
    enabled: bool = False
    runtime_root: str | None = None
    user_directory: str | None = None
    sandbox_root: str | None = None


def link_free(path):
    """拒绝符号链接/目录联接；按 reparse point 判断，避免 Windows 短路径被 resolve() 误判。"""
    current = Path(path)
    while True:
        try:
            info = os.lstat(current)
        except OSError:
            pass
        else:
            if stat.S_ISLNK(info.st_mode):
                return False
            if os.name == 'nt':
                attributes = getattr(info, 'st_file_attributes', 0)
                if attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
                    return False
        parent = current.parent
        if parent == current:
            return True
        current = parent


def bounded_json(path, maximum=1024 * 1024):
    if not link_free(path):
        raise ValueError('运行文件不能是链接或目录联接')
    if path.stat().st_size > maximum:
        raise ValueError('运行响应超过大小限制')
    result = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(result, dict):
        raise ValueError('运行响应须为 JSON 对象')
    return result


def atomic_json(path, data):
    if not link_free(path):
        raise ValueError('运行文件不能是链接或目录联接')
    temp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as output:
            output.write(compact_encode(data))
        replace_retry(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def child_path(root, name):
    path = root / name
    if not link_free(path):
        raise ValueError('运行目录不能包含链接或目录联接')
    return path


KEY_NAMES = {'backspace': 8, 'tab': 9, 'enter': 13, 'escape': 27, 'space': 32,
             'pageup': 33, 'pagedown': 34, 'end': 35, 'home': 36,
             'left': 37, 'up': 38, 'right': 39, 'down': 40, 'insert': 45, 'delete': 46}


def instance_ref(value):
    """接受实例编号，或 inspect_game 返回的 instance(100002)／@ref instance(100002)。"""
    if type(value) is int:
        result = value
    elif isinstance(value, str):
        match = re.fullmatch(r'(?:@ref\s+)?instance\((\d+)\)', value.strip())
        result = int(match.group(1)) if match else int(value) if value.isdigit() else None
    else:
        result = None
    if result is not None and 0 < result <= 2**63 - 1:
        return result
    raise ValueError('instance_id 须为正整数或 inspect_game 返回的 instance(...) 标识')


def key_code(key):
    """把单个按键名称或 1～255 的键码归一为 Windows 虚拟键码。"""
    if type(key) is int:
        if not 1 <= key <= 255:
            raise ValueError('键码须为 1～255')
        return key
    if not isinstance(key, str):
        raise ValueError('按键须为名称或单个英文字母/数字')
    name = key.lower()
    if name in KEY_NAMES:
        return KEY_NAMES[name]
    if re.fullmatch('[a-z0-9]', name):
        return ord(name.upper())
    raise ValueError('仅支持字母、数字及 ' + '/'.join(KEY_NAMES))


def key_codes(keys):
    if not isinstance(keys, list) or not 1 <= len(keys) <= 8:
        raise ValueError('keys 须包含 1～8 个按键')
    values = []
    for key in keys:
        value = key_code(key)
        if value in values:
            raise ValueError('按键不能重复')
        values.append(value)
    return values


def bridge_io_summary(data):
    """只解释有界文件诊断，不从累计重试数推断命令失败，也不返回任意路径。"""
    if not isinstance(data, dict):
        return {'available': False, 'state': 'unavailable'}

    def count(value, minimum=0):
        if type(value) not in (int, float) or not minimum <= value <= 2**53 - 1 or int(value) != value:
            return None
        return int(value)

    pending = count(data.get('pending_writes'))
    retries = count(data.get('retry_attempts', data.get('write_failures')))
    if pending is None or pending > 3 or retries is None:
        return {'available': False, 'state': 'unknown', 'reason': 'invalid_diagnostics'}
    detailed = 'retry_recoveries' in data
    recoveries = count(data.get('retry_recoveries')) if detailed else None
    stalled = count(data.get('stalled_writes')) if detailed else None
    if detailed and (recoveries is None or recoveries > retries or stalled is None or stalled > pending):
        return {'available': False, 'state': 'unknown', 'reason': 'invalid_diagnostics'}
    state = 'stalled' if stalled else 'retrying' if pending else \
        'recovered' if recoveries else 'unknown' if retries else 'healthy'
    files = []
    rows = data.get('files', [])
    if isinstance(rows, list):
        seen = set()
        for row in rows[:3]:
            if not isinstance(row, dict) or not isinstance(row.get('file'), str) or row['file'] not in {'ready.json', 'response.json', 'errors.json'}:
                continue
            if row['file'] in seen:
                continue
            seen.add(row['file'])
            clean = {'file': row['file']}
            for field in ('pending_age_ms', 'retry_attempts', 'current_retry_attempts', 'recovery_count', 'last_recovery_attempts',
                          'first_failure_frame', 'last_failure_frame', 'last_recovery_frame'):
                clean[field] = count(row.get(field), -1 if field.endswith('_frame') else 0)
            clean['pending'] = row.get('pending') if type(row.get('pending')) is bool else None
            state_name = row.get('state')
            clean['state'] = state_name if isinstance(state_name, str) and state_name in {'healthy', 'recovered', 'retrying', 'stalled'} else 'unknown'
            stage = row.get('last_failure_stage')
            clean['last_failure_stage'] = stage if isinstance(stage, str) and stage in {'', 'replace_destination', 'promote_temporary', 'temporary_file_missing'} else 'unknown'
            for field in ('request', 'last_failure_request', 'last_recovery_request'):
                request = row.get(field)
                clean[field] = request if isinstance(request, str) and re.fullmatch('[a-f0-9]{32}', request) else None
            files.append(clean)
    return {'available': True, 'detail_available': detailed, 'state': state,
            'pending_writes': pending, 'stalled_writes': stalled,
            'retry_attempts': retries, 'retry_recoveries': recoveries,
            'write_failures': retries, 'write_failures_semantics': 'retry_attempts_not_final_failures',
            'sample_frame': count(data.get('sample_frame')), 'stall_threshold_ms': 10000, 'files': files}


class Runtime:
    def __init__(self, project, options=None):
        self.project = project
        self.options = options or RuntimeOptions()
        self.guard = threading.RLock()
        self.mouse_guard = threading.Lock()
        self.interaction_guard = threading.RLock()
        self.session = None
        self.process = None
        self.pending = None
        self.lines = deque(maxlen=1000)
        self.sequence = 0
        self.timer = None
        self.redactions = []
        self.heartbeat = None
        self.pending_since = None
        self.build_diagnostics = deque(maxlen=100)
        self.stop_guard = threading.RLock()
        from .scenarios import SceneTests
        self.tests = SceneTests(self)
        self.test_deadline = None
        self.build_gate = None

    def _test_access(self):
        if self.tests.active:
            if threading.current_thread() is not self.tests.worker:
                raise ValueError('场景测试正在运行；只允许查询状态或 stop_game，避免交叉输入')
            if self.test_deadline is not None and time.monotonic() >= self.test_deadline:
                raise ValueError('场景测试时限已到')

    def _state(self, ready=False, code=None):
        if not self.session:
            return 'idle'
        if self.session.get('stopping'):
            return 'stopping'
        if self.session.get('stopped'):
            return 'stopped'
        if self.session.get('preparation_error'):
            return 'failed'
        if self.session.get('phase') in ('queued', 'preparing', 'validating', 'copying'):
            return 'preparing'
        return 'running' if ready else 'exited' if code is not None else 'building_or_starting'

    def _phase(self, session, phase):
        with self.guard:
            session['phase'] = phase
            session['phase_started'] = time.monotonic()

    @staticmethod
    def _check_cancel(session):
        if session and session.get('cancel') and session['cancel'].is_set():
            raise InterruptedError('启动准备已取消')

    def _bridge(self):
        return self.project.bridge_key()

    def _configuration(self):
        if not self.options.enabled:
            raise ValueError('运行能力未启用；需以 --allow-run 启动服务')
        if not self.options.runtime_root or not self.options.user_directory:
            raise ValueError('运行需要 --runtime-root 和 --user-directory 本机配置')
        root = Path(self.options.runtime_root).resolve(strict=True)
        user = Path(self.options.user_directory).resolve(strict=True)
        igor = root / 'bin/igor/windows/x64/Igor.exe'
        if not igor.is_file() or not user.is_dir() or not (user / 'licence.plist').is_file():
            raise ValueError('须配置已安装的 Windows x64 Runtime 和已登录的用户目录；服务不读取许可证内容')
        base = self.options.sandbox_root or os.environ.get('LOCALAPPDATA')
        if not base:
            raise ValueError('运行需要 Windows LOCALAPPDATA 目录')
        sandbox = Path(base).resolve(strict=True)
        if any(path.is_relative_to(self.project.root) for path in (root, user, sandbox)):
            raise ValueError('Runtime、用户配置及存档根目录须位于项目目录之外')
        return root, user, igor, sandbox

    def _require_session(self, session_id):
        if not self.options.enabled:
            raise ValueError('运行能力未启用')
        if not self.session or self.session['id'] != session_id:
            raise ValueError('运行会话不匹配；先查询 runtime_status')
        return self.session

    def _file(self, name):
        return child_path(self.session['bridge_directory'], name)

    def _readiness(self):
        if not self.session or self.session.get('stopped'):
            return {'ready': False, 'reason': 'no_active_session'}
        if self.session.get('compile_only'):
            return {'ready': False, 'reason': 'compile_only'}
        if self.process is not None and self.process.poll() is not None:
            return {'ready': False, 'reason': 'process_exited'}
        reason = 'heartbeat_missing'
        for attempt in range(3):
            try:
                path = self._file('ready.json')
                data = bounded_json(path)
                if data.get('session') != self.session['id'] or data.get('bridge') != self.session['bridge']:
                    return {'ready': False, 'reason': 'heartbeat_identity_mismatch'}
                self.heartbeat = {'mtime': path.stat().st_mtime, 'frame': data.get('frame'),
                                  'bridge_io': bridge_io_summary(data.get('bridge_io'))}
                reason = 'heartbeat_received'
                break
            except (OSError, json.JSONDecodeError, UnicodeError) as exc:
                reason = 'heartbeat_missing' if isinstance(exc, FileNotFoundError) else 'heartbeat_unreadable'
                if attempt < 2:
                    time.sleep(.01)
            except ValueError:
                return {'ready': False, 'reason': 'heartbeat_invalid'}
        if self.heartbeat:
            age = max(0, time.time() - self.heartbeat['mtime'])
            return {'ready': age < 10, 'reason': 'heartbeat_stale' if age >= 10 else reason,
                    'heartbeat_age_seconds': round(age, 3), 'heartbeat_frame': self.heartbeat['frame'],
                    'cached': reason != 'heartbeat_received'}
        return {'ready': False, 'reason': reason, 'heartbeat_age_seconds': None}

    def _ready(self):
        return self._readiness()['ready']

    def _io_status(self, current=False):
        heartbeat = self.heartbeat or {}
        return {**heartbeat.get('bridge_io', {'available': False, 'state': 'unavailable'}),
                'source': 'heartbeat', 'current': current,
                'sample_age_seconds': round(max(0, time.time() - heartbeat['mtime']), 3) if 'mtime' in heartbeat else None}

    def _redact(self, text):
        for value in sorted(self.redactions, key=len, reverse=True):
            if not value:
                continue
            for variant in {value, value.replace('\\', '/')}:
                text = re.sub(re.escape(variant), '[local-path]', text, flags=re.IGNORECASE)
        return text

    def _response(self, request_id):
        for name in ('response.json', 'response.json.tmp'):
            try:
                data = bounded_json(self._file(name), 4 * 1024 * 1024)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if data.get('session') != self.session['id'] or data.get('request') != request_id:
                continue
            if type(data.get('ok')) is not bool or not isinstance(data.get('data'), dict):
                raise ValueError('运行响应格式不正确')
            if name.endswith('.tmp'):
                # 完整且身份匹配的回执已证明执行完成；不重新发送命令，也不移动仍可能被游戏打开的文件。
                data['response_source'] = 'completed_temporary_file'
            return data
        return None

    def _build_status(self, ready=False):
        code = self.process.poll() if self.process is not None else None
        status = 'idle' if not self.session else 'succeeded' if ready or self.session.get('build_succeeded') else \
            'failed' if code is not None and code != 0 else \
            'succeeded' if code == 0 and self.session.get('compile_only') else \
            'unverified' if code == 0 or self.session.get('stopped') else 'building'
        if self.session and self.session.get('preparation_error'):
            status = 'failed'
        elif self.session and self.session.get('phase') == 'cancelled':
            status = 'cancelled'
        elif self.session and self.session.get('phase') in ('queued', 'preparing', 'validating', 'copying'):
            status = 'preparing'
        return {'build_status': status, 'compile_error_count': len(self.build_diagnostics),
                'build_diagnostics': list(self.build_diagnostics), 'compiled': status == 'succeeded'}

    def _errors(self):
        """读取桥接捕获的运行时异常；文件不存在、过期或损坏时返回空列表。"""
        try:
            data = bounded_json(self._file('errors.json'), 2 * 1024 * 1024)
        except (OSError, ValueError, UnicodeError):
            return []
        if not self.session or data.get('session') != self.session['id']:
            return []
        items = data.get('errors')
        if not isinstance(items, list):
            return []
        rows = []
        for item in items[:20]:
            if not isinstance(item, dict):
                continue
            row = {}
            for key in ('frame', 'line'):
                value = item.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    row[key] = value
            for key in ('stage', 'message', 'long_message', 'script'):
                value = item.get(key)
                if isinstance(value, str):
                    row[key] = self._redact(value[:4096])
            stack = item.get('stacktrace')
            if isinstance(stack, list):
                row['stacktrace'] = [self._redact(value[:1024]) for value in stack[:32]
                                     if isinstance(value, str)]
            rows.append(row)
        return rows

    def errors(self, session_id, after=0, limit=20):
        integer(after, 'after', 0, 2**63 - 1)
        integer(limit, 'limit', 1, 50)
        with self.guard:
            self._require_session(session_id)
            rows = self._errors()
            selected = [{'index': index, **row} for index, row in enumerate(rows, 1) if index > after][:limit]
            return {'session_id': session_id, 'errors': selected, 'count': len(selected),
                    'total': len(rows), 'next': selected[-1]['index'] if selected else after,
                    'exit_code': self.process.poll() if self.process is not None else None,
                    'state': self._state(self._ready(), self.process.poll() if self.process else None)}

    def status(self, wait_seconds=0):
        integer(wait_seconds, 'wait_seconds', 0, 10)
        deadline = time.monotonic() + wait_seconds
        while True:
            with self.guard:
                readiness = self._readiness()
                ready = readiness['ready']
                code = self.process.poll() if self.process is not None else None
                result = {'enabled': self.options.enabled, 'configured': bool(self.options.runtime_root and self.options.user_directory),
                    'session_id': self.session['id'] if self.session else None,
                    'bridge_ready': ready, 'exit_code': code,
                    'state': self._state(ready, code)}
                result['build_pid'] = self.process.pid if self.process is not None else None
                result['runner_pid'] = None
                if ready and self.process is not None and os.name == 'nt':
                    try:
                        from .win_mouse import find_game_window
                        window = find_game_window(self.process.pid, 'MORX_Run_' + self.session['id'])
                        result.update(runner_pid=window.pid, window_handle=window.handle)
                    except (ValueError, OSError):
                        result['window_unavailable'] = True
                result['source_snapshot_sha256'] = self.session.get('source_snapshot_sha256') if self.session else None
                result['bridge_diagnostics'] = readiness
                result['bridge_io'] = self._io_status(ready)
                result.update(self._build_status(ready))
                if self.session:
                    result['preparation'] = {key: self.session[key] for key in
                        ('phase', 'copied_files', 'copied_bytes', 'snapshot', 'preparation_error') if key in self.session}
                    if 'phase_started' in self.session:
                        result['preparation']['phase_elapsed_seconds'] = round(
                            time.monotonic() - self.session['phase_started'], 3)
                    if ready:
                        self.session['build_succeeded'] = True
                    result['error_count'] = len(self._errors())
                if self.pending:
                    result['pending_request'] = self.pending
                    result['pending_age_seconds'] = round(time.monotonic() - self.pending_since, 3) if self.pending_since else None
                    try:
                        late = self._response(self.pending)
                    except ValueError as exc:
                        result['response_error'] = self._redact(str(exc))
                        self.pending = None
                    else:
                        if late:
                            result['completed_request'] = late
                            self.pending = None
                            result.pop('pending_request', None)
                            result.pop('pending_age_seconds', None)
                            if late.get('response_source'):
                                result['response_recovery'] = late['response_source']
                if ready or code is not None or result['state'] in ('failed', 'stopped', 'stopping') or time.monotonic() >= deadline:
                    return result
            time.sleep(0.05)

    def _copy_project(self, destination, run_name, key, session=None):
        from .snapshots import plan
        manifest = []
        with self.project.lock:
            selection = plan(self.project, check_cancel=lambda: self._check_cancel(session))
            if session is not None:
                with self.guard:
                    session['snapshot'] = {k: v for k, v in selection.items() if k != 'files'}
            # 提前检查源副本和基础构建余量，最终构建产物大小无法由源码精确预测。
            needed = selection['total_bytes'] + 256 * 1024**2
            if shutil.disk_usage(destination).free < needed:
                raise ValueError('磁盘空间不足：快照及基础余量需要 ' + str(needed) + ' 字节')
            total = 0
            for count, (relative, planned_size) in enumerate(sorted(selection['files'].items()), 1):
                self._check_cancel(session)
                source = self.project.path(relative)
                before = source.stat()
                if before.st_size != planned_size:
                    raise ValueError('复制前源文件大小已改变：' + relative)
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                file_hash = hashlib.sha256()
                copied = 0
                with source.open('rb') as src, target.open('xb') as dst:
                    while True:
                        self._check_cancel(session)
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        copied += len(chunk)
                        total += len(chunk)
                        if copied > planned_size or total > 2 * 1024**3:
                            raise ValueError('复制时源文件大小已改变或超过 2 GiB：' + relative)
                        dst.write(chunk)
                        file_hash.update(chunk)
                        if session is not None:
                            with self.guard:
                                session['copied_bytes'] += len(chunk)
                after = source.stat()
                if copied != planned_size or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError('复制期间源文件被修改：' + relative)
                if session is not None:
                    with self.guard:
                        session['copied_files'] = count
                manifest.append((relative, file_hash.hexdigest()))
            if session is not None:
                session['source_snapshot_sha256'] = hashlib.sha256(
                    compact_encode(sorted(manifest))).hexdigest()
            # GameMaker 模板允许资源直接挂在工程根；副本改名时这些引用也必须更新。
            meta = parse_yy((destination / self.project.yyp.name).read_text(encoding='utf-8-sig'))
            for resource in meta.get('resources', []):
                self._check_cancel(session)
                path = destination / resource['id']['path']
                data = parse_yy(path.read_text(encoding='utf-8-sig'))
                if replace_refs(data, self.project.yyp.stem, self.project.yyp.name,
                                run_name, run_name + '.yyp'):
                    path.write_bytes(encode(data))
            replace_refs(meta, self.project.yyp.stem, self.project.yyp.name, run_name, run_name + '.yyp')
            meta.update(name=run_name, **{'%Name': run_name})
            (destination / self.project.yyp.name).unlink()
            yyp = destination / (run_name + '.yyp')
            yyp.write_bytes(encode(meta))
            order = destination / self.project.yyp.with_suffix('.resource_order').name
            if order.exists():
                order.rename(destination / (run_name + '.resource_order'))
            windows_path = destination / 'options/windows/options_windows.yy'
            windows = parse_yy(windows_path.read_text(encoding='utf-8-sig'))
            if windows.get('$GMWindowsOptions') != 'v2':
                raise ValueError('运行仅支持 Windows v2 配置')
            windows['option_windows_executable_name'] = run_name + '.exe'
            windows['option_windows_save_location'] = 0
            windows['option_windows_disable_sandbox'] = False
            windows_path.write_bytes(encode(windows))
            main_path = destination / 'options/main/options_main.yy'
            main = parse_yy(main_path.read_text(encoding='utf-8-sig'))
            main['option_gameguid'] = str(uuid4())
            main_path.write_bytes(encode(main))
        return yyp

    def _cleanup(self, session):
        """删除本服务创建的构建副本和存档目录；只操作名称与本次会话匹配的路径。"""
        session_id = session.get('id')
        results = []
        for target, path, expected in (
                ('project_copy', session.get('directory'), session_id),
                ('save_directory', session.get('save_directory'), 'MORX_Run_' + str(session_id))):
            item = {'target': target, 'cleaned': False}
            results.append(item)
            if not path:
                item['reason'] = 'missing_session_path'
                continue
            path = Path(path)
            if path.name != expected or not link_free(path):
                item['reason'] = 'unsafe_path'
                continue
            try:
                path.lstat()
            except FileNotFoundError:
                item.update(cleaned=True, reason='already_absent')
                continue
            except OSError as exc:
                item.update(reason='inspection_failed', error_type=type(exc).__name__)
                continue
            try:
                shutil.rmtree(path)
            except OSError as exc:
                item.update(reason='delete_failed', error_type=type(exc).__name__)
            try:
                path.lstat()
            except FileNotFoundError:
                item.update(cleaned=True, reason='removed')
            except OSError as exc:
                item.update(reason='inspection_failed', error_type=type(exc).__name__)
            else:
                item.setdefault('reason', 'directory_remains')
        work_root = session.get('directory')
        if work_root and results[0]['cleaned']:
            try:
                Path(work_root).parent.rmdir()
            except OSError:
                pass
        return {'cleaned': all(item['cleaned'] for item in results), 'cleanup': results}

    def run(self, dry_run=True, compile_only=False):
        with self.guard:
            if self.tests.active:
                raise ValueError('场景测试尚未结束，请先查询 game_test_status')
            root, user, igor, sandbox = self._configuration()
            if self.session and (not self.session.get('stopped') or self.session.get('stopping')):
                raise ValueError('已有运行会话，请先 stop_game；即使进程已退出也先读取日志')
            if self.session and self.session.get('cleanup_failed'):
                raise ValueError('上次会话目录尚未清理，请再次 stop_game 后重试')
            preview = {'dry_run': dry_run, 'project': str(self.project.yyp), 'target': 'Windows VM',
                       'isolated_project': True, 'isolated_save_directory': True,
                       'compile_only': compile_only}
            if dry_run:
                if not compile_only:
                    self._bridge()
                if not self.project.validate()['ok']:
                    raise ValueError('项目结构检查失败，先调用 inspect_project')
                from .snapshots import inspect as inspect_snapshot
                preview['snapshot'] = inspect_snapshot(self.project, limit=0)
                return preview
            session_id = uuid4().hex
            run_name = 'MORX_Run_' + session_id
            directory = child_path(sandbox, '_morx_runs') / session_id
            savedir = child_path(sandbox, run_name)
            session = {'id': session_id, 'bridge': None, 'bridge_directory': savedir / 'morx_mcp',
                'directory': directory, 'save_directory': savedir, 'stopped': False,
                'compile_only': compile_only, 'phase': 'preparing', 'phase_started': time.monotonic(),
                'copied_files': 0, 'copied_bytes': 0, 'cancel': threading.Event(),
                'preparation_done': threading.Event()}
            self.redactions = [str(user), str(root), str(self.project.root),
                               str(sandbox), str(savedir), str(Path.home())]
            self.session, self.process, self.pending = session, None, None
            self.heartbeat = self.pending_since = None
            self.build_diagnostics.clear()
            self.lines.clear()
            self.sequence = 0
            worker = threading.Thread(target=self._prepare, args=(session, root, user, igor), daemon=True)
            session['worker'] = worker
            self.timer = threading.Timer(900, self._expire, args=(session_id,))
            self.timer.daemon = True
            try:
                worker.start()
                self.timer.start()
            except Exception:
                session['cancel'].set()
                if worker.ident is None:
                    session['preparation_done'].set()
                    session['stopped'] = True
                raise
            return {**preview, 'session_id': session_id, 'state': 'preparing',
                'snapshot_project': str(directory / 'project' / (run_name + '.yyp')),
                'save_directory': str(savedir), 'compiled': False,
                'next': '查询 runtime_status 的 preparation、build_status 和 bridge_ready；勿重复启动'}

    def _acquire_build(self, session):
        if self.build_gate is None:
            return
        self._phase(session, 'queued')
        while not self.build_gate.acquire(timeout=0.1):
            self._check_cancel(session)
        try:
            self._check_cancel(session)
        except Exception:
            self.build_gate.release()
            raise

    def _hold_build(self, session):
        if self.build_gate is None:
            return
        try:
            # Igor Run 在游戏退出前可能不退出；桥接就绪说明构建已结束。
            process = self.process
            while process is not None and process.poll() is None:
                if not session['cancel'].is_set() and not session['compile_only'] and self._ready():
                    break
                time.sleep(0.1)
        finally:
            self.build_gate.release()

    def _prepare(self, session, root, user, igor):
        acquired = False
        try:
            self._acquire_build(session)
            acquired = True
            self._prepare_impl(session, root, user, igor)
        except InterruptedError:
            self._phase(session, 'cancelled')
        finally:
            session['preparation_done'].set()
            if acquired:
                self._hold_build(session)

    def _prepare_impl(self, session, root, user, igor):
        try:
            self._phase(session, 'validating')
            self._check_cancel(session)
            with self.project.lock:
                self._check_cancel(session)
                key = None if session['compile_only'] else self._bridge()
                if not self.project.validate()['ok']:
                    raise ValueError('项目结构检查失败，先调用 inspect_project')
                self._check_cancel(session)
                session['bridge'] = key
                directory = session['directory']
                directory.parent.mkdir(exist_ok=True)
                directory.mkdir(exist_ok=False)
                project_copy = directory / 'project'
                project_copy.mkdir()
                self._phase(session, 'copying')
                yyp = self._copy_project(project_copy, 'MORX_Run_' + session['id'], key, session)
            self._check_cancel(session)
            session['save_directory'].mkdir(exist_ok=False)
            session['bridge_directory'].mkdir()
            atomic_json(session['bridge_directory'] / 'session.json', {'session': session['id'], 'bridge': key})
            # Windows Run 自行选择输出包，不传 /of。
            args = [str(igor), '/uf=' + str(user), '/rp=' + str(root), '/project=' + str(yyp),
                '/cache=' + str(directory / 'cache'), '/temp=' + str(directory / 'temp'),
                '/runtime=VM', '--', 'Windows', 'Compile' if session['compile_only'] else 'Run']
            with self.guard:
                self._check_cancel(session)
                session['launch_args'] = args
                self.process = subprocess.Popen(args, cwd=directory, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                self._phase(session, 'launched')
                threading.Thread(target=self._collect, args=(self.process,), daemon=True).start()
        except InterruptedError:
            self._phase(session, 'cancelled')
        except Exception as exc:
            with self.guard:
                session['preparation_error'] = {'type': type(exc).__name__,
                                                'message': self._redact(str(exc)[:2048])}
                self._phase(session, 'failed')
        finally:
            session['preparation_done'].set()

    def _collect(self, process):
        buffer, tail = '', ''
        truncated = sensitive = False
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        try:
            while True:
                chunk = process.stdout.read1(4096)
                parts = decoder.decode(chunk, final=not chunk).split('\n')
                for index, part in enumerate(parts):
                    # 即使正文已截断，仍检查后续片段；尾部覆盖跨块的过滤标记。
                    sensitive = sensitive or bool(CREDENTIAL_OUTPUT.search(tail + part))
                    tail = (tail + part)[-32:]
                    remaining = 16384 - len(buffer)
                    buffer += part[:remaining]
                    truncated = truncated or len(part) > remaining
                    if index < len(parts) - 1 or (not chunk and (buffer or truncated)):
                        if sensitive:
                            line = '[credential-related output omitted]'
                        elif truncated:
                            marker = ' [line truncated]'
                            line = buffer[:16384 - len(marker)] + marker
                        else:
                            line = buffer
                        self._log(line, process)
                        buffer, tail = '', ''
                        truncated = sensitive = False
                if not chunk:
                    break
        finally:
            process.stdout.close()

    def _log(self, line, process=None):
        if CREDENTIAL_OUTPUT.search(line):
            line = '[credential-related output omitted]'
        else:
            line = self._redact(line)
        with self.guard:
            if process is not None and process is not self.process:
                return
            if re.search(r'(?i)\b(?:error|fatal)\b|\bat line \d+\s*:', line) and not re.search(
                    r'(?i)\b(?:no errors|0 errors|error_count|errors?\s*[:=]\s*0)\b', line):
                self.build_diagnostics.append(line[:2048])
            self.sequence += 1
            self.lines.append({'cursor': self.sequence, 'text': line.rstrip('\r')})

    def output(self, session_id, after=0, limit=100):
        integer(after, 'after', 0, 2**63 - 1)
        integer(limit, 'limit', 1, 200)
        with self.guard:
            self._require_session(session_id)
            rows = [row for row in self.lines if row['cursor'] > after][:limit]
            return {'session_id': session_id, 'lines': rows, 'next_cursor': rows[-1]['cursor'] if rows else after,
                'truncated': bool(self.lines and after < self.lines[0]['cursor'] - 1),
                'exit_code': self.process.poll() if self.process else None}

    def request(self, session_id, command, arguments=None, timeout=15):
        with self.guard:
            self._test_access()
            if self.tests.active and self.test_deadline is not None:
                timeout = min(timeout, max(0.001, self.test_deadline - time.monotonic()))
            self._require_session(session_id)
            if command not in {'state', 'capture', 'input', 'wait', 'event',
                               'set', 'spawn', 'destroy', 'mouse_trace', 'audio'}:
                raise ValueError('不支持的运行请求')
            if self.pending:
                raise ValueError('上个请求结果尚未确认；先查询 runtime_status，不要重试有副作用的操作')
            readiness = self._readiness()
            if not readiness['ready']:
                raise ValueError('游戏桥接未就绪：' + readiness['reason'] + '；查询 runtime_status 和 get_runtime_output')
            request_id = uuid4().hex
            self.pending = request_id
            self.pending_since = time.monotonic()
            atomic_json(self._file('request.json'), {'session': session_id, 'request': request_id,
                'command': command, 'args': arguments or {}})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.guard:
                if not self.session or self.session['id'] != session_id or self.session.get('stopped'):
                    raise ValueError('运行会话已停止')
                try:
                    result = self._response(request_id)
                except ValueError as exc:
                    self.pending = None
                    raise ValueError(self._redact(str(exc))) from None
                if result:
                    self.pending = None
                    if not result['ok']:
                        raise ValueError(self._redact(str(result['data'].get('error', '游戏请求失败'))))
                    if command == 'capture':
                        result['image'] = self._capture_bytes(request_id)
                    return result
            time.sleep(0.025)
        raise ValueError('运行请求超时，执行结果未知；查询 runtime_status 确认，勿直接重试')

    def _audio_args(self, sound_names=None, voices=None):
        sound_names = [] if sound_names is None else sound_names
        voices = [] if voices is None else voices
        if not isinstance(sound_names, list) or len(sound_names) > 32:
            raise ValueError('sound_names 最多 32 个声音名')
        for name in sound_names:
            if not isinstance(name, str):
                raise ValueError('声音名必须是字符串')
            name_check(name)
        if len(set(sound_names)) != len(sound_names):
            raise ValueError('声音名重复')
        if not isinstance(voices, list) or len(voices) > 16:
            raise ValueError('voices 最多 16 个明确登记的播放句柄变量')
        labels, normalized = set(), []
        for voice in voices:
            if not isinstance(voice, dict) or set(voice) - {'label', 'scope', 'variable', 'instance_id'}:
                raise ValueError('播放句柄只接受 label/scope/variable/instance_id')
            label, variable = voice.get('label'), voice.get('variable')
            if not isinstance(label, str) or not isinstance(variable, str):
                raise ValueError('播放句柄须提供 label 和 variable')
            name_check(label)
            name_check(variable)
            if label in labels:
                raise ValueError('播放句柄 label 重复')
            labels.add(label)
            scope = voice.get('scope', 'globals')
            if scope not in ('globals', 'instances'):
                raise ValueError('句柄 scope 须为 globals 或 instances')
            if scope == 'instances':
                target = instance_ref(voice.get('instance_id'))
            elif 'instance_id' in voice:
                raise ValueError('全局句柄不能指定 instance_id')
            else:
                target = None
            normalized.append({'label': label, 'variable': variable, 'scope': scope, 'instance_id': target})
        return {'sound_names': sound_names, 'voices': normalized}

    def inspect_audio(self, session_id, sound_names=None, voices=None, timeout=15):
        return self.request(session_id, 'audio', self._audio_args(sound_names, voices), timeout=timeout)

    def _state_args(self, object_name='', variables=None, limit=32,
                    scope='instances', instance_id=None):
        integer(limit, 'limit', 1, 100)
        if scope not in {'instances', 'globals'}:
            raise ValueError('scope 须为 instances 或 globals')
        if scope == 'globals':
            if object_name:
                raise ValueError('读取全局变量时 object_name 必须为空')
            if instance_id is not None:
                raise ValueError('读取全局变量时不支持 instance_id')
            if not isinstance(variables, list) or not variables:
                raise ValueError('读取全局变量须提供 1～16 个变量名')
            limit = 1
        else:
            variables = ['x', 'y', 'hspeed', 'vspeed', 'image_index', 'visible'] if variables is None else variables
        if not isinstance(variables, list) or len(variables) > 16 or any(
                not isinstance(v, str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}', v) for v in variables):
            raise ValueError('variables 最多 16 个变量名，不接受表达式')
        if object_name and not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}', object_name):
            raise ValueError('object_name 必须是有效的英语标识符')
        if instance_id is not None:
            if scope != 'instances':
                raise ValueError('instance_id 只用于实例读取')
            instance_id = instance_ref(instance_id)
        return object_name, variables, limit, scope, instance_id

    def inspect(self, session_id, object_name='', variables=None, limit=32,
                scope='instances', instance_id=None, max_depth=3):
        integer(max_depth, 'max_depth', 1, 8)
        object_name, variables, limit, scope, instance_id = self._state_args(
            object_name, variables, limit, scope, instance_id)
        args = {'object_name': object_name, 'variables': variables, 'limit': limit,
                'scope': scope, 'max_depth': max_depth}
        if instance_id is not None:
            args['instance_id'] = instance_id
        result = self.request(session_id, 'state', args)
        if isinstance(result.get('data'), dict) and 'error' in result['data']:
            raise ValueError(self._redact(str(result['data']['error'])))
        if 'bridge_io' in result.get('data', {}):
            result['data']['bridge_io'] = bridge_io_summary(result['data']['bridge_io'])
        if result.get('data', {}).get('window_width'):
            from . import win_mouse
            try:
                window = win_mouse.find_game_window(self.process.pid, 'MORX_Run_' + session_id)
                result['data']['input_coordinates'] = win_mouse.coordinate_metrics(window, result['data'])
            except (OSError, ValueError) as exc:
                result['data']['input_coordinates_error'] = self._redact(str(exc))
        return result

    @staticmethod
    def _set_value(value, depth, budget, location):
        """把 JSON 值限制为可安全写入 GML 的标量、数组或结构体。"""
        budget[0] -= 1
        if budget[0] < 0:
            raise ValueError(location + ' 的节点数超过 256')
        if depth > 3:
            raise ValueError(location + ' 的嵌套深度超过 4 层')
        if value is None:
            raise ValueError(location + ' 不支持 null；请使用 0、空数组或空结构体')
        if type(value) is bool:
            return
        if type(value) is int:
            if not -(2**53) <= value <= 2**53:
                raise ValueError(location + ' 的整数超出 GML real 精确范围')
            return
        if type(value) is float:
            if not math.isfinite(value):
                raise ValueError(location + ' 必须是有限数值')
            return
        if isinstance(value, str):
            if '\0' in value or len(value) > 4096:
                raise ValueError(location + ' 的字符串不能含 NUL 且最多 4096 字符')
            return
        if isinstance(value, list):
            if len(value) > 32:
                raise ValueError(location + ' 的数组最多 32 项')
            for index, child in enumerate(value):
                Runtime._set_value(child, depth + 1, budget, f'{location}[{index}]')
            return
        if isinstance(value, dict):
            if len(value) > 32:
                raise ValueError(location + ' 的结构体最多 32 个字段')
            for key, child in value.items():
                if not isinstance(key, str) or not key or len(key) > 64 or '\0' in key:
                    raise ValueError(location + ' 的结构体字段名必须是 1～64 字符的字符串')
                Runtime._set_value(child, depth + 1, budget, f'{location}.{key}')
            return
        raise ValueError(location + ' 只能包含布尔、数值、字符串、数组或结构体')

    def _set_args(self, values, scope='instances', object_name='', instance_id=None,
                  limit=32, create_missing=False):
        integer(limit, 'limit', 1, 100)
        if scope not in {'instances', 'globals'}:
            raise ValueError('scope 须为 instances 或 globals')
        if type(create_missing) is not bool:
            raise ValueError('create_missing 必须为布尔值')
        if not isinstance(values, dict) or not 1 <= len(values) <= 16:
            raise ValueError('values 须包含 1～16 个变量')
        for name, value in values.items():
            if not isinstance(name, str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}', name):
                raise ValueError('变量名必须是有效的英语标识符')
            self._set_value(value, 0, [256], name)
        from .runtime_bridge import OBJECT
        if scope == 'globals':
            if object_name:
                raise ValueError('写入全局变量时 object_name 必须为空')
            if instance_id is not None:
                raise ValueError('写入全局变量时不支持 instance_id')
            limit = 1
        else:
            if object_name:
                name_check(object_name)
                if object_name == OBJECT:
                    raise ValueError('受管理的测试桥接不能通过运行期工具修改')
            if instance_id is not None:
                instance_id = instance_ref(instance_id)
                limit = 1
            if not object_name and instance_id is None:
                raise ValueError('写入实例变量须提供 object_name 或 instance_id')
        try:
            raw = json.dumps(values, ensure_ascii=False, separators=(',', ':'))
        except (TypeError, ValueError) as exc:
            raise ValueError('values 无法序列化为 JSON') from exc
        if len(raw.encode('utf-8')) > 64 * 1024:
            raise ValueError('values 序列化后不能超过 64 KiB')
        args = {'values': values, 'scope': scope, 'object_name': object_name,
                'limit': limit, 'create_missing': create_missing}
        if instance_id is not None:
            args['instance_id'] = instance_id
        return args

    def set_variables(self, session_id, values, scope='instances', object_name='',
                      instance_id=None, limit=32, create_missing=False, dry_run=True):
        """写入全局或实例变量；先校验全部目标，再在桥接中一次提交。"""
        args = self._set_args(values, scope, object_name, instance_id, limit, create_missing)
        with self.guard:
            self._require_session(session_id)
            if not dry_run and not self._ready():
                raise ValueError('游戏桥接未就绪；查询 runtime_status 和 get_runtime_output')
        preview = {'dry_run': dry_run, 'session_id': session_id, **args}
        if dry_run:
            return preview
        return self.request(session_id, 'set', args)

    def _spawn_args(self, object_name, x=0, y=0, layer_name='', depth=0):
        name_check(object_name)
        from .runtime_bridge import OBJECT
        if object_name == OBJECT:
            raise ValueError('受管理的测试桥接不能通过运行期工具创建')
        for value, label in ((x, 'x'), (y, 'y')):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(label + ' 必须是有限数值')
        if not isinstance(layer_name, str) or len(layer_name) > 255 or '\0' in layer_name:
            raise ValueError('layer_name 必须是 0～255 字符的字符串')
        if type(depth) is not int or not -(10**6) <= depth <= 10**6:
            raise ValueError('depth 须为 -1000000～1000000 的整数')
        return {'object_name': object_name, 'x': x, 'y': y,
                'layer_name': layer_name, 'depth': depth}

    def spawn(self, session_id, object_name, x=0, y=0, layer_name='', depth=0, dry_run=True):
        """在运行中的当前房间创建一个实例；layer_name 为空时按 depth 放置。"""
        args = self._spawn_args(object_name, x, y, layer_name, depth)
        with self.guard:
            self._require_session(session_id)
            if not dry_run and not self._ready():
                raise ValueError('游戏桥接未就绪；查询 runtime_status 和 get_runtime_output')
        preview = {'dry_run': dry_run, 'session_id': session_id, **args}
        if dry_run:
            return preview
        return self.request(session_id, 'spawn', args)

    def _destroy_args(self, object_name='', instance_id=None, limit=1):
        integer(limit, 'limit', 1, 100)
        from .runtime_bridge import OBJECT
        if object_name:
            name_check(object_name)
            if object_name == OBJECT:
                raise ValueError('受管理的测试桥接不能通过运行期工具销毁')
        if instance_id is not None:
            instance_id = instance_ref(instance_id)
            limit = 1
        if not object_name and instance_id is None:
            raise ValueError('销毁实例须提供 object_name 或 instance_id')
        args = {'object_name': object_name, 'limit': limit}
        if instance_id is not None:
            args['instance_id'] = instance_id
        return args

    def destroy(self, session_id, object_name='', instance_id=None, limit=1, dry_run=True):
        """销毁单个实例或指定物体的最多 limit 个实例；不会销毁受管理桥接。"""
        args = self._destroy_args(object_name, instance_id, limit)
        with self.guard:
            self._require_session(session_id)
            if not dry_run and not self._ready():
                raise ValueError('游戏桥接未就绪；查询 runtime_status 和 get_runtime_output')
        preview = {'dry_run': dry_run, 'session_id': session_id, **args}
        if dry_run:
            return preview
        return self.request(session_id, 'destroy', args)

    def _event_args(self, object_name, event, number=0, collision_object=None,
                    key=None, instance_id=None, limit=32):
        if not isinstance(object_name, str) or not object_name:
            raise ValueError('object_name 不能为空')
        name_check(object_name)
        if not isinstance(event, str):
            raise ValueError('event 必须是事件名称')
        if type(number) is not int:
            raise ValueError('事件编号必须为整数')
        if key is not None:
            if number != 0:
                raise ValueError('key 与 number 只能提供一个')
            number = key_code(key)
        if instance_id is not None:
            instance_id = instance_ref(instance_id)
        integer(limit, 'limit', 1, 100)
        event_type, event_number, collision = trigger_spec(event, number, collision_object)
        args = {'object_name': object_name, 'event': event, 'event_type': event_type,
                'event_number': event_number, 'limit': limit}
        if collision is not None:
            args['collision_object'] = collision
        if instance_id is not None:
            args['instance_id'] = instance_id
        return args

    def trigger_event(self, session_id, object_name, event, number=0, collision_object=None,
                      key=None, instance_id=None, limit=32, dry_run=True):
        """通过桥接数据执行 event_perform；不注入物理输入，不改变前台窗口。"""
        args = self._event_args(object_name, event, number, collision_object, key,
                                instance_id, limit)
        with self.guard:
            self._require_session(session_id)
            if not dry_run and not self._ready():
                raise ValueError('游戏桥接未就绪；查询 runtime_status 和 get_runtime_output')
        preview = {'dry_run': dry_run, 'session_id': session_id, **args}
        if dry_run:
            return preview
        return self.request(session_id, 'event', args)

    def _sequence_step(self, step):
        if not isinstance(step, dict):
            raise ValueError('序列步骤须为对象')
        action = step.get('action')
        if action == 'mouse':
            return self._mouse_step(step)
        allowed = {'wait': {'action', 'frames'},
                   'input': {'action', 'keys', 'frames', 'mode'},
                   'capture': {'action'},
                   'state': {'action', 'object_name', 'variables', 'limit', 'scope', 'instance_id', 'max_depth'},
                   'set': {'action', 'values', 'scope', 'object_name', 'instance_id',
                           'limit', 'create_missing'},
                   'spawn': {'action', 'object_name', 'x', 'y', 'layer_name', 'depth'},
                   'destroy': {'action', 'object_name', 'instance_id', 'limit'},
                   'event': {'action', 'object_name', 'event', 'number', 'collision_object',
                             'key', 'instance_id', 'limit'}}
        if action not in allowed:
            raise ValueError('不支持的序列动作：' + str(action))
        extra = set(step) - allowed[action]
        if extra:
            raise ValueError('序列步骤包含未知参数：' + ', '.join(sorted(extra)))
        if action == 'wait':
            frames = step.get('frames', 1)
            integer(frames, 'frames', 1, 600)
            return {'action': 'wait', 'frames': frames}
        if action == 'input':
            frames = step.get('frames', 1)
            integer(frames, 'frames', 1, 300)
            key_codes(step.get('keys'))
            mode = step.get('mode', 'message')
            if mode not in {'message', 'state'}:
                raise ValueError('按键模式须为 message 或 state')
            return {'action': 'input', 'keys': step.get('keys'),
                    'frames': frames, 'mode': mode}
        if action == 'capture':
            return {'action': 'capture'}
        if action == 'set':
            args = self._set_args(step.get('values'), step.get('scope', 'instances'),
                step.get('object_name', ''), step.get('instance_id'),
                step.get('limit', 32), step.get('create_missing', False))
            return {'action': 'set', **args}
        if action == 'spawn':
            args = self._spawn_args(step.get('object_name'), step.get('x', 0),
                step.get('y', 0), step.get('layer_name', ''), step.get('depth', 0))
            return {'action': 'spawn', **args}
        if action == 'destroy':
            args = self._destroy_args(step.get('object_name', ''),
                step.get('instance_id'), step.get('limit', 1))
            return {'action': 'destroy', **args}
        if action == 'event':
            args = self._event_args(step.get('object_name'), step.get('event'),
                step.get('number', 0), step.get('collision_object'), step.get('key'),
                step.get('instance_id'), step.get('limit', 32))
            return {'action': 'event', **args}
        object_name, variables, limit, scope, instance_id = self._state_args(
            step.get('object_name', ''), step.get('variables'), step.get('limit', 32),
            step.get('scope', 'instances'), step.get('instance_id'))
        normalized = {'action': 'state', 'object_name': object_name,
                      'variables': variables, 'limit': limit, 'scope': scope}
        if 'max_depth' in step:
            integer(step['max_depth'], 'max_depth', 1, 8)
            normalized['max_depth'] = step['max_depth']
        if instance_id is not None:
            normalized['instance_id'] = instance_id
        return normalized

    def run_sequence(self, session_id, steps, dry_run=True, max_images=4):
        """按顺序执行受限动作；返回每步结果，截图以独立 PNG 字节列表返回。"""
        integer(max_images, 'max_images', 1, 4)
        if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
            raise ValueError('steps 须包含 1～32 个动作')
        normalized = [self._sequence_step(step) for step in steps]
        total_frames = sum(step.get('frames', 0) for step in normalized)
        if total_frames > 1800:
            raise ValueError('序列总帧数不能超过 1800')
        captures = sum(1 for step in normalized if step['action'] == 'capture')
        if captures > max_images:
            raise ValueError('截图数量超过 max_images')
        with self.guard:
            self._require_session(session_id)
        preview = {'session_id': session_id, 'steps': normalized, 'total_frames': total_frames,
                   'captures': captures}
        if dry_run:
            return {'dry_run': True, **preview}
        if not self._ready():
            raise ValueError('游戏桥接未就绪；查询 runtime_status 和 get_runtime_output')
        results, pictures = [], []
        deadline = time.monotonic() + 150
        for index, step in enumerate(normalized, 1):
            if time.monotonic() >= deadline:
                results.append({'index': index, 'action': step['action'], 'error': '序列超时'})
                return {'dry_run': False, 'session_id': session_id, 'ok': False,
                        'results': results, 'failed_step': index, 'pictures': pictures}
            action = step['action']
            try:
                if action == 'wait':
                    data = self.wait(session_id, step['frames'])
                elif action == 'input':
                    data = self.input(session_id, step['keys'], step['frames'],
                                      dry_run=False, mode=step['mode'])
                elif action == 'mouse':
                    data = self.mouse(session_id, step['mouse_action'], dry_run=False,
                                      **{k: v for k, v in step.items() if k not in ('action', 'mouse_action')})
                    results.append({'index': index, 'action': action, 'data': data})
                    continue
                elif action == 'state':
                    data = self.inspect(session_id, step['object_name'],
                                        step['variables'], step['limit'], step['scope'],
                                        step.get('instance_id'), step.get('max_depth', 3))
                elif action in ('set', 'spawn', 'destroy'):
                    data = self.request(session_id, action,
                                        {key: value for key, value in step.items()
                                         if key != 'action'})
                elif action == 'event':
                    data = self.request(session_id, 'event',
                                        {key: value for key, value in step.items() if key != 'action'})
                else:
                    data = self.request(session_id, 'capture')
                    picture = data.pop('image', None)
                    item = {'index': index, 'action': action, 'data': data.get('data'),
                            'frame': data.get('frame')}
                    if picture is not None:
                        item['image_index'] = len(pictures)
                        pictures.append(picture)
                    results.append(item)
                    continue
                results.append({'index': index, 'action': action,
                                'data': data.get('data'), 'frame': data.get('frame')})
            except ValueError as exc:
                results.append({'index': index, 'action': action,
                                'error': self._redact(str(exc))})
                return {'dry_run': False, 'session_id': session_id, 'ok': False,
                        'results': results, 'failed_step': index, 'pictures': pictures}
        return {'dry_run': False, 'session_id': session_id, 'ok': True,
                'results': results, 'pictures': pictures}

    def input(self, session_id, keys, frames=1, dry_run=True, mode='message'):
        if not self.interaction_guard.acquire(blocking=False):
            raise ValueError('上个键鼠动作尚未完成，不能交叉输入')
        try:
            return self._input(session_id, keys, frames, dry_run, mode)
        finally:
            self.interaction_guard.release()

    def _input(self, session_id, keys, frames=1, dry_run=True, mode='message'):
        """按住按键若干帧。message 通过窗口消息产生真实按下/释放，state 只改桥接按键状态。"""
        integer(frames, 'frames', 1, 300)
        if not dry_run:
            self._test_access()
        codes = key_codes(keys)
        if mode not in {'message', 'state'}:
            raise ValueError('按键模式须为 message 或 state')
        with self.guard:
            if not dry_run:
                self._test_access()
            self._require_session(session_id)
            if not dry_run and mode == 'message' and not self._ready():
                raise ValueError('游戏桥接未就绪；查询 runtime_status 和 get_runtime_output')
            if not dry_run and self.pending:
                raise ValueError('上个请求结果尚未确认；先查询 runtime_status，不要重复输入')
        preview = {'dry_run': dry_run, 'session_id': session_id, 'keys': keys,
                   'frames': frames, 'mode': mode}
        if dry_run:
            return preview
        if mode == 'state':
            return self.request(session_id, 'input', {'keys': codes, 'frames': frames},
                                timeout=max(15, frames / 10 + 10))
        from . import win_keyboard, win_mouse
        window = win_mouse.find_game_window(self.process.pid, 'MORX_Run_' + session_id)
        win_keyboard.key_down(window, codes)
        try:
            result = self.wait(session_id, frames)
        finally:
            win_keyboard.key_up(window, codes)
        return {'dry_run': False, **preview,
                'window': {'width': window.width, 'height': window.height},
                'data': result.get('data'), 'frame': result.get('frame')}

    def wait(self, session_id, frames=1):
        integer(frames, 'frames', 1, 600)
        return self.request(session_id, 'wait', {'frames': frames},
                            timeout=max(15, frames / 10 + 10))

    @staticmethod
    def _validate_mouse(action, x=None, y=None, button='left', delta=0, hold_ms=50,
                        mode='message', coordinate_space='client', end_x=None, end_y=None,
                        duration_ms=500, steps=20, settle_frames=2):
        if action not in {'move', 'click', 'wheel', 'drag'}:
            raise ValueError('鼠标动作须为 move、click、wheel 或 drag')
        if coordinate_space not in {'client', 'physical', 'window', 'gui'}:
            raise ValueError('coordinate_space 须为 client、physical、window 或 gui')
        integer(settle_frames, 'settle_frames', 0, 30)
        if action == 'drag':
            if mode != 'message':
                raise ValueError('drag 仅支持窗口消息模式')
            integer(end_x, 'end_x', 0, 32768)
            integer(end_y, 'end_y', 0, 32768)
            integer(duration_ms, 'duration_ms', 50, 3000)
            integer(steps, 'steps', 2, 60)
        elif end_x is not None or end_y is not None:
            raise ValueError('只有 drag 支持 end_x/end_y')
        if button not in {'left', 'right', 'middle'}:
            raise ValueError('鼠标按钮须为 left、right 或 middle')
        if mode not in {'message', 'physical'}:
            raise ValueError('鼠标模式须为 message 或 physical')
        integer(hold_ms, 'hold_ms', 10, 500)
        if type(delta) is not int or not -5 <= delta <= 5:
            raise ValueError('滚轮 delta 须为 -5～5 的整数')
        if action == 'wheel':
            if delta == 0:
                raise ValueError('滚轮 delta 不能为 0')
        elif delta != 0:
            raise ValueError('只有 wheel 动作使用 delta')
        if (x is None) != (y is None):
            raise ValueError('x 和 y 必须同时提供或同时省略')
        if x is not None:
            integer(x, 'x', 0, 32768)
            integer(y, 'y', 0, 32768)
        elif action in {'move', 'click', 'drag'}:
            raise ValueError('move/click/drag 必须提供 x 和 y')

    def _mouse_step(self, step):
        allowed = {'action', 'mouse_action', 'x', 'y', 'button', 'delta', 'hold_ms', 'mode',
                   'coordinate_space', 'end_x', 'end_y', 'duration_ms', 'steps', 'settle_frames'}
        if set(step) - allowed or step.get('mode', 'message') != 'message':
            raise ValueError('序列鼠标只支持 message 模式及明确的鼠标参数')
        args = {k: v for k, v in step.items() if k not in ('action', 'mouse_action')}
        self._validate_mouse(step.get('mouse_action'), **args)
        return {'action': 'mouse', 'mouse_action': step['mouse_action'], **args}

    def mouse(self, session_id, action, x=None, y=None, button='left', delta=0,
              hold_ms=50, mode='message', dry_run=True, coordinate_space='client',
              end_x=None, end_y=None, duration_ms=500, steps=20, settle_frames=2):
        if not dry_run:
            self._test_access()
        # 整个消息序列与逐帧记录属于同一动作，拒绝并发鼠标调用相互覆盖。
        if not self.mouse_guard.acquire(blocking=False):
            raise ValueError('上个鼠标动作尚未完成；等待原操作结果，不要并发或重复输入')
        try:
            if not self.interaction_guard.acquire(blocking=False):
                raise ValueError('上个键鼠动作尚未完成，不能交叉输入')
            try:
                return self._mouse(session_id, action, x, y, button, delta, hold_ms, mode, dry_run,
                                   coordinate_space, end_x, end_y, duration_ms, steps, settle_frames)
            finally:
                self.interaction_guard.release()
        finally:
            self.mouse_guard.release()

    def _mouse(self, session_id, action, x, y, button, delta, hold_ms, mode, dry_run,
               coordinate_space, end_x, end_y, duration_ms, steps, settle_frames):
        """向本次运行的游戏窗口投递鼠标动作；坐标为窗口客户区像素。
        默认 mode=message 使用窗口消息，不移动真实光标、不要求前台焦点；
        mode=physical 才使用系统级注入。
        """
        self._validate_mouse(action, x, y, button, delta, hold_ms, mode, coordinate_space,
                             end_x, end_y, duration_ms, steps, settle_frames)
        with self.guard:
            if not dry_run:
                self._test_access()
            self._require_session(session_id)
            if not self._ready():
                raise ValueError('游戏桥接未就绪；查询 runtime_status 和 get_runtime_output')
            if self.pending:
                raise ValueError('上个请求结果尚未确认；先查询 runtime_status，不要重复输入')
        from . import win_mouse
        window = win_mouse.find_game_window(self.process.pid, 'MORX_Run_' + session_id)
        engine = self.inspect(session_id, variables=[])['data'] if coordinate_space in {'window', 'gui'} else {}
        metrics = win_mouse.coordinate_metrics(window, engine)
        requested = {'x': x, 'y': y, 'end_x': end_x, 'end_y': end_y}
        if x is not None:
            x, y = win_mouse.convert_point(x, y, coordinate_space, metrics)
        if action == 'drag':
            end_x, end_y = win_mouse.convert_point(end_x, end_y, coordinate_space, metrics)
        preview = {'session_id': session_id, 'action': action, 'x': x, 'y': y,
                   'button': button if action in {'click', 'drag'} else None,
                   'delta': delta if action == 'wheel' else 0, 'mode': mode,
                   'window': {'width': window.width, 'height': window.height},
                   'coordinate_space': coordinate_space, 'requested': requested,
                   'coordinates': metrics, 'end_x': end_x, 'end_y': end_y,
                   'settle_frames': settle_frames}
        if dry_run:
            return {'dry_run': True, **preview}
        traced = mode == 'message' and action in {'click', 'drag'}
        dispatch = []
        trace_id = uuid4().hex
        dispatch_started = time.monotonic()

        def record_dispatch(message, flags, position):
            # 只记录本次工具投递的消息；投递顺序不冒充引擎处理顺序。
            dispatch.append({'order': len(dispatch) + 1, 'message': message,
                             'client_x': position & 0xFFFF, 'client_y': (position >> 16) & 0xFFFF,
                             'button_flags': flags, 'elapsed_ms': round((time.monotonic() - dispatch_started) * 1000, 3)})

        if traced:
            self.request(session_id, 'mouse_trace', {'phase': 'begin', 'trace_id': trace_id, 'button': button})
        if mode == 'message' and action in {'click', 'drag'} and settle_frames:
            win_mouse.send_mouse_message(window, 'move', x, y, on_dispatch=record_dispatch)
            self.wait(session_id, settle_frames)
        result = {'dry_run': False, **preview, 'input_dispatched': False, 'dispatch_complete': False}
        try:
            if action == 'drag':
                point = win_mouse.drag_message(window, x, y, end_x, end_y, button, duration_ms, steps,
                                               on_dispatch=record_dispatch)
            elif mode == 'message':
                point = win_mouse.send_mouse_message(window, action, x, y, button, delta, hold_ms,
                                                     on_dispatch=record_dispatch if traced else None)
            else:
                point = win_mouse.send_mouse(window, action, x, y, button, delta, hold_ms)
            result.update(cursor=point, input_dispatched=True, dispatch_complete=True)
        except (ValueError, OSError) as exc:
            if not traced:
                raise
            result['input_dispatched'] = bool(dispatch)
            result['dispatch_error'] = self._redact(str(exc))
        if traced:
            result['mouse_trace'] = {'trace_id': trace_id, 'dispatch': dispatch,
                                    'game_effect': 'unknown', 'automatic_replay': False}
        try:
            self.wait(session_id, 1)
            if traced:
                trace = self.request(session_id, 'mouse_trace', {'phase': 'end', 'trace_id': trace_id})
                if trace.get('data', {}).get('trace_id') != trace_id:
                    raise ValueError('鼠标记录身份不匹配，不能确认本次输入采样')
                result['mouse_trace']['engine'] = trace['data']
            snapshot = self.inspect(session_id, variables=[])
            result['observed_mouse'] = snapshot.get('data', {}).get('mouse')
            result['observation_frame'] = snapshot.get('frame')
        except ValueError as exc:
            result['observation_error'] = self._redact(str(exc))
        return result

    def save_capture(self, session_id, filename, dry_run=True):
        if not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}\.png', filename):
            raise ValueError('filename 须为 1～80 位英文、数字、下划线或连字符及 .png 后缀')
        if not self.project.writable and not dry_run:
            raise ValueError('导出截图需要 --write')
        relative = '.codex/morx-captures/' + filename
        target = self.project.path(relative)
        with self.guard:
            self._require_session(session_id)
            if target.exists():
                raise ValueError('截图导出拒绝覆盖已有文件')
            if dry_run:
                return {'dry_run': True, 'path': relative}
            result = self.request(session_id, 'capture')
            target = self.project.path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as output:
                output.write(result.pop('image'))
            return {**result, 'path': relative, 'dry_run': False}

    def _capture_bytes(self, request_id):
        path = self._file('capture_' + request_id + '.png')
        try:
            if path.stat().st_size > 64 * 1024 * 1024:
                raise ValueError('截图超过 64 MiB')
            with Image.open(path) as picture:
                if picture.format != 'PNG' or picture.width * picture.height > 16 * 1024 * 1024:
                    raise ValueError('截图格式或尺寸不受支持')
                if picture.width <= 1280 and picture.height <= 720:
                    return path.read_bytes()
                picture.thumbnail((1280, 720))
                output = io.BytesIO()
                picture.convert('RGB').save(output, format='PNG')
                return output.getvalue()
        finally:
            path.unlink(missing_ok=True)

    def stop(self, session_id, dry_run=True, _preserve=False):
        with self.stop_guard:
            return self._stop(session_id, dry_run, _preserve)

    def _stop(self, session_id, dry_run=True, _preserve=False):
        with self.guard:
            session = self._require_session(session_id)
            if dry_run:
                return {'dry_run': True, 'session_id': session_id, 'action': 'stop_owned_session'}
            if self.tests.active:
                self.tests.cancel.set()
            if session.get('cancel'):
                session['cancel'].set()
            session['stopping'] = True
            done = session.get('preparation_done')
        # 等待时释放状态锁；仍在使用目录的准备线程退出前不得删除副本。
        if done and not done.wait(1):
            return {'session_id': session_id, 'stopped': False, 'state': 'stopping',
                    'cleaned': False, 'cleanup': [], 'reason': 'preparation_in_progress',
                    'next': '准备已请求取消；稍后对同一会话再次 stop_game 完成清理'}
        with self.guard:
            errors = self._errors()
            self._readiness()
            io_status = self._io_status()
            if not session.get('stopped'):
                # 删除会话文件本身就是桥接的退出信号，下一帧 BeginStep 会结束游戏。
                self._file('session.json').unlink(missing_ok=True)
                session['stopped'] = True
                self.pending = None
                if self.timer:
                    self.timer.cancel()
                    self.timer = None
            process = self.process
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if os.name == 'nt':
                    subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'),
                        '/PID', str(process.pid), '/T', '/F'], stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW, check=False)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        cleanup = {'cleaned': False, 'cleanup': []}
        if not _preserve:
            cleanup = self._cleanup(session)
            session['cleanup_failed'] = not cleanup['cleaned']
        session['stopping'] = False
        return {'session_id': session_id, 'stopped': True,
                'exit_code': process.poll() if process else None, **cleanup,
                'error_count': len(errors), 'errors': errors, 'bridge_io': io_status}

    def restart(self, session_id, dry_run=True):
        with self.stop_guard:
            return self._restart(session_id, dry_run)

    def _restart(self, session_id, dry_run=True):
        """同一隔离副本重新执行 Igor Run；保留存档，不接管或拼接外部 Runner 命令。"""
        with self.guard:
            if self.tests.active:
                raise ValueError('场景测试尚未结束，不能重启')
            session = self._require_session(session_id)
            if (session.get('stopped') or session.get('stopping') or session.get('compile_only')
                    or not session.get('launch_args') or session.get('preparation_error')):
                raise ValueError('restart_game 只接受尚未停止的受管运行会话')
            preview = {'dry_run': dry_run, 'session_id': session_id,
                       'preserve_save': True, 'uses_existing_snapshot': True,
                       'rebuilds_snapshot': True}
            if dry_run:
                return preview
            self._configuration()
            self.stop(session_id, dry_run=False, _preserve=True)
            try:
                for name in ('ready.json', 'ready.json.tmp', 'response.json', 'response.json.tmp',
                             'request.json', 'errors.json', 'errors.json.tmp'):
                    self._file(name).unlink(missing_ok=True)
                atomic_json(self._file('session.json'), {'session': session_id, 'bridge': session['bridge']})
                self.heartbeat = self.pending_since = None
                self.pending = None
                self.build_diagnostics.clear()
                session.pop('build_succeeded', None)
                if self.build_gate is not None:
                    session['cancel'].clear()
                    session['preparation_done'] = threading.Event()
                    session['stopped'] = False
                    self.process = None
                    self._phase(session, 'queued')
                    worker = threading.Thread(target=self._restart_prepare, args=(session,), daemon=True)
                    session['worker'] = worker
                    try:
                        worker.start()
                    except Exception:
                        session['preparation_done'].set()
                        raise
                    self.timer = threading.Timer(900, self._expire, args=(session_id,))
                    self.timer.daemon = True
                    self.timer.start()
                    return {**preview, 'state': 'preparing', 'compiled': False}
                self.process = subprocess.Popen(session['launch_args'], cwd=session['directory'],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                session['stopped'] = False
                self._phase(session, 'launched')
                threading.Thread(target=self._collect, args=(self.process,), daemon=True).start()
                self.timer = threading.Timer(900, self._expire, args=(session_id,))
                self.timer.daemon = True
                self.timer.start()
            except Exception:
                # 启动失败仍保留副本与存档，允许读取日志后显式 stop_game 清理。
                session['stopped'] = True
                raise
            return {**preview, 'state': 'building_or_starting', 'compiled': False}

    def _restart_prepare(self, session):
        acquired = False
        try:
            self._acquire_build(session)
            acquired = True
            with self.guard:
                self._check_cancel(session)
                self.process = subprocess.Popen(session['launch_args'], cwd=session['directory'],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                self._phase(session, 'launched')
                threading.Thread(target=self._collect, args=(self.process,), daemon=True).start()
        except InterruptedError:
            self._phase(session, 'cancelled')
        except Exception as exc:
            with self.guard:
                session['preparation_error'] = {'type': type(exc).__name__, 'message': self._redact(str(exc)[:2048])}
                self._phase(session, 'failed')
        finally:
            session['preparation_done'].set()
            if acquired:
                self._hold_build(session)

    def _expire(self, session_id):
        try:
            result = self.stop(session_id, dry_run=False)
            if not result['stopped']:
                with self.guard:
                    self.timer = threading.Timer(1, self._expire, args=(session_id,))
                    self.timer.daemon = True
                    self.timer.start()
        except (ValueError, OSError, subprocess.SubprocessError):
            pass

    def close(self):
        if self.session:
            self._expire(self.session['id'])
