"""Android 原生设置与图标的事务、路径和身份边界。"""
from pathlib import Path
import tempfile
from PIL import Image
from morx_gms2_mcp import android
from morx_gms2_mcp.runtime import key_codes
from morx_gms2_mcp.storage import digest, encode
from tests.test_core import ProjectFixture


def android_fixture():
    values = {name: False for name in android.FLAGS}
    values.update({name: '' for name in android.DEPENDENCIES})
    values.update(display_name='匿名测试', package_domain='org', package_company='example', package_product='probe',
                  version='1.0.0.0', orient_landscape=True, arch_arm64=True, texture_page='2048x2048',
                  scale=0, screen_depth=0, display_layout='LAYOUT_IN_DISPLAY_CUTOUT_MODE_DEFAULT', application_tag_inject='',
                  gradle_version='8.10.2', gradle_plugin_version='8.8.0')
    values.update(launchscreen_fill=0, splashscreen_background_colour=255, splash_time=0,
                  splash_screens_landscape='${base_options_dir}/android/splash/landscape.png',
                  splash_screens_portrait='${base_options_dir}/android/splash/portrait.png')
    data = {'$GMAndroidOptions': 'v1', '%Name': 'Android', 'name': 'Android',
            'resourceType': 'GMAndroidOptions', 'resourceVersion': '2.0', 'unknown': {'preserve': [1, 2]}}
    data.update({android.PREFIX + key: value for key, value in values.items()})
    for native in ('icon', 'icon_adaptive', 'icon_adaptivebg'):
        for density in android.DENSITIES:
            data[android.PREFIX + native + '_' + density] = '${base_options_dir}/android/template.png'
    return data


class AndroidTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        self.options = self.root / android.OPTIONS
        self.options.parent.mkdir(parents=True)
        raw = encode(android_fixture()).replace(b'\n}\n', b',\n}\n')
        self.options.write_bytes(b'\xef\xbb\xbf' + raw.replace(b'\n', b'\r\n'))
        self.image = self.root / 'source.png'
        Image.new('RGBA', (120, 60), (20, 100, 180, 255)).save(self.image)

    def sha(self):
        return digest(self.options.read_bytes())

    def test_metadata_preview_restore_and_native_format(self):
        raw = self.options.read_bytes()
        changes = {'display_name': '中文标题 café', 'version': '2.3.4.5', 'permission_internet': True,
                   'permission_record_audio': None, 'target_sdk': '35', 'compile_sdk': '35', 'minimum_sdk': '23'}
        preview = android.configure(self.project, changes, self.sha())
        self.assertEqual(raw, self.options.read_bytes())
        self.assertFalse(preview['settings']['permission_record_audio'])
        result = android.configure(self.project, changes, self.sha(), False)
        self.assertEqual(result['changed'], [android.OPTIONS])
        updated = self.options.read_bytes()
        self.assertTrue(updated.startswith(b'\xef\xbb\xbf'))
        self.assertTrue(updated.endswith(b',\r\n}\r\n'))
        self.assertEqual(self.project.read(android.OPTIONS)['unknown'], {'preserve': [1, 2]})
        self.assertEqual(android.inspect(self.project)['artifact_version']['versionCode'], None)
        self.project.restore(result['transaction'], False)
        self.assertEqual(raw, self.options.read_bytes())

    def test_package_identity_and_invalid_combinations_never_write(self):
        raw = self.options.read_bytes()
        for changes in ({'package_company': 'Bad_Name'}, {'package_product': '0game'}, {'version': '1.2.3'},
                        {'orient_landscape': False}, {'arch_arm64': False}, {'minimum_sdk': '36', 'target_sdk': '35'},
                        {'scale': 2}, {'compile_sdk': '/tmp/sdk'}, {'permission_internet': 1},
                        {'texture_page': '1024x512'}, {'versionCode': 9}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                android.configure(self.project, changes, self.sha(), False)
            self.assertEqual(raw, self.options.read_bytes())
        result = android.configure(self.project, {'package_company': 'other'}, self.sha())
        self.assertTrue(result['package_name_changed'])
        self.assertEqual(result['package_name'], 'org.other.probe')

    def test_gamepad_bluetooth_note_is_visible_without_silently_changing_options(self):
        raw=self.options.read_bytes()
        changes={'gamepad_support':True,'permission_bluetooth':False}
        preview=android.configure(self.project,changes,self.sha())
        self.assertEqual(preview['permission_notes'][0]['code'],'gamepad_may_add_bluetooth_permissions')
        self.assertFalse(preview['settings']['permission_bluetooth'])
        self.assertEqual(self.options.read_bytes(),raw)
        android.configure(self.project,changes,self.sha(),False)
        actual=android.inspect(self.project)
        self.assertTrue(actual['settings']['gamepad_support'])
        self.assertFalse(actual['settings']['permission_bluetooth'])
        self.assertTrue(actual['permission_notes'])
        disabled=android.configure(self.project,{'gamepad_support':False},self.sha())
        self.assertEqual(disabled['permission_notes'],[])
        self.assertTrue(android.inspect(self.project)['settings']['gamepad_support'])

    def test_conflict_override_unknown_structure_and_readonly(self):
        stale = self.sha()
        self.options.write_bytes(encode(android_fixture()))
        with self.assertRaises(ValueError):
            android.configure(self.project, {'display_name': 'new'}, stale, False)
        self.project.writable = False
        with self.assertRaisesRegex(ValueError, '只读'):
            android.configure(self.project, {'display_name': 'new'}, self.sha(), False)
        self.project.writable = True
        for changes in ({'ConfigValues': {'Release': {}}}, {'$GMAndroidOptions': 'v99'}):
            data = android_fixture(); data.update(changes)
            self.options.write_bytes(encode(data))
            self.assertFalse(android.inspect(self.project)['supported'])
            with self.assertRaises(ValueError):
                android.configure(self.project, {'display_name': 'new'}, self.sha(), False)

    def test_application_xml_cannot_override_security_or_native_fields(self):
        for text in ('android:debuggable="true"', 'android:usesCleartextTraffic="true"',
                     'android:label="different"', '<service/>', '<!DOCTYPE a>', 'android:theme="&bad;"'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                android.configure(self.project, {'application_tag_inject': text}, self.sha())
        self.assertTrue(android.configure(self.project, {'application_tag_inject': 'android:supportsRtl="true"'}, self.sha())['dry_run'])

    def test_icons_complete_sizes_aspect_ratio_conflicts_and_restore(self):
        original = self.options.read_bytes(); source = self.image.read_bytes()
        preview = android.import_icons(self.project, 'source.png', self.sha())
        self.assertEqual(len(preview['outputs']), 18)
        self.assertEqual(preview['changed'][-1], android.OPTIONS)
        self.assertFalse((self.root / 'options/android/morx_icons').exists())
        result = android.import_icons(self.project, 'source.png', self.sha(), dry_run=False)
        for row in result['outputs']:
            with Image.open(self.root / row['path']) as image:
                self.assertEqual(image.size, (row['width'], row['height']))
                if '/background/' not in row['path']:
                    left, top, right, bottom = image.getbbox()
                    self.assertAlmostEqual((right-left)/(bottom-top), 2, delta=.2)
        self.assertEqual(self.image.read_bytes(), source)
        with self.assertRaises(ValueError):
            android.import_icons(self.project, 'source.png', self.sha(), dry_run=False)
        hashes = {row['path']: row['sha256'] for row in result['outputs']}
        noop = android.import_icons(self.project, 'source.png', self.sha(), expected_icon_hashes=hashes, dry_run=False)
        self.assertEqual(noop['changed'], [])
        self.project.restore(result['transaction'], False)
        self.assertEqual(self.options.read_bytes(), original)
        self.assertFalse((self.root / 'options/android/morx_icons').exists())

    def test_icons_source_hash_and_path_boundary(self):
        raw = self.options.read_bytes()
        with tempfile.TemporaryDirectory() as external:
            image = Path(external) / 'external.png'
            Image.new('RGBA', (16, 16)).save(image)
            with self.assertRaises(ValueError):
                android.import_icons(self.project, str(image), self.sha(), dry_run=False)
        with self.assertRaises(ValueError):
            android.import_icons(self.project, 'source.png', self.sha(), expected_sources={'source.png': '0'*64}, dry_run=False)
        self.assertEqual(raw, self.options.read_bytes())
        for path in ('options/android/arbitrary.txt', 'options/android/morx_icons/other/ldpi.png',
                     'options/android/morx_icons/legacy/custom.png'):
            with self.assertRaises(ValueError):
                self.project._check_write_path(path)

    def test_splash_import_both_directions_and_restore_images_and_settings(self):
        original = self.options.read_bytes()
        existing = self.root / 'options/android/splash/portrait.png'
        existing.parent.mkdir()
        Image.new('RGB', (24, 48), 'white').save(existing)
        old_portrait = existing.read_bytes()
        for name, size in [('wide.png', (120, 60)), ('tall.png', (60, 120))]:
            Image.new('RGB', size, (31, 88, 166)).save(self.root / name)
        source_before = {p: (self.root / p).read_bytes() for p in ('wide.png', 'tall.png')}
        changes = {'launchscreen_fill': 0, 'splashscreen_background_colour': '#1234AB', 'splash_time': 3}
        args = dict(landscape_path='wide.png', portrait_path='tall.png', changes=changes)
        preview = android.import_splash(self.project, self.sha(), **args)
        self.assertIsNone(preview['transaction'])
        self.assertEqual(original, self.options.read_bytes())
        self.assertEqual(old_portrait, existing.read_bytes())
        self.assertEqual(preview['splash']['fill_mode'], 'keep_aspect_ratio')
        self.assertEqual(preview['splash']['border_color'], '#1234AB')
        self.assertEqual(preview['splash']['minimum_seconds'], 3)
        self.assertEqual([(row['width'], row['height']) for row in preview['outputs']], [(120, 60), (60, 120)])
        pins = {row['source_path']: row['source_sha256'] for row in preview['outputs']}
        overwrite = {row['path']: row['before_sha256'] for row in preview['outputs']}
        with self.assertRaisesRegex(ValueError, '目标启动图'):
            android.import_splash(self.project, self.sha(), **args, expected_sources=pins, dry_run=False)
        self.assertEqual(old_portrait, existing.read_bytes())
        result = android.import_splash(self.project, self.sha(), **args, expected_sources=pins,
                                       expected_splash_hashes=overwrite, dry_run=False)
        self.assertEqual(self.project.read(android.OPTIONS)[android.PREFIX+'splashscreen_background_colour'], 0xFFAB3412)
        info = android.inspect(self.project)['splash']
        for row in result['outputs']:
            self.assertEqual((self.root / row['path']).read_bytes(), source_before[row['source_path']])
            self.assertEqual(info['images'][row['orientation']]['sha256'], row['source_sha256'])
            self.assertEqual((self.root / row['source_path']).read_bytes(), source_before[row['source_path']])
        self.project.restore(result['transaction'], False)
        self.assertEqual(self.options.read_bytes(), original)
        self.assertEqual(existing.read_bytes(), old_portrait)
        self.assertFalse(existing.with_name('landscape.png').exists())

    def test_splash_source_changes_wrong_format_and_aliases_are_rejected(self):
        original = self.options.read_bytes()
        source = self.root / 'wide.png'
        Image.new('RGB', (80, 40), 'blue').save(source)
        preview = android.import_splash(self.project, self.sha(), landscape_path='wide.png')
        pins = {'wide.png': preview['outputs'][0]['source_sha256']}
        Image.new('RGB', (80, 40), 'green').save(source)
        with self.assertRaisesRegex(ValueError, '源 PNG.*哈希过期'):
            android.import_splash(self.project, self.sha(), landscape_path='wide.png', expected_sources=pins, dry_run=False)
        for path in ('source.png', str(source), '../wide.png'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                android.import_splash(self.project, self.sha(), landscape_path=path)
        path = self.root / 'options/android/splash/landscape.png'
        path.parent.mkdir()
        path.write_bytes(source.read_bytes())
        with self.assertRaisesRegex(ValueError, '目标文件分开'):
            android.import_splash(self.project, self.sha(), landscape_path=path.relative_to(self.root).as_posix())
        self.assertEqual(original, self.options.read_bytes())
        with self.assertRaises(ValueError):
            self.project._check_write_path('options/android/splash/other.png')

    def test_splash_settings_only_and_orientation_preservation(self):
        source = self.root / 'tall.png'
        Image.new('RGB', (40, 80), 'blue').save(source)
        preview = android.import_splash(self.project, self.sha(), portrait_path='tall.png')
        self.assertEqual(preview['splash']['images']['landscape']['configured_path'],
                         '${base_options_dir}/android/splash/landscape.png')
        result = android.configure(self.project, {'launchscreen_fill': 2, 'splash_time': 10,
                                   'splashscreen_background_colour': 0x00FF0000}, self.sha(), False)
        self.assertEqual(result['splash']['fill_mode'], 'stretch')
        self.assertEqual(result['splash']['minimum_seconds'], 10)
        self.assertEqual(result['splash']['border_color'], '#0000FF')
        for key, value in [('launchscreen_fill', 3), ('splash_time', -1), ('splash_time', 11),
                           ('splash_time', True), ('splashscreen_background_colour', '#12345678'),
                           ('splash_screens_landscape', 'arbitrary.png')]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                android.configure(self.project, {key: value}, self.sha(), False)

    def test_oem_aliases_numeric_codes_and_duplicates(self):
        for alias in ('OEM_3', 'grave', 'backtick', 'tilde', 192):
            self.assertEqual(key_codes([alias]), [192])
        self.assertEqual(key_codes(['oem_plus', 'left']), [187, 37])
        for values in (['grave', 'tilde'], [False], [0], [256]):
            with self.assertRaises(ValueError):
                key_codes(values)
