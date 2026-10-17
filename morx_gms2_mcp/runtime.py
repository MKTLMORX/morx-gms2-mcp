"""固定项目的隔离构建、受管理进程与游戏内文件桥接。无桌面抓取或外部监听。"""
from collections import deque
from dataclasses import dataclass
import io
import json
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
from .storage import compact_encode, encode, parse_yy


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
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def child_path(root, name):
    path = root / name
    if not link_free(path):
        raise ValueError('运行目录不能包含链接或目录联接')
    return path


def key_codes(keys):
    named = {'backspace': 8, 'tab': 9, 'enter': 13, 'escape': 27, 'space': 32,
             'left': 37, 'up': 38, 'right': 39, 'down': 40}
    if not isinstance(keys, list) or not 1 <= len(keys) <= 8:
        raise ValueError('keys 须包含 1～8 个按键')
    values = []
    for key in keys:
        if not isinstance(key, str):
            raise ValueError('按键须为名称或单个英文字母/数字')
        name = key.lower()
        if name in named:
            value = named[name]
        elif re.fullmatch('[a-z0-9]', name):
            value = ord(name.upper())
        else:
            raise ValueError('仅支持字母、数字、方向键及 space/enter/escape/tab/backspace')
        if value in values:
            raise ValueError('按键不能重复')
        values.append(value)
    return values


