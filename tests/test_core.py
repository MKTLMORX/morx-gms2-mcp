"""验证资源变更边界与故障恢复，不依赖 IDE。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from morx_gms2_mcp.resources import Project, name_check
from morx_gms2_mcp.storage import encode, parse_yy


class ProjectFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / '.mcp-probe-project').touch()
        (self.root / 'test.yyp').write_bytes(encode({'$GMProject': 'v1', 'resourceType': 'GMProject',
            'name': 'test', 'Folders': [], 'TextureGroups': [{'name': 'Default'}],
            'AudioGroups': [{'name': 'audiogroup_default'}],
            'RoomOrderNodes': [{'roomId': {'name': 'Room1', 'path': 'rooms/Room1/Room1.yy'}}],
            'resources': [{'id': {'name': 'Room1', 'path': 'rooms/Room1/Room1.yy'}}]}))
        room = self.root / 'rooms/Room1/Room1.yy'
        room.parent.mkdir(parents=True)
        room.write_bytes(encode({'name': 'Room1', 'resourceType': 'GMRoom',
            'layers': [{'name': 'Instances', 'resourceType': 'GMRInstanceLayer', 'instances': []}],
            'instanceCreationOrder': []}))
        self.project = Project(self.root)


class ProjectTests(ProjectFixture):
    def test_tail_commas_and_quoted_text(self):
        self.assertEqual(parse_yy('\ufeff{"a": [1,], "b": "x,}\\\"",}'), {'a': [1], 'b': 'x,}"'})

    def test_path_and_marker_boundaries(self):
        with self.assertRaises(ValueError):
            self.project.path('../outside')
        for name in ('../bad', 'CON', 'a/b', 'bad name'):
            with self.assertRaises(ValueError):
                name_check(name)
        (self.root / '.mcp-probe-project').unlink()
        with self.assertRaises(ValueError):
            Project(self.root)

    def test_create_events_and_instance(self):
        self.project.create('scripts', 'scr_probe', 'function scr_probe() { return 1; }')
        self.project.create('objects', 'obj_probe')
        self.project.add_event('obj_probe', 'Create', 'value = scr_probe();')
        self.project.place('obj_probe', 'Room1', 'Instances', 'inst_probe', 0, 0)
        self.assertTrue(self.project.validate()['ok'])
        room = self.project.read('rooms/Room1/Room1.yy')
        self.assertEqual(room['instanceCreationOrder'][0]['name'], 'inst_probe')
        self.assertEqual(len(self.project.metadata()['resources']), 3)

    def test_duplicate_and_invalid_requests_do_not_overwrite(self):
        self.project.create('objects', 'obj_probe')
        self.project.add_event('obj_probe', 'Create', 'value = 1;')
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        calls = [lambda: self.project.create('objects', 'OBJ_PROBE'),
                 lambda: self.project.add_event('obj_probe', 'Create', 'value = 2;'),
                 lambda: self.project.place('obj_probe', 'Room1', 'Missing', 'inst_probe', 0, 0),
                 lambda: self.project.place('obj_probe', 'Room1', 'Instances', 'inst_probe', float('nan'), 0)]
        for call in calls:
            with self.assertRaises(ValueError):
                call()
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_conflict_detection(self):
        before = self.project.snapshot(['test.yyp'])
        self.project.yyp.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.project.commit({'test.yyp': b'new'}, before)
        self.assertEqual(self.project.yyp.read_bytes(), b'changed')

    def test_mid_write_failure_restores_existing_and_new_files(self):
        original = self.project._write
        old = self.project.yyp.read_bytes()
        calls = 0
        def fail_third(rel, data):
            nonlocal calls
            if not rel.startswith('.codex/'):
                calls += 1
                if calls == 3:
                    raise OSError('模拟第三次资源文件提交失败')
            original(rel, data)
        changes = {'test.yyp': b'changed', 'scripts/new/new.gml': b'new', 'scripts/last/last.gml': b'last'}
        with patch.object(self.project, '_write', side_effect=fail_third):
            with self.assertRaises(OSError):
                self.project.commit(changes, self.project.snapshot(changes))
        self.assertEqual(self.project.yyp.read_bytes(), old)
        self.assertFalse((self.root / 'scripts/new/new.gml').exists())
        self.assertFalse((self.root / 'scripts/last/last.gml').exists())
        self.assertEqual(list(self.root.glob('*.mcp-tmp-*')), [])
        self.assertEqual(self.project.transactions()[0]['status'], 'rolled_back')

    def test_broken_references_are_reported(self):
        self.project.create('objects', 'obj_probe')
        self.project.place('obj_probe', 'Room1', 'Instances', 'inst_probe', 0, 0)
        room_path = self.root / 'rooms/Room1/Room1.yy'
        room = self.project.read('rooms/Room1/Room1.yy')
        room['layers'][0]['instances'][0]['objectId']['path'] = 'missing.yy'
        room['instanceCreationOrder'] = []
        room_path.write_bytes(encode(room))
        result = self.project.validate()
        self.assertFalse(result['ok'])
        self.assertEqual(len(result['errors']), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
