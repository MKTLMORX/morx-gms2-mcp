"""资源构建与引用维护；所有变更集中经过事务层。"""
from __future__ import annotations
import io
import math
import os
import re
import wave
from pathlib import Path, PurePosixPath
from PIL import Image
from . import audio
from . import schemas
from . import tiles as tile_storage
from .storage import Storage, digest, encode, parse_yy
from .animation import number, timeline, retime, geometry, resize_geometry, automatic_bounds
from .display import WINDOWS_PATH, WINDOWS_FLAGS, VIEW_FIELDS, windows_settings, room_settings, check_expected
from .display import integer, boolean, title_check

KINDS = {'scripts': 'GMScript', 'objects': 'GMObject', 'rooms': 'GMRoom',
         'sprites': 'GMSprite', 'sounds': 'GMSound', 'shaders': 'GMShader',
         'tilesets': 'GMTileSet'}
DEFAULT_GROUPS = {'scripts': '脚本', 'objects': '物体/object', 'rooms': '房间',
                  'sprites': '精灵', 'sounds': '声音', 'shaders': '着色器',
                  'tilesets': '瓦片集'}
EVENTS = {'Create': (0, 0, 'Create'), 'Destroy': (1, 0, 'Destroy'),
          'Step': (3, 0, 'Step'), 'BeginStep': (3, 1, 'Step'), 'EndStep': (3, 2, 'Step'),
          'Draw': (8, 0, 'Draw'), 'DrawGUI': (8, 64, 'Draw'),
          'DrawResize': (8, 65, 'Draw'), 'DrawBegin': (8, 72, 'Draw'), 'DrawEnd': (8, 73, 'Draw'),
          'DrawGUIBegin': (8, 74, 'Draw'), 'DrawGUIEnd': (8, 75, 'Draw'),
          'DrawPre': (8, 76, 'Draw'), 'DrawPost': (8, 77, 'Draw'),
          'CleanUp': (12, 0, 'CleanUp'), 'RoomStart': (7, 4, 'Other'), 'RoomEnd': (7, 5, 'Other'),
          'GameStart': (7, 2, 'Other'), 'GameEnd': (7, 3, 'Other'),
          'AnimationEnd': (7, 7, 'Other'), 'NoMoreLives': (7, 6, 'Other'),
          'NoMoreHealth': (7, 9, 'Other'), 'EndOfPath': (7, 8, 'Other'),
          'Outside': (7, 0, 'Other'), 'Boundary': (7, 1, 'Other'),
          'AsyncNetworking': (7, 68, 'Other'), 'Networking': (7, 68, 'Other')}
EVENT_STEMS = {0: 'Create', 1: 'Destroy', 2: 'Alarm', 3: 'Step', 4: 'Collision', 5: 'Keyboard',
               6: 'Mouse', 7: 'Other', 8: 'Draw', 9: 'KeyPress', 10: 'KeyRelease', 12: 'CleanUp', 13: 'Gesture'}
INPUT_EVENTS = {'Keyboard': 5, 'KeyPress': 9, 'KeyRelease': 10}
MOUSE_EVENT_NUMBERS = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 50, 51, 52, 53, 54, 55, 56, 57, 58, 60, 61}


def event_spec(event, number, collision_object=None):
    """事件名与编号统一映射；创建与读写共用，避免类型判断漂移。"""
    if type(number) is not int:
        raise ValueError('事件编号必须为整数')
    if event == 'Collision':
        if number != 0:
            raise ValueError('Collision 事件编号必须为 0')
        if not isinstance(collision_object, str) or not collision_object:
            raise ValueError('Collision 事件须提供 collision_object')
        name_check(collision_object)
        return 4, 0, 'Collision', collision_object
    if event in INPUT_EVENTS:
        if not 1 <= number <= 255:
            raise ValueError('键盘事件键码须为 1～255')
        return INPUT_EVENTS[event], number, event, None
    if event == 'Mouse':
        if number not in MOUSE_EVENT_NUMBERS:
            raise ValueError('不支持的鼠标事件编号；0～11、50～58、60～61')
        return 6, number, 'Mouse', None
    if event == 'Alarm' and 0 <= number < 12:
        return 2, number, 'Alarm', None
    if event == 'User' and 0 <= number < 16:
        return 7, number + 10, 'Other', None
    if event in EVENTS and number == 0:
        return (*EVENTS[event], None)
    raise ValueError('不支持的事件或编号；Alarm 为 0～11，User 为 0～15')


def trigger_spec(event, number=0, collision_object=None):
    """运行期 event_perform 的映射；返回 (类型, 编号, 碰撞目标)。
    与事件文件创建共用编号规则，但不允许 Draw：Draw 事件不能在外层绘制流程之外强制执行。
    """
    typ, num, _, collision = event_spec(event, number, collision_object)
    if typ == 8:
        raise ValueError('Draw 事件不能由运行期事件触发器执行')
    if (typ, num) == (7, 68):
        raise ValueError('Networking 由真实网络回调触发，不能用事件触发器伪造 async_load')
    if event == 'Collision':
        return 4, 0, collision
    return typ, num, None


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


def find_refs(value, path, location='$'):
    """查找带 name/path 且路径匹配的资源引用。精灵动画帧的 Id 也带目标路径，
    但名称是帧 ID，因此不能只按名称匹配。
    """
    if isinstance(value, dict):
        if 'name' in value and value.get('path') == path:
            yield location
        for key, child in value.items():
            yield from find_refs(child, path, f'{location}.{key}')
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from find_refs(child, path, f'{location}[{index}]')


