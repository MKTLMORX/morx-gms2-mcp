"""本机签名配置引用；口令只在环境或当前用户 DPAPI 中，不通过工具参数返回。"""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import subprocess
from uuid import uuid4

from .android_toolchain import absolute, discover
from .exports import expected
from .resources import name_check
from .runtime import link_free
from .storage import FileConflict, digest, encode

REGISTRY = '.codex/morx-android-signing.json'


def registry(project):
    path = project.path(REGISTRY)
    raw = path.read_bytes() if path.is_file() else None
    if raw is not None and len(raw) > 65536:
        raise ValueError('签名配置超过64KiB')
    data = json.loads(raw.decode('utf-8-sig')) if raw is not None else {'version': 1, 'profiles': {}}
    if not isinstance(data, dict) or set(data) != {'version', 'profiles'} or data['version'] != 1:
        raise ValueError('不支持的签名配置')
    if not isinstance(data['profiles'], dict) or len(data['profiles']) > 32:
        raise ValueError('签名配置最多32项')
    for name, row in data['profiles'].items():
        name_check(name)
        if not isinstance(row, dict) or row.get('kind') not in ('development', 'existing'):
            raise ValueError('签名配置结构无效')
        absolute(row.get('keystore'), 'keystore')
        if not isinstance(row.get('alias'), str) or not re.fullmatch('[A-Za-z0-9_.-]{1,128}', row['alias']):
            raise ValueError('签名别名无效')
        if row['kind'] == 'existing':
            for key in ('store_password_env', 'key_password_env'):
                if not isinstance(row.get(key), str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}', row[key]):
                    raise ValueError('口令只接受环境变量名称引用')
        else:
            absolute(row.get('secret_file'), 'development_secret')
    return data, raw


