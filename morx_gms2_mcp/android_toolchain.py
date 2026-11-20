"""Android 工具链的只读发现和项目专属路径配置；不安装 SDK、不改 IDE。"""
import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from . import android
from .exports import expected
from .resources import Edit
from .runtime import link_free
from .storage import digest, encode, parse_yy

CONFIG = '.codex/morx-android-toolchain.json'
PATHS = {'sdk_root', 'ndk_root', 'jdk_root', 'bundletool_path', 'gradle_user_home', 'build_root', 'export_root'}
NATIVE_PATHS = {name: 'machine.Platform Settings.Android.Paths.' + stem + '_location'
                for name, stem in (('sdk_root', 'sdk'), ('ndk_root', 'ndk'), ('jdk_root', 'jdk'))}


def absolute(value, label, exists=False):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise ValueError(label + ' 须为明确的绝对路径，不含控制字符')
    path = Path(value)
    if not path.is_absolute() or not link_free(path):
        raise ValueError(label + ' 不接受相对路径、符号链接或目录联接')
    path = path.resolve(strict=exists)
    if path == Path(path.anchor):
        raise ValueError(label + ' 不能是磁盘根目录')
    return path


def configuration(project):
    path = project.path(CONFIG)
    raw = path.read_bytes() if path.is_file() else None
    if raw is not None and len(raw) > 16384:
        raise ValueError('Android 本机配置超过16KiB')
    data = json.loads(raw.decode('utf-8-sig')) if raw is not None else {'version': 1}
    if not isinstance(data, dict) or data.get('version') != 1 or set(data) - (PATHS | {'version'}):
        raise ValueError('不支持的 Android 本机路径配置')
    for name, value in data.items():
        if name != 'version' and value:
            absolute(value, name)
    return data, raw


def configure(project, paths, expected_sha256=None, dry_run=True):
    if not isinstance(paths, dict) or not paths or set(paths) - PATHS:
        raise ValueError('paths 只接受 sdk_root/ndk_root/jdk_root/bundletool_path/gradle_user_home/build_root/export_root')
    with project.lock:
        data, raw = configuration(project)
        expected(raw, expected_sha256, 'Android 本机配置')
        before = dict(data)
        for name, value in paths.items():
            if value is None:
                continue
            if value == '':
                data.pop(name, None)
            else:
                path = absolute(value, name)
                if name not in ('build_root', 'export_root', 'gradle_user_home') and not path.exists():
                    raise ValueError(name + ' 路径不存在；配置不自动安装工具')
                if name == 'bundletool_path' and (not path.is_file() or path.suffix.lower() != '.jar'):
                    raise ValueError('bundletool_path 须为已存在的 bundletool JAR')
                if name != 'bundletool_path' and path.exists() and not path.is_dir():
                    raise ValueError(name + ' 须为目录')
                if name in ('build_root', 'export_root') and (path == project.root or path in project.root.parents):
                    raise ValueError(name + ' 不能是源项目或其父目录')
                data[name] = str(path)
        result = project.commit({CONFIG: encode(data)}, {CONFIG: raw}, dry_run=dry_run)
        return {**result, 'before': before, 'settings': data,
                'note': '仅保存项目专属的路径引用；不修改系统环境变量、IDE偏好或安装工具'}


def ide_paths(options):
    path = Path(options.user_directory) / 'local_settings.json' if options.user_directory else None
    if path is None or not path.is_file() or path.stat().st_size > 2 * 1024**2 or not link_free(path):
        return {}
    data = parse_yy(path.read_text(encoding='utf-8-sig'))
    return {name: data[key] for name, key in NATIVE_PATHS.items() if isinstance(data.get(key), str) and data[key]}


def studio_sdk_candidates():
    roaming = os.environ.get('APPDATA')
    if not roaming:
        return []
    result = []
    base = Path(roaming) / 'Google'
    for file in sorted(base.glob('AndroidStudio*/options/jdk.table.xml'), reverse=True):
        if not link_free(file) or file.stat().st_size > 1024**2:
            continue
        raw = file.read_bytes()
        if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
            continue
        try:
            tree = ET.fromstring(raw)
        except ET.ParseError:
            continue
        for jdk in tree.iter('jdk'):
            kind, home = jdk.find('type'), jdk.find('homePath')
            if kind is not None and home is not None and kind.get('value') == 'Android SDK':
                value = home.get('value', '')
                if value and '$' not in value and value not in result:
                    result.append(value)
    return result