def replace_refs(value, name, path, new_name, new_path):
    """原地更新资源引用，返回更新数量；非资源名的引用只改路径。"""
    count = 0
    if isinstance(value, dict):
        if 'name' in value and value.get('path') == path:
            value['path'] = new_path
            if value.get('name') == name:
                value['name'] = new_name
            count += 1
        for child in value.values():
            count += replace_refs(child, name, path, new_name, new_path)
    elif isinstance(value, list):
        for child in value:
            count += replace_refs(child, name, path, new_name, new_path)
    return count


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

    def _texture_inventory(self, edit):
        from . import textures
        meta = edit.load(self.yyp.name)
        groups = textures.groups(meta)
        members = {row['name']: [] for row in groups}
        for entry in meta.get('resources', []):
            ref = entry['id']
            data = edit.load(ref['path'])
            group = data.get('textureGroupId')
            if group is None:
                continue
            if (not isinstance(group, dict) or group.get('name') not in members
                    or group.get('path') != 'texturegroups/' + group['name']):
                raise ValueError('纹理组引用无效：' + ref['name'])
            members[group['name']].append({'name': ref['name'], 'path': ref['path'],
                'type': data.get('resourceType'), 'sha256': digest(edit.raw(ref['path'])),
                'configuration_overrides': bool(data.get('ConfigValues'))})
        return meta, groups, members

    def inspect_texture_groups(self):
        from . import textures
        with self.lock:
            edit = Edit(self)
            meta, groups, members = self._texture_inventory(edit)
            result = []
            for row in groups:
                reason = None
                try:
                    textures.editable(meta, row)
                    if any(m['configuration_overrides'] for m in members[row['name']]):
                        raise ValueError('组内资源含配置覆盖，暂不修改组设置')
                except ValueError as exc:
                    reason = str(exc)
                result.append({'name': row['name'], 'settings': textures.settings(row),
                    'editable': reason is None, 'unsupported_reason': reason,
                    'definition': row, 'members': members[row['name']], 'notes': textures.notes(row)})
            return {'project_sha256': digest(edit.raw(self.yyp.name)), 'texture_groups': result}

    def _texture_hash(self, edit, expected):
        if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
            raise ValueError('必须提供 inspect_texture_groups 返回的 expected_project_sha256')
        if digest(edit.raw(self.yyp.name)) != expected:
            raise ValueError('项目描述已修改，请重新 inspect_texture_groups 并预览')

    def create_texture_group(self, name, expected_project_sha256, mips_to_generate=0,
                             border=2, autocrop=True, allow_scaling=True, dry_run=True):
        from . import textures
        name_check(name)
        with self.lock:
            edit = Edit(self)
            self._texture_hash(edit, expected_project_sha256)
            meta, groups, _ = self._texture_inventory(edit)
            if meta.get('ConfigValues'):
                raise ValueError('项目含配置覆盖，暂不修改')
            if any(g['name'].casefold() == name.casefold() for g in groups):
                raise ValueError('纹理组已存在，不覆盖')
            row = textures.new_group(name)
            textures.update(row, mips_to_generate, border, autocrop, allow_scaling)
            groups.append(row)
            edit.save(self.yyp.name, meta)
            return {**edit.commit(dry_run), 'name': name, 'settings': textures.settings(row),
                    'notes': textures.notes(row)}

    def configure_texture_group(self, name, expected_project_sha256, mips_to_generate=None,
                                border=None, autocrop=None, allow_scaling=None, dry_run=True):
        from . import textures
        with self.lock:
            edit = Edit(self)
            self._texture_hash(edit, expected_project_sha256)
            meta, groups, members = self._texture_inventory(edit)
            row = next((g for g in groups if g['name'] == name), None)
            if row is None:
                raise ValueError('纹理组不存在')
            textures.editable(meta, row)
            if any(m['configuration_overrides'] for m in members[name]):
                raise ValueError('组内资源含配置覆盖，暂不修改组设置')
            before = textures.settings(row)
            textures.update(row, mips_to_generate, border, autocrop, allow_scaling)
            edit.save(self.yyp.name, meta)
            return {**edit.commit(dry_run), 'name': name, 'before': before,
                    'after': textures.settings(row), 'affected_resources': members[name],
                    'notes': textures.notes(row)}

    def assign_sprite_texture_group(self, names, texture_group, expected_project_sha256,
                                    expected_resource_sha256, dry_run=True):
        from . import textures
        if (not isinstance(names, list) or not 1 <= len(names) <= 256
                or any(not isinstance(n, str) for n in names) or len(set(names)) != len(names)):
            raise ValueError('names 必须为 1～256 个不重复的精灵名')
        if not isinstance(expected_resource_sha256, dict) or set(expected_resource_sha256) != set(names):
            raise ValueError('必须提供与 names 完全对应的 expected_resource_sha256')
        with self.lock:
            edit = Edit(self)
            self._texture_hash(edit, expected_project_sha256)
            meta, groups, _ = self._texture_inventory(edit)
            table = {g['name']: g for g in groups}
            if texture_group not in table:
                raise ValueError('目标纹理组不存在')
            textures.editable(meta, table[texture_group])
            textures.update(table[texture_group])
            changes = []
            for name in names:
                ref, data = self._ref(edit, name, 'sprites')
                expected = expected_resource_sha256[name]
                if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
                    raise ValueError('精灵哈希无效：' + name)
                self._check_expected(edit, ref, expected)
                if (data.get('$GMSprite') != 'v2' or type(data.get('type')) is not int
                        or data['type'] != 0 or data.get('ConfigValues')):
                    raise ValueError('只分配无配置覆盖的 GMSprite v2 位图精灵：' + name)
                old = data.get('textureGroupId')
                if not old or old['name'] not in table:
                    raise ValueError('原纹理组引用无效：' + name)
                textures.editable(meta, table[old['name']])
                data['textureGroupId'] = {'name': texture_group, 'path': 'texturegroups/' + texture_group}
                changes.append({'name': name, 'before': old['name'], 'after': texture_group})
                edit.save(ref['path'], data)
            return {**edit.commit(dry_run), 'assignments': changes,
                    'notes': textures.notes(table[texture_group])}

    def _code_path(self, edit, name, event, number, collision_object=None):
        ref, data = self._ref(edit, name)
        if data['resourceType'] == 'GMScript' and event is None and number == 0 and collision_object is None:
            if data.get('isDnD'):
                raise ValueError('不修改 GML Visual 脚本')
            return f'scripts/{name}/{name}.gml'
        if data['resourceType'] != 'GMObject' or event is None:
            raise ValueError('须指定脚本，或物体及其已有事件')
        typ, num, stem, collision = event_spec(event, number, collision_object)
        if typ == 4:
            target = self._ref(edit, collision, 'objects')[0]
            events = [e for e in data['eventList'] if e['eventType'] == 4
                      and (e.get('collisionObjectId') or {}).get('path') == target['path']]
            path = f'objects/{name}/Collision_{collision}.gml'
        else:
            events = [e for e in data['eventList'] if e['eventType'] == typ and e['eventNum'] == num]
            path = f'objects/{name}/{stem}_{num}.gml'
        if len(events) != 1 or events[0].get('isDnD'):
            raise ValueError('事件不存在、不唯一或属于 GML Visual')
        return path

    def read_code(self, name, event=None, number=0, collision_object=None):
        with self.lock:
            edit = Edit(self)
            path = self._code_path(edit, name, event, number, collision_object)
            raw = edit.raw(path)
            if raw is None or len(raw) > 2 * 1024 * 1024:
                raise ValueError('代码文件不存在或超过 2 MiB')
            return {'name': name, 'event': event, 'number': number,
                    'collision_object': collision_object, 'path': path,
                    'sha256': digest(raw), 'code': raw.decode('utf-8-sig')}

    def update_code(self, name, code, expected_sha256, event=None, number=0,
                    collision_object=None, dry_run=True):
        if not isinstance(code, str) or '\0' in code or len(code.encode('utf-8')) > 2 * 1024 * 1024:
            raise ValueError('GML 须为不含 NUL、最多 2 MiB 的 UTF-8 文本')
        if not isinstance(expected_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_sha256):
            raise ValueError('必须传 read_code 返回的 expected_sha256')
        from .runtime_bridge import OBJECT
        if name == OBJECT:
            raise ValueError('不通过代码编辑工具修改受管理的测试桥接')
        with self.lock:
            edit = Edit(self)
            path = self._code_path(edit, name, event, number, collision_object)
            raw = edit.raw(path)
            if raw is None or digest(raw) != expected_sha256:
                raise ValueError('代码已被修改或不存在，请重新 read_code 并预览')
            normalized = code.replace('\r\n', '\n').replace('\r', '\n')
            if b'\r\n' in raw:
                normalized = normalized.replace('\n', '\r\n')
            edit.changes[path] = (b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b'') + normalized.encode('utf-8')
            result = edit.commit(dry_run)
            result['compiled'] = False
            return result

    def _bridge_object(self, edit):
        """读取受管理桥接；任何未受管理的同名物体都给出明确错误。"""
        from .runtime_bridge import OBJECT, bridge_key, sources
        ref, obj = self._ref(edit, OBJECT, 'objects')
        create = edit.raw(f'objects/{OBJECT}/Create_0.gml')
        if create is None:
            raise ValueError('sys_morx_runtime 已被普通物体占用或桥接代码缺失；请删除该物体后重试')
        try:
            key = bridge_key(create.decode('utf-8-sig'))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError('sys_morx_runtime 已被普通物体占用，未发现受管理的桥接标记；请删除该物体后重试') from exc
        expected = sources(key)
        files = {}
        for event, code in expected.items():
            _, number, stem = EVENTS[event]
            path = f'objects/{OBJECT}/{stem}_{number}.gml'
            raw = edit.raw(path)
            try:
                text = None if raw is None else raw.decode('utf-8-sig').replace('\r\n', '\n')
            except UnicodeDecodeError:
                text = None
            files[path] = (text, raw)
        return ref, obj, key, expected, files

    @staticmethod
    def _bridge_text(original, code):
        """写回桥接代码时保留已有 BOM/CRLF 风格。"""
        raw = code.encode('utf-8')
        if original:
            if b'\r\n' in original:
                raw = raw.replace(b'\n', b'\r\n')
            if original.startswith(b'\xef\xbb\xbf'):
                raw = b'\xef\xbb\xbf' + raw
        return raw

    def _bridge_rooms(self, edit):
        """返回启动房间及所有房间的桥接图层/实例状态。"""
        from .runtime_bridge import LAYER
        meta = edit.load(self.yyp.name)
        order = meta.get('RoomOrderNodes', [])
        if not order:
            raise ValueError('项目必须已有启动房间')
        start = order[0]['roomId']['name']
        states = []
        for node in order:
            name = node['roomId']['name']
            ref, room = self._ref(edit, name, 'rooms')
            top = [layer for layer in room.get('layers', []) if layer.get('name') == LAYER]
            nested = [layer for layer in walk_layers(room.get('layers', []))
                      if layer.get('name') == LAYER and layer not in top]
            stray = [instance for layer in walk_layers(room.get('layers', []))
                     for instance in layer.get('instances', [])
                     if instance.get('name') == 'inst_morx_runtime' and layer not in top]
            states.append({'name': name, 'ref': ref, 'room': room,
                           'top': top, 'nested': nested, 'stray': stray})
        return start, states

    @staticmethod
    def _room_editable(room):
        return room.get('$GMRoom') == 'v1' and not room.get('parentRoom') and not room.get('inheritLayers')

    def _bridge_room_ok(self, edit, bridge_ref):
        from .runtime_bridge import OBJECT
        start, states = self._bridge_rooms(edit)
        start_state = next((state for state in states if state['name'] == start), None)
        if start_state is None or not self._room_editable(start_state['room']):
            return False
        start_ok = False
        for state in states:
            if state['nested'] or state['stray']:
                return False
            if state['top']:
                if state['name'] != start or len(state['top']) != 1:
                    return False
                instances = state['top'][0].get('instances', [])
                if len(instances) != 1:
                    return False
                instance = instances[0]
                obj = instance.get('objectId') or {}
                if (instance.get('name') != 'inst_morx_runtime'
                        or obj.get('name') != OBJECT or obj.get('path') != bridge_ref['path']):
                    return False
                start_ok = True
        return start_ok

    def _sync_bridge_room(self, edit, bridge_ref):
        """把桥接实例收敛到当前启动房间，并从其他房间移除旧实例。"""
        from .runtime_bridge import LAYER
        start, states = self._bridge_rooms(edit)
        start_state = next((state for state in states if state['name'] == start), None)
        if start_state is None or not self._room_editable(start_state['room']):
            raise ValueError('桥接安装需要非继承的 GMRoom v1 启动房间')
        if self._bridge_room_ok(edit, bridge_ref):
            return start, False
        for state in states:
            if state['nested'] or state['stray']:
                raise ValueError('房间中存在无法自动修复的桥接图层或实例，请手动清理：' + state['name'])
            if state['top'] and not self._room_editable(state['room']):
                raise ValueError('房间包含继承设置，无法自动移除桥接图层：' + state['name'])
        for state in states:
            if not state['top']:
                continue
            removed = [instance.get('name') for layer in state['top'] for instance in layer.get('instances', [])]
            room = state['room']
            room['layers'] = [layer for layer in room.get('layers', []) if layer.get('name') != LAYER]
            room['instanceCreationOrder'] = [item for item in room.get('instanceCreationOrder', [])
                                             if item.get('name') not in removed]
            edit.save(state['ref']['path'], room)
        for state in states:
            if state['name'] != start:
                continue
            room = state['room']
            layer = schemas.instance_layer(LAYER, -1000000000)
            layer['instances'].append(schemas.instance('inst_morx_runtime', bridge_ref))
            room.setdefault('layers', []).append(layer)
            room.setdefault('instanceCreationOrder', []).append(
                {'name': 'inst_morx_runtime', 'path': state['ref']['path']})
            edit.save(state['ref']['path'], room)
            break
        return start, True

    def bridge_key(self):
        """校验受管理桥接并返回标识；运行前用它确认启动房间实例存在。"""
        from .runtime_bridge import OBJECT
        with self.lock:
            edit = Edit(self)
            ref, obj, key, expected, files = self._bridge_object(edit)
            if obj.get('persistent') is not True:
                raise ValueError('测试桥接物体不是常驻物体，请重新 install_runtime_bridge 修复')
            events = {(event['eventType'], event['eventNum']): event for event in obj['eventList']}
            for event, code in expected.items():
                typ, number, stem = EVENTS[event]
                text, _ = files[f'objects/{OBJECT}/{stem}_{number}.gml']
                found = events.get((typ, number))
                if found is None or found.get('isDnD'):
                    raise ValueError('测试桥接事件缺失或为 GML Visual，请重新 install_runtime_bridge 修复')
                if text != code:
                    raise ValueError('测试桥接代码与此服务版本不一致，请重新 install_runtime_bridge 修复')
            if not self._bridge_room_ok(edit, ref):
                raise ValueError('当前启动房间缺少受管理的测试桥接实例，请重新 install_runtime_bridge 修复')
            return key

    def bridge_status(self):
        """只读预检：升级不会隐式修改游戏项目，客户端可先提示同步桥接。"""
        from .runtime_bridge import OBJECT
        with self.lock:
            installed = any(item['id']['name'] == OBJECT for item in self.metadata()['resources'])
            if not installed:
                return {'installed': False, 'compatible': False, 'action': 'install_runtime_bridge',
                        'message': '运行前需要安装测试桥接；仅编译不需要桥接'}
            try:
                self.bridge_key()
            except (ValueError, KeyError, TypeError, OSError, UnicodeError):
                return {'installed': True, 'compatible': False, 'action': 'install_runtime_bridge',
                        'message': '项目桥接需同步或修复；先预览 install_runtime_bridge，再显式执行，服务不会自动修改项目'}
            return {'installed': True, 'compatible': True, 'action': None}

    def install_runtime_bridge(self, dry_run=True):
        from uuid import uuid4
        from .runtime_bridge import OBJECT, sources
        with self.lock:
            edit = Edit(self)
            meta = edit.load(self.yyp.name)
            if any(r['id']['name'] == OBJECT for r in meta['resources']):
                ref, obj, key, expected, files = self._bridge_object(edit)
                repaired = False
                if obj.get('persistent') is not True:
                    obj['persistent'] = True
                    repaired = True
                events = {(event['eventType'], event['eventNum']): event for event in obj['eventList']}
                for event, code in expected.items():
                    typ, number, stem = EVENTS[event]
                    path = f'objects/{OBJECT}/{stem}_{number}.gml'
                    text, raw = files[path]
                    existing = events.get((typ, number))
                    if existing is not None and existing.get('isDnD'):
                        raise ValueError('测试桥接事件为 GML Visual，无法自动修复；请删除该物体后重试')
                    if existing is None:
                        obj['eventList'].append(schemas.typed('GMEvent', 'v1', **{'%Name': ''}, name='',
                            collisionObjectId=None, eventNum=number, eventType=typ, isDnD=False))
                        repaired = True
                    if text != code:
                        data = self._bridge_text(raw, code)
                        if raw is None:
                            edit.new(path, data)
                        else:
                            edit.changes[path] = data
                        repaired = True
                if repaired:
                    edit.save(ref['path'], obj)
                room, moved = self._sync_bridge_room(edit, ref)
                result = edit.commit(dry_run)
                return {**result, 'bridge': OBJECT, 'room': room,
                        'already_installed': True, 'repaired': repaired or moved}
            start, states = self._bridge_rooms(edit)
            if any(state['top'] or state['nested'] or state['stray'] for state in states):
                raise ValueError('房间已有同名桥接图层或实例，但桥接物体不存在；请手动清理')
            key = uuid4().hex
            obj = schemas.object_resource(OBJECT)
            obj['persistent'] = True
            for event, code in sources(key).items():
                typ, number, stem = EVENTS[event]
                obj['eventList'].append(schemas.typed('GMEvent', 'v1', **{'%Name': ''}, name='',
                    collisionObjectId=None, eventNum=number, eventType=typ, isDnD=False))
                edit.new(f'objects/{OBJECT}/{stem}_{number}.gml', code.encode('utf-8'))
            path = self._register(edit, 'objects', OBJECT, obj, '物体/system')
            bridge_ref = {'name': OBJECT, 'path': path}
            room, _ = self._sync_bridge_room(edit, bridge_ref)
            return {**edit.commit(dry_run), 'bridge': OBJECT, 'room': room,
                    'already_installed': False, 'repaired': False}

    def inspect_display_settings(self, room_name=None):
        with self.lock:
            edit = Edit(self)
            raw = edit.raw(WINDOWS_PATH)
            windows = {'path': WINDOWS_PATH, 'available': raw is not None}
            if raw is not None:
                windows['sha256'] = digest(raw)
                try:
                    windows.update(supported=True, settings=windows_settings(edit.load(WINDOWS_PATH)))
                except ValueError as exc:
                    windows.update(supported=False, reason=str(exc))
            meta = edit.load(self.yyp.name)
            order = meta.get('RoomOrderNodes', [])
            start_room = order[0]['roomId']['name'] if order else None
            room_name = start_room if room_name is None else room_name
            room = None
            if room_name is not None:
                ref, data = self._ref(edit, room_name, 'rooms')
                room = {'name': room_name, 'path': ref['path'], 'sha256': digest(edit.raw(ref['path'])),
                        'is_start_room': room_name == start_room}
                try:
                    room.update(supported=True, settings=room_settings(data))
                except ValueError as exc:
                    room.update(supported=False, reason=str(exc))
            return {'project': str(self.yyp), 'writable': self.writable, 'windows': windows,
                'start_room': start_room, 'room': room,
                'notes': ['初始窗口取决于首房间及其可见视口，操作系统或运行时代码可能调整结果',
                    '房间尺寸、相机范围、视口尺寸及 GUI 尺寸分别管理；本工具不注入运行时代码',
                    '继承房间仅报告文件内的本地记录值，不解析继承后的有效配置',
                    'borderless 仅去除窗口边框，不代表铺满显示器的无边框全屏']}

    def configure_windows_display(self, title=None, start_fullscreen=None, borderless=None, resizable=None,
            allow_fullscreen_switching=None, display_cursor=None, vsync=None, interpolate_pixels=None,
            expected_sha256=None, dry_run=True):
        values = {'start_fullscreen': start_fullscreen, 'borderless': borderless, 'resizable': resizable,
            'allow_fullscreen_switching': allow_fullscreen_switching, 'display_cursor': display_cursor,
            'vsync': vsync, 'interpolate_pixels': interpolate_pixels}
        if title is not None:
            title_check(title)
        for name, value in values.items():
            if value is not None:
                boolean(value, name)
        with self.lock:
            edit = Edit(self)
            data = edit.load(WINDOWS_PATH)
            check_expected(edit.raw(WINDOWS_PATH), expected_sha256)
            windows_settings(data)
            original = encode(data)
            if title is not None:
                data['option_windows_display_name'] = title
            for name, value in values.items():
                if value is not None:
                    data[WINDOWS_FLAGS[name]] = value
            if encode(data) != original:
                edit.save(WINDOWS_PATH, data)
            result = edit.commit(dry_run)
            result['warnings'] = ['无边框窗口下，允许手动调整窗口大小不会生效'] if (
                data[WINDOWS_FLAGS['borderless']] and data[WINDOWS_FLAGS['resizable']]) else []
            return result

    def configure_room_display(self, room_name, room_width=None, room_height=None, enable_views=None,
            view_index=0, visible=None, camera_x=None, camera_y=None, camera_width=None, camera_height=None,
            viewport_x=None, viewport_y=None, viewport_width=None, viewport_height=None,
            expected_sha256=None, dry_run=True):
        integer(view_index, 'view_index', 0, 7)
        values = {'camera_x': camera_x, 'camera_y': camera_y, 'camera_width': camera_width,
            'camera_height': camera_height, 'viewport_x': viewport_x, 'viewport_y': viewport_y,
            'viewport_width': viewport_width, 'viewport_height': viewport_height}
        for name, value in {'room_width': room_width, 'room_height': room_height, **values}.items():
            if value is not None:
                minimum = -(2**31) if name in ('camera_x', 'camera_y') else 0 if name in ('viewport_x', 'viewport_y') else 1
                maximum = 2**31 - 1 if name in ('camera_x', 'camera_y') else 32768
                integer(value, name, minimum, maximum)
        for name, value in (('enable_views', enable_views), ('visible', visible)):
            if value is not None:
                boolean(value, name)
        with self.lock:
            edit = Edit(self)
            ref, data = self._ref(edit, room_name, 'rooms')
            check_expected(edit.raw(ref['path']), expected_sha256)
            room_settings(data)
            view = data['views'][view_index]
            if (data.get('parentRoom') or data['roomSettings']['inheritRoomSettings']
                    or data['viewSettings']['inheritViewSettings'] or view['inherit']):
                raise ValueError('暂不修改继承房间、继承显示设置或选中的继承视口')
            original = encode(data)
            for field, value in (('Width', room_width), ('Height', room_height)):
                if value is not None:
                    data['roomSettings'][field] = value
            if enable_views is not None:
                data['viewSettings']['enableViews'] = enable_views
            if visible is not None:
                view['visible'] = visible
            for name, value in values.items():
                if value is not None:
                    view[VIEW_FIELDS[name]] = value
            room_settings(data)
            if data['viewSettings']['enableViews'] and not any(v['visible'] for v in data['views']):
                raise ValueError('启用视口后至少须有一个可见视口；请显式设置 visible=true')
            if encode(data) != original:
                edit.save(ref['path'], data)
            return edit.commit(dry_run)

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

    def remove_empty_group(self, group, expected_sha256, dry_run=True):
        group_check(group)
        path = 'folders/' + group + '.yy'
        with self.lock:
            edit = Edit(self)
            check_expected(edit.raw(self.yyp.name), expected_sha256)
            if expected_sha256 is None:
                raise ValueError('必须提供 inspect_project 返回的 project_sha256')
            meta = edit.load(self.yyp.name)
            folders = meta.get('Folders', [])
            if sum(f.get('folderPath') == path for f in folders) != 1:
                raise ValueError('分组不存在或不唯一')
            children = [f['folderPath'] for f in folders
                        if f.get('folderPath', '').startswith(path[:-3] + '/')]
            resources = [item['id']['name'] for item in meta['resources']
                         if (edit.load(item['id']['path']).get('parent') or {}).get('path') == path]
            order_rel = self.yyp.with_suffix('.resource_order').name
            order = edit.load(order_rel, {'FolderOrderSettings': [], 'ResourceOrderSettings': []})
            references = [item for item in order.get('FolderOrderSettings', []) if item.get('path') == path]
            info = {'group': group, 'direct_resources': resources, 'child_groups': children,
                    'sort_references': references, 'empty': not resources and not children}
            if resources or children:
                if dry_run:
                    return {'dry_run': True, 'changed': [], **info}
                raise ValueError('只允许删除无资源、无子组的空分组')
            meta['Folders'] = [f for f in folders if f.get('folderPath') != path]
            order['FolderOrderSettings'] = [v for v in order.get('FolderOrderSettings', []) if v.get('path') != path]
            edit.save(self.yyp.name, meta)
            if edit.raw(order_rel) is not None:
                edit.save(order_rel, order)
            return {**edit.commit(dry_run), **info}

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

    def _resource_files(self, relative):
        root = self.path(relative)
        if not root.is_dir():
            raise ValueError('资源目录不存在：' + relative)
        files = []
        for current, directories, names in os.walk(root, followlinks=False):
            for directory in directories:
                self.path(Path(current, directory).relative_to(self.root).as_posix())
            for filename in names:
                rel = Path(current, filename).relative_to(self.root).as_posix()
                self.path(rel)
                if not Path(current, filename).is_file():
                    raise ValueError('资源目录包含不支持的文件：' + rel)
                files.append(rel)
        return sorted(files)

    def _resource_references(self, edit, ref):
        references = []
        project = edit.load(self.yyp.name)
        for entry in project['resources']:
            other = entry['id']
            if other['path'] == ref['path']:
                continue
            data = edit.load(other['path'])
            for location in find_refs(data, ref['path']):
                references.append({'resource': other['name'], 'path': other['path'],
                                   'field': location})
        rest = {key: value for key, value in project.items()
                if key not in ('resources', 'RoomOrderNodes')}
        for location in find_refs(rest, ref['path']):
            references.append({'resource': self.yyp.name, 'path': self.yyp.name,
                               'field': location})
        return references

    def _check_expected(self, edit, ref, expected_sha256):
        if expected_sha256 is None:
            return
        if not isinstance(expected_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_sha256):
            raise ValueError('expected_sha256 必须是 64 位小写十六进制')
        if digest(edit.raw(ref['path'])) != expected_sha256:
            raise ValueError('资源描述已被修改或不存在，请重新 inspect_resource 并预览')

    def delete_resource(self, name, expected_sha256=None, dry_run=True):
        """删除未被引用的资源及其目录；只删除已注册资源，不处理空分组。"""
        with self.lock:
            edit = Edit(self)
            ref, data = self._ref(edit, name)
            from .runtime_bridge import OBJECT
            if name == OBJECT:
                raise ValueError('受管理的测试桥接不能通过资源工具删除')
            self._check_expected(edit, ref, expected_sha256)
            references = self._resource_references(edit, ref)
            if references:
                shown = '; '.join(f"{item['resource']} ({item['field']})"
                                  for item in references[:8])
                raise ValueError('资源仍被引用，不能删除：' + shown)
            project = edit.load(self.yyp.name)
            project['resources'] = [entry for entry in project['resources']
                                    if not (entry['id']['name'] == name and
                                            entry['id']['path'] == ref['path'])]
            warnings = ['未检查 GML 代码中的资源名；删除后请自行搜索并更新']
            if data['resourceType'] == 'GMRoom':
                nodes = project.get('RoomOrderNodes', [])
                remaining = [node for node in nodes if not (
                    node['roomId']['name'] == name and node['roomId']['path'] == ref['path'])]
                if not remaining:
                    raise ValueError('不能删除最后一个房间')
                if nodes and nodes[0]['roomId']['path'] == ref['path']:
                    warnings.append('删除的是启动房间，新的首房间将成为启动房间')
                project['RoomOrderNodes'] = remaining
            edit.save(self.yyp.name, project)
            order_rel = self.yyp.with_suffix('.resource_order').name
            order = edit.load(order_rel, {'FolderOrderSettings': [],
                                          'ResourceOrderSettings': []})
            order['ResourceOrderSettings'] = [
                item for item in order.get('ResourceOrderSettings', [])
                if not (item['name'] == name and item['path'] == ref['path'])]
            edit.save(order_rel, order)
            directory = PurePosixPath(ref['path']).parent.as_posix()
            files = self._resource_files(directory)
            for rel in files:
                edit.raw(rel)
                edit.changes[rel] = None
            result = edit.commit(dry_run)
            result.update({'name': name, 'resource_path': ref['path'],
                           'deleted_files': files, 'warnings': warnings})
            return result

    def rename_resource(self, name, new_name, expected_sha256=None, dry_run=True):
        """重命名资源，移动文件并更新资源引用；不修改 GML 代码中的名称。"""
        name_check(new_name)
        if name.casefold() == new_name.casefold():
            raise ValueError('新名称必须与旧名称不同；不支持仅更改大小写')
        with self.lock:
            edit = Edit(self)
            ref, data = self._ref(edit, name)
            from .runtime_bridge import OBJECT
            if name == OBJECT or new_name == OBJECT:
                raise ValueError('受管理的测试桥接不能通过资源工具重命名')
            self._check_expected(edit, ref, expected_sha256)
            project = edit.load(self.yyp.name)
            if any(entry['id']['name'].casefold() == new_name.casefold()
                   for entry in project['resources']):
                raise ValueError('同名资源已存在')
            kind = PurePosixPath(ref['path']).parts[0]
            old_dir = f'{kind}/{name}'
            new_dir = f'{kind}/{new_name}'
            new_path = f'{new_dir}/{new_name}.yy'
            target = self.path(new_dir)
            if target.exists() and (not target.is_dir() or any(target.iterdir())):
                raise ValueError('目标资源目录已存在且非空')
            collision_moves = []
            if data['resourceType'] == 'GMObject':
                for entry in project['resources']:
                    other = entry['id']
                    if other['path'] == ref['path'] or other['path'].split('/')[0] != 'objects':
                        continue
                    other_data = edit.load(other['path'])
                    if not any(event['eventType'] == 4 and
                               (event.get('collisionObjectId') or {}).get('path') == ref['path']
                               for event in other_data.get('eventList', [])):
                        continue
                    old_file = f'objects/{other["name"]}/Collision_{name}.gml'
                    new_file = f'objects/{other["name"]}/Collision_{new_name}.gml'
                    if edit.raw(new_file) is not None:
                        raise ValueError('目标碰撞事件文件已存在：' + new_file)
                    if edit.raw(old_file) is None:
                        raise ValueError('碰撞事件文件缺失：' + old_file)
                    collision_moves.append((old_file, new_file))
            moves = []
            for rel in self._resource_files(old_dir):
                within = rel[len(old_dir) + 1:]
                head, slash, tail = within.partition('/')
                stem, dot, extension = head.partition('.')
                if dot and stem == name:
                    head = new_name + '.' + extension
                new_rel = f'{new_dir}/{head}' + (f'/{tail}' if slash else '')
                moves.append((rel, new_rel))
            for old_rel, new_rel in moves + collision_moves:
                edit.new(new_rel, edit.raw(old_rel))
            for old_rel, _ in moves + collision_moves:
                edit.changes[old_rel] = None
            replaced = replace_refs(data, name, ref['path'], new_name, new_path)
            data['name'] = new_name
            if '%Name' in data:
                data['%Name'] = new_name
            if data.get('soundFile') in (name + '.wav', name + '.ogg'):
                data['soundFile'] = new_name + data['soundFile'][len(name):]
            edit.save(new_path, data)
            for entry in project['resources']:
                other = entry['id']
                if other['path'] == ref['path']:
                    continue
                other_data = edit.load(other['path'])
                changed = replace_refs(other_data, name, ref['path'], new_name, new_path)
                if changed:
                    edit.save(other['path'], other_data)
                    replaced += changed
            replaced += replace_refs(project, name, ref['path'], new_name, new_path)
            edit.save(self.yyp.name, project)
            order_rel = self.yyp.with_suffix('.resource_order').name
            order = edit.load(order_rel, {'FolderOrderSettings': [],
                                          'ResourceOrderSettings': []})
            for item in order.get('ResourceOrderSettings', []):
                if item.get('name') == name and item.get('path') == ref['path']:
                    item['name'], item['path'] = new_name, new_path
                    replaced += 1
            replaced += replace_refs(order, name, ref['path'], new_name, new_path)
            edit.save(order_rel, order)
            result = edit.commit(dry_run)
            result.update({'old_name': name, 'new_name': new_name,
                           'old_path': ref['path'], 'new_path': new_path,
                           'files': [{'from': old, 'to': new} for old, new in moves],
                           'collision_files': [{'from': old, 'to': new}
                                               for old, new in collision_moves],
                           'references_updated': replaced,
                           'warnings': ['GML 代码中的资源名不会自动更新']})
            return result

    def add_event(self, name, event, code, number=0, collision_object=None, dry_run=False):
        typ, num, stem, collision = event_spec(event, number, collision_object)
        with self.lock:
            edit = Edit(self)
            ref, obj = self._ref(edit, name, 'objects')
            target = None
            if typ == 4:
                if collision == name:
                    raise ValueError('Collision 事件不能以自身为目标')
                target = self._ref(edit, collision, 'objects')[0]
                gml = f'objects/{name}/Collision_{collision}.gml'
                duplicate = any(e['eventType'] == 4 and
                    (e.get('collisionObjectId') or {}).get('path') == target['path']
                    for e in obj['eventList'])
            else:
                gml = f'objects/{name}/{stem}_{num}.gml'
                duplicate = any(e['eventType'] == typ and e['eventNum'] == num
                                for e in obj['eventList'])
            if duplicate:
                raise ValueError('事件已存在；不会覆盖')
            obj['eventList'].append(schemas.typed('GMEvent', 'v1', **{'%Name': ''}, name='',
                collisionObjectId=target, eventNum=num, eventType=typ, isDnD=False))
            edit.new(gml, code.encode('utf-8'))
            edit.save(ref['path'], obj)
            return edit.commit(dry_run)

    def remove_event(self, name, event, number=0, collision_object=None, dry_run=True):
        """删除物体事件及其 GML 文件；不删除物体，不修改其他事件。"""
        typ, num, stem, collision = event_spec(event, number, collision_object)
        with self.lock:
            edit = Edit(self)
            ref, obj = self._ref(edit, name, 'objects')
            if typ == 4:
                target = self._ref(edit, collision, 'objects')[0]
                matches = [item for item in obj['eventList'] if item['eventType'] == 4
                           and (item.get('collisionObjectId') or {}).get('path') == target['path']]
                path = f'objects/{name}/Collision_{collision}.gml'
            else:
                matches = [item for item in obj['eventList']
                           if item['eventType'] == typ and item['eventNum'] == num]
                path = f'objects/{name}/{stem}_{num}.gml'
            if not matches:
                raise ValueError('事件不存在')
            if len(matches) != 1:
                raise ValueError('事件不唯一，拒绝删除')
            if matches[0].get('isDnD'):
                raise ValueError('不删除 GML Visual 事件')
            if edit.raw(path) is None:
                raise ValueError('事件文件缺失：' + path)
            obj['eventList'].remove(matches[0])
            edit.save(ref['path'], obj)
            edit.changes[path] = None
            result = edit.commit(dry_run)
            result.update({'object_name': name, 'event': event, 'number': number,
                           'collision_object': collision, 'deleted_file': path})
            return result

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
            layers[0]['instances'].append(schemas.instance(instance, obj_ref, x, y))
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

    def _sprite_animation(self, edit, name, expected_sha256=None):
        ref, data = self._ref(edit, name, 'sprites')
        if expected_sha256 is not None:
            if not isinstance(expected_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_sha256):
                raise ValueError('expected_sha256 必须是 inspect_sprite 返回的 SHA-256')
            if digest(edit.raw(ref['path'])) != expected_sha256:
                raise ValueError('精灵描述已被修改，请重新 inspect_sprite 并预览')
        sequence, track, keys, frames = timeline(data, ref['path'])
        base = ref['path'].rsplit('/', 1)[0]
        for frame in frames:
            paths = [f'{base}/{frame}.png'] + [
                f"{base}/layers/{frame}/{layer['name']}.png" for layer in data['layers']]
            if any(not self.path(path).is_file() for path in paths):
                raise ValueError('精灵缺少帧图像，请先修复资源：' + frame)
        return ref, data, sequence, track, keys, frames

    def inspect_sprite(self, name):
        with self.lock:
            edit = Edit(self)
            ref, data, sequence, track, keys, frames = self._sprite_animation(edit, name)
            image_indices = {frame['name']: index for index, frame in enumerate(data['frames'])}
            return {'name': name, 'path': ref['path'], 'sha256': digest(edit.raw(ref['path'])),
                'image_sha256': self._sprite_image_hash(edit, name, data),
                'width': data['width'], 'height': data['height'],
                'origin': {'mode': data['origin'], 'x': sequence['xorigin'], 'y': sequence['yorigin']},
                'speed': sequence['playbackSpeed'],
                'speed_type': ('frames_per_second', 'frames_per_game_frame')[sequence['playbackSpeedType']],
                'duration_unit': 'animation_frames', 'length': sequence['length'],
                'geometry': geometry(data),
                'frames': [{'frame_id': key['Channels']['0']['Id']['name'], 'keyframe_id': key['id'],
                    'image_index': image_indices[key['Channels']['0']['Id']['name']],
                    'start': key['Key'], 'duration': key['Length']} for key in keys]}

    def _sprite_image_hash(self, edit, name, data):
        hashes = {}
        for frame in data['frames']:
            stem = f"sprites/{name}/{frame['name']}"
            paths = [stem + '.png'] + [f"sprites/{name}/layers/{frame['name']}/{layer['name']}.png"
                                      for layer in data['layers']]
            for path in paths:
                hashes[path] = digest(edit.raw(path))
        return digest(encode(hashes))

    def replace_sprite_images(self, name, png_paths, expected_sha256, expected_image_sha256, dry_run=True,
                              allow_resize=False, origin_policy='keep', bbox_policy='keep'):
        boolean(allow_resize, 'allow_resize')
        if not isinstance(origin_policy, str) or origin_policy not in {'keep', 'scale'}:
            raise ValueError('origin_policy 须为 keep 或 scale')
        if not isinstance(bbox_policy, str) or bbox_policy not in {'keep', 'scale', 'full_image'}:
            raise ValueError('bbox_policy 须为 keep、scale 或 full_image')
        with self.lock:
            edit = Edit(self)
            ref, data, sequence, track, keys, frames = self._sprite_animation(edit, name, expected_sha256)
            if not expected_sha256 or not expected_image_sha256 or self._sprite_image_hash(edit, name, data) != expected_image_sha256:
                raise ValueError('必须提供最新 inspect_sprite 的描述及图像哈希；图像可能已变化')
            if len(data['layers']) != 1:
                raise ValueError('仅支持单图像图层的普通位图精灵')
            if type(data.get('bboxMode')) is not int or data['bboxMode'] not in (0, 1, 2):
                raise ValueError('精灵碰撞框模式不受支持')
            if not isinstance(png_paths, list) or len(png_paths) != len(keys):
                raise ValueError('替换 PNG 数量必须与现有动画帧数一致，按动画顺序提供')
            size, alpha_bounds = None, None
            before_geometry = geometry(data)
            for key, source in zip(keys, png_paths):
                path = Path(source).resolve(strict=True)
                if path.suffix.lower() != '.png' or path.stat().st_size > 64 * 1024 * 1024:
                    raise ValueError('仅支持每张不超过 64 MiB 的 PNG')
                raw = path.read_bytes()
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format != 'PNG' or getattr(image, 'n_frames', 1) != 1:
                        raise ValueError('PNG 必须为单帧')
                    if max(image.size) > 8192 or (size and size != image.size):
                        raise ValueError('所有替换帧须等尺寸且单边不超过 8192')
                    size = image.size
                    if not allow_resize and size != (data['width'], data['height']):
                        raise ValueError('PNG 尺寸与现有精灵不同；需显式 allow_resize=true')
                    image.verify()
                if data.get('bboxMode') == 0:
                    integer(data.get('collisionTolerance'), 'collisionTolerance', 0, 255)
                    with Image.open(io.BytesIO(raw)) as image:
                        bounds = image.convert('RGBA').getchannel('A').point(
                            [255 if v > data['collisionTolerance'] else 0 for v in range(256)]).getbbox()
                    if bounds is not None:
                        alpha_bounds = bounds if alpha_bounds is None else (
                            min(bounds[0], alpha_bounds[0]), min(bounds[1], alpha_bounds[1]),
                            max(bounds[2], alpha_bounds[2]), max(bounds[3], alpha_bounds[3]))
                frame = key['Channels']['0']['Id']['name']
                layer = data['layers'][0]['name']
                for target in (f'sprites/{name}/{frame}.png', f'sprites/{name}/layers/{frame}/{layer}.png'):
                    edit.changes[target] = raw
            resized = size != (data['width'], data['height'])
            if resized:
                dependents = []
                for entry in edit.load(self.yyp.name)['resources']:
                    candidate = entry['id']
                    if candidate['path'].startswith('tilesets/'):
                        owner = edit.load(candidate['path'])
                        sprite = owner.get('spriteId') or {}
                        if sprite.get('path') == ref['path'] or sprite.get('name') == name:
                            dependents.append(candidate['name'])
                if dependents:
                    raise ValueError('精灵被瓦片集引用，不能改变尺寸：' + ', '.join(dependents))
            dimensions = {'before': before_geometry, 'after': geometry(data)}
            if resized:
                dimensions = resize_geometry(data, *size, origin_policy, bbox_policy, alpha_bounds)
            elif data.get('bboxMode') == 0:
                automatic_bounds(data, alpha_bounds)
                dimensions['after'] = geometry(data)
            if geometry(data) != before_geometry:
                edit.save(ref['path'], data)
            result = edit.commit(dry_run)
            result.update(resized=resized, geometry=dimensions, compiled=False)
            return result

    def configure_sprite(self, name, speed=None, speed_type=None, origin_x=None, origin_y=None,
                         expected_sha256=None, dry_run=True):
        if speed is not None:
            number(speed, '动画速度')
        speed_types = {'frames_per_second': 0, 'frames_per_game_frame': 1}
        if speed_type is not None and (not isinstance(speed_type, str) or speed_type not in speed_types):
            raise ValueError('speed_type 必须为 frames_per_second 或 frames_per_game_frame')
        for value in (origin_x, origin_y):
            if value is not None and (type(value) is not int or not -(2**31) <= value < 2**31):
                raise ValueError('原点坐标必须为 32 位整数，允许位于图像外')
        with self.lock:
            edit = Edit(self)
            ref, data, sequence, track, keys, frames = self._sprite_animation(edit, name, expected_sha256)
            original = encode(data)
            if speed is not None:
                sequence['playbackSpeed'] = float(speed)
            if speed_type is not None:
                sequence['playbackSpeedType'] = speed_types[speed_type]
            for field, value in (('xorigin', origin_x), ('yorigin', origin_y)):
                if value is not None:
                    sequence[field] = value
                    data['origin'] = 9  # 自定义原点；不修改另一坐标或碰撞框。
            if encode(data) != original:
                edit.save(ref['path'], data)
            return edit.commit(dry_run)

    def set_sprite_animation(self, name, frame_order=None, frame_durations=None,
                             expected_sha256=None, dry_run=True):
        with self.lock:
            edit = Edit(self)
            ref, data, sequence, track, keys, frames = self._sprite_animation(edit, name, expected_sha256)
            original = encode(data)
            if frame_order is not None or frame_durations is not None:
                order = retime(sequence, track, keys, frame_order, frame_durations)
                data['frames'] = [frames[frame] for frame in order]
            if encode(data) != original:
                edit.save(ref['path'], data)
            return edit.commit(dry_run)

    @staticmethod
    def _ogg_info(raw):
        """读取 Ogg Vorbis 头、声道、采样率和末页 granule 时长；不依赖外部解码器。"""
        if len(raw) < 28 or raw[:4] != b'OggS':
            raise ValueError('不是有效的 Ogg 文件')
        pos, page, last_granule, eos, first_packet = 0, 0, 0, False, None
        while pos < len(raw):
            if pos + 27 > len(raw) or raw[pos:pos + 4] != b'OggS':
                raise ValueError('Ogg 页头损坏')
            header_type = raw[pos + 5]
            granule = int.from_bytes(raw[pos + 6:pos + 14], 'little')
            count = raw[pos + 26]
            table_end = pos + 27 + count
            if table_end > len(raw):
                raise ValueError('Ogg 页段表截断')
            segments = raw[pos + 27:table_end]
            body_len = sum(segments)
            body_start = table_end
            body_end = body_start + body_len
            if body_end > len(raw):
                raise ValueError('Ogg 页数据截断')
            if page == 0:
                if not header_type & 0x02:
                    raise ValueError('Ogg 首页缺少 BOS 标志')
                packet_len = 0
                for segment in segments:
                    packet_len += segment
                    if segment < 255:
                        break
                if not 0 < packet_len <= body_len:
                    raise ValueError('Ogg 标识头截断')
                first_packet = raw[body_start:body_start + packet_len]
            if granule != 0xFFFFFFFFFFFFFFFF:
                last_granule = granule
            if header_type & 0x04:
                eos = True
            pos, page = body_end, page + 1
        if pos != len(raw):
            raise ValueError('Ogg 文件包含页外数据')
        if first_packet is None or len(first_packet) < 30 or first_packet[:7] != b'\x01vorbis':
            raise ValueError('不是 Ogg Vorbis 音频')
        if int.from_bytes(first_packet[7:11], 'little') != 0:
            raise ValueError('不支持的 Vorbis 版本')
        channels = first_packet[11]
        rate = int.from_bytes(first_packet[12:16], 'little')
        nominal = int.from_bytes(first_packet[20:24], 'little', signed=True)
        if channels not in (1, 2):
            raise ValueError('OGG 必须为单声道或双声道')
        if not 8000 <= rate <= 192000:
            raise ValueError('OGG 采样率须为 8000～192000')
        if last_granule <= 0:
            raise ValueError('OGG 缺少有效音频时长')
        duration = last_granule / rate
        if not math.isfinite(duration) or duration <= 0 or duration > 24 * 3600:
            raise ValueError('OGG 时长超出允许范围')
        bit_rate = round(nominal / 1000) if nominal > 0 else round(len(raw) * 8 / duration / 1000)
        return {'sample_rate': rate, 'channels': channels, 'duration': duration,
                'bit_rate': max(1, min(512, bit_rate)), 'eos': eos}

    def _sound(self, edit, name):
        ref, data = self._ref(edit, name, 'sounds')
        audio.settings(data)
        return ref, data

    def _sound_group(self, edit, data):
        group = data['audioGroupId']['name']
        name_check(group)
        groups = edit.load(self.yyp.name).get('AudioGroups', [])
        if sum(item.get('name') == group for item in groups) != 1:
            raise ValueError('音频组未唯一存在：' + group)
        if data['compression'] == 3 and group != 'audiogroup_default':
            raise ValueError('streamed 只接受 audiogroup_default；请显式设置 audio_group，不自动更改分组')

    def _sound_summary(self, edit, ref, data):
        filename = data.get('soundFile')
        if (not isinstance(filename, str) or not filename or len(filename) > 255
                or filename in ('.', '..') or re.search(r'[<>:"/\\|?*\x00-\x1f]', filename)):
            raise ValueError('声音源文件名不受支持')
        relative = ref['path'].rsplit('/', 1)[0] + '/' + filename
        source = self.path(relative)
        if source.is_file() and source.stat().st_size > 128 * 1024 * 1024:
            raise ValueError('声音源文件超过 128 MiB')
        raw = edit.raw(relative)
        return {'name': data['name'], 'path': ref['path'], 'sha256': digest(edit.raw(ref['path'])),
                'settings': audio.settings(data), 'duration': data.get('duration'),
                'source_file': {'path': relative, 'exists': raw is not None,
                                'sha256': digest(raw), 'bytes': len(raw) if raw is not None else None,
                                'format': Path(filename).suffix.lower().lstrip('.')},
                'configuration_overrides': bool(data.get('ConfigValues')),
                'notes': audio.notes(data)}

    def inspect_sound(self, name):
        with self.lock:
            edit = Edit(self)
            ref, data = self._sound(edit, name)
            result = self._sound_summary(edit, ref, data)
            result['audio_groups'] = [item['name'] for item in edit.load(self.yyp.name).get('AudioGroups', [])]
            return result

    def inspect_audio_groups(self):
        with self.lock:
            edit = Edit(self)
            meta = edit.load(self.yyp.name)
            rows = meta.get('AudioGroups', [])
            names = [r.get('name') for r in rows]
            if (any(not isinstance(n, str) or not n for n in names)
                    or len({n.casefold() for n in names}) != len(names)
                    or names.count('audiogroup_default') != 1):
                raise ValueError('音频组缺少默认组或存在重复名称')
            members = {n: [] for n in names}
            for entry in meta.get('resources', []):
                ref = entry['id']
                if not ref['path'].startswith('sounds/'):
                    continue
                data = edit.load(ref['path'])
                settings = audio.settings(data)
                if settings['audio_group'] not in members:
                    raise ValueError('声音音频组引用无效：' + ref['name'])
                members[settings['audio_group']].append({'name': ref['name'], 'path': ref['path'],
                    'sha256': digest(edit.raw(ref['path'])), 'streamed': settings['streamed'],
                    'configuration_overrides': bool(data.get('ConfigValues'))})
            return {'project_sha256': digest(edit.raw(self.yyp.name)),
                    'audio_groups': [{'name': r['name'], 'definition': r, 'members': members[r['name']]}
                                     for r in rows]}

    def create_audio_group(self, name, expected_project_sha256, dry_run=True):
        name_check(name)
        with self.lock:
            edit = Edit(self)
            if (not isinstance(expected_project_sha256, str)
                    or not re.fullmatch('[a-f0-9]{64}', expected_project_sha256)
                    or digest(edit.raw(self.yyp.name)) != expected_project_sha256):
                raise ValueError('项目哈希无效或过期，请重新 inspect_audio_groups')
            info = self.inspect_audio_groups()
            if any(g['name'].casefold() == name.casefold() for g in info['audio_groups']):
                raise ValueError('音频组已存在，不覆盖')
            meta = edit.load(self.yyp.name)
            if meta.get('ConfigValues'):
                raise ValueError('项目含配置覆盖，暂不创建音频组')
            if any(r['id']['name'].casefold() == name.casefold() for r in meta.get('resources', [])):
                raise ValueError('音频组名称与已有资源冲突')
            meta['AudioGroups'].append({'$GMAudioGroup': 'v1', '%Name': name, 'name': name,
                'exportDir': '', 'resourceType': 'GMAudioGroup', 'resourceVersion': '2.0', 'targets': -1})
            edit.save(self.yyp.name, meta)
            return {**edit.commit(dry_run), 'name': name,
                    'notes': [audio.PRELOAD_NOTE, '用 configure_sound 分配已有声音；流式声音仍限默认组']}

    def configure_sound(self, name, expected_sha256, volume=None, compression=None, preload=None,
                        compression_quality=None, conversion_mode=None, audio_group=None,
                        sample_rate=None, channel_format=None, bit_depth=None, dry_run=True):
        changes = audio.updates(volume, compression, preload, compression_quality,
                                conversion_mode, sample_rate, channel_format, bit_depth)
        if not isinstance(expected_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_sha256):
            raise ValueError('必须提供 inspect_sound 返回的 expected_sha256')
        if audio_group is not None:
            name_check(audio_group)
            changes['audioGroupId'] = {'name': audio_group, 'path': 'audiogroups/' + audio_group}
        with self.lock:
            edit = Edit(self)
            ref, data = self._sound(edit, name)
            if digest(edit.raw(ref['path'])) != expected_sha256:
                raise ValueError('声音描述已被修改，请重新 inspect_sound 并预览')
            if data.get('ConfigValues'):
                raise ValueError('声音含配置覆盖，暂不修改；请先在 IDE 中确认各配置')
            before = audio.settings(data)
            original = encode(data)
            data.update(changes)
            self._sound_group(edit, data)
            summary = self._sound_summary(edit, ref, data)
            if not summary['source_file']['exists']:
                raise ValueError('声音缺少源文件，请先修复资源')
            if encode(data) != original:
                edit.save(ref['path'], data)
            result = edit.commit(dry_run)
            result.update(before=before, settings=summary['settings'], notes=summary['notes'], compiled=False)
            return result

    def import_sound(self, name, audio_path, group='声音', volume=1.0, dry_run=False,
                     compression='uncompressed', preload=False, compression_quality=4,
                     conversion_mode='automatic', audio_group='audiogroup_default',
                     sample_rate=None, channel_format=None, bit_depth=16):
        name_check(name)
        name_check(audio_group)
        options = audio.updates(volume, compression, preload, compression_quality,
                                conversion_mode, sample_rate, channel_format, bit_depth)
        path = Path(audio_path).resolve(strict=True)
        suffix = path.suffix.lower()
        if suffix not in ('.wav', '.ogg') or path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError('支持不超过 128 MiB 的 PCM WAV 或 OGG Vorbis')
        raw = path.read_bytes()
        if suffix == '.wav':
            with wave.open(io.BytesIO(raw)) as sound:
                rate, frames = sound.getframerate(), sound.getnframes()
                channels, width = sound.getnchannels(), sound.getsampwidth()
                if channels not in (1, 2) or width not in (1, 2, 3) or not frames:
                    raise ValueError('WAV 必须为非空单声道或双声道 8／16／24 位 PCM')
                if len(sound.readframes(frames)) != frames * channels * width:
                    raise ValueError('WAV 数据截断')
            info = {'sample_rate': rate, 'channels': channels,
                    'duration': frames / rate, 'bit_rate': 128}
        else:
            info = self._ogg_info(raw)
        # 与 GameMaker 2026 原生 GMSound v2 的字段顺序一致；不要写已移除的 bitRate/type。
        data = {
            '$GMSound': 'v2',
            '%Name': name,
            'audioGroupId': {'name': audio_group, 'path': 'audiogroups/' + audio_group},
            'bitDepth': 1,
            'channelFormat': info['channels'] - 1,
            'compression': 0,
            'compressionQuality': 4,
            'conversionMode': 0,
            'duration': info['duration'],
            'exportDir': '',
            'name': name,
            'parent': None,
            'preload': False,
            'resourceType': 'GMSound',
            'resourceVersion': '2.0',
            'sampleRate': info['sample_rate'],
            'soundFile': name + suffix,
            'volume': volume,
        }
        data.update(options)
        audio.settings(data)
        with self.lock:
            edit = Edit(self)
            self._sound_group(edit, data)
            self._register(edit, 'sounds', name, data, group)
            edit.new(f'sounds/{name}/{name}{suffix}', raw)
            result = edit.commit(dry_run)
            result.update({'format': suffix[1:], 'sample_rate': info['sample_rate'],
                           'channels': info['channels'], 'duration': info['duration'],
                           'sha256': digest(raw), 'settings': audio.settings(data),
                           'notes': audio.notes(data), 'compiled': False})
            return result

    def import_font(self, name, font_path, dry_run=False):
        """把字体文件作为 IncludedFile 放入 datafiles/fonts；不生成烘焙 GMFont。"""
        name_check(name)
        path = Path(font_path).resolve(strict=True)
        suffix = path.suffix.lower()
        if suffix not in ('.ttf', '.tte', '.otf', '.ttc') or path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('支持不超过 64 MiB 的 TTF/TTE/OTF/TTC 字体')
        raw = path.read_bytes()
        signature = raw[:4]
        if signature == b'ttcf':
            if len(raw) < 16 or int.from_bytes(raw[8:12], 'big') < 1:
                raise ValueError('TTC 字体头损坏')
        elif signature not in (b'\x00\x01\x00\x00', b'OTTO', b'true', b'typ1'):
            raise ValueError('字体文件签名不受支持')
        file_name = name + suffix
        relative = f'datafiles/fonts/{file_name}'
        with self.lock:
            edit = Edit(self)
            project = edit.load(self.yyp.name)
            included = project.setdefault('IncludedFiles', [])
            if any(item.get('name', '').casefold() == file_name.casefold()
                   for item in included):
                raise ValueError('字体文件已存在；不会覆盖')
            if self.path(relative).exists():
                raise ValueError('目标字体文件已存在；不会覆盖')
            included.append({'$GMIncludedFile': '', '%Name': file_name, 'CopyToMask': -1,
                             'filePath': 'datafiles/fonts', 'name': file_name,
                             'resourceType': 'GMIncludedFile', 'resourceVersion': '2.0'})
            included.sort(key=lambda item: (item.get('filePath', ''), item.get('name', '')))
            edit.save(self.yyp.name, project)
            edit.new(relative, raw)
            result = edit.commit(dry_run)
            runtime_path = 'fonts/' + file_name
            result.update({'file_name': file_name, 'project_path': relative,
                           'runtime_path': runtime_path, 'sha256': digest(raw),
                           'usage': f'font_add("{runtime_path}", 16, 0, 0, 0, 65535)'})
            return result

    def create_shader(self, name, vertex_code, fragment_code, group='着色器', dry_run=False):
        with self.lock:
            edit = Edit(self)
            data = schemas.typed('GMShader', **{'%Name': name}, name=name, type=1)
            self._register(edit, 'shaders', name, data, group)
            edit.new(f'shaders/{name}/{name}.vsh', vertex_code.encode('utf-8'))
            edit.new(f'shaders/{name}/{name}.fsh', fragment_code.encode('utf-8'))
            return edit.commit(dry_run)

    def create_tileset(self, name, sprite, tile_width, tile_height, group='瓦片集', dry_run=False):
        name_check(name)
        integer(tile_width, 'tile_width', 1, 8192)
        integer(tile_height, 'tile_height', 1, 8192)
        with self.lock:
            edit = Edit(self)
            sprite_ref, sprite_data = self._ref(edit, sprite, 'sprites')
            if len(sprite_data.get('frames', [])) != 1:
                raise ValueError('瓦片集只支持单帧精灵')
            width, height = sprite_data.get('width'), sprite_data.get('height')
            if type(width) is not int or type(height) is not int:
                raise ValueError('精灵尺寸无效')
            if width % tile_width or height % tile_height:
                raise ValueError('精灵宽高须能被瓦片宽高整除')
            count = (width // tile_width) * (height // tile_height)
            if count < 1 or count > 262144:
                raise ValueError('瓦片数量须为 1～262144')
            data = schemas.tileset(name, sprite_ref, width, height, tile_width, tile_height)
            self._register(edit, 'tilesets', name, data, group)
            return edit.commit(dry_run)

    def inspect_tileset(self, name):
        with self.lock:
            ref, data = self._ref(Edit(self), name, 'tilesets')
            return {'name': name, 'path': ref['path'],
                    'sha256': digest(self.path(ref['path']).read_bytes()),
                    'sprite': data.get('spriteId'), 'tile_width': data.get('tileWidth'),
                    'tile_height': data.get('tileHeight'), 'columns': data.get('out_columns'),
                    'tile_count': data.get('tile_count'),
                    'sprite_no_export': data.get('spriteNoExport')}

    def create_tile_layer(self, room, layer, tileset, width=None, height=None,
                          x=0, y=0, depth=0, dry_run=False):
        name_check(layer)
        integer(x, 'x', 0, 32768)
        integer(y, 'y', 0, 32768)
        if type(depth) is not int:
            raise ValueError('图层 depth 必须为整数')
        with self.lock:
            edit = Edit(self)
            ref, data = self._ref(edit, room, 'rooms')
            tileset_ref, tileset_data = self._ref(edit, tileset, 'tilesets')
            if any(item['name'].casefold() == layer.casefold() for item in walk_layers(data['layers'])):
                raise ValueError('房间已存在同名图层')
            tile_width, tile_height = tileset_data.get('tileWidth'), tileset_data.get('tileHeight')
            if type(tile_width) is not int or type(tile_height) is not int:
                raise ValueError('瓦片集的瓦片尺寸无效')
            settings = data.get('roomSettings') or {}
            if width is None:
                if type(settings.get('Width')) is not int:
                    raise ValueError('房间缺少尺寸，须显式提供 width')
                width = (settings['Width'] + tile_width - 1) // tile_width
            if height is None:
                if type(settings.get('Height')) is not int:
                    raise ValueError('房间缺少尺寸，须显式提供 height')
                height = (settings['Height'] + tile_height - 1) // tile_height
            integer(width, 'width', 1, 1024)
            integer(height, 'height', 1, 1024)
            if width * height > 262144:
                raise ValueError('瓦片图层超过 262144 格')
            data['layers'].append(schemas.tile_layer(layer, tileset_ref, width, height, x, y, depth))
            edit.save(ref['path'], data)
            return edit.commit(dry_run)

    def inspect_tilemap(self, room, layer=None, x=0, y=0, width=None, height=None,
                        include_cells=False):
        integer(x, 'x', 0, 32768)
        integer(y, 'y', 0, 32768)
        with self.lock:
            ref, data = self._ref(Edit(self), room, 'rooms')
            layers = [item for item in walk_layers(data['layers'])
                      if item.get('resourceType') == 'GMRTileLayer' and
                      (layer is None or item['name'] == layer)]
            if len(layers) != 1:
                raise ValueError('须指定唯一的瓦片图层')
            target = layers[0]
            tiles = target.get('tiles') or {}
            map_width, map_height, values = tile_storage.read(tiles)
            width = map_width if width is None else width
            height = map_height if height is None else height
            integer(width, 'width', 1, 1024)
            integer(height, 'height', 1, 1024)
            if x + width > map_width or y + height > map_height:
                raise ValueError('查询区域超出瓦片图层')
            if width * height > 10000:
                raise ValueError('查询区域超过 10000 格')
            result = {'name': target['name'], 'tileset': target.get('tilesetId'),
                      'x': target.get('x', 0), 'y': target.get('y', 0),
                      'width': map_width, 'height': map_height, 'depth': target.get('depth', 0),
                      'non_empty': sum(1 for value in values if not tile_storage.empty(value)),
                      'room_sha256': digest(self.path(ref['path']).read_bytes())}
            if include_cells:
                cells = []
                for row in range(y, y + height):
                    for column in range(x, x + width):
                        value = values[row * map_width + column]
                        if tile_storage.empty(value):
                            continue
                        cells.append({'x': column, 'y': row, 'tile': value & tile_storage.INDEX_MASK,
                                      'flip_x': bool(value & schemas.TILE_FLIP_H),
                                      'flip_y': bool(value & schemas.TILE_FLIP_V),
                                      'rotate': bool(value & schemas.TILE_ROTATE)})
                result['cells'] = cells
            return result

    def set_tilemap_cells(self, room, layer, cells, expected_sha256=None, dry_run=False):
        if not isinstance(cells, list) or not 1 <= len(cells) <= 10000:
            raise ValueError('cells 须包含 1～10000 个格子')
        with self.lock:
            edit = Edit(self)
            ref, data = self._ref(edit, room, 'rooms')
            raw = edit.raw(ref['path'])
            if expected_sha256 is not None:
                if not isinstance(expected_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_sha256) \
                        or digest(raw) != expected_sha256:
                    raise ValueError('房间已被修改或哈希无效；请重新 inspect_tilemap')
            layers = [item for item in walk_layers(data['layers'])
                      if item.get('resourceType') == 'GMRTileLayer' and item['name'] == layer]
            if len(layers) != 1:
                raise ValueError('须指定唯一的瓦片图层')
            target = layers[0]
            tiles = target.get('tiles') or {}
            map_width, map_height, values = tile_storage.read(tiles)
            tileset_ref = target.get('tilesetId') or {}
            if not isinstance(tileset_ref, dict) or not tileset_ref.get('name'):
                raise ValueError('瓦片图层缺少瓦片集引用')
            _, tileset_data = self._ref(edit, tileset_ref.get('name'), 'tilesets')
            count = tileset_data.get('tile_count')
            if type(count) is not int:
                raise ValueError('瓦片集数量无效')
            updated = 0
            for cell in cells:
                if not isinstance(cell, dict):
                    raise ValueError('每个格子须为对象')
                extra = set(cell) - {'x', 'y', 'tile', 'flip_x', 'flip_y', 'rotate'}
                if extra:
                    raise ValueError('格子包含未知参数：' + ', '.join(sorted(extra)))
                column, row, tile = cell.get('x'), cell.get('y'), cell.get('tile')
                if type(column) is not int or type(row) is not int:
                    raise ValueError('格子坐标必须为整数')
                if not 0 <= column < map_width or not 0 <= row < map_height:
                    raise ValueError('格子坐标超出瓦片图层')
                if type(tile) is not int or not 0 <= tile <= count:
                    raise ValueError('瓦片索引须为 0～tile_count')
                flags = 0
                for key, flag in (('flip_x', schemas.TILE_FLIP_H),
                                  ('flip_y', schemas.TILE_FLIP_V),
                                  ('rotate', schemas.TILE_ROTATE)):
                    if key in cell:
                        if type(cell[key]) is not bool:
                            raise ValueError(key + ' 必须为布尔值')
                        if cell[key]:
                            flags |= flag
                value = schemas.EMPTY_TILE if tile == 0 else tile | flags
                index = row * map_width + column
                if values[index] != value:
                    updated += 1
                values[index] = value
            tile_storage.write(tiles, values)
            edit.save(ref['path'], data)
            result = edit.commit(dry_run)
            result['updated'] = updated
            return result

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
            return {**validate(self), 'project_sha256': digest(self.yyp.read_bytes())}