class _Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(value, protect):
    if os.name != 'nt':
        raise ValueError('开发签名口令保存需要 Windows 当前用户 DPAPI')
    source = ctypes.create_string_buffer(value)
    incoming = _Blob(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = _Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise ValueError('当前用户无法保护/读取开发签名口令；不自动重建密钥')
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(outgoing.data)


def passwords(row):
    if row['kind'] == 'development':
        path = absolute(row['secret_file'], 'development_secret', exists=True)
        if path.stat().st_size > 65536:
            raise ValueError('开发签名口令文件无效')
        password = _dpapi(path.read_bytes(), False).decode('utf-8')
        return password, password
    result = [os.environ.get(row[name]) for name in ('store_password_env', 'key_password_env')]
    if any(not value or len(value) > 1024 or '\0' in value or '\n' in value or '\r' in value for value in result):
        raise ValueError('签名口令环境变量缺失或无效；不在工具参数中传递口令')
    return tuple(result)


def configure(project, name, keystore_path, alias, store_password_env, key_password_env=None,
              expected_sha256=None, dry_run=True):
    name_check(name)
    if name == 'development':
        raise ValueError('development 为隔离开发签名保留名称，请用创建接口')
    path = absolute(keystore_path, 'keystore', exists=True)
    if not path.is_file() or path.is_relative_to(project.root) or path.stat().st_size > 16 * 1024**2:
        raise ValueError('已有keystore须为项目之外最多16MiB的普通文件，不复制进项目')
    row = {'kind': 'existing', 'keystore': str(path), 'alias': alias,
           'store_password_env': store_password_env, 'key_password_env': key_password_env or store_password_env}
    if not isinstance(alias, str) or not re.fullmatch('[A-Za-z0-9_.-]{1,128}', alias):
        raise ValueError('签名别名无效')
    for key in ('store_password_env', 'key_password_env'):
        if not isinstance(row[key], str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}', row[key]):
            raise ValueError('只接受口令环境变量名称，不接受口令内容')
    with project.lock:
        data, raw = registry(project)
        expected(raw, expected_sha256, '签名配置')
        previous = data['profiles'].get(name)
        if previous and (previous.get('keystore') != str(path) or previous.get('alias') != alias):
            raise ValueError('不自动轮换已有签名文件；请创建另一个明确命名的配置')
        if previous is None and len(data['profiles']) >= 32:
            raise ValueError('签名配置最多32项')
        data['profiles'][name] = row
        return {**project.commit({REGISTRY: encode(data)}, {REGISTRY: raw}, dry_run=dry_run),
                'profile': name, 'kind': 'existing', 'keystore_copied': False,
                'note': '只保存既有密钥的引用；口令不写入本配置'}


def _keytool(options, project):
    paths, _ = discover(project, options)
    root = paths['jdk_root']['path']
    tool = Path(root) / 'bin/keytool.exe' if root else None
    if tool is None or not tool.is_file() or not link_free(tool):
        raise ValueError('未找到已配置 JDK 的 keytool')
    return tool


def create_development(project, options, expected_sha256=None, dry_run=True):
    with project.lock:
        data, raw = registry(project)
        expected(raw, expected_sha256, '签名配置')
        if 'development' in data['profiles']:
            return {'dry_run': dry_run, 'profile': 'development', 'created': False,
                    'next': '使用 inspect_android_signing 检查现有配置；不会重建或轮换'}
        if len(data['profiles']) >= 32:
            raise ValueError('签名配置最多32项')
        identity = digest(str(project.yyp).encode('utf-8'))[:16]
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'MORX-GMS2-MCP' / identity / 'signing'
        destination = base / ('development-' + uuid4().hex)
        if not link_free(destination):
            raise ValueError('开发签名目录不能包含链接')
        row = {'kind': 'development', 'keystore': str(destination / 'development.p12'),
               'secret_file': str(destination / 'password.dpapi'), 'alias': 'morx-development'}
        if dry_run:
            return {'dry_run': True, 'profile': 'development', 'created': False,
                    'keystore': row['keystore'], 'password_storage': 'current_user_dpapi',
                    'note': '创建独立测试密钥；不会使用或覆盖用户已有正式签名'}
        if not project.writable or not options.enabled:
            raise ValueError('创建开发签名需要 --write 和 --allow-run')
        tool = _keytool(options, project)
        password = secrets.token_hex(24)
        protected = _dpapi(password.encode('utf-8'), True)
        destination.mkdir(parents=True, exist_ok=False)
        secret = destination / 'password.dpapi'
        secret.write_bytes(protected)
        try:
            env = {**os.environ, 'MORX_ANDROID_STORE_PASS': password}
            args = [str(tool), '-genkeypair', '-noprompt', '-storetype', 'PKCS12', '-keystore', row['keystore'],
                    '-storepass:env', 'MORX_ANDROID_STORE_PASS', '-keypass:env', 'MORX_ANDROID_STORE_PASS',
                    '-alias', row['alias'], '-keyalg', 'RSA', '-keysize', '2048', '-validity', '10000',
                    '-dname', 'CN=MORX Development']
            result = subprocess.run(args, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if result.returncode or not Path(row['keystore']).is_file():
                raise ValueError('开发签名生成失败；未修改任何已有密钥')
            data['profiles']['development'] = row
            committed = project.commit({REGISTRY: encode(data)}, {REGISTRY: raw}, dry_run=False)
            return {**committed, 'profile': 'development', 'created': True, 'password_storage': 'current_user_dpapi'}
        except FileConflict:
            # Both the active registry and the transaction backup can reference this key.
            raise
        except BaseException:
            # 只移除本调用使用UUID新建的明确文件，不递归操作其他签名目录。
            for path in (Path(row['keystore']), secret):
                path.unlink(missing_ok=True)
            destination.rmdir()
            raise


def inspect(project, options, name=None, verify_certificate=False):
    data, raw = registry(project)
    if name is not None and name not in data['profiles']:
        raise ValueError('签名配置不存在')
    profiles = []
    for profile, row in data['profiles'].items():
        if name is not None and profile != name:
            continue
        path = absolute(row['keystore'], 'keystore')
        result = {'name': profile, 'kind': row['kind'], 'keystore_exists': path.is_file(),
                  'alias': row['alias'], 'password_available': False, 'alias_verified': False,
                  'certificate_sha256': None}
        try:
            store, key = passwords(row)
            result['password_available'] = True
            if verify_certificate:
                if not options.enabled:
                    raise ValueError('证书检查需要 --allow-run')
                tool = _keytool(options, project)
                output = subprocess.run([str(tool), '-J-Duser.language=en', '-J-Duser.country=US', '-list', '-v',
                    '-keystore', str(path), '-alias', row['alias'], '-storepass:env', 'MORX_ANDROID_STORE_PASS'],
                    env={**os.environ, 'MORX_ANDROID_STORE_PASS': store}, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                match = re.search(r'SHA256:\s*([A-Fa-f0-9:]{64,96})', output.stdout.decode('utf-8', errors='replace'))
                result['alias_verified'] = output.returncode == 0 and bool(match)
                if match:
                    result['certificate_sha256'] = match[1].replace(':', '').lower()
                if not result['alias_verified']:
                    result['error'] = '无法验证密钥口令/别名；不返回keytool原始账号或证书身份信息'
        except (ValueError, OSError, subprocess.SubprocessError):
            result['error'] = '签名材料或本机口令不可用；不自动生成替代密钥'
        profiles.append(result)
    return {'configuration_sha256': digest(raw), 'profiles': profiles}


def igor_password(password, license_path):
    """只取原生设置编码必需的账号标识，在内存中转换口令；不导出或复制许可证。"""
    path = absolute(str(license_path), 'existing_license', exists=True)
    if path.stat().st_size > 2 * 1024**2:
        raise ValueError('本机授权格式无法用于 Android 签名')
    try:
        data = plistlib.loads(path.read_bytes())
        identity = data.get('email')
        # LTS原生授权将账号标识放在Licensee.Email；较早CLI授权放在根email。
        if not identity and isinstance(data.get('Licensee'), dict):
            identity = data['Licensee'].get('Email')
    except Exception:
        raise ValueError('本机授权格式无法用于 Android 签名') from None
    if not isinstance(identity, str) or not identity or len(identity) > 512:
        raise ValueError('本机授权缺少签名设置编码所需的账号标识')
    mask = identity.encode('utf-8').hex().upper().encode('ascii')
    units = bytearray(password.encode('utf-16-le', errors='surrogatepass'))
    for offset in range(0, len(units), 2):
        unit = int.from_bytes(units[offset:offset+2], 'little') ^ mask[(offset//2) % len(mask)]
        units[offset:offset+2] = unit.to_bytes(2, 'little')
    return base64.b64encode(bytes(units).decode('utf-16-le', errors='replace').encode('utf-8')).decode('ascii')
