"""网络事件的统一身份和资源事务，不使用合成回调冒充网络联调。"""
from morx_gms2_mcp.resources import event_spec, trigger_spec
from tests.test_core import ProjectFixture


class NetworkingTests(ProjectFixture):
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
