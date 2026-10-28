"""真实 MCP stdio 握手、工具元数据、预览、执行、错误与恢复。"""
import asyncio
import json
import os
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image
from tests.test_core import ProjectFixture
from tests.test_display import windows_fixture
from morx_gms2_mcp.display import WINDOWS_PATH
from morx_gms2_mcp.storage import encode
from tests.test_audio import make_wav
from morx_gms2_mcp.textures import new_group


class ProtocolTests(ProjectFixture):
    def run_protocol(self, writable):
        meta = self.project.metadata()
        meta['TextureGroups'] = [new_group('Default')]
        self.project.yyp.write_bytes(encode(meta))
        png = self.root / 'protocol.png'
        Image.new('RGBA', (4, 4), (255, 100, 0, 255)).save(png)
        self.project.import_sprite('spr_protocol', [str(png), str(png)])
        sprite_file = self.root / 'sprites/spr_protocol/spr_protocol.yy'
        original = sprite_file.read_bytes()
        resized_png = self.root / 'resized.png'
        Image.new('RGBA', (8, 6), (100, 200, 80, 255)).save(resized_png)
        wav = make_wav(self.root / 'protocol.wav')
        self.project.import_sound('snd_protocol', str(wav))
        sound_file = self.root / 'sounds/snd_protocol/snd_protocol.yy'
        original_sound = sound_file.read_bytes()
        self.project.create_room('rm_display_protocol', 800, 450)
        self.project.set_start_room('rm_display_protocol')
        windows_file = self.root / WINDOWS_PATH
        windows_file.parent.mkdir(parents=True)
        windows_file.write_bytes(encode(windows_fixture()))
        room_file = self.root / 'rooms/rm_display_protocol/rm_display_protocol.yy'
        original_windows, original_room = windows_file.read_bytes(), room_file.read_bytes()
        async def run():
            args = ['-m', 'morx_gms2_mcp', 'serve', '--project', str(self.root)]
            if writable:
                args.append('--write')
            params = StdioServerParameters(command=sys.executable, args=args,
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    init = await session.initialize()
                    self.assertEqual(init.serverInfo.name, 'morx-gms2-mcp')
                    self.assertLessEqual(len(init.instructions or ''), 80)
                    tools = (await session.list_tools()).tools
                    self.assertEqual(len(tools), 71)
                    self.assertTrue({'inspect_sprite', 'configure_sprite', 'set_sprite_animation',
                                     'set_game_variables', 'spawn_game_instance',
                                     'destroy_game_instance', 'remove_object_event',
                                     'import_font', 'inspect_sound', 'configure_sound'}.issubset({tool.name for tool in tools}))
                    for tool in tools:
                        self.assertIsNotNone(tool.annotations)
                        if not tool.annotations.readOnlyHint:
                            self.assertTrue(tool.inputSchema['properties']['dry_run']['default'])
                    async def call(name, args, error=False):
                        result = await session.call_tool(name, arguments=args)
                        self.assertEqual(bool(result.isError), error, str(result))
                        return json.loads(result.content[0].text) if not error else None
                    self.assertEqual((await call('server_info', {}))['writable'], writable)
                    self.assertFalse((await call('runtime_status', {}))['enabled'])
                    waiting = asyncio.create_task(session.call_tool('runtime_status', arguments={'wait_seconds': 2}))
                    await asyncio.sleep(.1)
                    try:
                        await asyncio.wait_for(call('server_info', {}), 1)
                        self.assertFalse(waiting.done(), 'stdio 被同步等待阻塞，无法并发响应')
                    finally:
                        await waiting
                    self.assertEqual((await call('list_game_sessions', {}))['sessions'], [])
                    snapshot = await call('inspect_runtime_snapshot', {'limit': 0})
                    self.assertGreater(snapshot['file_count'], 0)
                    self.assertEqual(snapshot['files'], [])
                    await call('configure_runtime_snapshot', {'dry_run': True})
                    if writable:
                        configured = await call('configure_runtime_snapshot', {'dry_run': False})
                        await call('configure_runtime_snapshot', {'dry_run': False}, True)
                        await call('restore_backup', {'transaction_id': configured['transaction'], 'dry_run': False})
                    else:
                        await call('configure_runtime_snapshot', {'dry_run': False}, True)
                    groups = await call('inspect_audio_groups', {})
                    create_audio = {'name': 'audio_protocol', 'expected_project_sha256': groups['project_sha256']}
                    self.assertTrue((await call('create_audio_group', create_audio))['dry_run'])
                    if writable:
                        group_tx = await call('create_audio_group', {**create_audio, 'dry_run': False})
                        await call('restore_backup', {'transaction_id': group_tx['transaction'], 'dry_run': False})
                    else:
                        await call('create_audio_group', {**create_audio, 'dry_run': False}, True)
                    await call('inspect_game_audio', {'session_id': 'missing'}, True)
                    await call('game_test_status', {'test_id': 'missing'}, True)
                    await call('save_game_test_report', {'test_id': 'missing'}, True)
                    await call('run_game_test', {'session_id': 'missing', 'steps': [{'action': 'capture'}]}, True)
                    textures = await call('inspect_texture_groups', {})
                    create = {'name': 'Smooth', 'expected_project_sha256': textures['project_sha256'],
                              'mips_to_generate': 4, 'border': 8}
                    self.assertTrue((await call('create_texture_group', create))['dry_run'])
                    if writable:
                        made = await call('create_texture_group', {**create, 'dry_run': False})
                        textures = await call('inspect_texture_groups', {})
                        member = textures['texture_groups'][0]['members'][0]
                        assign = {'names': ['spr_protocol'], 'texture_group': 'Smooth',
                            'expected_project_sha256': textures['project_sha256'],
                            'expected_resource_sha256': {'spr_protocol': member['sha256']}}
                        self.assertTrue((await call('assign_sprite_texture_group', assign))['dry_run'])
                        moved = await call('assign_sprite_texture_group', {**assign, 'dry_run': False})
                        await call('restore_backup', {'transaction_id': moved['transaction'], 'dry_run': False})
                        configure = {'name': 'Smooth', 'expected_project_sha256': textures['project_sha256'],
                                     'autocrop': False, 'dry_run': False}
                        changed = await call('configure_texture_group', configure)
                        await call('restore_backup', {'transaction_id': changed['transaction'], 'dry_run': False})
                        await call('restore_backup', {'transaction_id': made['transaction'], 'dry_run': False})
                    else:
                        await call('create_texture_group', {**create, 'dry_run': False}, True)
                    sound = await call('inspect_sound', {'name': 'snd_protocol'})
                    audio_options = {'name': 'snd_protocol', 'expected_sha256': sound['sha256'],
                                     'compression': 'streamed', 'preload': True, 'compression_quality': 7}
                    self.assertTrue((await call('configure_sound', audio_options))['dry_run'])
                    preview = await call('import_sound', {'name': 'snd_preview', 'audio_path': str(wav),
                        'compression': 'decompress_on_load', 'preload': True, 'conversion_mode': 'required'})
                    self.assertEqual(preview['settings']['compression'], 'decompress_on_load')
                    await call('configure_sound', {**audio_options, 'compression': 'invalid'}, True)
                    if writable:
                        result = await call('configure_sound', {**audio_options, 'dry_run': False})
                        self.assertTrue((await call('inspect_sound', {'name': 'snd_protocol'}))['settings']['streamed'])
                        await call('configure_sound', {**audio_options, 'dry_run': False}, True)
                        await call('restore_backup', {'transaction_id': result['transaction'], 'dry_run': False})
                    else:
                        await call('configure_sound', {**audio_options, 'dry_run': False}, True)
                    self.assertEqual(sound_file.read_bytes(), original_sound)
                    await call('run_game', {}, True)
                    preview = await call('create_script', {'name': 'scr_protocol', 'code': '// 测试'})
                    self.assertTrue(preview['dry_run'])
                    self.assertFalse((self.root / 'scripts').exists())
                    if writable:
                        result = await call('create_script', {'name': 'scr_protocol', 'code': '// 测试', 'dry_run': False})
                        code = await call('read_code', {'name': 'scr_protocol'})
                        edit = {'name': 'scr_protocol', 'code': '// 修改', 'expected_sha256': code['sha256']}
                        self.assertTrue((await call('update_code', edit))['dry_run'])
                        changed = await call('update_code', {**edit, 'dry_run': False})
                        await call('update_code', {**edit, 'dry_run': False}, True)
                        await call('restore_backup', {'transaction_id': changed['transaction'], 'dry_run': False})
                        await call('create_script', {'name': 'scr_protocol', 'dry_run': False}, True)
                        await call('restore_backup', {'transaction_id': result['transaction'], 'dry_run': False})
                        self.assertFalse((self.root / 'scripts').exists())
                    else:
                        await call('create_script', {'name': 'scr_protocol', 'dry_run': False}, True)
                    sprite = await call('inspect_sprite', {'name': 'spr_protocol'})
                    replacement = {'name': 'spr_protocol', 'png_paths': [str(png)] * 2,
                                   'expected_sha256': sprite['sha256'],
                                   'expected_image_sha256': sprite['image_sha256']}
                    self.assertTrue((await call('replace_sprite_images', replacement))['dry_run'])
                    resizing = {**replacement, 'png_paths': [str(resized_png)]*2,
                                'allow_resize': True, 'origin_policy': 'scale', 'bbox_policy': 'full_image'}
                    self.assertTrue((await call('replace_sprite_images', resizing))['resized'])
                    if writable:
                        result = await call('replace_sprite_images', {**resizing, 'dry_run': False})
                        resized = await call('inspect_sprite', {'name': 'spr_protocol'})
                        self.assertEqual((resized['width'], resized['height']), (8, 6))
                        await call('restore_backup', {'transaction_id': result['transaction'], 'dry_run': False})
                        self.assertEqual(sprite_file.read_bytes(), original)
                    else:
                        await call('replace_sprite_images', {**resizing, 'dry_run': False}, True)
                    await call('compile_game', {}, True)  # 没有 --allow-run 时不能启动编译器。
                    if not writable:
                        await call('replace_sprite_images', {**replacement, 'dry_run': False}, True)
                    order = [f['frame_id'] for f in sprite['frames']][::-1]
                    options = {'name': 'spr_protocol', 'speed': 24, 'origin_x': -1,
                               'expected_sha256': sprite['sha256']}
                    animation = {'name': 'spr_protocol', 'frame_order': order,
                                 'frame_durations': [2, 1], 'expected_sha256': sprite['sha256']}
                    self.assertTrue((await call('configure_sprite', options))['dry_run'])
                    self.assertTrue((await call('set_sprite_animation', animation))['dry_run'])
                    self.assertEqual(sprite_file.read_bytes(), original)
                    if writable:
                        result = await call('configure_sprite', {**options, 'dry_run': False})
                        configured = await call('inspect_sprite', {'name': 'spr_protocol'})
                        self.assertEqual(configured['speed'], 24)
                        self.assertEqual(configured['origin']['x'], -1)
                        await call('set_sprite_animation', {**animation, 'dry_run': False}, True)
                        await call('restore_backup', {'transaction_id': result['transaction'], 'dry_run': False})
                        result = await call('set_sprite_animation', {**animation, 'dry_run': False})
                        changed = await call('inspect_sprite', {'name': 'spr_protocol'})
                        self.assertEqual([f['frame_id'] for f in changed['frames']], order)
                        self.assertEqual(changed['length'], 3)
                        await call('restore_backup', {'transaction_id': result['transaction'], 'dry_run': False})
                    else:
                        await call('configure_sprite', {**options, 'dry_run': False}, True)
                        await call('set_sprite_animation', {**animation, 'dry_run': False}, True)
                    self.assertEqual(sprite_file.read_bytes(), original)
                    display = await call('inspect_display_settings', {})
                    self.assertTrue(display['windows']['supported'])
                    self.assertTrue(display['room']['is_start_room'])
                    windows_options = {'title': '协议显示测试', 'resizable': True,
                                       'expected_sha256': display['windows']['sha256']}
                    room_options = {'room_name': 'rm_display_protocol', 'view_index': 1,
                        'enable_views': True, 'visible': True, 'camera_width': 320, 'camera_height': 180,
                        'viewport_width': 640, 'viewport_height': 360,
                        'expected_sha256': display['room']['sha256']}
                    self.assertTrue((await call('configure_windows_display', windows_options))['dry_run'])
                    self.assertTrue((await call('configure_room_display', room_options))['dry_run'])
                    self.assertEqual(windows_file.read_bytes(), original_windows)
                    self.assertEqual(room_file.read_bytes(), original_room)
                    if writable:
                        result = await call('configure_windows_display', {**windows_options, 'dry_run': False})
                        changed = await call('inspect_display_settings', {})
                        self.assertEqual(changed['windows']['settings']['title'], '协议显示测试')
                        await call('configure_windows_display', {**windows_options, 'dry_run': False}, True)
                        await call('restore_backup', {'transaction_id': result['transaction'], 'dry_run': False})
                        result = await call('configure_room_display', {**room_options, 'dry_run': False})
                        changed = await call('inspect_display_settings', {'room_name': 'rm_display_protocol'})
                        self.assertEqual(changed['room']['settings']['views'][1]['camera']['width'], 320)
                        self.assertEqual(changed['room']['settings']['views'][1]['viewport']['width'], 640)
                        await call('restore_backup', {'transaction_id': result['transaction'], 'dry_run': False})
                    else:
                        await call('configure_windows_display', {**windows_options, 'dry_run': False}, True)
                        await call('configure_room_display', {**room_options, 'dry_run': False}, True)
                    self.assertEqual(windows_file.read_bytes(), original_windows)
                    self.assertEqual(room_file.read_bytes(), original_room)
                    self.assertTrue((await call('inspect_project', {}))['ok'])
        asyncio.run(run())

    def test_writable_protocol(self):
        self.run_protocol(True)

    def test_readonly_protocol(self):
        self.run_protocol(False)
