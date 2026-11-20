"""明确serial的ADB设备会话；不卸载、不清数据、不访问其他应用日志。"""
import base64
from collections import deque
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import time
from uuid import uuid4
from PIL import Image

from . import android_artifacts, android_toolchain
from .android_process import run_command
from .display import integer
from .resources import name_check
from .runtime import utc_time
from .storage import compact_encode


def focus_packages(output, field):
    """只从指定焦点字段取组件；不把窗口列表里的后台包当作前台。"""
    values = re.findall(r'^\s*' + re.escape(field) + r'\s*[=:]\s*([^\r\n]*)', output, re.M)
    packages = set()
    for value in values:
        match = re.search(r'(?<![A-Za-z0-9_.])([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+)/[A-Za-z0-9_.$]+', value)
        packages.add(match[1] if match else None)
    return packages


def positive_frame(value):
    # GML 的 JSON 整数值也可能编码为 60.0；拒绝 bool、非整数及非有限值。
    return type(value) in (int, float) and 0 < value <= 2**53-1 and value == int(value)


def display_rotation(output):
    candidates = []
    for line in output.splitlines():
        start = line.find('DisplayInfo{')
        if start < 0: continue
        # modes/HDR/cutout 里有嵌套花括号，rotation 通常位于它们之后。
        depth, quoted, escaped, fields = 1, False, False, []
        for char in line[start+len('DisplayInfo{'):]:
            if quoted:
                if escaped: escaped = False
                elif char == '\\': escaped = True
                elif char == '"': quoted = False
                continue
            if char == '"': quoted = True; continue
            if char == '{': depth += 1; continue
            if char == '}':
                depth -= 1
                if depth == 0: break
                continue
            if depth == 1: fields.append(char)
        else: continue
        text = ''.join(fields)
        identifiers = re.findall(r'\bdisplayId(?:\s*=\s*|\s+)(\d+)\b', text)
        rotations = re.findall(r'\brotation(?:\s*=\s*|\s+)(\d+)\b', text)
        if identifiers != ['0'] or len(rotations) != 1: continue
        prefix = line[:start]
        priority = 0 if re.search(r'\bmDisplayInfo\s*=', prefix) else 1 if re.search(r'\bmOverrideDisplayInfo\s*=', prefix) else 2
        candidates.append((priority, int(rotations[0])))
    if not candidates: raise ValueError('无法确定默认显示的旋转，拒绝输入')
    priority = min(row[0] for row in candidates)
    rotations = {value for rank,value in candidates if rank == priority}
    if len(rotations) != 1 or not rotations <= {0,1,2,3}:
        raise ValueError('默认显示旋转存在冲突，拒绝输入')
    return rotations.pop()


def serial_check(serial):
    if not isinstance(serial, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}', serial):
        raise ValueError('须明确提供设备serial，不接受自动选择或shell字符')
    return serial


def package_check(package):
    if not isinstance(package, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', package):
        raise ValueError('无效Android包名')
    return package


def redact(text):
    if re.search(r'(?i)password|passwd|credential|authorization|token|access.?key', text):
        return '[credential-related app log omitted]'
    text = re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '[account omitted]', text)
    return text[:2048]


