"""使用 Android 官方工具读取并验证最终 APK/AAB，不把文件存在或退出码当作成包证明。"""
import hashlib
from pathlib import Path
import re
import stat
import xml.etree.ElementTree as ET
import zipfile

from .android import ANDROID_NS
from .android_process import run_command
from .android_toolchain import absolute
from . import android_bridge


def checksum(path):
    hasher = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def parse_badging(text):
    package = next((line for line in text.splitlines() if line.startswith('package:')), '')
    attributes = dict(re.findall(r"([A-Za-z][A-Za-z0-9]*)='([^']*)'", package))
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z][a-zA-Z0-9_]*)+', attributes.get('name', '')):
        raise ValueError('APK工具输出缺少有效包名')
    code = attributes.get('versionCode', '')
    if not code.isdigit() or not 1 <= int(code) <= 2100000000 or 'versionName' not in attributes:
        raise ValueError('APK工具输出缺少有效版本信息')
    def sdk(*labels):
        values = [int(match[1]) for label in labels if (match := re.search(r"(?m)^" + label + r":'([0-9]+)'", text))]
        if len(set(values)) > 1: raise ValueError('APK工具返回相互冲突的SDK信息')
        return values[0] if values else None
    native = re.search(r'(?m)^native-code:\s*(.*)$', text)
    launch = re.search(r"(?m)^launchable-activity:.*?name='([^']+)'", text)
    permissions = sorted(set(re.findall(r"(?m)^uses-permission(?:-sdk-[0-9]+)?:.*?name='([^']+)'", text)))
    return {'package_name': attributes['name'], 'versionName': attributes['versionName'], 'versionCode': int(code),
            'min_sdk': sdk('sdkVersion', 'minSdkVersion'), 'target_sdk': sdk('targetSdkVersion'),
            'abis': sorted(re.findall(r"'([^']+)'", native[1])) if native else [], 'permissions': permissions,
            'internet_permission': 'android.permission.INTERNET' in permissions,
            'launchable_activity': launch[1] if launch else None,
            'debuggable': bool(re.search(r'(?m)^application-debuggable\b', text))}


def parse_manifest(text):
    if '<!DOCTYPE' in text or '<!ENTITY' in text:
        raise ValueError('成包Manifest不能包含DTD或实体')
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError('AAB Manifest无法解析') from exc
    namespace = '{' + ANDROID_NS + '}'
    code = root.get(namespace + 'versionCode', '')
    package = root.get('package', '')
    if root.tag != 'manifest' or not package or not code.isdigit() or not 1 <= int(code) <= 2100000000:
        raise ValueError('AAB Manifest缺少包名或版本')
    sdk = root.find('uses-sdk')
    app = root.find('application')
    permissions = sorted({node.get(namespace + 'name') for node in root.findall('uses-permission')
                          if node.get(namespace + 'name')})
    def api(name):
        value = sdk.get(namespace + name) if sdk is not None else None
        return int(value) if value and value.isdigit() else None
    return {'package_name': package, 'versionName': root.get(namespace + 'versionName'), 'versionCode': int(code),
            'min_sdk': api('minSdkVersion'), 'target_sdk': api('targetSdkVersion'), 'permissions': permissions,
            'internet_permission': 'android.permission.INTERNET' in permissions,
            'debuggable': app is not None and app.get(namespace + 'debuggable') == 'true',
            'launchable_activity': None}


