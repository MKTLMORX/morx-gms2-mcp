"""GMAndroidOptions v1 的有界配置与项目内平台图像。"""
import io
import re
import xml.etree.ElementTree as ET

from PIL import Image
from .display import boolean, integer, title_check
from .exports import expected
from .resources import Edit
from .storage import digest, encode

OPTIONS = 'options/android/options_android.yy'
PREFIX = 'option_android_'
ORIENTATIONS = ('landscape', 'landscape_flipped', 'portrait', 'portrait_flipped')
ABIS = {'arm64': 'arm64-v8a', 'armv7': 'armeabi-v7a', 'x86_64': 'x86_64'}
PERMISSIONS = ('internet', 'network_state', 'bluetooth', 'read_phone_state', 'record_audio', 'write_external_storage')
FLAGS = {*(f'orient_{name}' for name in ORIENTATIONS), *(f'arch_{name}' for name in ABIS),
         *(f'permission_{name}' for name in PERMISSIONS), 'interpolate_pixels', 'edge_to_edge_display',
         'tools_from_version', 'attribute_allow_backup', 'gamepad_support', 'lint',
         'proguard_minifying', 'proguard_shrinking'}
DEPENDENCIES = ('build_tools', 'compile_sdk', 'minimum_sdk', 'target_sdk', 'gradle_version', 'gradle_plugin_version')
SPLASH_FIELDS = {'launchscreen_fill', 'splashscreen_background_colour', 'splash_time'}
SPLASH_MODES = ('keep_aspect_ratio', 'crop_to_fit', 'stretch')
SPLASH_PATHS = {f'options/android/splash/{mode}.png' for mode in ('landscape', 'portrait')}
PNG_LIMIT = 16 * 1024**2
FIELDS = FLAGS | set(DEPENDENCIES) | SPLASH_FIELDS | {'display_name', 'package_domain', 'package_company', 'package_product',
         'version', 'texture_page', 'scale', 'screen_depth', 'display_layout', 'application_tag_inject'}
DENSITIES = ('ldpi', 'mdpi', 'hdpi', 'xhdpi', 'xxhdpi', 'xxxhdpi')
LEGACY_SIZES = (36, 48, 72, 96, 144, 192)
ADAPTIVE_SIZES = (81, 108, 162, 216, 324, 432)
LAYOUTS = tuple('LAYOUT_IN_DISPLAY_CUTOUT_MODE_' + name for name in ('DEFAULT', 'SHORT_EDGES', 'NEVER', 'ALWAYS'))
ANDROID_NS = 'http://schemas.android.com/apk/res/android'


def permission_notes(values):
    if values.get('gamepad_support') and not values.get('permission_bluetooth'):
        return [{'code':'gamepad_may_add_bluetooth_permissions','source':'gamepad_support',
                 'message':'开启Android手柄支持仍可能加入蓝牙权限，permission_bluetooth=false不能单独证明权限已移除',
                 'possible_permissions':['android.permission.BLUETOOTH','android.permission.BLUETOOTH_CONNECT'],
                 'verification':'以inspect_android_package读取最终APK权限为准；是否关闭gamepad_support由调用者明确选择'}]
    return []


def package_name(values):
    pieces = [values.get('package_' + name) for name in ('domain', 'company', 'product')]
    if any(not isinstance(part, str) or not re.fullmatch('[a-z][a-z0-9]*', part) for part in pieces):
        raise ValueError('包名三段均须以小写字母开头，只含小写字母和数字')
    result = '.'.join(pieces)
    if len(result) > 255:
        raise ValueError('组合包名超过255字符')
    return result


def application_attributes(value):
    if not isinstance(value, str) or len(value.encode('utf-8')) > 4096 or '<!' in value or '&' in value:
        raise ValueError('应用标签须为有界 XML 属性，不接受声明、实体或代码')
    try:
        node = ET.fromstring(f'<application xmlns:android="{ANDROID_NS}" {value}/>')
    except ET.ParseError as exc:
        raise ValueError('应用标签属性 XML 无效') from exc
    allowed = {'theme', 'description', 'supportsRtl', 'resizeableActivity'}
    if len(node) or node.text or any(key not in {'{' + ANDROID_NS + '}' + name for name in allowed} for key in node.attrib):
        raise ValueError('应用标签只支持 theme/description/supportsRtl/resizeableActivity；其他配置使用专用字段')
    for name in ('supportsRtl', 'resizeableActivity'):
        if node.attrib.get('{' + ANDROID_NS + '}' + name, 'true') not in ('true', 'false'):
            raise ValueError(name + ' 须为 true 或 false')
    return dict(node.attrib)


