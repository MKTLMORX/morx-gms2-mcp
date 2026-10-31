"""GMSound v2 的限定设置；不转码原音频，不代替运行期音频组加载。"""
from .animation import number
from .display import boolean, integer
import re

COMPRESSIONS = {'uncompressed': 0, 'compressed': 1, 'decompress_on_load': 2, 'streamed': 3}
CHANNEL_FORMATS = {'mono': 0, 'stereo': 1, '3d': 2}
CONVERSIONS = {'automatic': 0, 'required': 1}
PRELOAD_NOTE = 'preload 是资源标志；实际加载由目标平台、压缩模式和音频组共同决定，不保证整段流式音频常驻内存，也不代替 audio_group_load。'


def enum_value(value, label, choices):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(label + ' 须为 ' + '、'.join(choices))
    return choices[value]


def export_directory(value):
    if not isinstance(value, str) or len(value) > 240 or value.startswith(('\\', '/')):
        raise ValueError('导出目录须为游戏包内的相对目录；空字符串表示根目录')
    value = value.replace('\\', '/')
    if not value:
        return ''
    parts = [part for part in value.split('/') if part]
    if not parts or any(part in ('.', '..') or part.endswith((' ', '.'))
            or re.search(r'[<>:"|?*\x00-\x1f\x7f]', part)
            or re.match(r'(?i)^(?:con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)', part) for part in parts):
        raise ValueError('导出目录不接受绝对路径、盘符、目录穿越或非法 Windows 目录名')
    value.encode('utf-8')
    return '/'.join(parts)


def group_directory(meta, name):
    rows = [row for row in meta.get('AudioGroups', []) if row.get('name') == name]
    if len(rows) != 1:
        raise ValueError('音频组未唯一存在：' + name)
    row = rows[0]
    return export_directory(row.get('exportDir', '')), bool(row.get('ConfigValues') or meta.get('ConfigValues'))


def export_effective(data, meta):
    group = data['audioGroupId']['name']
    directory, overrides = group_directory(meta, group)
    applies = data['compression'] != 3 and group != 'audiogroup_default'
    source = 'sound' if data['compression'] == 3 else 'audio_group' if applies else 'main_game_data'
    effective = directory if applies else export_directory(data.get('exportDir', '')) if source == 'sound' else ''
    unknown = bool(overrides or data.get('ConfigValues'))
    return {'audio_group_export_directory': directory, 'audio_group_configuration_overrides': overrides,
            'audio_group_directory_applies': applies, 'effective_export_target': 'Windows VM',
            'effective_export_directory': None if unknown else effective,
            'export_directory_source': 'configuration_override' if unknown else source}


def updates(volume=None, compression=None, preload=None, compression_quality=None,
            conversion_mode=None, sample_rate=None, channel_format=None, bit_depth=None,
            export_directory_value=None):
    result = {}
    if volume is not None:
        number(volume, '音量')
        if volume > 1:
            raise ValueError('音量须为 0～1')
        result['volume'] = float(volume)
    for value, label, choices, field in (
            (compression, 'compression', COMPRESSIONS, 'compression'),
            (conversion_mode, 'conversion_mode', CONVERSIONS, 'conversionMode'),
            (channel_format, 'channel_format', CHANNEL_FORMATS, 'channelFormat')):
        if value is not None:
            result[field] = enum_value(value, label, choices)
    if preload is not None:
        boolean(preload, 'preload')
        result['preload'] = preload
    if compression_quality is not None:
        integer(compression_quality, 'compression_quality', 0, 10)
        result['compressionQuality'] = compression_quality
    if sample_rate is not None:
        integer(sample_rate, 'sample_rate', 8000, 192000)
        result['sampleRate'] = sample_rate
    if bit_depth is not None:
        if type(bit_depth) is not int or bit_depth not in (8, 16):
            raise ValueError('bit_depth 须为 8 或 16')
        result['bitDepth'] = (8, 16).index(bit_depth)
    if export_directory_value is not None:
        result['exportDir'] = export_directory(export_directory_value)
    return result


def settings(data):
    """拒绝未知格式/枚举，而不是把它们转换成可覆盖的默认值。"""
    try:
        if data.get('$GMSound') != 'v2' or data.get('resourceType') != 'GMSound':
            raise ValueError('仅支持 GMSound v2 声音设置')
        for field, choices in (('compression', COMPRESSIONS), ('channelFormat', CHANNEL_FORMATS),
                               ('conversionMode', CONVERSIONS)):
            if type(data[field]) is not int or data[field] not in choices.values():
                raise ValueError('不支持的声音字段：' + field)
        integer(data['bitDepth'], 'bitDepth', 0, 1)
        # 共用参数校验，避免查询结果与写入支持范围不一致。
        updates(volume=data['volume'], preload=data['preload'],
                compression_quality=data['compressionQuality'], sample_rate=data['sampleRate'])
        group = data['audioGroupId']
        if not isinstance(group, dict) or not isinstance(group['name'], str):
            raise ValueError('声音缺少有效音频组')
        if group['path'] != 'audiogroups/' + group['name']:
            raise ValueError('声音音频组引用不受支持')
        return {'volume': data['volume'],
                'compression': next(k for k, v in COMPRESSIONS.items() if v == data['compression']),
                'streamed': data['compression'] == 3, 'preload': data['preload'],
                'compression_quality': data['compressionQuality'],
                'conversion_mode': next(k for k, v in CONVERSIONS.items() if v == data['conversionMode']),
                'sample_rate': data['sampleRate'],
                'channel_format': next(k for k, v in CHANNEL_FORMATS.items() if v == data['channelFormat']),
                'bit_depth': (8, 16)[data['bitDepth']], 'audio_group': group['name'],
                'export_directory': export_directory(data.get('exportDir', ''))}
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError('声音设置结构不完整，拒绝修改') from exc


