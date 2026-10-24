"""位图精灵的普通逐帧时间轴；保留原字段，只编辑已知结构。"""
import math


def number(value, label, minimum=0, positive=False):
    try:
        valid = (type(value) in (int, float) and math.isfinite(value)
                 and value >= minimum and (not positive or value > 0))
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(label + ('必须为有限正数' if positive else '必须为有限非负数'))
    return value


def geometry(data):
    return {'width': data['width'], 'height': data['height'],
            'origin': {'mode': data['origin'], 'x': data['sequence']['xorigin'], 'y': data['sequence']['yorigin']},
            'bbox': {'mode': data['bboxMode'], 'left': data['bbox_left'], 'right': data['bbox_right'],
                     'top': data['bbox_top'], 'bottom': data['bbox_bottom']}}


def automatic_bounds(data, bounds):
    if bounds is None:
        raise ValueError('自动碰撞框没有超过透明度阈值的像素，拒绝猜测空碰撞框')
    data.update(bbox_left=bounds[0], bbox_top=bounds[1], bbox_right=bounds[2]-1, bbox_bottom=bounds[3]-1)


def resize_geometry(data, width, height, origin_policy, bbox_policy, alpha_bounds=None):
    """只调整尺寸、原点和碰撞框；帧/时间轴/图层身份保持原样。"""
    if data.get('nineSlice') or data.get('ConfigValues'):
        raise ValueError('尺寸变化暂不支持九宫格或配置覆盖精灵')
    try:
        before = geometry(data)
        box = before['bbox']
        if (type(box['mode']) is not int or box['mode'] not in (0, 1, 2)
                or any(type(box[k]) is not int for k in ('left', 'right', 'top', 'bottom'))
                or not 0 <= box['left'] <= box['right'] < before['width']
                or not 0 <= box['top'] <= box['bottom'] < before['height']):
            raise ValueError('原碰撞框无效，拒绝调整尺寸')
        if type(data['origin']) is not int or not 0 <= data['origin'] <= 9:
            raise ValueError('原点模式不受支持')
        if origin_policy == 'scale':
            data['sequence']['xorigin'] = round(before['origin']['x'] * width / before['width'])
            data['sequence']['yorigin'] = round(before['origin']['y'] * height / before['height'])
        for field in ('xorigin', 'yorigin'):
            if not -(2**31) <= data['sequence'][field] < 2**31:
                raise ValueError('调整后的原点超出 32 位整数范围')
        # 固定明确的像素原点，避免 IDE 按旧的预设模式再次改变它。
        data['origin'] = 9
        if bbox_policy == 'scale':
            data['bboxMode'] = 2
            data['bbox_left'] = math.floor(box['left'] * width / before['width'])
            data['bbox_right'] = math.ceil((box['right'] + 1) * width / before['width']) - 1
            data['bbox_top'] = math.floor(box['top'] * height / before['height'])
            data['bbox_bottom'] = math.ceil((box['bottom'] + 1) * height / before['height']) - 1
        elif bbox_policy == 'full_image' or box['mode'] == 1:
            if bbox_policy == 'full_image':
                data['bboxMode'] = 1
            data.update(bbox_left=0, bbox_top=0, bbox_right=width-1, bbox_bottom=height-1)
        elif box['mode'] == 0:
            automatic_bounds(data, alpha_bounds)
        elif box['right'] >= width or box['bottom'] >= height:
            raise ValueError('保留的手动碰撞框超出新尺寸；请选择 bbox_policy=scale 或 full_image')
        data['width'], data['height'] = width, height
        return {'before': before, 'after': geometry(data),
                'origin_policy': origin_policy, 'bbox_policy': bbox_policy}
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('精灵尺寸或碰撞框结构不完整，拒绝修改') from exc