def field_value(name, value):
    if name not in FIELDS:
        raise ValueError('不支持的 Android 配置字段：' + str(name))
    if name in FLAGS:
        boolean(value, name)
    elif name in ('scale', 'screen_depth'):
        integer(value, name, 0, 1)
    elif name == 'launchscreen_fill':
        integer(value, name, 0, len(SPLASH_MODES) - 1)
    elif name == 'splash_time':
        integer(value, name, 0, 10)
    elif name == 'splashscreen_background_colour':
        integer(value, name, 0, 0xFFFFFFFF)
    elif name == 'display_layout':
        if value not in LAYOUTS:
            raise ValueError('display_layout 须为受支持的原生 LAYOUT_IN_DISPLAY_CUTOUT_MODE 枚举')
    elif name == 'texture_page':
        if value not in {f'{size}x{size}' for size in (256, 512, 1024, 2048, 4096, 8192)}:
            raise ValueError('texture_page 须为256～8192的正方形二次幂尺寸')
    elif name == 'application_tag_inject':
        application_attributes(value)
    else:
        if (not isinstance(value, str) or len(value) > 255
                or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value)):
            raise ValueError(name + ' 须为最多255字符且不含控制字符的文本')
        value.encode('utf-8')
        if name == 'display_name':
            title_check(value)
        elif name == 'version':
            if (not re.fullmatch(r'(?:0|[1-9][0-9]{0,4})(?:\.(?:0|[1-9][0-9]{0,4})){3}', value)
                    or any(int(part) > 65535 for part in value.split('.'))):
                raise ValueError('version 须为四段0～65535整数；成包版本号由最终产物读取')
        elif name in DEPENDENCIES and value:
            pattern = r'[1-9][0-9]{0,2}' if name.endswith('_sdk') else r'[0-9]+(?:\.[0-9]+){1,3}'
            if not re.fullmatch(pattern, value):
                raise ValueError(name + ' 须为明确版本/API数字；空串继承，不接受路径或构建参数')
    return value


def settings(data):
    return {key: data.get(PREFIX + key) for key in sorted(FIELDS) if PREFIX + key in data}


def validate_settings(values):
    for key, value in values.items():
        field_value(key, value)
    package = package_name(values)
    if not any(values.get('orient_' + name) is True for name in ORIENTATIONS):
        raise ValueError('Android 至少启用一个屏幕方向')
    if not any(values.get('arch_' + name) is True for name in ABIS):
        raise ValueError('Android 至少启用一种 CPU 架构')
    apis = [int(values[name]) if values.get(name) else None for name in ('minimum_sdk', 'target_sdk', 'compile_sdk')]
    for first, second in ((0, 1), (1, 2), (0, 2)):
        if apis[first] is not None and apis[second] is not None and apis[first] > apis[second]:
            raise ValueError('API 设置须满足 minimum_sdk ≤ target_sdk ≤ compile_sdk')
    return package


def editable(data, meta):
    if (data.get('$GMAndroidOptions') != 'v1' or data.get('resourceType') != 'GMAndroidOptions'
            or data.get('resourceVersion') != '2.0'):
        raise ValueError('仅支持已有 GMAndroidOptions v1 / resourceVersion 2.0')
    if data.get('ConfigValues') or meta.get('ConfigValues'):
        raise ValueError('Android/项目含配置覆盖，暂不修改；请先明确配置来源')


