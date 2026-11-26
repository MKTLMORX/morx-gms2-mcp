"""网络事件的统一身份和资源事务，不使用合成回调冒充网络联调。"""
from morx_gms2_mcp.resources import event_spec, trigger_spec
from tests.test_core import ProjectFixture


class NetworkingTests(ProjectFixture):
    def test_save_load_and_system_transactions(self):
        for name, number in [('AsyncImageLoaded', 60), ('AsyncSaveLoad', 72), ('AsyncSystem', 75)]:
            with self.subTest(event=name):
                before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
                self.project.add_event('obj_network', name, '// preview\n', dry_run=True)
                self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
                self.project.add_event('obj_network', name, '// callback\n')
                read = self.project.read_code('obj_network', name)
                self.assertTrue(read['path'].endswith(f'Other_{number}.gml'))
                path = self.root / read['path']
                path.write_bytes(b'\xef\xbb\xbf// callback\r\n')
                read = self.project.read_code('obj_network', name)
                self.project.update_code('obj_network', '// changed\n', read['sha256'], name, dry_run=False)
                self.assertEqual(path.read_bytes(), b'\xef\xbb\xbf// changed\r\n')
                with self.assertRaises(ValueError):
                    self.project.update_code('obj_network', '// stale', read['sha256'], name, dry_run=False)
                with self.assertRaises(ValueError):
                    self.project.add_event('obj_network', name, '// duplicate')
                removed = self.project.remove_event('obj_network', name, dry_run=False)
                self.assertFalse(path.exists())
                self.project.restore(removed['transaction'], False)
                self.assertEqual(path.read_bytes(), b'\xef\xbb\xbf// changed\r\n')
                self.assertTrue(self.project.validate()['ok'])

    def test_async_events_reject_manual_trigger_and_arbitrary_numbers(self):
        for name, number in [('AsyncImageLoaded', 60), ('AsyncSaveLoad', 72), ('AsyncSystem', 75)]:
            self.assertEqual(event_spec(name, 0), (7, number, 'Other', None))
            with self.assertRaises(ValueError):
                event_spec(name, number)
            with self.assertRaisesRegex(ValueError, '真实 IO'):
                trigger_spec(name)
        for name in ('AsyncUnknown', 'Other', 'CloseButton'):
            with self.assertRaises(ValueError):
                event_spec(name, 72)

    def setUp(self):
        super().setUp()
        self.project.create('objects', 'obj_network')

    def test_alias_identity_preview_read_update_remove_and_restore(self):
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.project.add_event('obj_network', 'AsyncNetworking', '// 网络\n', dry_run=True)
        self.assertEqual(before, {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.project.add_event('obj_network', 'Networking', '// 网络\n')
        read = self.project.read_code('obj_network', 'AsyncNetworking')
        self.assertIn('Other_68.gml', read['path'])
        self.project.update_code('obj_network', '// 更新\n', read['sha256'], 'Networking', dry_run=False)
        with self.assertRaises(ValueError):
            self.project.update_code('obj_network', '// stale', read['sha256'], 'AsyncNetworking', dry_run=False)
        with self.assertRaises(ValueError):
            self.project.add_event('obj_network', 'AsyncNetworking', '// duplicate')
        self.assertTrue(self.project.validate()['ok'])
        removed = self.project.remove_event('obj_network', 'AsyncNetworking', dry_run=False)
        self.assertFalse((self.root / read['path']).exists())
        self.project.restore(removed['transaction'], False)
        self.assertEqual(self.project.read_code('obj_network', 'Networking')['code'], '// 更新\n')

    def test_explicit_mapping_rejects_number_and_manual_trigger(self):
        self.assertEqual(event_spec('Networking', 0), (7, 68, 'Other', None))
        self.assertEqual(event_spec('AsyncNetworking', 0), event_spec('Networking', 0))
        for name in ['Networking', 'AsyncNetworking']:
            with self.assertRaises(ValueError):
                event_spec(name, 68)
            with self.assertRaisesRegex(ValueError, '真实网络'):
                trigger_spec(name)
        self.assertEqual(event_spec('User', 1), (7, 11, 'Other', None))
        self.assertEqual(event_spec('Alarm', 11), (2, 11, 'Alarm', None))
