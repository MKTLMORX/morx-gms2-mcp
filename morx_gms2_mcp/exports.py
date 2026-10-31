"""Windows 导出元信息与项目内 ICO；所有写入使用同一事务。"""
import io
from pathlib import Path
import re
import struct

from PIL import Image
from .display import WINDOWS_PATH, boolean, integer, title_check
from .storage import digest, encode

FIELDS = {'executable_name': 'option_windows_executable_name', 'display_name': 'option_windows_display_name',
          'version': 'option_windows_version', 'product': 'option_windows_product_info',
          'description': 'option_windows_description_info', 'company': 'option_windows_company_info',
          'copyright': 'option_windows_copyright_info', 'icon': 'option_windows_icon'}
ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]
RESERVED = re.compile(r'(?i)^(?:con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)')


def file_name(value, suffix):
    if (not isinstance(value, str) or len(value) > 255 or not value.casefold().endswith(suffix)
            or len(value) <= len(suffix) or value != value.strip() or RESERVED.match(value)
            or re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', value) or value[:-len(suffix)].endswith(('.', ' '))):
        raise ValueError('文件名须为不含目录/保留名的 Windows ' + suffix + ' 文件名')
    value.encode('utf-8')
    return value


def icon_path(value):
    if not isinstance(value, str):
        raise ValueError('图标路径须为项目内 options/windows/icons/*.ico')
    if value.startswith('${options_dir}/'):
        value = 'options/' + value[len('${options_dir}/'):]
    prefix = 'options/windows/icons/'
    if not value.startswith(prefix):
        raise ValueError('图标路径须为项目内 options/windows/icons/*.ico；外部文件先用 import_windows_icon')
    file_name(value[len(prefix):], '.ico')
    return value


def expected(raw, sha, label):
    if sha is not None and (not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha)):
        raise ValueError(label + '哈希格式无效')
    if digest(raw) != sha:
        raise ValueError(label + '哈希过期，请重新查询/预览')


def editable(data):
    if data.get('$GMWindowsOptions') != 'v2' or data.get('resourceType') != 'GMWindowsOptions':
        raise ValueError('仅支持已有 GMWindowsOptions v2 Windows 配置')
    if data.get('ConfigValues'):
        raise ValueError('Windows 配置含 ConfigValues 覆盖，暂不修改')


def ico_sizes(raw):
    if len(raw) < 22 or len(raw) > 16 * 1024**2:
        raise ValueError('ICO 文件大小无效（最大16 MiB）')
    reserved, kind, count = struct.unpack_from('<HHH', raw)
    if reserved or kind != 1 or not 1 <= count <= 64 or 6 + count * 16 > len(raw):
        raise ValueError('ICO 文件头或目录项无效')
    sizes, ranges = [], []
    try:
        for index in range(count):
            entry = raw[6+index*16:22+index*16]
            w, h, _, reserved, planes, depth, length, offset = struct.unpack('<BBBBHHII', entry)
            w, h = w or 256, h or 256
            if (reserved or planes not in (0, 1) or depth not in (0, 1, 4, 8, 16, 24, 32)
                    or w != h or not length or offset < 6+count*16 or offset+length > len(raw)
                    or any(offset < end and offset+length > start for start, end in ranges)):
                raise ValueError('ICO 图像目录、尺寸或字节范围无效')
            ranges.append((offset, offset+length))
            payload = raw[offset:offset+length]
            if payload.startswith(b'\x89PNG\r\n\x1a\n'):
                with Image.open(io.BytesIO(payload)) as frame:
                    if frame.size != (w, h):
                        raise ValueError('ICO PNG 尺寸与目录不一致')
                    frame.verify()
            else:
                if len(payload) < 12:
                    raise ValueError('ICO DIB 图像头缺失')
                header = struct.unpack_from('<I', payload)[0]
                if header == 12:
                    width, height = struct.unpack_from('<HH', payload, 4)
                elif header in (40, 52, 56, 108, 124) and len(payload) >= header:
                    width, height = struct.unpack_from('<ii', payload, 4)
                else:
                    raise ValueError('不支持或损坏的 ICO DIB 图像头')
                if width != w or abs(height) != h * 2:
                    raise ValueError('ICO DIB 尺寸与目录不一致')
            # 单独解码每一项，不能只验证 Pillow 默认选择的最大帧。
            single = struct.pack('<HHH', 0, 1, 1) + entry[:12] + struct.pack('<I', 22) + payload
            with Image.open(io.BytesIO(single)) as frame:
                frame.load()
                if frame.size != (w, h):
                    raise ValueError('ICO 图像尺寸与目录不一致')
            sizes.append(w)
    except (OSError, SyntaxError, struct.error, Image.DecompressionBombError) as exc:
        raise ValueError('ICO 图像损坏或无法解码') from exc
    return sorted(set(sizes))


def inspect_windows(project):
    from .resources import Edit
    with project.lock:
        edit = Edit(project)
        raw = edit.raw(WINDOWS_PATH)
        if raw is None:
            return {'available': False, 'path': WINDOWS_PATH, 'sha256': None}
        data = edit.load(WINDOWS_PATH)
        result = {'available': True, 'path': WINDOWS_PATH, 'sha256': digest(raw),
                  'settings': {key: data.get(field) for key, field in FIELDS.items()},
                  'configuration_overrides': bool(data.get('ConfigValues')),
                  'supported': data.get('$GMWindowsOptions') == 'v2' and data.get('resourceType') == 'GMWindowsOptions'}
        configured = data.get(FIELDS['icon'])
        result['icon'] = {'configured_path': configured, 'project_path': None, 'sha256': None}
        if isinstance(configured, str) and not configured.startswith('${base_options_dir}/'):
            try:
                relative = icon_path(configured)
                path = project.path(relative)
                if path.is_file() and path.stat().st_size <= 16 * 1024**2:
                    icon = edit.raw(relative)
                    result['icon'].update(project_path=relative, sha256=digest(icon), sizes=ico_sizes(icon))
                else:
                    result['icon'].update(project_path=relative, error='图标不存在或过大')
            except ValueError as exc:
                result['icon']['error'] = str(exc)
        return result