def _splash_info(data, project=None):
    result = {'images': {}}
    fill = data.get(PREFIX + 'launchscreen_fill')
    colour = data.get(PREFIX + 'splashscreen_background_colour')
    result['fill_mode'] = SPLASH_MODES[fill] if type(fill) is int and 0 <= fill < len(SPLASH_MODES) else None
    result['minimum_seconds'] = data.get(PREFIX + 'splash_time')
    result['border_color'] = (f'#{colour & 255:02X}{(colour >> 8) & 255:02X}{(colour >> 16) & 255:02X}'
                              if type(colour) is int and 0 <= colour <= 0xFFFFFFFF else None)
    for orientation in ('landscape', 'portrait'):
        reference = data.get(PREFIX + 'splash_screens_' + orientation)
        row = result['images'][orientation] = {'configured_path': reference}
        if project is None or not isinstance(reference, str):
            continue
        reference = reference.replace('\\', '/')
        if reference.startswith('${options_dir}/android/'):
            relative = 'options/' + reference[len('${options_dir}/'):]
            try:
                path = project.path(relative)
                row.update(project_path=relative, exists=path.is_file(), sha256=None)
                if path.suffix.lower() == '.png' and path.is_file() and path.stat().st_size <= PNG_LIMIT:
                    row['sha256'] = digest(path.read_bytes())
            except (ValueError, OSError):
                row['unreadable'] = True
        elif reference.startswith('${base_options_dir}/android/'):
            row['inherited'] = True
    return result


def inspect(project):
    with project.lock:
        edit = Edit(project)
        raw = edit.raw(OPTIONS)
        if raw is None:
            return {'available': False, 'path': OPTIONS, 'sha256': None, 'supported': False,
                    'unsupported_reasons': ['项目缺少原生 Android 选项']}
        data = edit.load(OPTIONS)
        values = settings(data)
        reasons = []
        try:
            editable(data, edit.load(project.yyp.name))
            package = validate_settings(values)
        except ValueError as exc:
            reasons.append(str(exc))
            package = None
        icons = {}
        for native, kind in (('icon', 'legacy'), ('icon_adaptive', 'foreground'), ('icon_adaptivebg', 'background')):
            for density in DENSITIES:
                reference = data.get(PREFIX + native + '_' + density)
                row = {'configured_path': reference, 'sha256': None}
                if isinstance(reference, str) and reference.startswith('${options_dir}/android/'):
                    relative = 'options/' + reference[len('${options_dir}/'):]
                    path = project.path(relative)
                    row.update(project_path=relative, exists=path.is_file())
                    if path.is_file() and path.stat().st_size <= 16 * 1024**2:
                        row['sha256'] = digest(path.read_bytes())
                icons[kind + '/' + density] = row
        return {'available': True, 'path': OPTIONS, 'sha256': digest(raw), 'resource_version': data.get('$GMAndroidOptions'),
                'supported': not reasons, 'unsupported_reasons': reasons,
                'configuration_overrides': bool(data.get('ConfigValues') or edit.load(project.yyp.name).get('ConfigValues')),
                'settings': values, 'package_name': package, 'icons': icons,
                'permission_notes': permission_notes(values),
                'dependency_sources': {name: {'project_value': values.get(name),
                    'source': 'project' if values.get(name) else 'inherited_unresolved',
                    'effective_value': values.get(name) or None} for name in DEPENDENCIES},
                'artifact_version': {'versionName': None, 'versionCode': None, 'source': 'requires_final_package'},
                'supported_fields': sorted(FIELDS), 'splash': _splash_info(data, project)}


def _normalized_changes(changes, allowed):
    if not isinstance(changes, dict) or not changes or len(changes) > len(allowed):
        raise ValueError('changes 须为有界 Android 设置对象；null保留')
    result = {}
    for key, value in changes.items():
        if key in ('splash_screens_landscape', 'splash_screens_portrait'):
            raise ValueError('启动图路径请使用 import_android_splash 导入 PNG：' + key)
        if key not in allowed:
            raise ValueError('不支持的 Android 配置字段：' + str(key))
        if value is None:
            continue
        if key == 'splashscreen_background_colour' and isinstance(value, str):
            if not re.fullmatch('#[a-fA-F0-9]{6}', value):
                raise ValueError('启动图边框色须为 #RRGGBB 或原生无符号整数')
            red, green, blue = bytes.fromhex(value[1:])
            value = 0xFF000000 | (blue << 16) | (green << 8) | red
        result[key] = field_value(key, value)
    return result


def _apply_changes(data, changes):
    for key, value in changes.items():
        if PREFIX + key not in data:
            raise ValueError('当前原生格式没有字段，拒绝虚构：' + key)
        data[PREFIX + key] = value


