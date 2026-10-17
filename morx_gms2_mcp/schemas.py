"""GameMaker 2026 描述结构；只定义元数据，不依赖参考项目资源。"""
from uuid import uuid4

EMPTY_TILE = 0x80000000
TILE_FLIP_H = 0x10000000
TILE_FLIP_V = 0x20000000
TILE_ROTATE = 0x40000000


def typed(kind, version='', **fields):
    return {'$' + kind: version, 'resourceType': kind, 'resourceVersion': '2.0', **fields}


def instance_layer(name, depth=0):
    return typed('GMRInstanceLayer', **{'%Name': name}, name=name, depth=depth,
        effectEnabled=True, effectType=None, gridX=32, gridY=32, hierarchyFrozen=False,
        inheritLayerDepth=False, inheritLayerSettings=False, inheritSubLayers=True,
        inheritVisibility=True, instances=[], layers=[], properties=[], userdefinedDepth=False, visible=True)


def instance(name, object_ref, x=0, y=0):
    return typed('GMRInstance', 'v4', **{'%Name': name}, name=name,
        colour=4294967295, frozen=False, hasCreationCode=False, ignore=False, imageIndex=0, imageSpeed=1.0,
        inheritCode=False, inheritedItemId=None, inheritItemSettings=False, isDnd=False,
        objectId=object_ref, properties=[], rotation=0.0, scaleX=1.0, scaleY=1.0, x=x, y=y)


def tileset(name, sprite_ref, width, height, tile_width, tile_height):
    columns, rows = width // tile_width, height // tile_height
    count = columns * rows
    # 派生属性按 GMTileSet 的声明顺序排列；基类字段放在派生字段之后。
    return {
        '$GMTileSet': 'v1',
        '%Name': name,
        'spriteId': sprite_ref,
        'tileWidth': tile_width,
        'tileHeight': tile_height,
        'tilexoff': 0,
        'tileyoff': 0,
        'tilehsep': 0,
        'tilevsep': 0,
        'spriteNoExport': False,
        'textureGroupId': {'name': 'Default', 'path': 'texturegroups/Default'},
        'out_tilehborder': 2,
        'out_tilevborder': 2,
        'out_columns': columns,
        'tile_count': count,
        'autoTileSets': [],
        'tileAnimationFrames': [],
        'tileAnimationSpeed': 15.0,
        'macroPageTiles': {'SerialiseWidth': 0, 'SerialiseHeight': 0, 'TileSerialiseData': []},
        'parent': None,
        'resourceVersion': '2.0',
        'name': name,
        'tags': [],
        'resourceType': 'GMTileSet',
    }


def tile_layer(name, tileset_ref, width, height, x=0, y=0, depth=0):
    # 与 2026 房间图层相同的基础字段顺序；瓦片字段紧跟 %Name。
    return {
        '$GMRTileLayer': '',
        '%Name': name,
        'tilesetId': tileset_ref,
        'x': x,
        'y': y,
        'tiles': {'SerialiseWidth': width, 'SerialiseHeight': height,
                  'TileSerialiseData': [EMPTY_TILE] * (width * height)},
        'depth': depth,
        'effectEnabled': True,
        'effectType': None,
        'gridX': 32,
        'gridY': 32,
        'hierarchyFrozen': False,
        'inheritLayerDepth': False,
        'inheritLayerSettings': False,
        'inheritSubLayers': True,
        'inheritVisibility': True,
        'layers': [],
        'name': name,
        'properties': [],
        'resourceType': 'GMRTileLayer',
        'resourceVersion': '2.0',
        'userdefinedDepth': False,
        'visible': True,
    }


