"""GMSound v2 的限定设置；不转码原音频，不代替运行期音频组加载。"""
from .animation import number
from .display import boolean, integer

COMPRESSIONS = {'uncompressed': 0, 'compressed': 1, 'decompress_on_load': 2, 'streamed': 3}
CHANNEL_FORMATS = {'mono': 0, 'stereo': 1, '3d': 2}
CONVERSIONS = {'automatic': 0, 'required': 1}
PRELOAD_NOTE = 'preload 是资源标志；实际加载由目标平台、压缩模式和音频组共同决定，不保证整段流式音频常驻内存，也不代替 audio_group_load。'


def enum_value(value, label, choices):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(label + ' 须为 ' + '、'.join(choices))
    return choices[value]


def updates(volume=None, compression=None, preload=None, compression_quality=None,
            conversion_mode=None, sample_rate=None, channel_format=None, bit_depth=None):
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
                'bit_depth': (8, 16)[data['bitDepth']], 'audio_group': group['name']}
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
