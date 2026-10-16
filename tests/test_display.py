"""显示设置的格式、权限边界、独立尺寸及原字节撤销。"""
from copy import deepcopy
from unittest.mock import patch
from morx_gms2_mcp import schemas
from morx_gms2_mcp.display import WINDOWS_PATH, WINDOWS_FLAGS
from morx_gms2_mcp.resources import Project
from morx_gms2_mcp.storage import encode
from tests.test_core import ProjectFixture


def windows_fixture():
    return schemas.typed('GMWindowsOptions', 'v2', name='Windows', **{'%Name': 'Windows'},
        option_windows_display_name='Display Test', option_windows_start_fullscreen=False,
        option_windows_borderless=False, option_windows_resize_window=False,
        option_windows_allow_fullscreen_switching=False, option_windows_display_cursor=True,
        option_windows_vsync=True, option_windows_interpolate_pixels=True,
        option_windows_scale=0, option_windows_disable_sandbox=False,
        option_windows_executable_name='test.exe', custom_unknown={'keep': True})


class DisplayTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        self.windows = self.root / WINDOWS_PATH
        self.windows.parent.mkdir(parents=True)
        self.windows.write_bytes(encode(windows_fixture()))
        self.room_path = 'rooms/Room1/Room1.yy'
        self.room_file = self.root / self.room_path
        self.room_file.write_bytes(encode(schemas.room('Room1', 800, 450)))

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def test_inspect_separates_windows_room_camera_and_viewport(self):
        self.project.ensure_group('房间')  # 提前创建锁，之后读取应无额外文件变化。
        before = self.snapshot()
        info = self.project.inspect_display_settings()
        self.assertEqual(info['windows']['settings']['title'], 'Display Test')
        self.assertTrue(info['windows']['supported'])
        self.assertEqual(info['start_room'], 'Room1')
        self.assertTrue(info['room']['is_start_room'])
        self.assertEqual(info['room']['settings']['room_width'], 800)
        self.assertEqual(len(info['room']['settings']['views']), 8)
        self.assertEqual(info['room']['settings']['views'][0]['camera']['width'], 800)
        self.assertEqual(info['room']['settings']['views'][0]['viewport']['height'], 450)
        self.assertFalse(info['room']['settings']['enable_views'])
        self.assertEqual(before, self.snapshot())

    def test_inspect_missing_or_unsupported_files_does_not_create_them(self):
        self.windows.unlink()
        self.room_file.write_bytes(encode({'resourceType': 'GMRoom', 'name': 'Room1'}))
        info = self.project.inspect_display_settings()
        self.assertFalse(info['windows']['available'])
        self.assertFalse(info['room']['supported'])
        self.assertFalse(self.windows.exists())
        self.windows.write_bytes(encode({'resourceType': 'GMWindowsOptions'}))
        self.assertFalse(self.project.inspect_display_settings()['windows']['supported'])

    def test_windows_edit_preserves_unrelated_fields_and_restores_bytes(self):
        self.windows.write_bytes(b'\xef\xbb\xbf' + encode(windows_fixture()).replace(b'\n', b'\r\n'))
        before = self.windows.read_bytes()
        original_room = self.room_file.read_bytes()
        options = {'title': '窗口测试', 'start_fullscreen': True, 'borderless': True,
            'resizable': False, 'allow_fullscreen_switching': True, 'display_cursor': False,
            'vsync': False, 'interpolate_pixels': False}
        result = self.project.configure_windows_display(**options, dry_run=False)
        self.assertEqual(result['changed'], [WINDOWS_PATH])
        current = self.project.read(WINDOWS_PATH)
        self.assertEqual(self.project.inspect_display_settings()['windows']['settings'], options)
        self.assertEqual(current['custom_unknown'], {'keep': True})
        self.assertFalse(current['option_windows_disable_sandbox'])
        self.assertEqual(current['option_windows_scale'], 0)
        self.assertEqual(current['option_windows_executable_name'], 'test.exe')
        raw = self.windows.read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))
        self.assertEqual(self.room_file.read_bytes(), original_room)
        self.assertTrue(self.project.validate()['ok'])
        self.project.restore(result['transaction'], dry_run=False)
        self.assertEqual(self.windows.read_bytes(), before)

    def test_previews_and_readonly_service_leave_resources_and_backups_unchanged(self):
        self.project.inspect_display_settings()
        before = self.snapshot()
        project = Project(self.root, writable=False)
        calls = [lambda write: project.configure_windows_display(title='Preview', dry_run=not write),
                 lambda write: project.configure_room_display('Room1', room_width=1000, dry_run=not write)]
        for call in calls:
            self.assertTrue(call(False)['dry_run'])
            with self.assertRaises(ValueError):
                call(True)
        self.assertEqual(before, self.snapshot())

    def test_room_edits_keep_other_views_layers_and_instances(self):
        self.project.create('objects', 'obj_display')
        self.project.place('obj_display', 'Room1', 'Instances', 'inst_display', 30, 40)
        original = self.project.read(self.room_path)
        original['custom_room'] = 'keep'
        original['views'][3]['custom_view'] = 'keep'
        self.room_file.write_bytes(b'\xef\xbb\xbf' + encode(original).replace(b'\n', b'\r\n'))
        before = self.room_file.read_bytes()
        original_windows = self.windows.read_bytes()
        result = self.project.configure_room_display('Room1', room_width=1600, room_height=900,
            enable_views=True, view_index=3, visible=True, camera_x=-10, camera_y=20,
            camera_width=320, camera_height=180, viewport_x=0, viewport_y=0,
            viewport_width=960, viewport_height=540, dry_run=False)
        self.assertEqual(result['changed'], [self.room_path])
        current = self.project.read(self.room_path)
        self.assertEqual(current['roomSettings']['Width'], 1600)
        self.assertEqual(current['views'][3]['wview'], 320)
        self.assertEqual(current['views'][3]['wport'], 960)
        self.assertEqual(current['views'][3]['custom_view'], 'keep')
        self.assertEqual(current['layers'], original['layers'])
        self.assertEqual(current['instanceCreationOrder'], original['instanceCreationOrder'])
        for index in (0, 1, 2, 4, 5, 6, 7):
            self.assertEqual(current['views'][index], original['views'][index])
        self.assertEqual(self.windows.read_bytes(), original_windows)
        self.assertTrue(self.room_file.read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n', self.room_file.read_bytes().replace(b'\r\n', b''))
        self.assertTrue(self.project.validate()['ok'])
        self.project.restore(result['transaction'], dry_run=False)
        self.assertEqual(self.room_file.read_bytes(), before)

    def test_dimensions_do_not_implicitly_enable_or_resize_other_parts(self):
        self.project.configure_room_display('Room1', room_width=2000, camera_width=400, dry_run=False)
        info = self.project.inspect_display_settings()['room']['settings']
        self.assertEqual(info['room_width'], 2000)
        self.assertEqual(info['room_height'], 450)
        self.assertEqual(info['views'][0]['camera']['width'], 400)
        self.assertEqual(info['views'][0]['viewport']['width'], 800)
        self.assertFalse(info['enable_views'])
        self.assertFalse(info['views'][0]['visible'])

    def test_enabling_requires_visible_view_and_cannot_hide_last_active_view(self):
        before = self.room_file.read_bytes()
        with self.assertRaisesRegex(ValueError, '至少'):
            self.project.configure_room_display('Room1', enable_views=True, dry_run=False)
        self.assertEqual(self.room_file.read_bytes(), before)
        self.project.configure_room_display('Room1', enable_views=True, visible=True, dry_run=False)
        before = self.room_file.read_bytes()
        with self.assertRaisesRegex(ValueError, '至少'):
            self.project.configure_room_display('Room1', visible=False, dry_run=False)
        self.assertEqual(self.room_file.read_bytes(), before)
        self.project.configure_room_display('Room1', enable_views=False, visible=False, dry_run=False)

    def test_noop_does_not_reformat_or_create_transaction(self):
        self.project.inspect_display_settings()
        self.windows.write_bytes(self.windows.read_bytes().replace(b'\n', b'\r\n'))
        before = self.snapshot()
        self.assertEqual(self.project.configure_windows_display(title='Display Test', dry_run=False)['changed'], [])
        self.assertEqual(self.project.configure_room_display('Room1', room_width=800, dry_run=False)['changed'], [])
        self.assertEqual(before, self.snapshot())

    def test_invalid_parameters_do_not_write(self):
        self.project.inspect_display_settings()
        before = self.snapshot()
        for title in ('', '  ', 'bad\nname', 'bad\0name', 'x'*256, '\ud800', 12):
            with self.assertRaises(ValueError):
                self.project.configure_windows_display(title=title, dry_run=False)
        for field in WINDOWS_FLAGS:
            with self.assertRaises(ValueError):
                self.project.configure_windows_display(**{field: 'false'}, dry_run=False)
        for options in ({'view_index': -1}, {'view_index': 8}, {'view_index': True}, {'room_width': 0},
                        {'camera_height': 1.5}, {'room_height': 32769}, {'viewport_x': -1},
                        {'viewport_width': float('inf')}, {'visible': 1}, {'enable_views': 'true'}):
            with self.assertRaises(ValueError):
                self.project.configure_room_display('Room1', **options, dry_run=False)
        self.assertEqual(before, self.snapshot())

    def test_viewport_bounds_checked_before_write(self):
        before = self.room_file.read_bytes()
        with self.assertRaises(ValueError):
            self.project.configure_room_display('Room1', enable_views=True, visible=True,
                                                viewport_x=32768, viewport_width=2, dry_run=False)
        self.assertEqual(self.room_file.read_bytes(), before)

    def test_inheritance_is_preserved_and_not_silently_overridden(self):
        original = self.project.read(self.room_path)
        variants = []
        for field in ('inheritRoomSettings', 'inheritViewSettings', 'inherit'):
            data = deepcopy(original)
            target = data['roomSettings'] if field == 'inheritRoomSettings' else data['viewSettings'] if field == 'inheritViewSettings' else data['views'][0]
            target[field] = True
            variants.append(data)
        data = deepcopy(original)
        data['parentRoom'] = {'name': 'Parent', 'path': 'rooms/Parent/Parent.yy'}
        variants.append(data)
        for data in variants:
            self.room_file.write_bytes(encode(data))
            before = self.room_file.read_bytes()
            with self.assertRaisesRegex(ValueError, '继承'):
                self.project.configure_room_display('Room1', room_width=1000, dry_run=False)
            self.assertEqual(self.room_file.read_bytes(), before)

    def test_stale_hash_and_external_conflicts_rejected(self):
        info = self.project.inspect_display_settings()
        self.project.configure_windows_display(title='New', expected_sha256=info['windows']['sha256'], dry_run=False)
        with self.assertRaisesRegex(ValueError, '重新'):
            self.project.configure_windows_display(title='Old', expected_sha256=info['windows']['sha256'], dry_run=False)
        self.project.configure_room_display('Room1', room_width=1000, expected_sha256=info['room']['sha256'], dry_run=False)
        with self.assertRaisesRegex(ValueError, '重新'):
            self.project.configure_room_display('Room1', room_height=600, expected_sha256=info['room']['sha256'], dry_run=False)
        original_commit = self.project.commit
        external = self.windows.read_bytes() + b' '
        def conflict(changes, before, **kwargs):
            self.windows.write_bytes(external)
            return original_commit(changes, before, **kwargs)
        with patch.object(self.project, 'commit', side_effect=conflict):
            with self.assertRaisesRegex(ValueError, '外部修改'):
                self.project.configure_windows_display(title='Other', dry_run=False)
        self.assertEqual(self.windows.read_bytes(), external)

    def test_missing_or_future_platform_config_rejected_without_creating(self):
        self.windows.unlink()
        with self.assertRaises(ValueError):
            self.project.configure_windows_display(title='No', dry_run=False)
        self.assertFalse(self.windows.exists())
        data = windows_fixture()
        data['$GMWindowsOptions'] = 'future'
        self.windows.write_bytes(encode(data))
        before = self.windows.read_bytes()
        with self.assertRaises(ValueError):
            self.project.configure_windows_display(title='No', dry_run=False)
        self.assertEqual(self.windows.read_bytes(), before)

    def test_options_write_allowlist_and_restore_conflict(self):
        for path in ('options/main/options_main.yy', 'options/html5/options_html5.yy',
                     'options/windows/other.yy', 'options//windows/options_windows.yy', 'options/windows/options_windows.yy.bak'):
            with self.assertRaises(ValueError):
                self.project.commit({path: b'bad'}, self.project.snapshot([path]))
        result = self.project.configure_windows_display(title='New', dry_run=False)
        external = self.windows.read_bytes() + b' '
        self.windows.write_bytes(external)
        with self.assertRaises(ValueError):
            self.project.restore(result['transaction'], dry_run=False)
        self.assertEqual(self.windows.read_bytes(), external)

    def test_project_validation_reports_invalid_display_settings(self):
        data = windows_fixture()
        data['option_windows_vsync'] = 'true'
        self.windows.write_bytes(encode(data))
        self.assertFalse(self.project.validate()['ok'])
        self.windows.write_bytes(encode(windows_fixture()))
        data = self.project.read(self.room_path)
        data['views'][0]['wview'] = -1
        self.room_file.write_bytes(encode(data))
        self.assertFalse(self.project.validate()['ok'])
