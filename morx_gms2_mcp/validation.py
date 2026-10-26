"""只读校验注册资源及已支持的引用，不扫描参考目录。"""
from pathlib import PurePosixPath
from .resources import KINDS, EVENT_STEMS, group_check, walk_layers
from .animation import number
from .display import WINDOWS_PATH, windows_settings, room_settings
from . import tiles as tile_storage


def validate(project):
    errors, warnings, assets, documents = [], [], [], {}
    try:
        meta = project.metadata()
        if project.path(WINDOWS_PATH).is_file():
            try:
                windows_settings(project.read(WINDOWS_PATH))
            except (ValueError, KeyError, TypeError, OSError) as exc:
                errors.append(f'{WINDOWS_PATH}: {exc}')
        entries = meta['resources']
        folders = {f['folderPath']: f for f in meta.get('Folders', [])}
        if len(folders) != len(meta.get('Folders', [])):
            errors.append('存在重复分组')
        for path, folder in folders.items():
            if not path.startswith('folders/') or not path.endswith('.yy'):
                errors.append('无效虚拟分组路径：' + path)
                continue
            group_check(path[8:-3])
            parent = path.rsplit('/', 1)[0]
            if parent != 'folders' and parent + '.yy' not in folders:
                errors.append('分组缺少父组：' + path)
            if folder['name'] != path.rsplit('/', 1)[-1][:-3]:
                errors.append('分组名称不一致：' + path)
        refs, names = {}, set()
        for entry in entries:
            ref = entry['id']
            name, path = ref['name'], ref['path']
            if name.casefold() in names or path.casefold() in {p.casefold() for p in refs}:
                errors.append('重复资源：' + name)
            names.add(name.casefold())
            refs[path] = name
            parts = PurePosixPath(path).parts
            if len(parts) != 3 or parts[1] != name or parts[2] != name + '.yy':
                errors.append('资源注册路径不符合原生结构：' + path)
                continue
            try:
                data = project.read(path)
                documents[path] = data
                kind = data['resourceType']
                if data['name'] != name or data.get('%Name', name) != name:
                    errors.append('资源名称不一致：' + path)
                if parts[0] in KINDS and KINDS[parts[0]] != kind:
                    errors.append('资源类型和目录不一致：' + path)
                parent = data.get('parent')
                if parent and parent['path'] != project.yyp.name:
                    if parent['path'] not in folders or folders[parent['path']]['name'] != parent['name']:
                        errors.append('资源分组引用无效：' + path)
                assets.append({'name': name, 'path': path, 'type': kind, 'group': parent})
            except (ValueError, KeyError, TypeError, OSError) as exc:
                errors.append(f'{path}: {exc}')

        def require_file(path):
            if not project.path(path).is_file():
                errors.append('缺少资源文件：' + path)

        def check_ref(ref, kind, owner):
            if ref is None:
                return
            data = documents.get(ref['path'])
            if data is None or data.get('name') != ref['name'] or data.get('resourceType') != kind:
                errors.append('资源引用无效：' + owner + ' -> ' + str(ref))

        for path, data in documents.items():
            try:
                name, kind = data['name'], data['resourceType']
                base = path.rsplit('/', 1)[0]
                if kind == 'GMScript':
                    require_file(base + '/' + name + '.gml')
                elif kind == 'GMObject':
                    for key, typ in (('spriteId', 'GMSprite'), ('spriteMaskId', 'GMSprite'), ('parentObjectId', 'GMObject')):
                        check_ref(data.get(key), typ, path)
                    events = set()
                    for event in data['eventList']:
                        identity = (event['eventType'], event['eventNum'],
                            (event.get('collisionObjectId') or {}).get('path')
                            if event['eventType'] == 4 else None)
                        if identity in events:
                            errors.append('重复事件：' + path)
                        events.add(identity)
                        stem = EVENT_STEMS.get(identity[0])
                        if identity[0] == 4:
                            check_ref(event['collisionObjectId'], 'GMObject', path)
                            suffix = event['collisionObjectId']['name']
                        else:
                            suffix = str(identity[1])
                        if stem:
                            require_file(f'{base}/{stem}_{suffix}.gml')
                        else:
                            warnings.append('未校验的事件类型：' + str(identity))
                    seen, ancestor = {name}, data.get('parentObjectId')
                    while ancestor and ancestor['path'] in documents:
                        if ancestor['name'] in seen:
                            errors.append('父物体循环：' + path)
                            break
                        seen.add(ancestor['name'])
                        ancestor = documents[ancestor['path']].get('parentObjectId')
                elif kind == 'GMRoom':
                    if data.get('$GMRoom') == 'v1':
                        room_settings(data)
                    instances, layers = [], set()
                    for layer in walk_layers(data['layers']):
                        if layer['name'].casefold() in layers:
                            errors.append('重复图层：' + path)
                        layers.add(layer['name'].casefold())
                        for instance in layer.get('instances', []):
                            instances.append(instance['name'])
                            check_ref(instance['objectId'], 'GMObject', path)
                        check_ref(layer.get('spriteId'), 'GMSprite', path)
                        if layer.get('resourceType') == 'GMRTileLayer':
                            if not layer.get('tilesetId'):
                                errors.append('瓦片图层缺少瓦片集引用：' + path)
                            else:
                                check_ref(layer.get('tilesetId'), 'GMTileSet', path)
                            tiles = layer.get('tiles') or {}
                            try:
                                _, _, values = tile_storage.read(tiles)
                            except ValueError as exc:
                                errors.append(f'{path}: {exc}')
                            else:
                                target = (layer.get('tilesetId') or {}).get('path')
                                tileset = documents.get(target)
                                count = tileset.get('tile_count') if tileset else None
                                if type(count) is int:
                                    for value in values:
                                        if type(value) is not int:
                                            errors.append('瓦片索引不是整数：' + path)
                                            break
                                        if tile_storage.empty(value):
                                            continue
                                        if not 0 <= (value & tile_storage.INDEX_MASK) <= count:
                                            errors.append('瓦片索引超出瓦片集：' + path)
                                            break
                    order = [i['name'] for i in data['instanceCreationOrder']]
                    if sorted(instances) != sorted(order) or len(instances) != len(set(instances)):
                        errors.append('房间实例与创建顺序不一致：' + path)
                    if any(i['path'] != path for i in data['instanceCreationOrder']):
                        errors.append('实例创建顺序房间引用错误：' + path)
                    if data.get('parentRoom'):
                        warnings.append('继承房间仅进行基础引用检查：' + path)
                elif kind == 'GMSprite':
                    sequence = data.get('sequence', {})
                    if sequence and data.get('type') == 0:
                        number(sequence['playbackSpeed'], '动画速度')
                        number(sequence['length'], '动画总长度')
                    frames = {f['name'] for f in data['frames']}
                    if len(frames) != len(data['frames']):
                        errors.append('精灵帧编号重复：' + path)
                    for frame in frames:
                        require_file(f'{base}/{frame}.png')
                        for layer in data['layers']:
                            require_file(f"{base}/layers/{frame}/{layer['name']}.png")
                    for track in data.get('sequence', {}).get('tracks', []):
                        if track.get('resourceType') == 'GMSpriteFramesTrack':
                            for key in track['keyframes']['Keyframes']:
                                number(key['Key'], '关键帧起点')
                                number(key['Length'], '逐帧时长', positive=not key.get('Stretch'))
                                if key['Key'] >= sequence['length']:
                                    errors.append('关键帧起点超出动画总长度：' + path)
                                if not key.get('Stretch') and key['Key'] + key['Length'] > sequence['length'] + 1e-9:
                                    errors.append('关键帧末端超出动画总长度：' + path)
                                for channel in key['Channels'].values():
                                    if channel['Id']['name'] not in frames or channel['Id']['path'] != path:
                                        errors.append('精灵动画帧引用错误：' + path)
                    group = data.get('textureGroupId')
                    if group and group['name'] not in {g['name'] for g in meta.get('TextureGroups', [])}:
                        errors.append('纹理组引用错误：' + path)
                elif kind == 'GMTileSet':
                    sprite_ref = data.get('spriteId')
                    check_ref(sprite_ref, 'GMSprite', path)
                    tile_width, tile_height = data.get('tileWidth'), data.get('tileHeight')
                    if type(tile_width) is not int or type(tile_height) is not int or \
                            tile_width < 1 or tile_height < 1:
                        errors.append('瓦片尺寸无效：' + path)
                    else:
                        sprite = documents.get((sprite_ref or {}).get('path'))
                        if sprite:
                            width, height = sprite.get('width'), sprite.get('height')
                            if type(width) is not int or type(height) is not int or \
                                    width % tile_width or height % tile_height:
                                errors.append('精灵尺寸与瓦片尺寸不整除：' + path)
                            elif data.get('tile_count') != (width // tile_width) * (height // tile_height):
                                errors.append('瓦片数量与精灵尺寸不一致：' + path)
                    group = data.get('textureGroupId')
                    if group and group['name'] not in {g['name'] for g in meta.get('TextureGroups', [])}:
                        errors.append('纹理组引用错误：' + path)
                elif kind == 'GMSound':
                    require_file(base + '/' + data['soundFile'])
                    if data['audioGroupId']['name'] not in {g['name'] for g in meta.get('AudioGroups', [])}:
                        errors.append('音频组引用错误：' + path)
                elif kind == 'GMShader':
                    require_file(base + '/' + name + '.vsh')
                    require_file(base + '/' + name + '.fsh')
                else:
                    warnings.append('该资源类型仅检查注册和分组：' + kind)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                errors.append(f'{path}: {exc}')
        rooms = [n['roomId'] for n in meta.get('RoomOrderNodes', [])]
        for ref in rooms:
            check_ref(ref, 'GMRoom', 'RoomOrderNodes')
        room_paths = [r['path'] for r in rooms]
        if len(room_paths) != len(set(room_paths)):
            errors.append('房间顺序存在重复')
        if set(room_paths) != {p for p, d in documents.items() if d['resourceType'] == 'GMRoom'}:
            errors.append('房间顺序与注册房间不一致')
        order_path = project.yyp.with_suffix('.resource_order').name
        if project.path(order_path).is_file():
            order = project.read(order_path)
            for key, known in (('FolderOrderSettings', folders), ('ResourceOrderSettings', refs)):
                seen = set()
                for item in order.get(key, []):
                    if item['path'] not in known:
                        errors.append('排序文件存在悬空引用：' + item['path'])
                    if item['path'] in seen:
                        errors.append('排序文件存在重复引用：' + item['path'])
                    seen.add(item['path'])
        pending = [t['id'] for t in project.transactions() if t['status'] in ('prepared', 'writing', 'recovery_required')]
        if pending:
            errors.append('存在待恢复事务：' + ', '.join(pending))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        errors.append(str(exc))
    return {'ok': not errors, 'project': str(project.yyp), 'writable': project.writable, 'assets': assets,
            'errors': errors, 'warnings': warnings, 'ide_verified': False, 'compiled': False}