def timeline(data, path):
    """返回序列、帧轨道、按时间排列的关键帧及图像帧；拒绝隐式修复。"""
    try:
        if (data.get('resourceType') != 'GMSprite' or data.get('$GMSprite') != 'v2'
                or type(data.get('type')) is not int or data['type'] != 0):
            raise ValueError('仅支持 GMSprite v2 位图精灵')
        frames = data['frames']
        frame_map = {frame['name']: frame for frame in frames}
        if not frames or len(frame_map) != len(frames):
            raise ValueError('精灵帧为空或编号重复')
        if any(not isinstance(name, str) or not name for name in frame_map):
            raise ValueError('无效精灵帧编号')
        if any(type(data[field]) is not int or data[field] <= 0 for field in ('width', 'height')):
            raise ValueError('无效精灵尺寸')
        layers = data['layers']
        layer_names = [layer['name'] for layer in layers]
        if (not layers or any(not isinstance(name, str) or not name for name in layer_names)
                or len(set(layer_names)) != len(layer_names)):
            raise ValueError('精灵图像图层为空或编号无效')
        sequence = data['sequence']
        if type(data['origin']) is not int or any(type(sequence[field]) is not int for field in ('xorigin', 'yorigin')):
            raise ValueError('无效精灵原点')
        if sequence.get('$GMSequence') != 'v1' or sequence.get('timeUnits') != 1:
            raise ValueError('仅支持以帧为时间单位的 GMSequence v1')
        number(sequence['playbackSpeed'], '动画速度')
        if type(sequence['playbackSpeedType']) is not int or sequence['playbackSpeedType'] not in (0, 1):
            raise ValueError('不支持的动画速度单位')
        number(sequence['length'], '动画总长度', positive=True)
        tracks = sequence['tracks']
        if len(tracks) != 1 or tracks[0].get('resourceType') != 'GMSpriteFramesTrack':
            raise ValueError('仅支持单一精灵帧轨道')
        track = tracks[0]
        if track.get('tracks') or track.get('modifiers'):
            raise ValueError('暂不支持带子轨道或修饰器的动画')
        keys = track['keyframes']['Keyframes']
        if len(keys) != len(frames):
            raise ValueError('动画关键帧必须与图像帧一一对应')
        seen, key_ids = set(), set()
        for key in keys:
            number(key['Key'], '关键帧起点')
            number(key['Length'], '逐帧时长', positive=True)
            if key.get('Stretch') or key.get('Disabled') or key.get('IsCreationKey'):
                raise ValueError('暂不支持自动延伸、禁用或创建关键帧')
            if set(key['Channels']) != {'0'}:
                raise ValueError('仅支持单通道精灵帧')
            ref = key['Channels']['0']['Id']
            if ref['path'] != path or ref['name'] not in frame_map or ref['name'] in seen:
                raise ValueError('动画帧引用错误或重复')
            seen.add(ref['name'])
            if not isinstance(key['id'], str) or not key['id'] or key['id'] in key_ids:
                raise ValueError('动画关键帧编号无效或重复')
            key_ids.add(key['id'])
        keys = sorted(keys, key=lambda key: key['Key'])
        end = 0.0
        for key in keys:
            if not math.isclose(key['Key'], end, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError('暂不支持带间隙或重叠的动画时间轴')
            next_end = key['Key'] + key['Length']
            if not math.isfinite(next_end) or next_end <= key['Key']:
                raise ValueError('动画时间轴长度超出有效范围')
            end = next_end
        if not math.isclose(sequence['length'], end, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError('动画总长度与关键帧不一致')
        return sequence, track, keys, frame_map
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError('精灵动画结构不完整，拒绝修改') from exc


def retime(sequence, track, keys, frame_order, frame_durations):
    """时长按结果顺序提供；未提供时长时，每帧保留自己的原时长。"""
    for store in ('events', 'moments'):
        store_data = sequence.get(store, {})
        if not isinstance(store_data, dict) or not isinstance(store_data.get('Keyframes', []), list):
            raise ValueError('动画事件结构不完整，拒绝调整时间轴')
        if store_data.get('Keyframes'):
            raise ValueError('含广播消息或时刻的动画暂不支持重排或调整时长')
    if sequence.get('eventToFunction') or sequence.get('eventStubScript') or track.get('events'):
        raise ValueError('含事件的动画暂不支持重排或调整时长')
    by_frame = {key['Channels']['0']['Id']['name']: key for key in keys}
    if frame_order is None:
        frame_order = list(by_frame)
    if (not isinstance(frame_order, list) or any(not isinstance(item, str) for item in frame_order)
            or len(frame_order) != len(by_frame) or set(frame_order) != set(by_frame)):
        raise ValueError('frame_order 必须恰好包含每个现有帧 ID 一次；不增删帧')
    if frame_durations is not None:
        if not isinstance(frame_durations, list) or len(frame_durations) != len(frame_order):
            raise ValueError('frame_durations 必须为与结果帧顺序等长的时长列表')
        for value in frame_durations:
            number(value, '逐帧时长', positive=True)
    result, end = [], 0.0
    for index, frame in enumerate(frame_order):
        key = by_frame[frame]
        duration = key['Length'] if frame_durations is None else frame_durations[index]
        next_end = end + duration
        if not math.isfinite(next_end) or next_end <= end:
            raise ValueError('动画时间轴长度超出有效范围')
        key['Key'], key['Length'] = end, float(duration)
        result.append(key)
        end = next_end
    track['keyframes']['Keyframes'] = result
    sequence['length'] = end
    return frame_order