def inspect(path, toolchain, expected_sha256=None, cancel=None, *, require_current_bridge=True):
    path = absolute(str(path), 'Android产物', exists=True)
    if not path.is_file() or not 0 < path.stat().st_size <= 2 * 1024**3 or path.suffix.lower() not in ('.apk', '.aab'):
        raise ValueError('产物须为最多2GiB的普通APK/AAB文件')
    sha = checksum(path)
    if expected_sha256 is not None and sha != expected_sha256:
        raise ValueError('Android产物哈希已改变，请重新检查')
    kind = path.suffix.lower()[1:]
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 100000 or sum(row.file_size for row in entries) > 8 * 1024**3:
                raise ValueError('Android产物ZIP目录超出大小限制')
            names = {row.filename for row in entries}
            if len(names) != len(entries):
                raise ValueError('Android产物含重复ZIP路径')
            for row in entries:
                parts = Path(row.filename.replace('\\', '/')).parts
                if (row.filename.startswith(('/', '\\')) or '..' in parts or ':' in row.filename or row.flag_bits & 1
                        or stat.S_ISLNK(row.external_attr >> 16)):
                    raise ValueError('Android产物含不安全ZIP项')
                if Path(row.filename).suffix.lower() in ('.jks', '.keystore', '.p12', '.dpapi') or Path(row.filename).name in (
                        'licence.plist', 'local_settings.json', 'morx-android-signing.json'):
                    raise ValueError('Android产物包含禁止交付的签名或本机配置文件')
            required = {'AndroidManifest.xml'} if kind == 'apk' else {'BundleConfig.pb', 'base/manifest/AndroidManifest.xml'}
            if not required <= names:
                raise ValueError('产物缺少原生APK/AAB结构')
            abis = sorted({match[1] for name in names if (match := re.match(r'(?:base/)?lib/([^/]+)/[^/]+\.so$', name))})
            bridge = android_bridge.marker(archive, require_current=require_current_bridge)
    except zipfile.BadZipFile as exc:
        raise ValueError('产物不是完整的APK/AAB ZIP') from exc
    paths, components = toolchain['paths'], toolchain['components']
    java = components.get('java')
    keytool = components.get('keytool')
    if not java or not keytool:
        raise ValueError('产物验证需要已配置JDK')
    def command(arguments):
        result = run_command(arguments, timeout=45, cancel=cancel)
        if result['exit_code']:
            raise ValueError('Android官方产物工具验证失败；不将未验证文件标记为成功')
        return result['output']
    if kind == 'apk':
        sdk_path = paths['sdk_root']['path']
        if not sdk_path:
            raise ValueError('APK验证需要已配置或发现的Android SDK')
        sdk = Path(sdk_path)
        tools = toolchain['dependencies']['build_tools']['effective_value']
        if not tools:
            candidates = [value for value in components.get('installed_build_tools', []) if re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', value)]
            tools = max(candidates, key=lambda value: tuple(int(part) for part in value.split('.'))) if candidates else None
        if not tools:
            raise ValueError('APK验证需要已安装的Build Tools；不会自动下载')
        folder = sdk / 'build-tools' / tools
        metadata = parse_badging(command([str(folder / 'aapt2.exe'), 'dump', 'badging', str(path)]))
        verified = command([java, '-jar', str(folder / 'lib/apksigner.jar'), 'verify', '--verbose', '--print-certs', str(path)])
        fingerprints = re.findall(r'certificate SHA-256 digest:\s*([a-fA-F0-9]{64})', verified)
        if not fingerprints:
            raise ValueError('APK签名校验未返回公开证书摘要')
        signature = {'verified': True, 'tool': 'apksigner', 'certificate_sha256': sorted(set(v.lower() for v in fingerprints))}
    else:
        bundle = paths['bundletool_path']['path']
        if not bundle:
            raise ValueError('验证AAB需要显式配置bundletool JAR；不会将AAB当作可直接安装APK')
        command([java, '-jar', bundle, 'validate', '--bundle=' + str(path)])
        metadata = parse_manifest(command([java, '-jar', bundle, 'dump', 'manifest', '--bundle=' + str(path), '--module=base']))
        jdk = Path(paths['jdk_root']['path'])
        verified = command([str(jdk / 'bin/jarsigner.exe'), '-J-Duser.language=en', '-J-Duser.country=US', '-verify', str(path)])
        if 'jar verified.' not in verified:
            raise ValueError('AAB JAR签名未通过验证')
        certificate = command([keytool, '-J-Duser.language=en', '-J-Duser.country=US', '-printcert', '-jarfile', str(path)])
        fingerprints = re.findall(r'SHA256:\s*([a-fA-F0-9:]{64,96})', certificate)
        fingerprints = [value.replace(':', '').lower() for value in fingerprints
                        if re.fullmatch('[a-fA-F0-9]{64}', value.replace(':', ''))]
        if not fingerprints:
            raise ValueError('AAB签名未返回公开证书摘要')
        signature = {'verified': True, 'tool': 'jarsigner/keytool',
                     'certificate_sha256': sorted(set(fingerprints))}
    if metadata.get('abis') and metadata['abis'] != abis:
        raise ValueError('官方工具与ZIP中的ABI不一致')
    metadata['abis'] = abis
    if checksum(path) != sha:
        raise ValueError('验证过程中产物被修改')
    return {'path': str(path), 'artifact_type': kind, 'sha256': sha, 'bytes': path.stat().st_size,
            **metadata, 'signature': signature, 'format_verified': True,
            'development_bridge': bridge,
            'development_bridge_compatibility': None if bridge is None else {
                'protocol': bridge['protocol'], 'supported_protocol': android_bridge.PROTOCOL,
                'runtime_supported': bridge['protocol'] == android_bridge.PROTOCOL},
            'metadata_source': 'aapt2' if kind == 'apk' else 'bundletool', 'installable_directly': kind == 'apk'}
