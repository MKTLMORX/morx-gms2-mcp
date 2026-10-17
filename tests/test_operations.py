"""高风险边界、恢复和资源组合测试；测试项目完全由夹具创建。"""
import io
import json
import subprocess
import sys
import wave
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from morx_gms2_mcp.cli import bind, client_config
from morx_gms2_mcp.resources import Project
from morx_gms2_mcp.storage import encode
from tests.test_core import ProjectFixture


class OperationTests(ProjectFixture):
    def test_groups_and_move_keep_native_path(self):
        self.project.create('scripts', 'scr_test', group='脚本/battle/timing')
        paths = {x['folderPath'] for x in self.project.metadata()['Folders']}
        self.assertEqual(paths, {'folders/脚本.yy', 'folders/脚本/battle.yy', 'folders/脚本/battle/timing.yy'})
        self.project.move_resource('scr_test', '脚本/game')
        self.assertTrue((self.root / 'scripts/scr_test/scr_test.gml').exists())
        self.assertEqual(self.project.inspect_resource('scr_test')['resource']['parent']['path'], 'folders/脚本/game.yy')
        self.assertTrue(self.project.validate()['ok'], self.project.validate())

    def test_group_idempotence_and_invalid_names(self):
        self.project.ensure_group('物体/ui')
        self.assertEqual(self.project.ensure_group('物体/ui')['changed'], [])
        for name in ('', '../bad', 'a//b', 'a/b.', 'a\\b', 'a:b'):
            with self.assertRaises(ValueError):
                self.project.ensure_group(name)

    def test_preview_does_not_create_resource_or_backup(self):
        original = self.project.yyp.read_bytes()
        result = self.project.create('objects', 'sys_preview', dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertEqual(self.project.yyp.read_bytes(), original)
        self.assertFalse((self.root / 'objects').exists())
        self.assertEqual(self.project.transactions(), [])

    def test_readonly_service(self):
        project = Project(self.root, writable=False)
        self.assertTrue(project.create('scripts', 'scr_a', dry_run=True)['dry_run'])
        with self.assertRaises(ValueError):
            project.create('scripts', 'scr_a')

    def test_restore_new_resource_and_recreate(self):
        original = self.project.yyp.read_bytes()
        result = self.project.create('scripts', 'scr_a', 'return 1;')
        self.assertTrue(self.project.restore(result['transaction'])['dry_run'])
        self.assertTrue((self.root / 'scripts/scr_a/scr_a.gml').exists())
        self.project.restore(result['transaction'], dry_run=False)
        self.assertEqual(self.project.yyp.read_bytes(), original)
        self.assertFalse((self.root / 'scripts/scr_a').exists())
        self.project.create('scripts', 'scr_a')

    def test_restore_rejects_changed_file_and_old_transaction(self):
        first = self.project.create('scripts', 'scr_a')
        second = self.project.create('scripts', 'scr_b')
        with self.assertRaises(ValueError):
            self.project.restore(first['transaction'], dry_run=False)
        file = self.root / 'scripts/scr_b/scr_b.gml'
        file.write_bytes(b'user change')
        with self.assertRaises(ValueError):
            self.project.restore(second['transaction'], dry_run=False)
        self.assertEqual(file.read_bytes(), b'user change')

    def test_restore_backup_integrity(self):
        result = self.project.create('scripts', 'scr_a')
        old = self.project.journal / result['transaction'] / 'before/test.yyp'
        old.write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            self.project.restore(result['transaction'], dry_run=False)

    def test_crash_recovery_blocks_other_mutations(self):
        result = self.project.create('scripts', 'scr_a')
        rel, manifest = self.project._manifest(result['transaction'])
        manifest['status'] = 'writing'
        self.project._write(rel, encode(manifest))
        with self.assertRaises(ValueError):
            self.project.create('scripts', 'scr_b')
        self.assertFalse(self.project.validate()['ok'])
        self.project.restore(result['transaction'], dry_run=False)
        self.assertTrue(self.project.validate()['ok'])

    def test_process_lock_prevents_second_writer(self):
        code = 'from pathlib import Path; from morx_gms2_mcp.resources import Project; p=Project(Path(__import__("sys").argv[1])); p.ensure_group("blocked")'
        with self.project.lock:
            result = subprocess.run([sys.executable, '-c', code, str(self.root)], capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.project.metadata()['Folders'], [])

    def test_binding_and_client_config(self):
        (self.root / '.codex/gms2-resource-binding.json').unlink()
        self.assertEqual(bind(self.root), bind(self.root))
        self.assertEqual(Project(self.root).yyp, self.project.yyp)
        claude = json.loads(client_config(self.root, 'claude', True))['mcpServers']['morx-gms2-mcp']
        self.assertIn('--write', claude['args'])
        self.assertEqual(claude['command'], sys.executable)
        self.assertIn('[mcp_servers.morx-gms2-mcp]', client_config(self.root, 'codex', False))
        binding = self.root / '.codex/gms2-resource-binding.json'
        binding.write_bytes(encode({'format': 1, 'project': str(self.root / 'wrong.yyp')}))
        with self.assertRaises(ValueError):
            Project(self.root)

    def test_binding_normalizes_project_path(self):
        (self.root / '.codex/gms2-resource-binding.json').unlink()
        result = bind(self.root / 'rooms' / '..')
        self.assertEqual(Path(result['bound']), self.project.yyp)
        self.assertEqual(bind(self.root), result)
        self.assertEqual(Project(self.root).yyp, self.project.yyp)

    def test_path_escape_and_reserved_write_targets(self):
        for value in ('../outside', '/outside', 'C:/outside', 'scripts\\bad'):
            with self.assertRaises(ValueError):
                self.project.path(value)
        for value in ('AGENTS.md', '.codex/config.toml', '参考项目/test.yy'):
            with self.assertRaises(ValueError):
                self.project.commit({value: b'bad'}, self.project.snapshot([value]))

    def test_room_events_and_parent_cycle(self):
        self.project.create('objects', 'obj_parent')
        self.project.create('objects', 'sys_child')
        self.project.configure_object('sys_child', persistent=True, parent='obj_parent')
        with self.assertRaises(ValueError):
            self.project.configure_object('obj_parent', parent='sys_child')
        for event, number in [('DrawGUI', 0), ('BeginStep', 0), ('EndStep', 0), ('CleanUp', 0), ('Alarm', 11), ('User', 15)]:
            self.project.add_event('sys_child', event, '// 测试\n', number)
        self.assertTrue((self.root / 'objects/sys_child/Draw_64.gml').exists())
        self.assertTrue((self.root / 'objects/sys_child/Other_25.gml').exists())
        self.project.create_room('rm_test', 800, 450)
        self.project.add_layer('rm_test', 'UI', -100)
        self.project.place('sys_child', 'rm_test', 'UI', 'inst_test', 10, 20)
        self.project.set_start_room('rm_test')
        self.assertEqual(self.project.metadata()['RoomOrderNodes'][0]['roomId']['name'], 'rm_test')
        self.assertTrue(self.project.validate()['ok'], self.project.validate())

    def test_collision_keyboard_mouse_and_other_events(self):
        self.project.create('objects', 'obj_hit')
        self.project.create('objects', 'obj_target')
        self.project.add_event('obj_hit', 'Collision', 'hp -= 1;', collision_object='obj_target')
        self.project.add_event('obj_hit', 'Keyboard', 'x += 1;', 65)
        self.project.add_event('obj_hit', 'KeyPress', '//', 32)
        self.project.add_event('obj_hit', 'KeyRelease', '//', 37)
        self.project.add_event('obj_hit', 'Mouse', '//', 4)
        self.project.add_event('obj_hit', 'GameStart', '//')
        self.project.add_event('obj_hit', 'AnimationEnd', '//')
        self.project.add_event('obj_hit', 'DrawPre', '//')
        for path in ('Collision_obj_target.gml', 'Keyboard_65.gml', 'KeyPress_32.gml',
                     'KeyRelease_37.gml', 'Mouse_4.gml', 'Other_2.gml', 'Other_7.gml', 'Draw_76.gml'):
            self.assertTrue((self.root / 'objects/obj_hit' / path).exists(), path)
        obj = self.project.read('objects/obj_hit/obj_hit.yy')
        collision = [e for e in obj['eventList'] if e['eventType'] == 4][0]
        self.assertEqual(collision['collisionObjectId']['name'], 'obj_target')
        info = self.project.read_code('obj_hit', 'Collision', collision_object='obj_target')
        self.assertEqual(info['path'], 'objects/obj_hit/Collision_obj_target.gml')
        self.assertIn('hp -= 1', info['code'])
        self.project.update_code('obj_hit', 'hp -= 2;', info['sha256'],
                                 'Collision', collision_object='obj_target', dry_run=False)
        self.assertEqual(self.project.read_code('obj_hit', 'Collision',
            collision_object='obj_target')['code'], 'hp -= 2;')
        with self.assertRaises(ValueError):
            self.project.add_event('obj_hit', 'Collision', '//', collision_object='obj_target')
        with self.assertRaises(ValueError):
            self.project.add_event('obj_hit', 'Keyboard', '//', 65)
        with self.assertRaises(ValueError):
            self.project.add_event('obj_hit', 'Collision', '//', collision_object='obj_hit')
        with self.assertRaises(ValueError):
            self.project.add_event('obj_hit', 'Mouse', '//', 99)
        with self.assertRaises(ValueError):
            self.project.add_event('obj_hit', 'Keyboard', '//', 0)
        self.assertTrue(self.project.validate()['ok'], self.project.validate())

    def test_tileset_and_tile_layer(self):
        sprite_path = self.root / 'tiles.png'
        Image.new('RGBA', (32, 16), (20, 40, 60, 255)).save(sprite_path)
        self.project.import_sprite('spr_tiles', [str(sprite_path)])
        self.project.create_tileset('ts_test', 'spr_tiles', 16, 16, '瓦片集/测试')
        info = self.project.inspect_tileset('ts_test')
        self.assertEqual((info['columns'], info['tile_count']), (2, 2))
        self.assertEqual((info['tile_width'], info['tile_height']), (16, 16))
        self.project.create_tile_layer('Room1', 'Tiles', 'ts_test', width=4, height=3)
        result = self.project.set_tilemap_cells('Room1', 'Tiles',
            [{'x': 0, 'y': 0, 'tile': 1}, {'x': 1, 'y': 0, 'tile': 2, 'flip_x': True},
             {'x': 3, 'y': 2, 'tile': 1, 'rotate': True}])
        self.assertEqual(result['updated'], 3)
        tilemap = self.project.inspect_tilemap('Room1', 'Tiles', include_cells=True)
        self.assertEqual((tilemap['width'], tilemap['height']), (4, 3))
        self.assertEqual(tilemap['non_empty'], 3)
        self.assertEqual([cell['tile'] for cell in tilemap['cells']], [1, 2, 1])
        self.assertTrue(tilemap['cells'][1]['flip_x'])
        self.assertTrue(tilemap['cells'][2]['rotate'])
        with self.assertRaises(ValueError):
            self.project.set_tilemap_cells('Room1', 'Tiles', [{'x': 0, 'y': 0, 'tile': 1}],
                                           expected_sha256='0' * 64)
        for cells in ([{'x': 4, 'y': 0, 'tile': 1}], [{'x': 0, 'y': 0, 'tile': 3}],
                      [{'x': 0, 'y': 0, 'tile': 1, 'flip_x': 1}],
                      [{'x': 0, 'y': 0, 'tile': 1, 'bad': True}]):
            with self.assertRaises(ValueError):
                self.project.set_tilemap_cells('Room1', 'Tiles', cells)
        with self.assertRaises(ValueError):
            self.project.create_tile_layer('Room1', 'Tiles', 'ts_test', 4, 3)
        with self.assertRaises(ValueError):
            self.project.create_tileset('ts_bad', 'spr_tiles', 12, 16)
        self.assertTrue(self.project.validate()['ok'], self.project.validate())

    def test_png_wav_shader_resources(self):
        paths = []
        for index in range(2):
            path = self.root / f'input{index}.png'
            Image.new('RGBA', (16, 16), (index * 200, 100, 100, 255)).save(path)
            paths.append(str(path))
        original = [Path(p).read_bytes() for p in paths]
        self.project.import_sprite('spr_test', paths, '精灵/测试', origin_x=8, origin_y=8)
        sprite = self.project.inspect_resource('spr_test')['resource']
        self.assertEqual(len(sprite['frames']), 2)
        self.assertEqual(sprite['sequence']['xorigin'], 8)
        for index, frame in enumerate(sprite['frames']):
            self.assertEqual((self.root / f"sprites/spr_test/{frame['name']}.png").read_bytes(), original[index])
        path = self.root / 'sound.wav'
        with wave.open(str(path), 'wb') as sound:
            sound.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
            sound.writeframes(b'\0\0' * 4410)
        self.project.import_sound('sfx_test', str(path))
        self.project.create_shader('shd_test', '// vertex', '// fragment')
        self.project.create('objects', 'obj_sprite')
        self.project.configure_object('obj_sprite', sprite='spr_test')
        self.assertTrue(self.project.validate()['ok'], self.project.validate())

    def test_invalid_media_no_resources_created(self):
        a, b = self.root / 'a.png', self.root / 'b.png'
        Image.new('RGBA', (4, 4)).save(a)
        Image.new('RGBA', (8, 8)).save(b)
        with self.assertRaises(ValueError):
            self.project.import_sprite('spr_bad', [str(a), str(b)])
        self.assertFalse((self.root / 'sprites').exists())
        bad = self.root / 'bad.wav'
        bad.write_bytes(b'bad')
        with self.assertRaises((wave.Error, EOFError)):
            self.project.import_sound('sfx_bad', str(bad))

    def test_metadata_preserves_bom_crlf_unknown_fields(self):
        meta = self.project.metadata()
        meta['custom_unknown'] = {'preserve': True}
        self.project.yyp.write_bytes(b'\xef\xbb\xbf' + encode(meta).replace(b'\n', b'\r\n'))
        self.project.ensure_group('脚本')
        raw = self.project.yyp.read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))
        self.assertTrue(self.project.metadata()['custom_unknown']['preserve'])