def _path_state(value, source):
    if not value:
        return {'path': None, 'source': source, 'state': 'not_configured'}
    try:
        path = absolute(value, source)
        return {'path': str(path), 'source': source, 'state': 'present' if path.exists() else 'path_not_found'}
    except (ValueError, OSError):
        return {'path': None, 'source': source, 'state': 'invalid_path'}


def discover(project, options):
    data, raw = configuration(project)
    ide = ide_paths(options)
    result = {}
    environment = {'sdk_root': ('ANDROID_HOME', 'ANDROID_SDK_ROOT'), 'ndk_root': ('ANDROID_NDK_ROOT', 'ANDROID_NDK_HOME'),
                   'jdk_root': ('JAVA_HOME',), 'gradle_user_home': ('GRADLE_USER_HOME',)}
    for name in PATHS:
        if data.get(name):
            result[name] = _path_state(data[name], 'project_local_configuration')
        elif ide.get(name):
            result[name] = _path_state(ide[name], 'ide_preference')
        else:
            found = [(os.environ[var], 'environment:' + var) for var in environment.get(name, ()) if os.environ.get(var)]
            if found:
                result[name] = _path_state(*found[0])
    if 'sdk_root' not in result:
        candidates = studio_sdk_candidates()
        default = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Android/Sdk'
        candidates += [str(default)]
        valid = [candidate for candidate in candidates if Path(candidate).is_dir() and link_free(candidate)]
        result['sdk_root'] = _path_state(valid[0] if valid else None, 'android_studio_or_standard_location')
    if 'jdk_root' not in result:
        candidate = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Android/Android Studio/jbr'
        result['jdk_root'] = _path_state(str(candidate) if candidate.is_dir() else None, 'android_studio_jbr')
    if 'ndk_root' not in result:
        sdk = result['sdk_root']['path']
        candidates = sorted((Path(sdk) / 'ndk').glob('*/source.properties')) if sdk else []
        result['ndk_root'] = _path_state(str(candidates[0].parent) if len(candidates) == 1 else None,
                                        'sdk_side_by_side' if len(candidates) == 1 else 'not_configured_or_ambiguous')
        result['ndk_root']['candidates'] = [str(path.parent) for path in candidates]
    result.setdefault('gradle_user_home', _path_state(str(Path.home() / '.gradle'), 'standard_location'))
    identity = digest(str(project.yyp).encode('utf-8'))[:16]
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'MORX-GMS2-MCP' / identity
    for name, folder in (('build_root', 'builds'), ('export_root', 'packages')):
        result.setdefault(name, _path_state(str(base / folder), 'managed_default'))
    result.setdefault('bundletool_path', _path_state(None, 'not_configured'))
    return result, digest(raw)


def _properties(path):
    if not path.is_file() or not link_free(path) or path.stat().st_size > 65536:
        return {}
    values = {}
    for line in path.read_text(encoding='utf-8-sig', errors='replace').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('"')
    return values


