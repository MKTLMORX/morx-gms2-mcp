"""资源构建与引用维护；所有变更集中经过事务层。"""
from __future__ import annotations
import io
import math
import re
import wave
from pathlib import Path
from PIL import Image
from . import schemas
from .storage import Storage, digest, encode, parse_yy

KINDS = {'scripts': 'GMScript', 'objects': 'GMObject', 'rooms': 'GMRoom',
         'sprites': 'GMSprite', 'sounds': 'GMSound', 'shaders': 'GMShader'}
DEFAULT_GROUPS = {'scripts': '脚本', 'objects': '物体/object', 'rooms': '房间',
                  'sprites': '精灵', 'sounds': '声音', 'shaders': '着色器'}
EVENTS = {'Create': (0, 0, 'Create'), 'Destroy': (1, 0, 'Destroy'),
          'Step': (3, 0, 'Step'), 'BeginStep': (3, 1, 'Step'), 'EndStep': (3, 2, 'Step'),
          'Draw': (8, 0, 'Draw'), 'DrawGUI': (8, 64, 'Draw'),
          'DrawBegin': (8, 72, 'Draw'), 'DrawEnd': (8, 73, 'Draw'),
          'DrawGUIBegin': (8, 74, 'Draw'), 'DrawGUIEnd': (8, 75, 'Draw'),
          'CleanUp': (12, 0, 'CleanUp'), 'RoomStart': (7, 4, 'Other'), 'RoomEnd': (7, 5, 'Other')}
EVENT_STEMS = {0: 'Create', 1: 'Destroy', 2: 'Alarm', 3: 'Step', 4: 'Collision', 5: 'Keyboard',
               6: 'Mouse', 7: 'Other', 8: 'Draw', 9: 'KeyPress', 10: 'KeyRelease', 12: 'CleanUp', 13: 'Gesture'}


def name_check(name):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', name):
        raise ValueError('资源名必须是 1～64 位英文标识符')
    if name.upper() in {'CON', 'PRN', 'AUX', 'NUL', *[f'COM{i}' for i in range(10)], *[f'LPT{i}' for i in range(10)]}:
        raise ValueError('禁止 Windows 保留名称')


def group_check(group):
    if not group or len(group) > 240:
        raise ValueError('分组路径不能为空或过长')
    parts = group.split('/')
    for part in parts:
        if not part or part in ('.', '..') or part.endswith((' ', '.')) or re.search(r'[<>:"\\|?*\x00-\x1f]', part):
            raise ValueError('无效分组路径')
    return parts


def finite(value, label, minimum=None):
    if not math.isfinite(value) or (minimum is not None and value < minimum):
        raise ValueError(label + '超出允许范围')


def walk_layers(layers):
    for layer in layers:
        yield layer
        yield from walk_layers(layer.get('layers', []))


class Edit:
    def __init__(self, project):
        self.project, self.before, self.changes = project, {}, {}

    def raw(self, rel):
        if rel not in self.before:
            self.before.update(self.project.snapshot([rel]))
        return self.changes.get(rel, self.before[rel])

    def load(self, rel, default=None):
        raw = self.raw(rel)
        if raw is None:
            if default is None:
                raise ValueError('文件不存在：' + rel)
            return default
        return parse_yy(raw.decode('utf-8-sig'))

    def save(self, rel, data):
        original = self.raw(rel)
        raw = encode(data)
        if original:
            if b'\r\n' in original:
                raw = raw.replace(b'\n', b'\r\n')
            if original.startswith(b'\xef\xbb\xbf'):
                raw = b'\xef\xbb\xbf' + raw
        self.changes[rel] = raw

    def new(self, rel, data):
        if self.raw(rel) is not None:
            raise ValueError('文件已存在，不覆盖：' + rel)
        self.changes[rel] = data

    def commit(self, dry_run=False):
        # 清单最后提交；恢复反向执行，优先移除注册再恢复资源。
        changes = {k: v for k, v in self.changes.items() if k != self.project.yyp.name}
        if self.project.yyp.name in self.changes:
            changes[self.project.yyp.name] = self.changes[self.project.yyp.name]
        return self.project.commit(changes, self.before, dry_run=dry_run)


