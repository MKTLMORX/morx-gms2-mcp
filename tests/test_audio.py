"""声音配置和尺寸替换的事务、冲突与格式回归；不启动游戏。"""
import copy
import wave
from unittest.mock import patch

from PIL import Image, ImageDraw
from morx_gms2_mcp.storage import encode, digest
from tests.test_core import ProjectFixture


def make_wav(path):
    with wave.open(str(path), 'wb') as writer:
        writer.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
        writer.writeframes(b'\0' * 4410 * 4)
    return path


class AudioTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        self.wav = make_wav(self.root / 'source.wav')
        self.project.import_sound('snd_demo', str(self.wav), group='声音/效果')
        self.descriptor = self.root / 'sounds/snd_demo/snd_demo.yy'

    def test_inspect_and_compatible_import_defaults(self):
        info = self.project.inspect_sound('snd_demo')
        self.assertEqual(info['settings']['compression'], 'uncompressed')
        self.assertFalse(info['settings']['preload'])
        self.assertEqual(info['settings']['channel_format'], 'stereo')
        self.assertEqual(info['sha256'], digest(self.descriptor.read_bytes()))
        self.assertEqual(info['source_file']['sha256'], digest(self.wav.read_bytes()))
        self.assertEqual(info['audio_groups'], ['audiogroup_default'])
        self.assertIn('audio_group_load', info['notes'][0])

    def test_import_all_modes_and_target_settings_do_not_convert_source(self):
        original = self.wav.read_bytes()
        for index, mode in enumerate(['uncompressed', 'compressed', 'decompress_on_load', 'streamed']):
            name = 'snd_mode_' + str(index)
            result = self.project.import_sound(name, str(self.wav), compression=mode, preload=True,
                compression_quality=8, sample_rate=22050, channel_format='mono', bit_depth=8,
                conversion_mode='required')
            info = self.project.inspect_sound(name)
            self.assertEqual(info['settings']['compression'], mode)
            self.assertEqual(info['settings']['sample_rate'], 22050)
            self.assertEqual(info['settings']['channel_format'], 'mono')
            self.assertEqual(info['settings']['bit_depth'], 8)
            self.assertTrue(info['settings']['preload'])
            self.assertEqual(result['sample_rate'], 44100)  # 返回的源信息与目标设置有区分。
            self.assertEqual((self.root/f'sounds/{name}/{name}.wav').read_bytes(), original)
            self.assertEqual(self.project.read(f'sounds/{name}/{name}.yy')['compression'], index)
        self.assertEqual(self.wav.read_bytes(), original)

    def test_configure_preview_identity_encoding_conflict_and_exact_restore(self):
        data = self.project.read('sounds/snd_demo/snd_demo.yy')
        data['future_setting'] = {'preserve': [1, 2]}
        self.descriptor.write_bytes(b'\xef\xbb\xbf' + encode(data).replace(b'\n', b'\r\n'))
        original = self.descriptor.read_bytes()
        manifest = self.project.yyp.read_bytes()
        info = self.project.inspect_sound('snd_demo')
        preview = self.project.configure_sound('snd_demo', info['sha256'], compression='streamed',
                    preload=True, volume=.25, compression_quality=7)
        self.assertEqual(self.descriptor.read_bytes(), original)
        self.assertEqual(preview['before']['compression'], 'uncompressed')
        self.assertEqual(preview['settings']['compression'], 'streamed')
        result = self.project.configure_sound('snd_demo', info['sha256'], compression='streamed',
                    preload=True, volume=.25, compression_quality=7, dry_run=False)
        updated = self.project.read('sounds/snd_demo/snd_demo.yy')
        for key in ['name', '%Name', 'parent', 'soundFile', 'future_setting']:
            self.assertEqual(updated[key], data[key])
        self.assertTrue(self.descriptor.read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertIn(b'\r\n', self.descriptor.read_bytes())
        self.assertEqual(self.project.yyp.read_bytes(), manifest)
        self.assertEqual(result['changed'], ['sounds/snd_demo/snd_demo.yy'])
        with self.assertRaisesRegex(ValueError, '已被修改'):
            self.project.configure_sound('snd_demo', info['sha256'], volume=.5, dry_run=False)
        self.project.restore(result['transaction'], False)
        self.assertEqual(self.descriptor.read_bytes(), original)

    def test_audio_group_validation_and_explicit_stream_transition(self):
        meta = self.project.metadata()
        meta['AudioGroups'].append({'name': 'audiogroup_sfx'})
        self.project.yyp.write_bytes(encode(meta))
        info = self.project.inspect_sound('snd_demo')
        self.project.configure_sound('snd_demo', info['sha256'], audio_group='audiogroup_sfx', dry_run=False)
        info = self.project.inspect_sound('snd_demo')
        with self.assertRaisesRegex(ValueError, 'streamed'):
            self.project.configure_sound('snd_demo', info['sha256'], compression='streamed', dry_run=False)
        with self.assertRaisesRegex(ValueError, '未唯一存在'):
            self.project.configure_sound('snd_demo', info['sha256'], audio_group='missing', dry_run=False)
        self.project.configure_sound('snd_demo', info['sha256'], compression='streamed',
                                     audio_group='audiogroup_default', dry_run=False)
        self.assertEqual(self.project.inspect_sound('snd_demo')['settings']['audio_group'], 'audiogroup_default')

    def test_invalid_options_do_not_create_resource(self):
        for options in [{'volume': True}, {'volume': float('nan')}, {'volume': 1.01},
                        {'compression': 3}, {'compression': 'unknown'}, {'preload': 1},
                        {'compression_quality': -1}, {'compression_quality': 11}, {'compression_quality': True},
                        {'sample_rate': 7999}, {'channel_format': 'surround'}, {'bit_depth': 24},
                        {'conversion_mode': 'invalid'}, {'audio_group': '../bad'}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.project.import_sound('snd_invalid', str(self.wav), **options)
            self.assertFalse((self.root/'sounds/snd_invalid').exists())

    def test_configuration_overrides_and_unknown_formats_are_not_overwritten(self):
        data = self.project.read('sounds/snd_demo/snd_demo.yy')
        data['ConfigValues'] = {'mobile': {'volume': '0.4'}}
        self.descriptor.write_bytes(encode(data))
        info = self.project.inspect_sound('snd_demo')
        self.assertTrue(info['configuration_overrides'])
        with self.assertRaisesRegex(ValueError, '配置覆盖'):
            self.project.configure_sound('snd_demo', info['sha256'], volume=.5)
        for field, value in [('compression', 99), ('$GMSound', 'v3')]:
            changed = {**data, field: value}
            self.descriptor.write_bytes(encode(changed))
            with self.assertRaises(ValueError):
                self.project.inspect_sound('snd_demo')

    def test_missing_source_noop_readonly_and_unsafe_filename(self):
        info = self.project.inspect_sound('snd_demo')
        noop = self.project.configure_sound('snd_demo', info['sha256'], dry_run=False)
        self.assertEqual(noop['changed'], [])
        self.project.writable = False
        self.assertTrue(self.project.configure_sound('snd_demo', info['sha256'], volume=.2)['dry_run'])
        with self.assertRaisesRegex(ValueError, '只读'):
            self.project.configure_sound('snd_demo', info['sha256'], volume=.2, dry_run=False)
        (self.root/'sounds/snd_demo/snd_demo.wav').unlink()
        self.assertFalse(self.project.inspect_sound('snd_demo')['source_file']['exists'])
        with self.assertRaisesRegex(ValueError, '缺少源文件'):
            self.project.configure_sound('snd_demo', info['sha256'], volume=.2)
        data = self.project.read('sounds/snd_demo/snd_demo.yy')
        data['soundFile'] = '../outside.wav'
        self.descriptor.write_bytes(encode(data))
        with self.assertRaisesRegex(ValueError, '源文件名'):
            self.project.inspect_sound('snd_demo')


class SpriteResizeTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        self.png = self.root/'input.png'
        Image.new('RGBA', (8, 4), (10, 20, 30, 255)).save(self.png)
        self.project.import_sprite('spr_resize', [str(self.png)]*2, origin_x=2, origin_y=1)
        self.project.set_sprite_animation('spr_resize', frame_durations=[2, 3], dry_run=False)
        self.descriptor = self.root/'sprites/spr_resize/spr_resize.yy'

    def replace(self, **options):
        info = self.project.inspect_sprite('spr_resize')
        return self.project.replace_sprite_images('spr_resize', [str(self.png)]*2,
                    info['sha256'], info['image_sha256'], allow_resize=True, **options)

    def test_resize_one_transaction_keeps_references_ids_timing_and_exact_restore(self):
        data = self.project.read('sprites/spr_resize/spr_resize.yy')
        original = {p: p.read_bytes() for p in self.descriptor.parent.rglob('*') if p.is_file()}
        project = self.project.yyp.read_bytes()
        Image.new('RGBA', (12, 6), (100, 80, 60, 255)).save(self.png)
        preview = self.replace(origin_policy='scale', bbox_policy='full_image')
        self.assertEqual({p: p.read_bytes() for p in original}, original)
        self.assertEqual(preview['geometry']['after']['origin'], {'mode': 9, 'x': 3, 'y': 2})
        result = self.replace(origin_policy='scale', bbox_policy='full_image', dry_run=False)
        changed = self.project.read('sprites/spr_resize/spr_resize.yy')
        for field in ['name', '%Name', 'frames', 'layers', 'parent', 'textureGroupId']:
            self.assertEqual(changed[field], data[field])
        sequence = copy.deepcopy(changed['sequence'])
        sequence['xorigin'], sequence['yorigin'] = 2, 1
        self.assertEqual(sequence, data['sequence'])
        self.assertEqual(self.project.yyp.read_bytes(), project)
        self.assertEqual(set(original), {p for p in self.descriptor.parent.rglob('*') if p.is_file()})
        self.project.restore(result['transaction'], False)
        self.assertEqual({p: p.read_bytes() for p in original}, original)

    def test_shrink_manual_bbox_requires_valid_keep_or_explicit_policy(self):
        data = self.project.read('sprites/spr_resize/spr_resize.yy')
        data.update(bboxMode=2, bbox_left=2, bbox_right=6, bbox_top=1, bbox_bottom=3)
        self.descriptor.write_bytes(encode(data))
        Image.new('RGBA', (4, 2), (30, 40, 50, 255)).save(self.png)
        before = self.descriptor.read_bytes()
        with self.assertRaisesRegex(ValueError, '手动碰撞框超出'):
            self.replace(dry_run=False)
        self.assertEqual(self.descriptor.read_bytes(), before)
        result = self.replace(bbox_policy='scale', dry_run=False)
        self.assertEqual(result['geometry']['after']['bbox'], {'mode': 2, 'left': 1, 'right': 3, 'top': 0, 'bottom': 1})
        self.assertEqual(result['geometry']['after']['origin']['x'], 2)

    def test_automatic_bbox_uses_union_alpha_threshold_and_updates_same_size(self):
        data = self.project.read('sprites/spr_resize/spr_resize.yy')
        data.update(bboxMode=0, collisionTolerance=128)
        self.descriptor.write_bytes(encode(data))
        frame_a, frame_b = self.root/'frame_a.png', self.root/'frame_b.png'
        a, b = Image.new('RGBA', (12, 6)), Image.new('RGBA', (12, 6))
        a.putpixel((1, 1), (1, 2, 3, 128))  # 等于阈值，不应扩展碰撞框。
        ImageDraw.Draw(a).rectangle((3, 2, 4, 3), fill=(1, 2, 3, 255))
        ImageDraw.Draw(b).rectangle((8, 4, 10, 5), fill=(1, 2, 3, 255))
        a.save(frame_a)
        b.save(frame_b)
        info = self.project.inspect_sprite('spr_resize')
        result = self.project.replace_sprite_images('spr_resize', [str(frame_a), str(frame_b)],
            info['sha256'], info['image_sha256'], allow_resize=True, dry_run=False)
        self.assertEqual(result['geometry']['after']['bbox'], {'mode': 0, 'left': 3, 'top': 2, 'right': 10, 'bottom': 5})
        a, b = Image.new('RGBA', (12, 6)), Image.new('RGBA', (12, 6))
        a.putpixel((4, 1), (1, 2, 3, 255))
        b.putpixel((8, 3), (1, 2, 3, 255))
        a.save(frame_a)
        b.save(frame_b)
        info = self.project.inspect_sprite('spr_resize')
        result = self.project.replace_sprite_images('spr_resize', [str(frame_a), str(frame_b)],
            info['sha256'], info['image_sha256'], dry_run=False)
        self.assertFalse(result['resized'])
        self.assertEqual(result['geometry']['after']['bbox'], {'mode': 0, 'left': 4, 'top': 1, 'right': 8, 'bottom': 3})

    def test_empty_automatic_bbox_refuses_to_invent_collision_area(self):
        data = self.project.read('sprites/spr_resize/spr_resize.yy')
        data['bboxMode'] = 0
        self.descriptor.write_bytes(encode(data))
        original = self.descriptor.read_bytes()
        Image.new('RGBA', (12, 6), (0, 0, 0, 0)).save(self.png)
        with self.assertRaisesRegex(ValueError, '空碰撞框'):
            self.replace(dry_run=False)
        self.assertEqual(original, self.descriptor.read_bytes())
        self.assertTrue(self.replace(bbox_policy='full_image', dry_run=False)['resized'])

    def test_resize_refuses_unsupported_metadata_and_mismatched_frames(self):
        data = self.project.read('sprites/spr_resize/spr_resize.yy')
        Image.new('RGBA', (12, 6)).save(self.png)
        for extra in [{'nineSlice': {'enabled': True}}, {'ConfigValues': {'mobile': {'width': '4'}}}]:
            self.descriptor.write_bytes(encode({**data, **extra}))
            with self.assertRaises(ValueError):
                self.replace(dry_run=False)
        self.descriptor.write_bytes(encode(data))
        other = self.root/'other.png'
        Image.new('RGBA', (13, 6)).save(other)
        info = self.project.inspect_sprite('spr_resize')
        with self.assertRaisesRegex(ValueError, '等尺寸'):
            self.project.replace_sprite_images('spr_resize', [str(self.png), str(other)],
                info['sha256'], info['image_sha256'], allow_resize=True, dry_run=False)

    def test_mid_resize_write_failure_rolls_back_images_and_descriptor(self):
        original = {p: p.read_bytes() for p in self.descriptor.parent.rglob('*') if p.is_file()}
        Image.new('RGBA', (12, 6)).save(self.png)
        write = self.project._write
        failed = [False]
        def fault(path, raw):
            if path == 'sprites/spr_resize/spr_resize.yy' and not failed[0]:
                failed[0] = True
                raise OSError('injected failure')
            return write(path, raw)
        with patch.object(self.project, '_write', side_effect=fault), self.assertRaises(OSError):
            self.replace(dry_run=False)
        self.assertEqual({p: p.read_bytes() for p in original}, original)