def executable_version(path):
    """只读PE固定版本资源，不启动IDE/Igor，也不把目录名称当作实际文件版本。"""
    if os.name != 'nt' or not path or not path.is_file(): return None
    import ctypes
    from ctypes import wintypes
    class FixedVersion(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in ('signature','structure','file_ms','file_ls',
                     'product_ms','product_ls','mask','flags','os','kind','subtype','date_ms','date_ls')]
    api = ctypes.WinDLL('version', use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    api.VerQueryValueW.argtypes = [ctypes.c_void_p,wintypes.LPCWSTR,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(wintypes.UINT)]
    size = api.GetFileVersionInfoSizeW(str(path), None)
    if not 0<size<1024**2: return None
    buffer = ctypes.create_string_buffer(size)
    pointer = ctypes.c_void_p(); length = wintypes.UINT()
    if not api.GetFileVersionInfoW(str(path),0,size,buffer) or not api.VerQueryValueW(buffer,'\\',ctypes.byref(pointer),ctypes.byref(length)):
        return None
    if length.value<ctypes.sizeof(FixedVersion): return None
    value = ctypes.cast(pointer,ctypes.POINTER(FixedVersion)).contents
    if value.signature != 0xfeef04bd: return None
    return '.'.join(str(part) for part in (value.file_ms>>16,value.file_ms&65535,value.file_ls>>16,value.file_ls&65535))


def installed_ides():
    base = Path(os.environ.get('ProgramFiles','C:/Program Files'))
    paths = sorted(base.glob('GameMaker*/GameMaker*.exe'))[:16]
    return [{'path':str(path), 'file_version':executable_version(path)} for path in paths if link_free(path)]


def build_path_issue(path, windows=None):
    windows = os.name == 'nt' if windows is None else windows
    if windows and path and not str(path).isascii():
        return {'code':'non_ascii_build_root', 'component':'build_root',
                'message':'Windows下Android Gradle Plugin会拒绝非ASCII项目路径，请显式选择ASCII隔离构建根',
                'next':{'tool':'configure_android_toolchain','paths':{'build_root':'D:/GameBuilds/Android'}},
                'source_project_move_required':False, 'overridePathCheck_injected':False}
    return None


def _artifact_components(sdk, jdk):
    binaries = {'java': jdk / 'bin/java.exe' if jdk else None,
                'keytool': jdk / 'bin/keytool.exe' if jdk else None,
                'adb': sdk / 'platform-tools/adb.exe' if sdk else None}
    tools = sdk / 'build-tools' if sdk else None
    return {**{name: str(path) if path and path.is_file() else None for name, path in binaries.items()},
            'installed_build_tools': sorted(path.name for path in tools.iterdir() if path.is_dir())
                                     if tools and tools.is_dir() else []}


def artifact_tools(project, options):
    """验包与 ADB 只依赖本机工具，不读取工程构建选项或获取资源写入锁。"""
    paths, _ = discover(project, options)
    sdk = Path(paths['sdk_root']['path']) if paths['sdk_root']['path'] else None
    jdk = Path(paths['jdk_root']['path']) if paths['jdk_root']['path'] else None
    components = _artifact_components(sdk, jdk)
    if sdk:
        components['installed_build_tools'] = [version for version in components['installed_build_tools']
            if (sdk / 'build-tools' / version / 'aapt2.exe').is_file()
            and (sdk / 'build-tools' / version / 'lib/apksigner.jar').is_file()]
    return {'paths': paths, 'components': components, 'dependencies': {'build_tools': {'effective_value': None}}}


def inspect(project, options, backend='VM', artifact_type='apk'):
    if backend not in ('VM', 'YYC') or artifact_type not in ('apk', 'aab'):
        raise ValueError('backend须为VM/YYC，artifact_type须为apk/aab')
    paths, checksum = discover(project, options)
    configured = android.inspect(project)
    dependencies = configured.get('dependency_sources', {})
    root = Path(options.runtime_root) if options.runtime_root else None
    igor = root / 'bin/igor/windows/x64/Igor.exe' if root else None
    runtime = {'path': str(root) if root else None, 'version': root.name.removeprefix('runtime-') if root else None,
               'version_source': 'runtime_directory_name',
               'igor_path': str(igor) if igor and igor.is_file() else None,
               'igor_file_version': executable_version(igor),
               'igor_present': bool(igor and igor.is_file()),
               'android_module_present': bool(root and (root / 'android/runner').is_dir()),
               'authorization': 'license_present_unverified' if options.user_directory and
                    (Path(options.user_directory) / 'licence.plist').is_file() else 'not_configured_or_missing',
               'authorization_note': '存在文件不证明授权可用；最终以Igor结果为准，不返回许可证内容'}
    ndk_required = backend == 'YYC' or runtime['version'] in {'2026.0.0.23'}
    runtime['vm_ndk_requirement_verified'] = runtime['version'] == '2026.0.0.23'
    issues = []
    path_issue = build_path_issue(paths['build_root']['path'])
    if path_issue: issues.append(path_issue)
    if not configured.get('supported'):
        issues.append({'code': 'android_options_invalid', 'details': configured.get('unsupported_reasons')})
    if not runtime['igor_present'] or not runtime['android_module_present']:
        issues.append({'code': 'runtime_target_unavailable'})
    if runtime['authorization'] != 'license_present_unverified':
        issues.append({'code': 'authorization_not_configured'})
    for name in ('sdk_root', 'jdk_root') + (('ndk_root',) if ndk_required else ()):
        if paths[name]['state'] != 'present':
            issues.append({'code': 'toolchain_' + paths[name]['state'], 'component': name})
    sdk = Path(paths['sdk_root']['path']) if paths['sdk_root']['path'] else None
    jdk = Path(paths['jdk_root']['path']) if paths['jdk_root']['path'] else None
    java = jdk / 'bin/java.exe' if jdk else None
    keytool = jdk / 'bin/keytool.exe' if jdk else None
    if not java or not java.is_file() or not keytool.is_file():
        issues.append({'code': 'jdk_incomplete'})
    java_version = _properties(jdk / 'release').get('JAVA_VERSION') if jdk else None
    ndk = Path(paths['ndk_root']['path']) if paths['ndk_root']['path'] else None
    ndk_version = _properties(ndk / 'source.properties').get('Pkg.Revision') if ndk else None
    ndk_valid = bool(ndk and ndk_version and (ndk / 'ndk-build.cmd').is_file()
                     and (ndk / 'toolchains/llvm/prebuilt/windows-x86_64/bin/clang++.exe').is_file())
    if ndk_required and not ndk_valid:
        issues.append({'code': 'ndk_incomplete', 'component': 'ndk_root',
                       'next': '配置真实NDK根目录；不会用SDK目录或空目录冒充NDK'})
    plugin = dependencies.get('gradle_plugin_version', {}).get('effective_value')
    gradle = dependencies.get('gradle_version', {}).get('effective_value')
    # 已确认的 AGP 8.8 兼容范围：不把一个插件版本的规则推广到未知版本。
    if plugin and plugin.startswith('8.8.'):
        java_match = re.match(r'(?:1\.)?([0-9]+)', java_version or '')
        if not java_match or int(java_match[1]) < 17:
            issues.append({'code': 'version_incompatible', 'component': 'jdk', 'required': '>=17', 'found': java_version})
        if gradle and tuple(int(v) for v in gradle.split('.')) < (8, 10, 2):
            issues.append({'code': 'version_incompatible', 'component': 'gradle', 'required': '>=8.10.2', 'found': gradle})
        compile_value = dependencies.get('compile_sdk', {}).get('effective_value')
        if compile_value and int(compile_value) > 35:
            issues.append({'code': 'version_incompatible', 'component': 'compile_sdk', 'required': '<=35 for AGP 8.8', 'found': compile_value})
    components = {**_artifact_components(sdk, jdk),
                  'java_version': java_version, 'ndk_version': ndk_version, 'ndk_complete': ndk_valid,
                  'installed_platforms': sorted(path.name for path in (sdk / 'platforms').iterdir() if path.is_dir())
                        if sdk and (sdk / 'platforms').is_dir() else []}
    runner = root / 'android/runner/ProjectFiles/src/main/jniLibs' if root else None
    runtime['vm_abis'] = sorted(path.name for path in runner.iterdir() if path.is_dir() and (path/'libyoyo.so').is_file()) if runner and runner.is_dir() else []
    selected_abis = sorted(value for key,value in android.ABIS.items() if configured.get('settings',{}).get('arch_'+key))
    if backend == 'VM' and any(value not in runtime['vm_abis'] for value in selected_abis):
        issues.append({'code':'runtime_abi_unavailable','selected':selected_abis,'available':runtime['vm_abis']})
    if gradle:
        home = Path(paths['gradle_user_home']['path'])
        components['gradle_distribution_cached'] = any((folder / ('gradle-' + gradle) / 'bin/gradle.bat').is_file()
             for folder in (home/'wrapper/dists'/('gradle-'+gradle+'-bin')).glob('*'))
        components['gradle_dependencies_cached'] = 'unverified_until_build'
    for name in android.DEPENDENCIES:
        value = dependencies.get(name, {}).get('effective_value')
        if not value:
            issues.append({'code': 'inherited_dependency_unresolved', 'component': name,
                           'next': '在原生项目选项中显式配置；不猜测IDE/Runtime继承值'})
    compile_sdk = dependencies.get('compile_sdk', {}).get('effective_value')
    tools = dependencies.get('build_tools', {}).get('effective_value')
    if sdk and compile_sdk and not (sdk / 'platforms' / ('android-' + compile_sdk) / 'android.jar').is_file():
        issues.append({'code': 'requires_download', 'component': 'platforms;android-' + compile_sdk})
    if sdk and tools:
        folder = sdk / 'build-tools' / tools
        for name in ('aapt2.exe', 'apksigner.bat', 'zipalign.exe'):
            if not (folder / name).is_file():
                issues.append({'code': 'requires_download', 'component': 'build-tools;' + tools + '/' + name})
    if artifact_type == 'aab' and paths['bundletool_path']['state'] != 'present':
        issues.append({'code': 'bundletool_not_configured'})
    return {'ready': not issues, 'backend': backend, 'artifact_type': artifact_type,
            'configuration_path': CONFIG, 'configuration_sha256': checksum, 'paths': paths, 'runtime': runtime,
            'installed_ides': installed_ides(),
            'dependencies': dependencies, 'components': components, 'issues': issues,
            'permission_notes': configured.get('permission_notes', []),
            'ndk_required': ndk_required, 'downloads_performed': False,
            'compatibility': 'known_agp_8_8_rules_checked' if plugin and plugin.startswith('8.8.') else 'unverified_version_pair',
            'note': '预检不等于编译成功；未知版本组合与Gradle依赖缓存仍须构建时验证'}