class Project(Storage):
    def metadata(self):
        return self.read(self.yyp.name)

    def _group(self, edit, group):
        parts = group_check(group)
        project = edit.load(self.yyp.name)
        order_rel = self.yyp.with_suffix('.resource_order').name
        order = edit.load(order_rel, {'FolderOrderSettings': [], 'ResourceOrderSettings': []})
        for i, part in enumerate(parts):
            path = 'folders/' + '/'.join(parts[:i + 1]) + '.yy'
            existing = [f for f in project.get('Folders', []) if f['folderPath'].casefold() == path.casefold()]
            if existing and existing[0]['folderPath'] != path:
                raise ValueError('分组大小写与现有路径不一致')
            if not existing:
                project.setdefault('Folders', []).append(schemas.typed('GMFolder', **{'%Name': part},
                    name=part, folderPath=path))
                peers = [v['order'] for v in order['FolderOrderSettings']
                         if v['path'].rsplit('/', 1)[0] == path.rsplit('/', 1)[0]]
                order['FolderOrderSettings'].append({'name': part, 'path': path, 'order': max(peers, default=-1) + 1})
        edit.save(order_rel, order)
        edit.save(self.yyp.name, project)
        return {'name': parts[-1], 'path': 'folders/' + group + '.yy'}

    def ensure_group(self, group, dry_run=False):
        with self.lock:
            edit = Edit(self)
            self._group(edit, group)
            return edit.commit(dry_run)

    def _register(self, edit, kind, name, data, group):
        name_check(name)
        group = group or DEFAULT_GROUPS[kind]
        project = edit.load(self.yyp.name)
        if any(x['id']['name'].casefold() == name.casefold() for x in project['resources']):
            raise ValueError('同名资源已存在；不会覆盖或重复注册')
        directory = self.path(f'{kind}/{name}')
        if directory.exists() and any(directory.iterdir()):
            raise ValueError('资源目录已存在；不会覆盖未注册文件')
        parent = self._group(edit, group)
        rel = f'{kind}/{name}/{name}.yy'
        data['parent'] = parent
        edit.new(rel, encode(data))
        project = edit.load(self.yyp.name)
        project['resources'].append({'id': {'name': name, 'path': rel}})
        if kind == 'rooms':
            project.setdefault('RoomOrderNodes', []).append({'roomId': {'name': name, 'path': rel}})
        edit.save(self.yyp.name, project)
        order_rel = self.yyp.with_suffix('.resource_order').name
        order = edit.load(order_rel)
        order['ResourceOrderSettings'].append({'name': name, 'path': rel,
            'order': max((v['order'] for v in order['ResourceOrderSettings']), default=-1) + 1})
        edit.save(order_rel, order)
        return rel

    def _ref(self, edit, name, kind=None):
        name_check(name)
        refs = [x['id'] for x in edit.load(self.yyp.name)['resources'] if x['id']['name'] == name]
        if len(refs) != 1:
            raise ValueError('资源未唯一注册：' + name)
        data = edit.load(refs[0]['path'])
        if kind and data['resourceType'] != KINDS[kind]:
            raise ValueError('资源类型不匹配：' + name)
        return refs[0], data

    def resource(self, kind, name):
        return self._ref(Edit(self), name, kind)[0]['path']

    def create(self, kind, name, code='', group=None, dry_run=False):
        with self.lock:
            edit = Edit(self)
            if kind == 'scripts':
                data = schemas.typed('GMScript', 'v1', **{'%Name': name}, name=name, isCompatibility=False, isDnD=False)
            elif kind == 'objects':
                data = schemas.object_resource(name)
                if group is None:
                    group = '物体/system' if name.startswith('sys_') else '物体/ui' if name.startswith('ui_') else '物体/object'
            else:
                raise ValueError('此方法只创建脚本和物体')
            self._register(edit, kind, name, data, group)
            if kind == 'scripts':
                edit.new(f'scripts/{name}/{name}.gml', code.encode('utf-8'))
            return edit.commit(dry_run)

    def move_resource(self, name, group, dry_run=False):
        with self.lock:
            edit = Edit(self)
            ref, data = self._ref(edit, name)
            data['parent'] = self._group(edit, group)
            edit.save(ref['path'], data)
            return edit.commit(dry_run)

    def add_event(self, name, event, code, number=0, dry_run=False):
        if event == 'Alarm' and number in range(12):
            typ, num, stem = 2, number, 'Alarm'
        elif event == 'User' and number in range(16):
            typ, num, stem = 7, number + 10, 'Other'
        elif event in EVENTS and number == 0:
            typ, num, stem = EVENTS[event]
        else:
            raise ValueError('不支持的事件或编号；Alarm 为 0～11，User 为 0～15')
        with self.lock:
            edit = Edit(self)
            ref, obj = self._ref(edit, name, 'objects')
            gml = f'objects/{name}/{stem}_{num}.gml'
            if any(e['eventType'] == typ and e['eventNum'] == num for e in obj['eventList']):
                raise ValueError('事件已存在；不会覆盖')
            obj['eventList'].append(schemas.typed('GMEvent', 'v1', **{'%Name': ''}, name='',
                collisionObjectId=None, eventNum=num, eventType=typ, isDnD=False))
            edit.new(gml, code.encode('utf-8'))
            edit.save(ref['path'], obj)
            return edit.commit(dry_run)

    def create_room(self, name, width=1366, height=768, group='房间', dry_run=False):
        if not 1 <= width <= 32768 or not 1 <= height <= 32768:
            raise ValueError('房间尺寸须为 1～32768')
        with self.lock:
            edit = Edit(self)
            self._register(edit, 'rooms', name, schemas.room(name, width, height), group)
            return edit.commit(dry_run)

    def add_layer(self, room, layer, depth=0, dry_run=False):
        name_check(layer)
        with self.lock:
            edit = Edit(self)
            ref, data = self._ref(edit, room, 'rooms')
            if any(l['name'].casefold() == layer.casefold() for l in walk_layers(data['layers'])):
                raise ValueError('房间已存在同名图层')
            data['layers'].append(schemas.instance_layer(layer, depth))
            edit.save(ref['path'], data)
            return edit.commit(dry_run)

    def configure_object(self, name, persistent=None, visible=None, sprite=None, parent=None, dry_run=False):
        with self.lock:
            edit = Edit(self)
            ref, data = self._ref(edit, name, 'objects')
            for field, value in (('persistent', persistent), ('visible', visible)):
                if value is not None:
                    if type(value) is not bool:
                        raise ValueError('物体标志必须为布尔值')
                    data[field] = value
            for field, value, kind in (('spriteId', sprite, 'sprites'), ('parentObjectId', parent, 'objects')):
                if value is not None:
                    data[field] = self._ref(edit, value, kind)[0] if value else None
            ancestor, seen = data['parentObjectId'], {name}
            while ancestor:
                if ancestor['name'] in seen:
                    raise ValueError('父物体继承形成循环')
                seen.add(ancestor['name'])
                ancestor = self._ref(edit, ancestor['name'], 'objects')[1]['parentObjectId']
            edit.save(ref['path'], data)
            return edit.commit(dry_run)

    def set_start_room(self, room, dry_run=False):
        with self.lock:
            edit = Edit(self)
            ref, _ = self._ref(edit, room, 'rooms')
            project = edit.load(self.yyp.name)
            project['RoomOrderNodes'] = [{'roomId': ref}] + [n for n in project['RoomOrderNodes'] if n['roomId'] != ref]
            edit.save(self.yyp.name, project)
            return edit.commit(dry_run)

    def place(self, obj, room, layer, instance, x=0, y=0, dry_run=False):
        name_check(instance)
        finite(x, 'X 坐标')
        finite(y, 'Y 坐标')
        with self.lock:
            edit = Edit(self)
            obj_ref, _ = self._ref(edit, obj, 'objects')
            ref, data = self._ref(edit, room, 'rooms')
            all_layers = list(walk_layers(data['layers']))
            layers = [l for l in all_layers if l['name'] == layer and l['resourceType'] == 'GMRInstanceLayer']
            if len(layers) != 1:
                raise ValueError('必须指定唯一实例图层')
            if any(i['name'].casefold() == instance.casefold() for l in all_layers for i in l.get('instances', [])):
                raise ValueError('同名实例已存在')
            layers[0]['instances'].append(schemas.typed('GMRInstance', 'v4', **{'%Name': instance}, name=instance,
                colour=4294967295, frozen=False, hasCreationCode=False, ignore=False, imageIndex=0, imageSpeed=1.0,
                inheritCode=False, inheritedItemId=None, inheritItemSettings=False, isDnd=False, objectId=obj_ref,
                properties=[], rotation=0.0, scaleX=1.0, scaleY=1.0, x=x, y=y))
            data['instanceCreationOrder'].append({'name': instance, 'path': ref['path']})
            edit.save(ref['path'], data)
            return edit.commit(dry_run)

    def import_sprite(self, name, png_paths, group='精灵', speed=10.0, origin_x=0, origin_y=0, dry_run=False):
        name_check(name)
        finite(speed, '动画速度', 0)
        if not 1 <= len(png_paths) <= 256:
            raise ValueError('须提供 1～256 张等尺寸 PNG，按列表顺序导入')
        images, size = [], None
        for value in png_paths:
            path = Path(value).resolve(strict=True)
            if path.suffix.lower() != '.png' or path.stat().st_size > 64 * 1024 * 1024:
                raise ValueError('仅支持每张不超过 64 MiB 的 PNG')
            raw = path.read_bytes()
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != 'PNG' or getattr(image, 'n_frames', 1) != 1:
                    raise ValueError('仅支持单帧 PNG；动画请传入多张 PNG')
                if size and size != image.size:
                    raise ValueError('所有帧必须等尺寸')
                size = image.size
                if max(size) > 8192:
                    raise ValueError('精灵尺寸不可超过 8192')
                image.verify()
            images.append(raw)
        data, frames, layer = schemas.sprite(name, *size, len(images), speed, origin_x, origin_y)
        with self.lock:
            edit = Edit(self)
            project = edit.load(self.yyp.name)
            if not any(g['name'] == 'Default' for g in project.get('TextureGroups', [])):
                raise ValueError('项目缺少 Default 纹理组')
            self._register(edit, 'sprites', name, data, group)
            for frame, raw in zip(frames, images):
                edit.new(f'sprites/{name}/{frame}.png', raw)
                edit.new(f'sprites/{name}/layers/{frame}/{layer}.png', raw)
            return edit.commit(dry_run)

    def import_sound(self, name, wav_path, group='声音', volume=1.0, dry_run=False):
        name_check(name)
        finite(volume, '音量', 0)
        if volume > 1:
            raise ValueError('音量须为 0～1')
        path = Path(wav_path).resolve(strict=True)
        if path.suffix.lower() != '.wav' or path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError('本版支持不超过 128 MiB 的 PCM WAV')
        raw = path.read_bytes()
        with wave.open(io.BytesIO(raw)) as sound:
            rate, frames, channels, width = sound.getframerate(), sound.getnframes(), sound.getnchannels(), sound.getsampwidth()
            if channels not in (1, 2) or width not in (1, 2, 3) or not frames:
                raise ValueError('WAV 必须为非空单声道或双声道 8／16／24 位 PCM')
            if len(sound.readframes(frames)) != frames * channels * width:
                raise ValueError('WAV 数据截断')
        data = schemas.typed('GMSound', 'v2', **{'%Name': name}, name=name,
            audioGroupId={'name': 'audiogroup_default', 'path': 'audiogroups/audiogroup_default'},
            bitDepth=1, channelFormat=0, compression=0, compressionQuality=4, conversionMode=0,
            duration=frames/rate, exportDir='', preload=False, sampleRate=rate, soundFile=name+'.wav', volume=volume)
        with self.lock:
            edit = Edit(self)
            if not any(g['name'] == 'audiogroup_default' for g in edit.load(self.yyp.name).get('AudioGroups', [])):
                raise ValueError('项目缺少默认音频组')
            self._register(edit, 'sounds', name, data, group)
            edit.new(f'sounds/{name}/{name}.wav', raw)
            return edit.commit(dry_run)

    def create_shader(self, name, vertex_code, fragment_code, group='着色器', dry_run=False):
        with self.lock:
            edit = Edit(self)
            data = schemas.typed('GMShader', **{'%Name': name}, name=name, type=1)
            self._register(edit, 'shaders', name, data, group)
            edit.new(f'shaders/{name}/{name}.vsh', vertex_code.encode('utf-8'))
            edit.new(f'shaders/{name}/{name}.fsh', fragment_code.encode('utf-8'))
            return edit.commit(dry_run)

    def inspect_resource(self, name):
        with self.lock:
            ref, data = self._ref(Edit(self), name)
            paths = [ref['path']]
            directory = self.path(ref['path']).parent
            paths += [p.relative_to(self.root).as_posix() for p in directory.iterdir() if p.suffix in ('.gml', '.vsh', '.fsh')]
            return {'resource': data, 'files': [{'path': p, 'sha256': digest(self.path(p).read_bytes())} for p in paths]}

    def validate(self):
        from .validation import validate
        with self.lock:
            return validate(self)