def room(name, width, height):
    view = dict(hborder=32, hport=height, hspeed=-1, hview=height, inherit=False, objectId=None,
        vborder=32, visible=False, vspeed=-1, wport=width, wview=width, xport=0, xview=0, yport=0, yview=0)
    return typed('GMRoom', 'v1', **{'%Name': name}, name=name, creationCodeFile='', inheritCode=False,
        inheritCreationOrder=False, inheritLayers=False, instanceCreationOrder=[], isDnd=False,
        layers=[instance_layer('Instances')], parentRoom=None,
        physicsSettings=dict(inheritPhysicsSettings=False, PhysicsWorld=False, PhysicsWorldGravityX=0.0,
            PhysicsWorldGravityY=10.0, PhysicsWorldPixToMetres=0.1),
        roomSettings=dict(Height=height, Width=width, persistent=False, inheritRoomSettings=False),
        sequenceId=None, views=[dict(view) for _ in range(8)], volume=1.0,
        viewSettings=dict(clearDisplayBuffer=True, clearViewBackground=False, enableViews=False, inheritViewSettings=False))


def object_resource(name):
    return typed('GMObject', **{'%Name': name}, name=name, eventList=[], managed=True,
        overriddenProperties=[], parentObjectId=None, persistent=False, properties=[], physicsAngularDamping=0.1,
        physicsDensity=0.5, physicsFriction=0.2, physicsGroup=1, physicsKinematic=False, physicsLinearDamping=0.1,
        physicsObject=False, physicsRestitution=0.1, physicsSensor=False, physicsShape=1, physicsShapePoints=[],
        physicsStartAwake=True, solid=False, spriteId=None, spriteMaskId=None, visible=True)


def sprite(name, width, height, count, speed, origin_x, origin_y):
    frames = [str(uuid4()) for _ in range(count)]
    layer = str(uuid4())
    rel = f'sprites/{name}/{name}.yy'
    keys = [typed('Keyframe<SpriteFrameKeyframe>', Channels={'0': typed('SpriteFrameKeyframe',
        Id={'name': frame, 'path': rel})}, Disabled=False, id=str(uuid4()), IsCreationKey=False,
        Key=float(index), Length=1.0, Stretch=False) for index, frame in enumerate(frames)]
    track = typed('GMSpriteFramesTrack', builtinName=0, events=[], inheritsTrackColour=True, interpolation=1,
        isCreationTrack=False, keyframes=typed('KeyframeStore<SpriteFrameKeyframe>', Keyframes=keys),
        modifiers=[], name='frames', spriteId=None, trackColour=0, tracks=[], traits=0)
    sequence = typed('GMSequence', 'v1', **{'%Name': name}, name=name, autoRecord=True, backdropHeight=768,
        backdropImageOpacity=0.5, backdropImagePath='', backdropWidth=1366, backdropXOffset=0.0, backdropYOffset=0.0,
        events=typed('KeyframeStore<MessageEventKeyframe>', Keyframes=[]), eventStubScript=None, eventToFunction={},
        length=float(count), lockOrigin=False, moments=typed('KeyframeStore<MomentsEventKeyframe>', Keyframes=[]),
        playback=1, playbackSpeed=speed, playbackSpeedType=0, showBackdrop=True, showBackdropImage=False,
        timeUnits=1, tracks=[track], visibleRange=None, volume=1.0, xorigin=origin_x, yorigin=origin_y)
    data = typed('GMSprite', 'v2', **{'%Name': name}, name=name, bboxMode=1, bbox_bottom=height-1,
        bbox_left=0, bbox_right=width-1, bbox_top=0, collisionKind=1, collisionTolerance=0,
        DynamicTexturePage=False, edgeFiltering=False, For3D=False,
        frames=[typed('GMSpriteFrame', 'v1', **{'%Name': f}, name=f) for f in frames],
        gridX=0, gridY=0, height=height, HTile=False,
        layers=[typed('GMImageLayer', **{'%Name': layer}, name=layer, blendMode=0, displayName='default',
            isLocked=False, opacity=100.0, visible=True)], nineSlice=None, origin=9, preMultiplyAlpha=False,
        sequence=sequence, swatchColours=None, swfPrecision=0.5,
        textureGroupId={'name': 'Default', 'path': 'texturegroups/Default'}, type=0, VTile=False, width=width)
    return data, frames, layer