class AndroidManager:
    def __init__(self, project, options):
        self.project, self.options = project, options
        self.guard = threading.RLock()
        self.sessions = {}
        self.installs = {}

    def _chain(self):
        if not self.options.enabled:
            raise ValueError('ADB操作需 --allow-run；不会自动启用设备调试或安装SDK')
        chain = android_toolchain.artifact_tools(self.project, self.options)
        if not chain['components']['adb']:
            raise ValueError('未发现已安装的ADB；先检查Android工具链配置')
        return chain

    def _adb(self, arguments, serial=None, timeout=15, capture_limit=1024**2, check=True):
        command = [self._chain()['components']['adb']]
        if serial is not None: command += ['-s', serial_check(serial)]
        # ADB server是设备共享服务，不能随一个短命ADB客户端的Job一起终止。
        result = run_command(command + arguments, timeout=timeout, capture_limit=capture_limit, manage_tree=False)
        if check and result['exit_code']:
            # 只返回受限分类；不把完整设备命令输出/包路径当作错误日志导出。
            output = result['output']
            reason = next((value for value in ('unauthorized', 'offline', 'not found', 'INSTALL_FAILED_UPDATE_INCOMPATIBLE',
                         'INSTALL_FAILED_VERSION_DOWNGRADE', 'INSTALL_FAILED_INSUFFICIENT_STORAGE', 'not debuggable') if value in output), 'adb_failed')
            raise ValueError('ADB操作失败：' + reason + '；不会自动卸载、清数据或重新安装')
        return result

    def devices(self):
        output = self._adb(['devices', '-l'])['output']
        rows = []
        for line in output.splitlines():
            match = re.match(r'^(\S+)\s+(device|offline|unauthorized|recovery|sideload|no permissions)(?:\s+(.*))?$', line)
            if not match: continue
            serial_check(match[1])
            if len(rows) >= 32: raise ValueError('设备超过32个，请缩小ADB连接范围')
            attributes = dict(re.findall(r'(model|product|device|transport_id):([^\s]+)', match[3] or ''))
            row = {'serial': match[1], 'state': match[2], **attributes}
            if row['state'] == 'device':
                api = self._adb(['shell', 'getprop', 'ro.build.version.sdk'], row['serial'])['output'].strip()
                abi = self._adb(['shell', 'getprop', 'ro.product.cpu.abilist'], row['serial'])['output'].strip()
                row.update(api=int(api) if api.isdigit() else None,
                           abis=[value for value in abi.split(',') if re.fullmatch('[A-Za-z0-9_-]+', value)])
            rows.append(row)
        return {'devices': rows, 'explicit_serial_required': True, 'multitouch': False,
                'device_settings_changed': False}

    def _device(self, serial):
        serial_check(serial)
        row = next((row for row in self.devices()['devices'] if row['serial'] == serial), None)
        if not row or row['state'] != 'device': raise ValueError('目标设备未连接、离线或尚未授权')
        return row

    def _inspect(self, path, expected_sha256):
        if not re.fullmatch('[a-f0-9]{64}', expected_sha256 or ''):
            raise ValueError('须提供已检查APK的expected_sha256')
        try:
            artifact = android_artifacts.inspect(path, self._chain(), expected_sha256)
        except ValueError as exc:
            raise ValueError('待安装/运行APK校验失败：' + str(exc)) from None
        if artifact['artifact_type'] != 'apk': raise ValueError('只直接安装APK；AAB须先用bundletool生成设备APK')
        package_check(artifact['package_name'])
        return artifact

    def _installed(self, serial, package):
        package_check(package)
        result = self._adb(['shell', 'pm', 'path', package], serial, check=False)
        output = result['output']
        if result['exit_code'] and output.strip() and 'package not found' not in output.lower():
            raise ValueError('无法检查目标包是否已安装；不会猜测或继续安装')
        paths = [line[8:].strip() for line in output.splitlines() if line.startswith('package:')]
        if not paths:
            if output.strip() and 'package not found' not in output.lower():
                raise ValueError('目标包路径查询结果不明确')
            return None
        path = next((path for path in paths if path.endswith('/base.apk')), paths[0])
        if (not re.fullmatch(r'/(?:data/app|system/app|system/priv-app)/[A-Za-z0-9_./+=~-]+\.apk', path)
                or '..' in path.split('/')):
            raise ValueError('已安装APK路径不受支持；不读取其他设备文件')
        # 只取目标应用的可执行包核对公开签名，不拉取数据目录。
        with tempfile.TemporaryDirectory(prefix='morx-installed-apk-') as directory:
            local = Path(directory) / 'base.apk'
            self._adb(['pull', path, str(local)], serial, timeout=120)
            try:
                # 旧包只用于核对身份/升级，协议不兼容不能阻断合法的保数据覆盖。
                # ZIP、官方签名、包名与版本检查仍完整执行；候选包和运行保持严格校验。
                artifact = android_artifacts.inspect(local, self._chain(), require_current_bridge=False)
            except ValueError as exc:
                raise ValueError('设备已安装APK校验失败：' + str(exc)) from None
        if artifact['package_name'] != package: raise ValueError('设备APK包名不匹配')
        return {**{key: artifact[key] for key in ('package_name', 'versionName', 'versionCode', 'signature')},
                'development_bridge_compatibility': artifact['development_bridge_compatibility']}

    def install(self, serial, path, expected_sha256, allow_upgrade=False, dry_run=True):
        with self.guard:
            device = self._device(serial)
            artifact = self._inspect(path, expected_sha256)
            package = artifact['package_name']
            if any(row['serial'] == serial and row['package'] == package and row['state'] in ('running', 'background')
                   for row in self.sessions.values()):
                raise ValueError('目标应用有活动受管会话，先停止再升级')
            if artifact['min_sdk'] is not None and (not device['api'] or device['api'] < artifact['min_sdk']):
                raise ValueError('设备API低于APK最低要求')
            if artifact['abis'] and not set(artifact['abis']) & set(device['abis']):
                raise ValueError('APK ABI与设备不兼容')
            installed = self._installed(serial, package)
            compatible = installed is None or installed['signature']['certificate_sha256'] == artifact['signature']['certificate_sha256']
            downgrade = installed is not None and installed['versionCode'] > artifact['versionCode']
            ready = compatible and not downgrade and (installed is None or allow_upgrade)
            preview = {'dry_run': dry_run, 'serial': serial, 'artifact': artifact, 'installed': installed,
                       'upgrade': installed is not None, 'signature_compatible': compatible, 'downgrade': downgrade,
                       'allow_upgrade': allow_upgrade, 'preserve_data': True, 'ready': ready}
            if dry_run: return preview
            if not ready: raise ValueError('安装预检未通过；升级须明确allow_upgrade，签名冲突/降级不会自动修复')
            # 读取设备与包元数据的间隔中，源文件也必须保持不变。
            if android_artifacts.checksum(Path(artifact['path'])) != expected_sha256:
                raise ValueError('待安装APK已改变')
            self._adb(['install', '-r', artifact['path']], serial, timeout=180)
            actual = self._installed(serial, package)
            if not actual or any(actual[key] != artifact[key] for key in ('versionCode', 'signature')):
                raise ValueError('安装完成后的版本/签名不匹配，未启动应用')
            self.installs[(serial, package)] = {'sha256': expected_sha256, 'artifact': artifact}
            return {**preview, 'installed_verified': True}

    def _pid(self, serial, package):
        result = self._adb(['shell', 'pidof', '-s', package_check(package)], serial, check=False)
        value = result['output'].strip()
        if not value: return None
        if not re.fullmatch('[1-9][0-9]{0,9}', value): raise ValueError('不能确定目标应用的唯一PID')
        return int(value)

    def _foreground(self, row):
        # API35 的 windows 子命令可能没有 DisplayContent 焦点字段。
        # 只保留判断，不将窗口列表或其他应用标题返回给调用者。
        output = self._adb(['shell', 'dumpsys', 'window'], row['serial'])['output']
        focus = focus_packages(output, 'mCurrentFocus')
        if focus:
            return focus == {row['package']}
        # 明确的 null/其他窗口会在上面拒绝；只在字段缺失时核对 Activity。
        focused = focus_packages(output, 'mFocusedApp')
        activity = self._adb(['shell', 'dumpsys', 'activity', 'activities'], row['serial'])['output']
        resumed = focus_packages(activity, 'topResumedActivity')
        if not resumed:
            resumed = focus_packages(activity, 'mResumedActivity')
        return resumed == {row['package']} and (not focused or focused == {row['package']})

    def _row(self, session_id, active=True):
        if session_id not in self.sessions: raise ValueError('Android会话不存在或已淘汰')
        row = self.sessions[session_id]
        if active:
            if row['state'] not in ('running', 'background'): raise ValueError('会话已经结束')
            if self._pid(row['serial'], row['package']) != row['pid']:
                row.update(state='exited', exit_reason='process_exited_or_replaced', finished_at=utc_time())
                raise ValueError('原应用进程已退出或被替换；不会接管新进程')
        return row

    def run(self, serial, path, expected_sha256, max_seconds=900, dry_run=True):
        integer(max_seconds, 'max_seconds', 1, 900)
        with self.guard:
            self._device(serial)
            artifact = self._inspect(path, expected_sha256)
            package = artifact['package_name']
            component = artifact.get('launchable_activity')
            if (not component or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*|\.[A-Za-z_][A-Za-z0-9_.]*', component)
                    or (not component.startswith('.') and not component.startswith(package + '.'))):
                raise ValueError('APK没有明确属于该包的可启动Activity')
            prior = self.installs.get((serial, package))
            if not prior or prior['sha256'] != expected_sha256:
                raise ValueError('先通过本服务install_android_package核对并安装该APK；不接管外部已运行应用')
            if self._pid(serial, package) is not None: raise ValueError('目标包已经运行，请用户结束原运行后重试')
            preview = {'dry_run': dry_run, 'serial': serial, 'package_name': package,
                       'component': package + '/' + component, 'max_seconds': max_seconds,
                       'development_bridge': bool(artifact.get('development_bridge'))}
            if dry_run: return preview
            if sum(row['state'] in ('running', 'background') for row in self.sessions.values()) >= 4:
                raise ValueError('最多4个活动Android会话')
            if len(self.sessions) >= 32:
                for sid, row in list(self.sessions.items()):
                    if row['state'] not in ('running', 'background'): del self.sessions[sid]
                    if len(self.sessions) < 32: break
            sid = uuid4().hex
            row = {'id': sid, 'serial': serial, 'package': package, 'component': preview['component'],
                   'state': 'starting', 'created_at': utc_time(), 'deadline': time.monotonic() + max_seconds,
                   'started_epoch': time.time(),
                   'expires_at': utc_time(time.time() + max_seconds), 'bridge': artifact.get('development_bridge'),
                   'logs': deque(maxlen=200), 'seen': deque(maxlen=500), 'cursor': 0, 'touch': None,
                   'lock': threading.RLock(), 'request': 0}
            self.sessions[sid] = row
            try:
                if row['bridge']:
                    if not artifact['debuggable']: raise ValueError('开发桥接需要已验证debuggable APK')
                    self._write(row, 'session.json', {'bridge': row['bridge']['bridge'], 'session': sid})
                    # 先暂存一次无副作用握手，应用启动后须由真正的 GML 回复。
                    row['bridge_probe'] = uuid4().hex
                    self._write(row, 'request.json', {'session': sid, 'request': row['bridge_probe'],
                                                     'command': 'ping', 'args': {}})
                self._adb(['shell', 'am', 'start', '-W', '-n', row['component']], serial, timeout=30)
                row['pid'] = self._pid(serial, package)
                if row['pid'] is None: raise ValueError('启动后没有目标应用PID')
                row['state'] = 'running'
                timer = threading.Timer(max_seconds, self._expire, args=(sid,))
                timer.daemon = True; row['timer'] = timer; timer.start()
            except Exception:
                row.update(state='failed', exit_reason='launch_failed', finished_at=utc_time())
                raise
            return {**preview, 'session_id': sid, 'state': row['state'], 'next': '查询android_game_status，桥接就绪后再读状态/截图'}

    def _write(self, row, name, data):
        if name not in ('session.json', 'request.json'): raise ValueError('不支持的桥接文件')
        raw = compact_encode(data)
        if len(raw) > 16384: raise ValueError('桥接请求超过16KiB')
        text = base64.b64encode(raw).decode('ascii')
        directory = 'files/morx_mcp'
        # 包名/文件名/负载均经过独立白名单；不接受用户shell文本。
        script = f"mkdir -p {directory} && printf %s {text} | base64 -d > {directory}/{name}.tmp && mv {directory}/{name}.tmp {directory}/{name}"
        self._adb(['shell', 'run-as', package_check(row['package']), 'sh', '-c', "'" + script + "'"], row['serial'])

    def _read(self, row, name, image=False):
        if name not in ('ready.json', 'response.json', 'errors.json') and not re.fullmatch(r'capture_[a-f0-9]{32}\.png', name):
            raise ValueError('不支持的桥接读取文件')
        # exec-out 不传播远端 cat/run-as 的失败码，会把“文件不存在”误当 JSON。
        # 数据已为 JSON/base64 文本，使用支持远端退出码的 shell 传输。
        command = ['shell', 'run-as', package_check(row['package'])]
        command += ['base64' if image else 'cat', 'files/morx_mcp/' + name]
        result = self._adb(command, row['serial'], capture_limit=32*1024**2 if image else 1024**2, check=False)
        if result['exit_code']: return None
        try:
            if image: return base64.b64decode(result['output'], validate=False)
            text = result['output'].strip().lstrip('\ufeff')
            if not text.strip(): return None
            data = json.loads(text)
            if not isinstance(data, dict) or data.get('session') != row['id']: return None
            return data
        except (ValueError, TypeError):
            raise ValueError('应用开发桥接返回了无效数据') from None

    def status(self, session_id):
        with self.guard:
            row = self._row(session_id, False)
            if row['state'] in ('running', 'background'):
                if self._pid(row['serial'], row['package']) != row['pid']:
                    row.update(state='exited', exit_reason='process_exited_or_replaced', finished_at=utc_time())
            if row['state']=='exited':
                try:self._collect_logs(row)
                except Exception as exc:row['log_collection_note']='final_log_collection_failed:'+type(exc).__name__
            health, ready = self._bridge_health(row)
            return {'session_id': session_id, 'serial': row['serial'], 'package_name': row['package'],
                    'state': row['state'], 'pid': row.get('pid'), 'created_at': row['created_at'], 'expires_at': row['expires_at'],
                    'remaining_seconds': max(0, round(row['deadline']-time.monotonic(), 1)) if row['state'] in ('running','background') else 0,
                    'development_bridge': bool(row['bridge']), 'bridge_ready': health['ready'], 'bridge': ready,
                    'bridge_status': health,
                    'exit_reason': row.get('exit_reason'), 'touch_down': row['touch'] is not None,
                    'touch_release_error':row.get('touch_release_error'), 'stop_error':row.get('stop_error'),
                    'last_runtime_error':row.get('last_runtime_error'),
                    'bridge_cleanup_error':row.get('bridge_cleanup_error'), 'multitouch': False}

    def _bridge_health(self, row):
        health = {'ready': False, 'reason': 'development_bridge_required',
                  'handshake_confirmed': False, 'storage': 'files/morx_mcp'}
        if not row.get('bridge'): return health, None
        if row['state'] != 'running':
            health['reason'] = 'session_not_running'
            return health, None
        ready = self._read(row, 'ready.json')
        health['reason'] = 'gml_heartbeat_missing'
        if not ready: return health, None
        if (ready.get('session') != row['id'] or ready.get('bridge') != row['bridge']['bridge']
                or ready.get('protocol') != 2 or ready.get('channel') != 'gml_file'
                or ready.get('storage') != 'game_save_id'):
            health['reason'] = 'gml_protocol_mismatch; rebuild_development_apk'
            return health, ready
        frame = ready.get('frame')
        if not positive_frame(frame):
            health['reason'] = 'gml_not_advancing'
            return health, ready
        now = time.monotonic()
        if frame > row.get('bridge_frame', -1):
            row.update(bridge_frame=frame, bridge_progress_at=now)
        if not row.get('bridge_handshake'):
            response = self._read(row, 'response.json')
            data = response.get('data') if response else None
            if (row.get('bridge_probe') and response and response.get('session') == row['id']
                    and response.get('request') == row['bridge_probe']
                    and response.get('ok') is True and isinstance(data, dict)
                    and data.get('channel') == 'gml_file' and data.get('protocol') == 2
                    and data.get('storage') == 'game_save_id' and data.get('bridge') == row['bridge']['bridge']
                    and positive_frame(data.get('frame'))):
                row['bridge_handshake'] = True
        health.update(handshake_confirmed=bool(row.get('bridge_handshake')),
                      frame=frame, progress_age_seconds=round(max(0, now-row['bridge_progress_at']), 2))
        if frame < row['bridge_frame'] or now-row['bridge_progress_at'] > 10:
            health['reason'] = 'gml_heartbeat_stale'
        elif not row.get('bridge_handshake'):
            health['reason'] = 'awaiting_gml_handshake'
        else:
            health.update(ready=True, reason='ready')
        return health, ready

    def _expire(self, session_id):
        try: self.stop(session_id, False, 'session_timeout')
        except Exception as exc:
            self.sessions[session_id]['stop_error'] = type(exc).__name__

    def _release(self, row):
        touch = row.get('touch')
        if touch is None: return
        try:
            self._adb(['shell', 'input', 'touchscreen', 'motionevent', 'UP', str(touch[0]), str(touch[1])], row['serial'])
        except Exception as exc:
            row['touch_release_error'] = type(exc).__name__
            raise
        else:
            row['touch'] = None
            row.pop('touch_release_error', None)
        finally:
            timer = row.pop('touch_timer', None)
            if timer: timer.cancel()

    def stop(self, session_id, dry_run=True, reason='explicit_stop'):
        with self.guard:
            row = self._row(session_id, False)
            result = {'session_id': session_id, 'dry_run': dry_run, 'state': row['state'], 'preserve_data': True}
            if dry_run or row['state'] not in ('running', 'background'): return result
            with row['lock']:
                self._release(row)
                if self._pid(row['serial'], row['package']) == row['pid']:
                    try:self._collect_logs(row)
                    except Exception as exc:row['log_collection_note']='final_log_collection_failed:'+type(exc).__name__
                    if row['state'] in ('running','background') and self._pid(row['serial'],row['package'])==row['pid']:
                        self._adb(['shell', 'am', 'force-stop', row['package']], row['serial'])
                    else:reason='process_exited_or_replaced'
                else: reason = 'process_exited_or_replaced'
                row['timer'].cancel()
                row.update(state='stopped', exit_reason=reason, finished_at=utc_time())
                return {**result, 'state': row['state'], 'exit_reason': reason}

    def lifecycle(self, session_id, action, dry_run=True):
        if action not in ('background', 'resume'): raise ValueError('action须为background/resume')
        with self.guard:
            row = self._row(session_id)
            if not dry_run:
                self._release(row)
                if action == 'background':
                    if not self._foreground(row): raise ValueError('目标应用不在前台，不发送Home键')
                    self._adb(['shell', 'input', 'keyevent', 'KEYCODE_HOME'], row['serial'])
                else:
                    self._adb(['shell', 'am', 'start', '-W', '-n', row['component']], row['serial'], timeout=30)
                row['state'] = 'background' if action == 'background' else 'running'
            return {'session_id': session_id, 'dry_run': dry_run, 'action': action, 'state': row['state']}

    def _metrics(self, row):
        size = self._adb(['shell', 'wm', 'size'], row['serial'])['output']
        density = self._adb(['shell', 'wm', 'density'], row['serial'])['output']
        sizes = re.findall(r'(?:Physical|Override) size:\s*(\d+)x(\d+)', size)
        densities = re.findall(r'(?:Physical|Override) density:\s*(\d+)', density)
        if not sizes: raise ValueError('无法确定屏幕尺寸，拒绝猜测输入坐标')
        width, height = map(int, sizes[-1])
        # wm size给出自然方向；显示旋转由DisplayInfo明确读取。
        display = self._adb(['shell', 'dumpsys', 'display'], row['serial'])['output']
        rotation = display_rotation(display)
        if rotation in (1, 3): width, height = height, width
        return {'width': width, 'height': height, 'rotation': rotation,
                'density_dpi': int(densities[-1]) if densities else None,
                'source': 'adb_wm_and_display', 'coordinate_space': 'display_pixels'}

    def input(self, session_id, action, x=None, y=None, end_x=None, end_y=None, duration_ms=300, dry_run=True):
        if action not in ('tap', 'swipe', 'down', 'move', 'up', 'back'):
            raise ValueError('只支持单指tap/swipe/down/move/up/back；多指未实现')
        integer(duration_ms, 'duration_ms', 1, 2000)
        with self.guard:
            row = self._row(session_id)
            with row['lock']:
                try:
                    if not self._foreground(row): raise ValueError('目标应用不在前台，拒绝输入')
                    metrics = self._metrics(row)
                    if action != 'back':
                        integer(x, 'x', 0, metrics['width']-1); integer(y, 'y', 0, metrics['height']-1)
                    if action == 'swipe':
                        integer(end_x, 'end_x', 0, metrics['width']-1); integer(end_y, 'end_y', 0, metrics['height']-1)
                    if action in ('down','move','up'):
                        help_text = self._adb(['shell', 'input'], row['serial'], check=False)['output']
                        if 'motionevent' not in help_text: raise ValueError('此Android input不支持独立按下/移动/抬起；可用原子swipe')
                    if (action in ('move','up') and row['touch'] is None) or (action == 'down' and row['touch'] is not None):
                        raise ValueError('触点状态不匹配')
                    if row['touch'] is not None and action not in ('move','up'):
                        raise ValueError('触点仍按下，请先抬起')
                    if not dry_run:
                        if action == 'back': args = ['keyevent', 'KEYCODE_BACK']
                        # 原生 tap 的 DOWN/UP 可在同一游戏帧内完成，duration_ms 也被忽略。
                        # 同点 swipe 保持明确时长，让轮询触点状态的 Runner 能看到按下。
                        elif action == 'tap': args = ['touchscreen', 'swipe', str(x), str(y), str(x), str(y), str(duration_ms)]
                        elif action == 'swipe': args = ['touchscreen', 'swipe', str(x), str(y), str(end_x), str(end_y), str(duration_ms)]
                        else:
                            args = ['touchscreen', 'motionevent', action.upper(), str(x), str(y)]
                            row['touch'] = (x,y)
                        if action in ('tap','swipe'): row['touch']=(x,y)
                        self._adb(['shell', 'input'] + args, row['serial'], timeout=10)
                        if action in ('up','tap','swipe'):
                            row['touch'] = None
                            timer = row.pop('touch_timer', None)
                            if timer: timer.cancel()
                        elif action in ('down','move'):
                            timer = row.pop('touch_timer', None)
                            if timer: timer.cancel()
                            timer = threading.Timer(2, self._touch_expire, args=(row,))
                            timer.daemon = True; row['touch_timer'] = timer; timer.start()
                    return {'session_id': session_id, 'action': action, 'dry_run': dry_run,
                            'display': metrics, 'touch_down': row['touch'] is not None, 'multitouch': False,
                            'touch_lease_seconds': 2 if row['touch'] else None}
                except Exception:
                    if not dry_run: self._release(row)
                    raise

    def _touch_expire(self, row):
        with row['lock']:
            try: self._release(row)
            except Exception as exc: row['touch_release_error'] = type(exc).__name__

    def _request(self, row, action, arguments):
        if not row['bridge']: raise ValueError('仅显式开发桥接APK支持应用状态/画面；不会改用整机截屏')
        if not self._foreground(row): raise ValueError('目标应用不在前台')
        with row['lock']:
            health, _ = self._bridge_health(row)
            if not health['ready']:
                raise ValueError('开发桥接尚未就绪：' + health['reason'] + '；查询android_game_status，不重放请求')
            request = uuid4().hex
            self._write(row, 'request.json', {'session': row['id'], 'request': request, 'command': action, 'args': arguments})
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                response = self._read(row, 'response.json')
                if response and response.get('request') == request:
                    if not response.get('ok'): raise ValueError('应用桥接拒绝本次请求')
                    return response
                time.sleep(.05)
            raise ValueError('开发桥接请求超时，结果未知；未自动重放请求：' + request)

    def inspect(self, session_id, variables=None, object_name=None, max_depth=3):
        variables = variables or []
        if not isinstance(variables, list) or len(variables)>16 or len(set(variables)) != len(variables):
            raise ValueError('variables须为最多16项的明确变量名')
        for name in variables: name_check(name)
        if object_name is not None: name_check(object_name)
        integer(max_depth, 'max_depth', 1, 8)
        with self.guard:
            row = self._row(session_id)
            result = self._request(row, 'state', {'scope': 'instances' if object_name else 'globals',
                        'object_name': object_name or '', 'variables': variables, 'limit':16, 'max_depth': max_depth})
            return {'session_id':session_id, 'source':'development_gml_bridge', 'result':result,
                    'dimensions_unit':'game_pixels', 'density_unit':'not_dp', 'soft_keyboard_height':None}

    def capture(self, session_id):
        with self.guard:
            row = self._row(session_id)
            result = self._request(row, 'capture', {})
            request = result['request']
            name = 'capture_' + request + '.png'
            try:
                raw = self._read(row, name, True)
                if not raw or len(raw)>24*1024**2: raise ValueError('应用截图缺失或超过24MiB')
                with Image.open(io.BytesIO(raw)) as picture:
                    if picture.format != 'PNG' or picture.width*picture.height>32*1024**2: raise ValueError('截图尺寸/格式超限')
                    size = list(picture.size)
                    expected = result.get('data', {}).get('capture', {}).get('raw_size')
                    if expected != size: raise ValueError('截图尺寸与GML完整窗口范围不一致，拒绝返回可能裁切的画面')
                    picture.verify()
            finally:
                try:
                    if not re.fullmatch(r'capture_[a-f0-9]{32}\.png',name): raise ValueError('截图文件标识无效')
                    self._adb(['shell','run-as',row['package'],'rm','-f','files/morx_mcp/'+name],row['serial'])
                except Exception as exc: row['bridge_cleanup_error']=type(exc).__name__
            if not self._foreground(row): raise ValueError('截图期间应用离开前台，本次画面不返回')
            return {'session_id':session_id, 'scope':'application_window', 'raw_size':size,
                    'display':self._metrics(row), 'source':'development_gml_screen_save_part',
                    'capture':result.get('data', {}).get('capture'),
                    'includes_other_apps':False, 'image':raw}

    def _collect_logs(self,row):
        if not row.get('pid') or row.get('final_logs_collected') or row['state'] not in ('running','background','exited'):
            return
        if row['state'] in ('running','background') and self._pid(row['serial'],row['package'])!=row['pid']:
            row.update(state='exited',exit_reason='process_exited_or_replaced',finished_at=utc_time())
        if row['state']=='exited':
            # 结束后仍可读取本PID留下的崩溃记录；若PID仍被进程占用，则只保留缓存，避免复用泄露。
            owner=self._adb(['shell','ps','-p',str(row['pid']),'-o','NAME'],row['serial'],check=False)['output']
            if any(line.strip() and line.strip()!='NAME' for line in owner.splitlines()):
                row['log_collection_note']='original_pid_still_in_use; cached logs only'
                row['final_logs_collected']=True
                return
        text=self._adb(['logcat','--pid='+str(row['pid']),'-d','-t','200','-v','epoch'],row['serial'])['output']
        for line in text.splitlines():
            if not line or line.startswith('---------') or line in row['seen']: continue
            match=re.match(r'^\s*([0-9]+\.[0-9]+)\s+(\d+)\s+',line)
            if not match or int(match[2])!=row['pid'] or float(match[1])<row.get('started_epoch',0):continue
            row['seen'].append(line);row['cursor']+=1
            category='java_crash' if 'FATAL EXCEPTION' in line else 'native_crash' if re.search(r'Fatal signal|SIGSEGV|SIGABRT',line) else 'gml_error' if re.search(r'Error in action|ERROR!!!|gml_Object_',line) else 'app_log'
            entry={'cursor':row['cursor'],'category':category,'text':redact(line)}
            row['logs'].append(entry)
            if category!='app_log':row['last_runtime_error']=entry
        if row['state']=='exited':row['final_logs_collected']=True

    def logs(self, session_id, after=0, limit=50):
        integer(after, 'after', 0, 2**63-1); integer(limit, 'limit', 1, 100)
        with self.guard:
            row = self._row(session_id, False)
            self._collect_logs(row)
            lines = [line for line in row['logs'] if line['cursor']>after][:limit]
            return {'session_id':session_id, 'pid':row.get('pid'), 'lines':lines,
                    'next_cursor':lines[-1]['cursor'] if lines else after,
                    'truncated':bool(row['logs'] and after<row['logs'][0]['cursor']-1), 'scope':'target_pid_only',
                    'collection_note':row.get('log_collection_note')}

    def close(self):
        for sid in list(self.sessions):
            try: self.stop(sid, False, 'service_shutdown')
            except Exception as exc: self.sessions[sid]['stop_error'] = type(exc).__name__
