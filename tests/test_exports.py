"""导出设置、ICO 完整性与批量音频目录的事务边界。"""
import io
import struct
from unittest.mock import patch
from PIL import Image
from morx_gms2_mcp import audio, exports
from morx_gms2_mcp.storage import encode
from tests.test_core import ProjectFixture
from tests.test_display import windows_fixture
from tests.test_audio import make_wav


class ExportTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        self.windows = self.root / exports.WINDOWS_PATH
        self.windows.parent.mkdir(parents=True)
        data = windows_fixture()
        data.update(option_windows_version='1.0.0.0', option_windows_company_info='Template',
                    option_windows_icon='${base_options_dir}/windows/icons/icon.ico')
        self.windows.write_bytes(b'\xef\xbb\xbf' + encode(data).replace(b'\n', b'\r\n'))
        self.png = self.root / 'source.png'
        image = Image.new('RGBA', (160, 120))
        image.paste((40, 160, 220, 255), (40, 40, 120, 80))
        image.save(self.png)

    def sha(self):
        return exports.inspect_windows(self.project)['sha256']

    def test_metadata_preview_unicode_noop_conflict_and_exact_restore(self):
        original = self.windows.read_bytes()
        values = dict(executable_name='Export Probe.exe', display_name='测试游戏', version='1.2.3.4',
                      company='', copyright='', product='导出验证', description='Unicode 描述 café')
        exports.configure_windows(self.project, self.sha(), **values)
        self.assertEqual(original, self.windows.read_bytes())
        sha = self.sha()
        tx = exports.configure_windows(self.project, sha, False, **values)
        self.assertEqual(exports.inspect_windows(self.project)['settings']['company'], '')
        self.assertEqual(self.project.read(exports.WINDOWS_PATH)['custom_unknown'], {'keep': True})
        self.assertTrue(self.windows.read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertIn(b'\r\n', self.windows.read_bytes())
        with self.assertRaisesRegex(ValueError, '哈希过期'):
            exports.configure_windows(self.project, sha, False, product='stale')
        self.assertEqual(exports.configure_windows(self.project, self.sha(), False, **values)['changed'], [])
        self.project.restore(tx['transaction'], False)
        self.assertEqual(original, self.windows.read_bytes())

    def test_invalid_metadata_and_overrides_do_not_write(self):
        original = self.windows.read_bytes()
        for key, value in [('executable_name', x) for x in ('../bad.exe', 'CON.exe', '.exe', 'bad.dll', 'x?.exe')]+[
                ('version', v) for v in ('1.2.3', '1.2.3.65536', '1.2.3.-1', '01.2.3.4')]+[('display_name','')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                exports.configure_windows(self.project, self.sha(), False, **{key: value})
        self.assertEqual(original, self.windows.read_bytes())
        data = self.project.read(exports.WINDOWS_PATH); data['ConfigValues'] = {'Release': {'x': 1}}
        self.windows.write_bytes(encode(data))
        self.assertTrue(exports.inspect_windows(self.project)['configuration_overrides'])
        with self.assertRaisesRegex(ValueError, '覆盖'):
            exports.configure_windows(self.project, self.sha(), False, company='')

    def test_native_trailing_commas_bom_and_line_endings_are_preserved(self):
        raw=self.windows.read_bytes().replace(b'\r\n}\r\n',b',\r\n}\r\n')
        self.windows.write_bytes(raw)
        result=exports.configure_windows(self.project,self.sha(),False,company='中文')
        updated=self.windows.read_bytes()
        self.assertTrue(updated.startswith(b'\xef\xbb\xbf'))
        self.assertTrue(updated.endswith(b',\r\n}\r\n'))
        self.assertEqual(self.project.read(exports.WINDOWS_PATH)['option_windows_company_info'],'中文')
        self.project.restore(result['transaction'],False)
        self.assertEqual(self.windows.read_bytes(),raw)

    def test_png_icon_sizes_preview_hash_guard_and_atomic_restore(self):
        source = self.png.read_bytes(); original = self.windows.read_bytes(); sha = self.sha()
        preview = exports.import_icon(self.project, str(self.png), sha, trim_transparency=True)
        self.assertEqual(preview['sizes'], exports.ICON_SIZES)
        self.assertEqual(list(preview['transform']['crop_box']), [40, 40, 120, 80])
        self.assertFalse((self.root / preview['target_path']).exists())
        result = exports.import_icon(self.project, str(self.png), sha, trim_transparency=True, dry_run=False)
        icon = self.root / result['target_path']
        self.assertEqual(exports.ico_sizes(icon.read_bytes()), exports.ICON_SIZES)
        with Image.open(icon) as image:
            self.assertEqual(image.getbbox(), (16, 72, 240, 184))
        self.assertEqual(self.png.read_bytes(), source)
        info = exports.inspect_windows(self.project)
        self.assertEqual(info['icon']['sha256'], result['target_sha256'])
        with self.assertRaisesRegex(ValueError, '目标图标哈希过期'):
            exports.import_icon(self.project, str(self.png), self.sha(), dry_run=False)
        self.assertEqual(exports.import_icon(self.project, str(self.png), self.sha(),
            expected_icon_sha256=info['icon']['sha256'], trim_transparency=True, dry_run=False)['changed'], [])
        self.project.restore(result['transaction'], False)
        self.assertFalse(icon.exists())
        self.assertEqual(self.windows.read_bytes(), original)

    def test_ico_checks_every_image_directory_and_rejects_damage(self):
        buffer = io.BytesIO()
        Image.new('RGBA', (256, 256), 'red').save(buffer, format='ICO', sizes=[(16,16),(256,256)])
        raw = buffer.getvalue()
        self.assertEqual(exports.ico_sizes(raw), [16,256])
        broken = bytearray(raw)
        # 破坏较小图，不能被默认只解码最大帧的检查漏过。
        length, offset = struct.unpack_from('<II', broken, 14)
        broken[offset:offset+length] = b'x' * length
        for data in (b'bad', raw[:-10], bytes(broken), b'\0\0\2\0'+raw[4:]):
            with self.assertRaises((ValueError, OSError)):
                exports.ico_sizes(data)
        ico = self.root / 'prepared.ico'; ico.write_bytes(raw)
        result = exports.import_icon(self.project, str(ico), self.sha(), dry_run=False)
        self.assertEqual(self.project.path(result['target_path']).read_bytes(), raw)
        buffer=io.BytesIO()
        Image.new('RGBA',(32,32),'blue').save(buffer,format='ICO',bitmap_format='bmp',sizes=[(32,32)])
        self.assertEqual(exports.ico_sizes(buffer.getvalue()),[32])

    def test_icon_readonly_path_boundary_and_failed_transaction(self):
        for path in ('../icon.ico','options/windows/icons/../icon.ico','datafiles/icon.ico','options/windows/icons/x.png'):
            with self.assertRaises(ValueError):
                exports.import_icon(self.project,str(self.png),self.sha(),destination=path,dry_run=False)
        self.project.writable=False
        self.assertTrue(exports.import_icon(self.project,str(self.png),self.sha())['dry_run'])
        with self.assertRaises(ValueError):
            exports.import_icon(self.project,str(self.png),self.sha(),dry_run=False)
        self.project.writable=True
        original=self.windows.read_bytes()
        write=self.project._write
        def fail(path,raw):
            if path==exports.WINDOWS_PATH and raw!=original:raise OSError('simulated disk write failure')
            return write(path,raw)
        with patch.object(self.project,'_write',side_effect=fail),self.assertRaises(OSError):
            exports.import_icon(self.project,str(self.png),self.sha(),dry_run=False)
        self.assertFalse(self.project.path('options/windows/icons/morx.ico').exists())
        self.assertEqual(self.windows.read_bytes(),original)


class AudioExportTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        meta=self.project.metadata()
        meta['AudioGroups'][0].update({'$GMAudioGroup':'v1','resourceType':'GMAudioGroup','exportDir':'','targets':-1})
        self.project.yyp.write_bytes(encode(meta))
        wav=make_wav(self.root/'probe.wav')
        for name in ('snd_a','snd_b'):
            self.project.import_sound(name,str(wav),compression='streamed',conversion_mode='automatic',volume=.25)

    def rows(self, directory):
        return [{'name':name,'expected_sha256':self.project.inspect_sound(name)['sha256'],'export_directory':directory}
                for name in ('snd_a','snd_b')]

    def test_streamed_batch_directory_and_group_precedence_restore(self):
        originals={name:self.project.inspect_sound(name) for name in ('snd_a','snd_b')}
        preview=audio.configure_export(self.project,self.rows('audio\\bgm'))
        self.assertTrue(preview['dry_run'])
        tx=audio.configure_export(self.project,self.rows('audio\\bgm'),dry_run=False)
        for name, before in originals.items():
            now=self.project.inspect_sound(name)
            self.assertEqual(now['effective_export_directory'],'audio/bgm')
            self.assertEqual(now['source_file'],before['source_file'])
            self.assertEqual({k:v for k,v in now['settings'].items() if k!='export_directory'},
                             {k:v for k,v in before['settings'].items() if k!='export_directory'})
        added=self.project.create_audio_group('audio_group_probe',self.project.inspect_audio_groups()['project_sha256'],False)
        info=self.project.inspect_sound('snd_a')
        assigned=self.project.configure_sound('snd_a',info['sha256'],compression='compressed',audio_group='audio_group_probe',dry_run=False)
        groups=self.project.inspect_audio_groups()
        group=groups['audio_groups'][1]
        group_tx=audio.configure_export(self.project,audio_groups=[{'name':group['name'],
            'expected_sha256':group['sha256'],'export_directory':'group_audio'}],
            expected_project_sha256=groups['project_sha256'],dry_run=False)
        self.assertEqual(self.project.inspect_sound('snd_a')['effective_export_directory'],'group_audio')
        self.assertEqual(self.project.inspect_sound('snd_b')['effective_export_directory'],'audio/bgm')
        self.assertEqual(self.project.inspect_sound('snd_a')['settings']['export_directory'],'audio/bgm')
        self.project.restore(group_tx['transaction'],False)
        self.project.restore(assigned['transaction'],False)
        self.project.restore(added['transaction'],False)
        self.project.restore(tx['transaction'],False)
        self.assertEqual(self.project.inspect_sound('snd_a')['effective_export_directory'],'')

    def test_conflict_invalid_directory_and_single_setting_are_guarded(self):
        rows=self.rows('audio'); rows[1]['expected_sha256']='0'*64
        before=self.project.inspect_sound('snd_a')['sha256']
        with self.assertRaises(ValueError):audio.configure_export(self.project,rows,dry_run=False)
        self.assertEqual(self.project.inspect_sound('snd_a')['sha256'],before)
        for directory in ('../audio','C:/audio','//host/path','audio/../../x','audio/NUL','audio/hidden.'):
            with self.subTest(directory=directory),self.assertRaises(ValueError):audio.export_directory(directory)
        result=self.project.configure_sound('snd_a',before,export_directory='audio',dry_run=False)
        self.assertEqual(result['settings']['export_directory'],'audio')
        info=self.project.inspect_sound('snd_a')
        self.assertEqual(self.project.configure_sound('snd_a',info['sha256'],export_directory='audio',dry_run=False)['changed'],[])
        self.project.configure_sound('snd_a',info['sha256'],export_directory='',dry_run=False)
        self.assertEqual(self.project.inspect_sound('snd_a')['effective_export_directory'],'')

    def test_default_group_directory_is_disabled_and_overrides_are_not_silently_ignored(self):
        groups=self.project.inspect_audio_groups();default=groups['audio_groups'][0]
        self.assertFalse(default['export_directory_enabled'])
        with self.assertRaisesRegex(ValueError,'默认音频组'):
            audio.configure_export(self.project,audio_groups=[{'name':default['name'],
                'expected_sha256':default['sha256'],'export_directory':'ignored'}],
                expected_project_sha256=groups['project_sha256'],dry_run=False)
        path=self.project.path('sounds/snd_a/snd_a.yy')
        data=self.project.read('sounds/snd_a/snd_a.yy');data['ConfigValues']={'Release':{'exportDir':'release'}}
        path.write_bytes(encode(data));info=self.project.inspect_sound('snd_a')
        self.assertIsNone(info['effective_export_directory'])
        with self.assertRaisesRegex(ValueError,'覆盖'):
            self.project.configure_sound('snd_a',info['sha256'],export_directory='audio',dry_run=False)