def configure(project, changes, expected_sha256, dry_run=True):
    changes = _normalized_changes(changes, FIELDS)
    with project.lock:
        edit = Edit(project)
        data = edit.load(OPTIONS)
        editable(data, edit.load(project.yyp.name))
        expected(edit.raw(OPTIONS), expected_sha256, 'Android 配置')
        before = settings(data)
        try:
            before_package = package_name(before)
        except ValueError:
            before_package = None
        _apply_changes(data, changes)
        after = settings(data)
        package = validate_settings(after)
        if after != before:
            edit.save(OPTIONS, data, preserve_trailing=True)
        return {**edit.commit(dry_run), 'before': before, 'settings': after,
                'permission_notes': permission_notes(after),
                'package_name': package, 'package_name_changed': before_package != package,
                'package_identity_note': '包名变更会产生独立应用，不能作为原包升级；本工具不知道该包是否已发布',
                'compiled': False, 'splash': _splash_info(data)}


def _png(project, relative, checksum=None):
    path = project.path(relative)
    if path.suffix.lower() != '.png' or not path.is_file() or path.stat().st_size > 16 * 1024**2:
        raise ValueError('图标源须为绑定项目内最多16MiB的PNG文件')
    raw = path.read_bytes()
    if checksum is not None:
        expected(raw, checksum, '源 PNG')
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != 'PNG' or max(image.size) > 4096:
                raise ValueError('图标源须为不超过4096×4096的PNG')
            image.load()
            return image.convert('RGBA'), digest(raw)
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError('PNG图标无法解码') from exc


