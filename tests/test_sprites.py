"""已有精灵编辑的字节保护、时间轴、非法输入及撤销验证。"""
from copy import deepcopy
from unittest.mock import patch
from PIL import Image
from morx_gms2_mcp.resources import Project
from morx_gms2_mcp.storage import encode
from tests.test_core import ProjectFixture


class SpriteTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        paths = []
        for index in range(3):
            path = self.root / f'frame{index}.png'
            Image.new('RGBA', (16, 12), (index * 80, 50, 100, 255)).save(path)
            paths.append(str(path))
        self.project.import_sprite('spr_edit', paths)
        self.relative = 'sprites/spr_edit/spr_edit.yy'
        self.file = self.root / self.relative
        self.info = self.project.inspect_sprite('spr_edit')
        self.ids = [frame['frame_id'] for frame in self.info['frames']]

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def test_inspect_does_not_change_files(self):
        before = self.snapshot()
        info = self.project.inspect_sprite('spr_edit')
        self.assertEqual((info['width'], info['height']), (16, 12))
        self.assertEqual(info['speed_type'], 'frames_per_second')
        self.assertEqual([frame['duration'] for frame in info['frames']], [1, 1, 1])
        self.assertEqual([frame['image_index'] for frame in info['frames']], [0, 1, 2])
        self.assertEqual(info['duration_unit'], 'animation_frames')
        self.assertEqual(len(info['sha256']), 64)
        self.assertEqual(before, self.snapshot())

    def test_preview_and_readonly_reject_writes(self):
        before = self.snapshot()
        project = Project(self.root, writable=False)
        self.assertTrue(project.configure_sprite('spr_edit', speed=20)['dry_run'])
        self.assertTrue(project.set_sprite_animation('spr_edit', frame_durations=[2, 1, 3])['dry_run'])
        for call in (lambda: project.configure_sprite('spr_edit', speed=20, dry_run=False),
                     lambda: project.set_sprite_animation('spr_edit', frame_order=self.ids[::-1], dry_run=False)):
            with self.assertRaises(ValueError):
                call()
        self.assertEqual(before, self.snapshot())

    def test_configure_only_changes_metadata_and_restores_bytes(self):
        before = self.snapshot()
        original = self.project.read(self.relative)
        result = self.project.configure_sprite('spr_edit', speed=0.5, speed_type='frames_per_game_frame',
                                               origin_x=-3, origin_y=20, dry_run=False)
        self.assertEqual(result['changed'], [self.relative])
        info = self.project.inspect_sprite('spr_edit')
        self.assertEqual(info['origin'], {'mode': 9, 'x': -3, 'y': 20})
        self.assertEqual((info['speed'], info['speed_type']), (0.5, 'frames_per_game_frame'))
        current = self.project.read(self.relative)
        self.assertEqual(current['frames'], original['frames'])
        self.assertEqual(current['bbox_top'], original['bbox_top'])
        for path, raw in before.items():
            if path != self.relative:
                self.assertEqual((self.root / path).read_bytes(), raw, path)
        self.project.restore(result['transaction'], dry_run=False)
        self.assertEqual(self.file.read_bytes(), before[self.relative])

    def test_partial_origin_and_speed_preserve_other_settings(self):
        self.project.configure_sprite('spr_edit', speed=0, origin_x=8, dry_run=False)
        info = self.project.inspect_sprite('spr_edit')
        self.assertEqual(info['speed'], 0)
        self.assertEqual(info['speed_type'], 'frames_per_second')
        self.assertEqual(info['origin']['y'], 0)
        self.assertEqual(info['length'], 3)

    def test_reorder_durations_keep_frame_ids_pngs_and_unknown_fields(self):
        data = self.project.read(self.relative)
        data['custom_field'] = {'keep': True}
        data['frames'][0]['custom_frame'] = 'keep'
        keys = data['sequence']['tracks'][0]['keyframes']['Keyframes']
        keys[0]['custom_key'] = 'keep'
        self.file.write_bytes(b'\xef\xbb\xbf' + encode(data).replace(b'\n', b'\r\n'))
        before = self.snapshot()
        result = self.project.set_sprite_animation('spr_edit', self.ids[::-1], [2, 1, 3.5], dry_run=False)
        self.assertEqual(result['changed'], [self.relative])
        info = self.project.inspect_sprite('spr_edit')
        self.assertEqual([f['frame_id'] for f in info['frames']], self.ids[::-1])
        self.assertEqual([f['start'] for f in info['frames']], [0, 2, 3])
        self.assertEqual([f['duration'] for f in info['frames']], [2, 1, 3.5])
        self.assertEqual(info['length'], 6.5)
        current = self.project.read(self.relative)
        self.assertEqual(current['custom_field'], {'keep': True})
        self.assertEqual(current['frames'][-1]['custom_frame'], 'keep')
        current_keys = current['sequence']['tracks'][0]['keyframes']['Keyframes']
        self.assertEqual(current_keys[-1]['custom_key'], 'keep')
        self.assertEqual({k['id'] for k in keys}, {k['id'] for k in current_keys})
        raw = self.file.read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))
        for path, raw in before.items():
            if path != self.relative:
                self.assertEqual((self.root / path).read_bytes(), raw, path)
        self.assertTrue(self.project.validate()['ok'])
        self.project.restore(result['transaction'], dry_run=False)
        self.assertEqual(self.file.read_bytes(), before[self.relative])

    def test_reorder_preserves_each_frames_duration(self):
        self.project.set_sprite_animation('spr_edit', frame_durations=[2, 3, 4], dry_run=False)
        self.project.set_sprite_animation('spr_edit', frame_order=self.ids[::-1], dry_run=False)
        self.assertEqual([f['duration'] for f in self.project.inspect_sprite('spr_edit')['frames']], [4, 3, 2])

    def test_noop_does_not_create_backup_or_reformat(self):
        raw = self.file.read_bytes()
        self.file.write_bytes(raw.replace(b'\n', b'\r\n'))
        before = self.snapshot()
        self.assertEqual(self.project.configure_sprite('spr_edit', speed=10, dry_run=False)['changed'], [])
        self.assertEqual(self.project.set_sprite_animation('spr_edit', dry_run=False)['changed'], [])
        self.assertEqual(self.project.set_sprite_animation('spr_edit', self.ids, [1, 1, 1], dry_run=False)['changed'], [])
        self.assertEqual(before, self.snapshot())

    def test_invalid_parameters_leave_all_files_unchanged(self):
        before = self.snapshot()
        for speed in (-1, float('nan'), float('inf'), True, '10', 10**400):
            with self.subTest(speed=str(speed)[:20]), self.assertRaises(ValueError):
                self.project.configure_sprite('spr_edit', speed=speed, dry_run=False)
        for value in (1.5, True, 2**31):
            with self.assertRaises(ValueError):
                self.project.configure_sprite('spr_edit', origin_x=value, dry_run=False)
        with self.assertRaises(ValueError):
            self.project.configure_sprite('spr_edit', speed_type='fps', dry_run=False)
        for order in ([], self.ids[:2], self.ids + [self.ids[0]], [self.ids[0]] * 3, ['missing'] * 3):
            with self.assertRaises(ValueError):
                self.project.set_sprite_animation('spr_edit', frame_order=order, dry_run=False)
        for durations in ([], [1, 2], [0, 1, 1], [-1, 1, 1], [True, 1, 1], [float('nan'), 1, 1],
                          [float('inf'), 1, 1], [1e308] * 3, [1e308, 1e-20, 1]):
            with self.assertRaises(ValueError):
                self.project.set_sprite_animation('spr_edit', frame_durations=durations, dry_run=False)
        self.assertEqual(before, self.snapshot())

    def test_stale_inspection_rejected_for_both_tools(self):
        self.project.configure_sprite('spr_edit', speed=12, expected_sha256=self.info['sha256'], dry_run=False)
        before = self.snapshot()
        for call in (lambda: self.project.configure_sprite('spr_edit', speed=14,
                         expected_sha256=self.info['sha256'], dry_run=False),
                     lambda: self.project.set_sprite_animation('spr_edit', frame_order=self.ids[::-1],
                         expected_sha256=self.info['sha256'], dry_run=False)):
            with self.assertRaisesRegex(ValueError, '重新 inspect_sprite'):
                call()
        self.assertEqual(before, self.snapshot())

    def test_events_preserved_by_configure_but_block_retiming(self):
        data = self.project.read(self.relative)
        data['sequence']['events']['Keyframes'] = [{'Key': 1, 'custom_event': 'keep'}]
        self.file.write_bytes(encode(data))
        self.project.configure_sprite('spr_edit', speed=12, dry_run=False)
        self.assertEqual(self.project.read(self.relative)['sequence']['events'], data['sequence']['events'])
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.project.set_sprite_animation('spr_edit', frame_durations=[1, 2, 1], dry_run=False)
        self.assertEqual(before, self.snapshot())

    def test_unsupported_or_damaged_timelines_rejected_without_writes(self):
        original = self.project.read(self.relative)
        def keys(data):
            return data['sequence']['tracks'][0]['keyframes']['Keyframes']
        variants = []
        for field, value in (('type', 2), ('$GMSprite', 'future')):
            data = deepcopy(original)
            data[field] = value
            variants.append(data)
        for field, value in (('Stretch', True), ('Disabled', True), ('Length', 0), ('Key', 1)):
            data = deepcopy(original)
            keys(data)[0][field] = value
            variants.append(data)
        data = deepcopy(original)
        data['sequence']['tracks'].append(deepcopy(data['sequence']['tracks'][0]))
        variants.append(data)
        data = deepcopy(original)
        keys(data)[0]['Channels']['0']['Id']['name'] = self.ids[1]
        variants.append(data)
        data = deepcopy(original)
        data['sequence']['length'] = 99
        variants.append(data)
        data = deepcopy(original)
        del data['sequence']['tracks'][0]['keyframes']
        variants.append(data)
        for data in variants:
            self.file.write_bytes(encode(data))
            before = self.snapshot()
            with self.assertRaises(ValueError):
                self.project.configure_sprite('spr_edit', speed=12, dry_run=False)
            with self.assertRaises(ValueError):
                self.project.set_sprite_animation('spr_edit', frame_order=self.ids[::-1], dry_run=False)
            self.assertEqual(before, self.snapshot())

    def test_missing_image_is_not_silently_accepted(self):
        (self.file.parent / f'{self.ids[0]}.png').unlink()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, '缺少帧图像'):
            self.project.configure_sprite('spr_edit', speed=12, dry_run=False)
        self.assertEqual(before, self.snapshot())

    def test_external_change_during_edit_detected(self):
        original_commit = self.project.commit
        external = self.file.read_bytes() + b' '
        def conflict(changes, before, **kwargs):
            self.file.write_bytes(external)
            return original_commit(changes, before, **kwargs)
        with patch.object(self.project, 'commit', side_effect=conflict):
            with self.assertRaisesRegex(ValueError, '外部修改'):
                self.project.configure_sprite('spr_edit', speed=12, dry_run=False)
        self.assertEqual(self.file.read_bytes(), external)

    def test_project_validation_reports_invalid_animation_timing(self):
        original = self.project.read(self.relative)
        for value in (0, -1, float('nan'), 100):
            data = deepcopy(original)
            data['sequence']['tracks'][0]['keyframes']['Keyframes'][0]['Length'] = value
            self.file.write_bytes(encode(data))
            self.assertFalse(self.project.validate()['ok'])