def notes(data):
    result = [PRELOAD_NOTE]
    if data['compression'] == 0:
        result.append('未压缩模式不使用 compression_quality；bit_depth 仅在未压缩导出时生效。')
    else:
        result.append('压缩模式使用 compression_quality；bit_depth 不决定压缩音频的质量。')
    if data.get('soundFile', '').lower().endswith('.ogg') and data['conversionMode'] == 0:
        result.append('OGG automatic 可直接复制源文件；若需保证质量、采样率或声道转换，请显式选择 conversion_mode=required。')
    return result


def configure_export(project, sounds=None, audio_groups=None, expected_project_sha256=None, dry_run=True):
    """批量修改目录，所有目标校验完毕后才写入，组定义同时受项目哈希保护。"""
    from .resources import Edit, name_check
    from .exports import expected
    from .storage import digest, encode
    sounds, audio_groups = [] if sounds is None else sounds, [] if audio_groups is None else audio_groups
    if (not isinstance(sounds, list) or not isinstance(audio_groups, list)
            or not 1 <= len(sounds) + len(audio_groups) <= 128):
        raise ValueError('批量目录配置须包含1～128个声音/音频组')
    for rows in (sounds, audio_groups):
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'name', 'expected_sha256', 'export_directory'}:
                raise ValueError('每个目标须提供 name、expected_sha256、export_directory（null保留）')
            name_check(row['name'])
            if row['name'].casefold() in seen:
                raise ValueError('批量目标重复')
            seen.add(row['name'].casefold())
            if row['export_directory'] is not None:
                export_directory(row['export_directory'])
    with project.lock:
        edit = Edit(project)
        meta = edit.load(project.yyp.name)
        if meta.get('ConfigValues'):
            raise ValueError('项目含配置覆盖，暂不修改音频导出目录')
        project.inspect_audio_groups()  # 校验默认组、组重名及现有声音引用。
        definitions = {row['name']: row for row in meta.get('AudioGroups', [])}
        summary = []
        if audio_groups:
            expected(edit.raw(project.yyp.name), expected_project_sha256, '项目')
        changed_groups = False
        for row in audio_groups:
            data = definitions.get(row['name'])
            if (not data or data.get('$GMAudioGroup') != 'v1' or data.get('resourceType') != 'GMAudioGroup'
                    or data.get('targets') != -1 or data.get('ConfigValues')):
                raise ValueError('仅配置已有全平台 GMAudioGroup v1，不能含配置覆盖')
            expected(encode(data), row['expected_sha256'], '音频组 ' + row['name'])
            before = export_directory(data.get('exportDir', ''))
            if row['export_directory'] is not None:
                after = export_directory(row['export_directory'])
                if row['name'] == 'audiogroup_default' and after:
                    raise ValueError('默认音频组包含于主游戏数据，没有独立导出目录；这里只允许清空')
                if data.get('exportDir') != after:
                    data['exportDir'] = after
                    changed_groups = True
            summary.append({'kind': 'audio_group', 'name': row['name'], 'before': before,
                            'after': export_directory(data.get('exportDir', ''))})
        for row in sounds:
            ref, data = project._sound(edit, row['name'])
            project._sound_group(edit, data)
            expected(edit.raw(ref['path']), row['expected_sha256'], '声音 ' + row['name'])
            if data.get('ConfigValues') or group_directory(meta, data['audioGroupId']['name'])[1]:
                raise ValueError('声音或音频组含配置覆盖，暂不修改导出目录')
            before = settings(data)['export_directory']
            if row['export_directory'] is not None:
                after = export_directory(row['export_directory'])
                if data.get('exportDir') != after:
                    data['exportDir'] = after
                    edit.save(ref['path'], data, preserve_trailing=True)
            summary.append({'kind': 'sound', 'name': row['name'], 'before': before,
                'after': settings(data)['export_directory'], **export_effective(data, meta)})
        if changed_groups:
            edit.save(project.yyp.name, meta, preserve_trailing=True)
        return {**edit.commit(dry_run), 'targets': summary, 'compiled': False,
                'notes': ['Windows：自定义组的非流式音频使用组目录（空串为根）；流式声音使用声音目录；默认组非流式音频嵌入主包。',
                          '不移动源文件、不修改压缩/音量等参数，最终路径须通过导出检查']}