class Runtime:
    def __init__(self, project, options=None):
        self.project = project
        self.options = options or RuntimeOptions()
        self.guard = threading.RLock()
        self.session = None
        self.process = None
        self.pending = None
        self.lines = deque(maxlen=1000)
        self.sequence = 0
        self.timer = None
        self.redactions = []

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

    def _ready(self):
        if not self.session or self.session.get('stopped'):
            return False
        if self.process is not None and self.process.poll() is not None:
            return False
        try:
            path = self._file('ready.json')
            data = bounded_json(path)
            return (data.get('session') == self.session['id'] and data.get('bridge') == self.session['bridge']
                    and time.time() - path.stat().st_mtime < 10)
        except (OSError, ValueError):
            return False

    def _redact(self, text):
        for value in sorted(self.redactions, key=len, reverse=True):
            if not value:
                continue
            for variant in {value, value.replace('\\', '/')}:
                text = re.sub(re.escape(variant), '[local-path]', text, flags=re.IGNORECASE)
        return text

    def _response(self, request_id):
        try:
            data = bounded_json(self._file('response.json'), 4 * 1024 * 1024)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        # 大小超限、非对象等确定性错误继续抛出，由调用方清理 pending。
        if data.get('session') != self.session['id'] or data.get('request') != request_id:
            return None
        if type(data.get('ok')) is not bool or not isinstance(data.get('data'), dict):
            raise ValueError('运行响应格式不正确')
        return data

    def status(self, wait_seconds=0):
        integer(wait_seconds, 'wait_seconds', 0, 10)
        deadline = time.monotonic() + wait_seconds
        while True:
            with self.guard:
                ready = self._ready()
                code = self.process.poll() if self.process is not None else None
                result = {'enabled': self.options.enabled, 'configured': bool(self.options.runtime_root and self.options.user_directory),
                    'session_id': self.session['id'] if self.session else None,
                    'bridge_ready': ready, 'exit_code': code,
                    'state': 'idle' if not self.session else 'stopped' if self.session.get('stopped') else
                        'running' if ready else 'exited' if code is not None else 'building_or_starting'}
                if self.pending:
                    result['pending_request'] = self.pending
                    try:
                        late = self._response(self.pending)
                    except ValueError as exc:
                        result['response_error'] = self._redact(str(exc))
                        self.pending = None
                    else:
                        if late:
                            result['completed_request'] = late
                            self.pending = None
                if ready or code is not None or time.monotonic() >= deadline:
                    return result
            time.sleep(0.05)

    def _copy_project(self, destination, run_name, key):
        total, count = 0, 0
        with self.project.lock:
            for directory, folders, files in os.walk(self.project.root, followlinks=False):
                current = Path(directory)
                relative_dir = current.relative_to(self.project.root)
                if current == self.project.root:
                    folders[:] = [f for f in folders if f not in {'.git', '.codex', '.venv', 'dist', 'build', 'node_modules'}]
                for folder in folders:
                    relative = (relative_dir / folder).as_posix()
                    self.project.path(relative)
                    (destination / relative).mkdir(parents=True, exist_ok=True)
                for name in files:
                    relative = (relative_dir / name).as_posix()
                    source = current / name
                    if not link_free(source):
                        raise ValueError('运行副本不复制链接或目录联接：' + relative)
                    count += 1
                    total += source.stat().st_size
                    if count > 100000 or total > 2 * 1024**3:
                        raise ValueError('项目运行副本超过 100000 文件或 2 GiB')
                    shutil.copyfile(source, destination / relative)
            meta = parse_yy((destination / self.project.yyp.name).read_text(encoding='utf-8-sig'))
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
        for path, expected in ((session.get('directory'), session_id),
                               (session.get('save_directory'), 'MORX_Run_' + str(session_id))):
            if not path:
                continue
            path = Path(path)
            if path.name != expected or not link_free(path):
                continue
            shutil.rmtree(path, ignore_errors=True)
        work_root = session.get('directory')
        if work_root:
            try:
                Path(work_root).parent.rmdir()
            except OSError:
                pass

    def run(self, dry_run=True):
        with self.guard:
            root, user, igor, sandbox = self._configuration()
            if self.session and not self.session.get('stopped'):
                raise ValueError('已有运行会话，请先 stop_game；即使进程已退出也先读取日志')
            key = self._bridge()
            validation = self.project.validate()
            if not validation['ok']:
                raise ValueError('项目结构检查失败，先调用 inspect_project')
            preview = {'dry_run': dry_run, 'project': str(self.project.yyp), 'target': 'Windows VM',
                       'isolated_project': True, 'isolated_save_directory': True}
            if dry_run:
                return preview
            session_id = uuid4().hex
            run_name = 'MORX_Run_' + session_id
            work_root = child_path(sandbox, '_morx_runs')
            directory = work_root / session_id
            savedir = child_path(sandbox, run_name)
            bridge_directory = savedir / 'morx_mcp'
            session = {'id': session_id, 'bridge': key, 'bridge_directory': bridge_directory,
                'directory': directory, 'save_directory': savedir, 'stopped': False}
            self.redactions = [str(user), str(root), str(self.project.root),
                               str(sandbox), str(savedir), str(Path.home())]
            try:
                work_root.mkdir(exist_ok=True)
                directory.mkdir(exist_ok=False)
                project_copy = directory / 'project'
                project_copy.mkdir()
                yyp = self._copy_project(project_copy, run_name, key)
                savedir.mkdir(exist_ok=False)
                bridge_directory.mkdir()
                atomic_json(bridge_directory / 'session.json', {'session': session_id, 'bridge': key})
                # Windows Run 不接受 /of：Igor 会在 cwd 下生成 output 并由 Run 步骤自行查找；
                # 传 /of 会让 Runner 去加载错误的输出文件名。
                args = [str(igor), '/uf=' + str(user), '/rp=' + str(root), '/project=' + str(yyp),
                    '/cache=' + str(directory / 'cache'), '/temp=' + str(directory / 'temp'),
                    '/runtime=VM', '--', 'Windows', 'Run']
                self.session, self.pending = session, None
                self.lines.clear()
                self.sequence = 0
                self.process = subprocess.Popen(args, cwd=directory, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            except OSError as exc:
                session['stopped'] = True
                self._cleanup(session)
                raise ValueError('无法创建运行目录或启动 Igor；检查 Runtime 安装') from exc
            except Exception:
                session['stopped'] = True
                self._cleanup(session)
                raise
            threading.Thread(target=self._collect, args=(self.process,), daemon=True).start()
            self.timer = threading.Timer(900, self._expire, args=(session_id,))
            self.timer.daemon = True
            self.timer.start()
            return {**preview, 'session_id': session_id, 'state': 'building_or_starting',
                'snapshot_project': str(yyp), 'save_directory': str(savedir), 'compiled': False,
                'next': '查询 runtime_status；bridge_ready=true 后可截图和检查状态'}

    def _collect(self, process):
        buffer = ''
        try:
            while True:
                chunk = process.stdout.read1(4096)
                if not chunk:
                    break
                buffer += chunk.decode('utf-8', errors='replace')
                rows = buffer.split('\n')
                buffer = rows.pop()[-16384:]
                for row in rows:
                    self._log(row[:16384])
            if buffer:
                self._log(buffer)
        finally:
            process.stdout.close()

    def _log(self, line):
        if re.search(r'(?i)licen[cs]e|access.?key|api.?key|secret|token|password|authorization|authentication', line):
            line = '[credential-related output omitted]'
        else:
            line = self._redact(line)
        with self.guard:
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
            self._require_session(session_id)
            if command not in {'state', 'capture', 'input', 'wait'}:
                raise ValueError('不支持的运行请求')
            if not self._ready():
                raise ValueError('游戏桥接未就绪；查询 runtime_status 和 get_runtime_output')
            if self.pending:
                raise ValueError('上个请求结果尚未确认；先查询 runtime_status，不要重试有副作用的操作')
            request_id = uuid4().hex
            self.pending = request_id
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

    def inspect(self, session_id, object_name='', variables=None, limit=32):
        integer(limit, 'limit', 1, 100)
        variables = ['x', 'y', 'hspeed', 'vspeed', 'image_index', 'visible'] if variables is None else variables
        if not isinstance(variables, list) or len(variables) > 16 or any(
                not isinstance(v, str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}', v) for v in variables):
            raise ValueError('variables 最多 16 个变量名，不接受表达式')
        if object_name and not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}', object_name):
            raise ValueError('object_name 必须是有效的英语标识符')
        result = self.request(session_id, 'state', {'object_name': object_name,
            'variables': variables, 'limit': limit})
        if isinstance(result.get('data'), dict) and 'error' in result['data']:
            raise ValueError(self._redact(str(result['data']['error'])))
        return result

    def input(self, session_id, keys, frames=1, dry_run=True):
        integer(frames, 'frames', 1, 300)
        codes = key_codes(keys)
        with self.guard:
            self._require_session(session_id)
        if dry_run:
            return {'dry_run': True, 'session_id': session_id, 'keys': keys, 'frames': frames}
        return self.request(session_id, 'input', {'keys': codes, 'frames': frames},
                            timeout=max(15, frames / 10 + 10))

    def wait(self, session_id, frames=1):
        integer(frames, 'frames', 1, 600)
        return self.request(session_id, 'wait', {'frames': frames},
                            timeout=max(15, frames / 10 + 10))

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

    def stop(self, session_id, dry_run=True):
        with self.guard:
            session = self._require_session(session_id)
            if dry_run:
                return {'dry_run': True, 'session_id': session_id, 'action': 'stop_owned_session'}
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
        self._cleanup(session)
        return {'session_id': session_id, 'stopped': True,
                'exit_code': process.poll() if process else None, 'cleaned': True}

    def _expire(self, session_id):
        try:
            self.stop(session_id, dry_run=False)
        except (ValueError, OSError, subprocess.SubprocessError):
            pass

    def close(self):
        if self.session:
            self._expire(self.session['id'])