def configure_windows(project, expected_sha256, dry_run=True, **values):
    from .resources import Edit
    changes = {}
    for key, value in values.items():
        if key not in FIELDS:
            raise ValueError('未知导出字段：' + key)
        if value is None:
            continue
        if not isinstance(value, str) or len(value) > 255 or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in value):
            raise ValueError(key + ' 须为最多255字符的文本，不含控制字符')
        value.encode('utf-8')
        if key == 'executable_name':
            file_name(value, '.exe')
        elif key == 'display_name':
            title_check(value)
        elif key == 'version':
            if not re.fullmatch(r'(?:0|[1-9][0-9]{0,4})(?:\.(?:0|[1-9][0-9]{0,4})){3}', value) or any(int(v) > 65535 for v in value.split('.')):
                raise ValueError('version 须为四段0～65535整数，例如 1.2.3.0')
        elif key == 'icon':
            value = icon_path(value)
        changes[FIELDS[key]] = value
    with project.lock:
        edit = Edit(project)
        data = edit.load(WINDOWS_PATH)
        editable(data)
        expected(edit.raw(WINDOWS_PATH), expected_sha256, 'Windows 配置')
        original = encode(data)
        if FIELDS['icon'] in changes:
            relative = changes[FIELDS['icon']]
            path = project.path(relative)
            if not path.is_file() or path.stat().st_size > 16 * 1024**2:
                raise ValueError('图标不存在或过大；先用 import_windows_icon 导入')
            ico_sizes(edit.raw(relative))
            changes[FIELDS['icon']] = '${options_dir}/' + relative[len('options/'):]
        data.update(changes)
        if encode(data) != original:
            edit.save(WINDOWS_PATH, data, preserve_trailing=True)
        return {**edit.commit(dry_run), 'settings': {key: data.get(field) for key, field in FIELDS.items()},
                'compiled': False, 'notes': ['不改写 GML 中的版本；EXE 图标及元信息须通过最终打包检查']}


def import_icon(project, source_path, expected_windows_sha256, destination='options/windows/icons/morx.ico',
                expected_icon_sha256=None, expected_source_sha256=None, trim_transparency=False,
                padding=16, dry_run=True):
    from .resources import Edit
    destination = icon_path(destination)
    boolean(trim_transparency, 'trim_transparency')
    integer(padding, 'padding（256画布像素）', 0, 96)
    source = Path(source_path).resolve(strict=True)
    if not source.is_file() or source.stat().st_size > 16 * 1024**2:
        raise ValueError('图标输入必须为最多16 MiB的 PNG 或 ICO 文件')
    raw = source.read_bytes()
    if expected_source_sha256 is not None:
        expected(raw, expected_source_sha256, '源图标')
    transform = None
    if source.suffix.casefold() == '.ico':
        sizes = ico_sizes(raw)
        if trim_transparency or padding != 16:
            raise ValueError('trim_transparency/padding 仅用于 PNG 输入；ICO 原样导入')
        output = raw
    elif source.suffix.casefold() == '.png':
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != 'PNG' or image.width > 4096 or image.height > 4096:
                    raise ValueError('PNG 须不超过4096×4096')
                image.load()
                image = image.convert('RGBA')
            box = image.getbbox() if trim_transparency else None
            if box:
                image = image.crop(box)
            width, height = image.size
            scale = (256 - 2 * padding) / max(width, height)
            image = image.resize((max(1, round(width*scale)), max(1, round(height*scale))), Image.Resampling.LANCZOS)
            canvas = Image.new('RGBA', (256, 256))
            canvas.alpha_composite(image, ((256-image.width)//2, (256-image.height)//2))
            buffer = io.BytesIO()
            canvas.save(buffer, format='ICO', sizes=[(size, size) for size in ICON_SIZES])
            output = buffer.getvalue()
            sizes = ico_sizes(output)
            transform = {'trim_transparency': trim_transparency, 'crop_box': box,
                         'padding_at_256': padding, 'canvas': [256, 256], 'preserve_aspect_ratio': True}
        except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
            raise ValueError('PNG 图标损坏或无法解码') from exc
    else:
        raise ValueError('图标输入仅支持 PNG 或 ICO')
    with project.lock:
        edit = Edit(project)
        data = edit.load(WINDOWS_PATH)
        editable(data)
        expected(edit.raw(WINDOWS_PATH), expected_windows_sha256, 'Windows 配置')
        previous = edit.raw(destination)
        expected(previous, expected_icon_sha256, '目标图标')
        edit.changes[destination] = output
        original = encode(data)
        data[FIELDS['icon']] = '${options_dir}/' + destination[len('options/'):]
        if encode(data) != original:
            edit.save(WINDOWS_PATH, data, preserve_trailing=True)
        return {**edit.commit(dry_run), 'source_sha256': digest(raw), 'target_path': destination,
                'target_sha256': digest(output), 'sizes': sizes, 'transform': transform, 'compiled': False}