def import_icons(project, source_path, expected_sha256, background_path=None, background_color='#FFFFFF',
                 padding_percent=20, expected_sources=None, expected_icon_hashes=None, dry_run=True):
    integer(padding_percent, 'padding_percent', 0, 45)
    if not isinstance(background_color, str) or not re.fullmatch('#[a-fA-F0-9]{6}', background_color):
        raise ValueError('background_color 须为 #RRGGBB 不透明颜色')
    for mapping in (expected_sources, expected_icon_hashes):
        if mapping is not None and (not isinstance(mapping, dict) or len(mapping) > 24):
            raise ValueError('图标哈希须为有界的项目相对路径到SHA256映射')
    expected_sources, expected_icon_hashes = expected_sources or {}, expected_icon_hashes or {}
    source_names = {source_path} | ({background_path} if background_path else set())
    if set(expected_sources) - source_names:
        raise ValueError('源哈希含未使用的路径')
    with project.lock:
        edit = Edit(project)
        data = edit.load(OPTIONS)
        editable(data, edit.load(project.yyp.name))
        expected(edit.raw(OPTIONS), expected_sha256, 'Android 配置')
        foreground, source_sha = _png(project, source_path, expected_sources.get(source_path))
        background, background_sha = (_png(project, background_path, expected_sources.get(background_path))
                                      if background_path else (None, None))
        outputs = []
        for kind, native, sizes in (('legacy', 'icon', LEGACY_SIZES), ('foreground', 'icon_adaptive', ADAPTIVE_SIZES),
                                    ('background', 'icon_adaptivebg', ADAPTIVE_SIZES)):
            for density, size in zip(DENSITIES, sizes):
                relative = f'options/android/morx_icons/{kind}/{density}.png'
                old = edit.raw(relative)
                expected(old, expected_icon_hashes.get(relative), '目标图标 ' + relative)
                canvas = Image.new('RGBA', (size, size), background_color if kind == 'background' else (0, 0, 0, 0))
                image = background if kind == 'background' else foreground
                if image is not None:
                    padding = round(size * padding_percent / 100) if kind != 'background' else 0
                    maximum = size - 2 * padding
                    factor = maximum / max(image.size)
                    resized = image.resize(tuple(max(1, round(edge * factor)) for edge in image.size), Image.Resampling.LANCZOS)
                    canvas.alpha_composite(resized, ((size - resized.width)//2, (size - resized.height)//2))
                buffer = io.BytesIO()
                canvas.save(buffer, format='PNG')
                edit.changes[relative] = buffer.getvalue()
                field = PREFIX + native + '_' + density
                if field not in data:
                    raise ValueError('当前原生配置缺少图标字段：' + field)
                data[field] = '${options_dir}/' + relative[len('options/'):]
                outputs.append({'path': relative, 'width': size, 'height': size, 'sha256': digest(buffer.getvalue())})
        if set(expected_icon_hashes) - {row['path'] for row in outputs}:
            raise ValueError('目标图标哈希含未使用的路径')
        if PREFIX + 'icon_adaptive_generate' in data:
            data[PREFIX + 'icon_adaptive_generate'] = False
        if encode(data) != encode(edit.load(OPTIONS)):
            edit.save(OPTIONS, data, preserve_trailing=True)
        return {**edit.commit(dry_run), 'outputs': outputs, 'source_sha256': source_sha,
                'background_sha256': background_sha, 'padding_percent': padding_percent,
                'preserve_aspect_ratio': True, 'compiled': False}


def _splash_png(edit, relative):
    path = edit.project.path(relative)
    if path.suffix.lower() != '.png' or not path.is_file():
        raise ValueError('启动图源须为项目内 PNG：' + relative)
    with path.open('rb') as file:
        raw = file.read(PNG_LIMIT + 1)
    if len(raw) > PNG_LIMIT:
        raise ValueError('启动图源不能超过 16 MiB：' + relative)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if (image.format != 'PNG' or raw[24:26] != b'\x08\x02'
                    or 'transparency' in image.info or image.is_animated):
                raise ValueError('启动图须为不透明的 24 位 RGB PNG：' + relative)
            if max(image.size) > 4096:
                raise ValueError('启动图任一边不能超过 4096 像素：' + relative)
            image.load()
            size = image.size
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError('启动图 PNG 无法解码：' + relative) from exc
    edit.before[relative] = raw
    return raw, size


def import_splash(project, expected_sha256, landscape_path=None, portrait_path=None, changes=None,
                  expected_sources=None, expected_splash_hashes=None, dry_run=True):
    sources = {mode: path for mode, path in (('landscape', landscape_path), ('portrait', portrait_path))
               if path is not None}
    if not sources or any(not isinstance(path, str) or not path for path in sources.values()):
        raise ValueError('请指定 landscape_path 或 portrait_path 项目内 PNG')
    changes = {} if changes is None else _normalized_changes(changes, SPLASH_FIELDS)
    destinations = {mode: f'options/android/splash/{mode}.png' for mode in sources}
    for mapping, keys in ((expected_sources, set(sources.values())),
                          (expected_splash_hashes, set(destinations.values()))):
        if mapping is not None and (not isinstance(mapping, dict) or set(mapping) - keys):
            raise ValueError('启动图哈希映射含未使用的路径')
    expected_sources, expected_splash_hashes = expected_sources or {}, expected_splash_hashes or {}
    with project.lock:
        edit = Edit(project)
        data = edit.load(OPTIONS)
        editable(data, edit.load(project.yyp.name))
        expected(edit.raw(OPTIONS), expected_sha256, 'Android 配置')
        _apply_changes(data, changes)
        validate_settings(settings(data))
        protected = {project.path(path).relative_to(project.root).as_posix().casefold()
                     for path in destinations.values()}
        outputs = []
        for mode, source in sources.items():
            if project.path(source).relative_to(project.root).as_posix().casefold() in protected:
                raise ValueError('启动图源须与本次写入的目标文件分开：' + source)
            relative = destinations[mode]
            field = PREFIX + 'splash_screens_' + mode
            if field not in data:
                raise ValueError('当前原生配置缺少启动图字段：' + field)
            raw, (width, height) = _splash_png(edit, source)
            if not dry_run or source in expected_sources:
                expected(raw, expected_sources.get(source), '源 PNG ' + source)
            target = project.path(relative)
            if target.is_file() and target.stat().st_size > PNG_LIMIT:
                raise ValueError('现有启动图超过 16 MiB：' + relative)
            old = edit.raw(relative)
            if not dry_run or relative in expected_splash_hashes:
                expected(old, expected_splash_hashes.get(relative), '目标启动图 ' + relative)
            edit.changes[relative] = raw
            data[field] = '${options_dir}/' + relative[len('options/'):]
            outputs.append({'orientation': mode, 'source_path': source, 'source_sha256': digest(raw),
                            'path': relative, 'before_sha256': digest(old), 'width': width, 'height': height})
        if data != edit.load(OPTIONS):
            edit.save(OPTIONS, data, preserve_trailing=True)
        return {**edit.commit(dry_run), 'outputs': outputs, 'splash': _splash_info(data), 'compiled': False}
