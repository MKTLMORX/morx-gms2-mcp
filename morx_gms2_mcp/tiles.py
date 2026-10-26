"""房间瓦片存储适配；保持原编码类型，限制解码后的内存规模。"""
from itertools import groupby

from .schemas import EMPTY_TILE

MAX_CELLS = 4 * 1024 * 1024
INDEX_MASK = (1 << 19) - 1


def empty(value):
    return value & 0xFFFFFFFF == EMPTY_TILE


def _value(value):
    if type(value) is not int or not -(1 << 31) <= value < (1 << 32):
        raise ValueError('瓦片数据须为 32 位整数')
    return value


def read(data):
    """返回尺寸和独立格子列表；支持旧数组及格式 1 的有符号段长度。"""
    if not isinstance(data, dict):
        raise ValueError('瓦片图层数据须为对象')
    width, height = data.get('SerialiseWidth'), data.get('SerialiseHeight')
    if type(width) is not int or type(height) is not int or min(width, height) < 1:
        raise ValueError('瓦片图层尺寸无效')
    size = width * height
    if size > MAX_CELLS:
        raise ValueError('瓦片图层超过 4194304 格')
    form = data.get('TileDataFormat', 0)
    if type(form) is not int or form not in (0, 1):
        raise ValueError('不支持的瓦片数据格式')
    field = 'TileCompressedData' if form else 'TileSerialiseData'
    other = 'TileSerialiseData' if form else 'TileCompressedData'
    raw = data.get(field)
    if not isinstance(raw, list) or data.get(other) not in (None, []):
        raise ValueError('瓦片图层数据缺失或存在冲突编码')
    if not form:
        if len(raw) != size:
            raise ValueError('瓦片数量与图层尺寸不一致')
        return width, height, [_value(item) for item in raw]
    if len(raw) > size * 2:
        raise ValueError('压缩瓦片数据超过长度限制')
    values, cursor = [], 0
    while cursor < len(raw):
        length = raw[cursor]
        cursor += 1
        if type(length) is not int or len(values) + abs(length) > size:
            raise ValueError('压缩瓦片段长度无效或超出图层尺寸')
        if length == 0:
            continue
        stored = 1 if length < 0 else length
        if cursor + stored > len(raw):
            raise ValueError('压缩瓦片段数据不完整')
        segment = [_value(item) for item in raw[cursor:cursor + stored]]
        if any(item >= 1 << 31 for item in segment):
            raise ValueError('压缩瓦片数据须使用有符号 32 位整数')
        values.extend(segment * (-length) if length < 0 else segment)
        cursor += stored
    if len(values) != size:
        raise ValueError('瓦片数量与图层尺寸不一致')
    return width, height, values


def write(data, values):
    """写回已读取的格子；重复段和连续单值段分别编码，不转换原格式。"""
    if data.get('TileDataFormat', 0) == 0:
        data['TileSerialiseData'] = values
        return
    packed, singles = [], []
    signed = (value - (1 << 32) if value >= 1 << 31 else value for value in values)
    for value, run in groupby(signed):
        count = sum(1 for _ in run)
        if count == 1:
            singles.append(value)
            continue
        if singles:
            packed.extend([len(singles), *singles])
            singles.clear()
        packed.extend([-count, value])
    if singles:
        packed.extend([len(singles), *singles])
    data['TileCompressedData'] = packed
