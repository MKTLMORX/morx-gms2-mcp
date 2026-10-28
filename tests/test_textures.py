"""纹理组事务、引用、过期请求与位图身份保护。"""
from copy import deepcopy
from unittest.mock import patch
from PIL import Image
from tests.test_core import ProjectFixture
from morx_gms2_mcp.resources import Project
from morx_gms2_mcp.storage import encode
from morx_gms2_mcp.textures import new_group


class TextureTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        meta = self.project.metadata()
        meta['TextureGroups'] = [new_group('Default')]
        meta['TextureGroups'][0]['future_field'] = {'keep': 42}
        self.project.yyp.write_bytes(b'\xef\xbb\xbf' + encode(meta).replace(b'\n', b'\r\n'))
        png = self.root / 'source.png'
        Image.new('RGBA', (32, 32), (20, 150, 200, 255)).save(png)
        for name in ('spr_one', 'spr_two'):
            self.project.import_sprite(name, [str(png)], dry_run=False)

    def info(self):
        return self.project.inspect_texture_groups()

    def create_group(self):
        return self.project.create_texture_group('Smooth', self.info()['project_sha256'],
            mips_to_generate=4, border=8, dry_run=False)

    def assignment(self):
        info = self.info()
        return dict(names=['spr_one', 'spr_two'], texture_group='Smooth',
            expected_project_sha256=info['project_sha256'], expected_resource_sha256={
                m['name']: m['sha256'] for g in info['texture_groups'] for m in g['members']})

    def test_create_preview_conflict_undo_encoding(self):
        before = self.project.yyp.read_bytes()
        sha = self.info()['project_sha256']
        preview = self.project.create_texture_group('Smooth', sha, 4, 8)
        self.assertTrue(preview['dry_run'])
        self.assertEqual(before, self.project.yyp.read_bytes())
        result = self.create_group()
        self.assertTrue(self.project.yyp.read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertIn(b'\r\n', self.project.yyp.read_bytes())
        self.assertEqual(self.project.metadata()['TextureGroups'][0]['future_field'], {'keep': 42})
        with self.assertRaisesRegex(ValueError, '已修改'):
            self.project.create_texture_group('Other', sha, dry_run=False)
        with self.assertRaisesRegex(ValueError, '已存在'):
            self.project.create_texture_group('smooth', self.info()['project_sha256'])
        self.project.restore(result['transaction'], dry_run=False)
        self.assertEqual(before, self.project.yyp.read_bytes())

    def test_assign_batch_preserves_everything_except_group_and_undo(self):
        self.create_group()
        before = {p.relative_to(self.root).as_posix(): p.read_bytes()
                  for p in (self.root / 'sprites').rglob('*') if p.is_file()}
        args = self.assignment()
        self.assertTrue(self.project.assign_sprite_texture_group(**args)['dry_run'])
        result = self.project.assign_sprite_texture_group(**args, dry_run=False)
        self.assertEqual(len(result['changed']), 2)
        self.assertTrue(self.project.validate()['ok'])
        for name in args['names']:
            data = self.project.read(f'sprites/{name}/{name}.yy')
            self.assertEqual(data['textureGroupId'], {'name': 'Smooth', 'path': 'texturegroups/Smooth'})
        for path, raw in before.items():
            if path.endswith('.png'):
                self.assertEqual(raw, self.project.path(path).read_bytes())
        with self.assertRaises(ValueError):
            self.project.assign_sprite_texture_group(**args, dry_run=False)
        self.project.restore(result['transaction'], dry_run=False)
        for path, raw in before.items():
            self.assertEqual(raw, self.project.path(path).read_bytes())

    def test_configure_lists_affected_resources_and_preserves_unknown(self):
        info = self.info()
        result = self.project.configure_texture_group('Default', info['project_sha256'],
            mips_to_generate=4, border=8, autocrop=False, allow_scaling=False, dry_run=False)
        self.assertEqual({r['name'] for r in result['affected_resources']}, {'spr_one', 'spr_two'})
        self.assertEqual(result['after'], dict(mips_to_generate=4, border=8, autocrop=False, allow_scaling=False))
        self.assertEqual(self.project.metadata()['TextureGroups'][0]['future_field'], {'keep': 42})

    def test_invalid_parameters_and_mipmap_border_do_not_write(self):
        sha = self.info()['project_sha256']
        before = self.project.yyp.read_bytes()
        for options in ({'mips_to_generate': True}, {'mips_to_generate': -1}, {'mips_to_generate': 5},
                        {'border': 65}, {'border': 1.5}, {'autocrop': 1}, {'allow_scaling': 'yes'},
                        {'mips_to_generate': 4}, {'mips_to_generate': 1, 'border': 2}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.project.configure_texture_group('Default', sha, **options, dry_run=False)
        self.assertEqual(before, self.project.yyp.read_bytes())

    def test_readonly_preview_but_no_write(self):
        project = Project(self.root, writable=False)
        sha = self.info()['project_sha256']
        self.assertTrue(project.create_texture_group('Smooth', sha)['dry_run'])
        with self.assertRaisesRegex(ValueError, '只读'):
            project.create_texture_group('Smooth', sha, dry_run=False)

    def test_batch_preflights_all_resources_and_rolls_back_write_failure(self):
        self.create_group()
        args = self.assignment()
        args['expected_resource_sha256']['spr_two'] = '0' * 64
        paths = [f'sprites/{n}/{n}.yy' for n in args['names']]
        before = self.project.snapshot(paths)
        with self.assertRaises(ValueError):
            self.project.assign_sprite_texture_group(**args, dry_run=False)
        self.assertEqual(before, self.project.snapshot(paths))
        args = self.assignment()
        original = self.project._write
        failed = False
        def fail_second(path, raw):
            nonlocal failed
            if path == paths[1] and not failed:
                failed = True
                raise OSError('controlled write failure')
            return original(path, raw)
        with patch.object(self.project, '_write', side_effect=fail_second), self.assertRaises(OSError):
            self.project.assign_sprite_texture_group(**args, dry_run=False)
        self.assertEqual(before, self.project.snapshot(paths))

    def test_unsupported_groups_are_readable_but_not_editable(self):
        original = self.project.metadata()
        for change in ({'ConfigValues': {'Mobile': {'border': '4'}}}, {'loadType': 'dynamic'},
                       {'targets': 1}, {'resourceVersion': '1.3'}, {'mipsToGenerate': 12},
                       {'groupParent': {'name': 'Other', 'path': 'texturegroups/Other'}}):
            meta = deepcopy(original)
            meta['TextureGroups'][0].update(change)
            self.project.yyp.write_bytes(encode(meta))
            info = self.info()
            self.assertFalse(info['texture_groups'][0]['editable'])
            with self.assertRaises(ValueError):
                self.project.configure_texture_group('Default', info['project_sha256'], border=8)

    def test_duplicate_groups_bad_reference_and_overrides(self):
        original = self.project.metadata()
        meta = deepcopy(original)
        meta['TextureGroups'].append(new_group('default'))
        self.project.yyp.write_bytes(encode(meta))
        with self.assertRaisesRegex(ValueError, '重复'):
            self.info()
        self.project.yyp.write_bytes(encode(original))
        self.create_group()
        args = self.assignment()
        path = self.root / 'sprites/spr_one/spr_one.yy'
        data = self.project.read('sprites/spr_one/spr_one.yy')
        data['textureGroupId']['path'] = 'texturegroups/Bad'
        path.write_bytes(encode(data))
        with self.assertRaisesRegex(ValueError, '引用无效'):
            self.info()
        data['textureGroupId']['path'] = 'texturegroups/Default'
        data['ConfigValues'] = {'Other': {'textureGroupId': {'name': 'Default'}}}
        path.write_bytes(encode(data))
        with self.assertRaises(ValueError):
            self.project.assign_sprite_texture_group(**self.assignment(), dry_run=False)
        info = self.info()
        with self.assertRaisesRegex(ValueError, '配置覆盖'):
            self.project.configure_texture_group('Default', info['project_sha256'], border=8)
