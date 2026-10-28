"""依赖快照不遍历参考目录；保护资源完整性、策略事务及实际复制边界。"""
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from morx_gms2_mcp import snapshots
from morx_gms2_mcp.storage import digest, encode
from tests.test_core import ProjectFixture
from tests import test_runtime as fixtures


class SnapshotTests(ProjectFixture):
    def file(self, name, content=b'x'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_selected_dependencies_keep_companions_and_ignore_large_unregistered_trees(self):
        self.file('reference/nested/other.yyp')
        self.file('server/database.bin')
        self.file('sprites/unregistered/frame.png')
        self.file('options/windows/icon.ico')
        self.file('datafiles/fonts/font.ttf')
        self.file('extensions/ext/ext.yy', encode({'name': 'ext'}))
        self.file('extensions/ext/host.dll')
        meta = self.project.read('test.yyp')
        meta['resources'].append({'id': {'name': 'ext', 'path': 'extensions/ext/ext.yy'}})
        (self.root / 'test.yyp').write_bytes(encode(meta))
        real_walk = snapshots.os.walk
        visits = []
        def walk(path, **kwargs):
            # Windows 临时目录可能使用 8.3 短名；与 Project 使用同一规范路径。
            visits.append(Path(path).relative_to(self.project.root).as_posix())
            return real_walk(path, **kwargs)
        with patch('morx_gms2_mcp.snapshots.os.walk', side_effect=walk):
            result = snapshots.plan(self.project)
        self.assertIn('extensions/ext/host.dll', result['files'])
        self.assertIn('datafiles/fonts/font.ttf', result['files'])
        self.assertIn('options/windows/icon.ico', result['files'])
        self.assertFalse(any(v in visits for v in ('reference', 'server', 'sprites')))
        self.assertNotIn('reference/nested/other.yyp', result['files'])
        self.assertEqual(result['total_bytes'], sum(result['files'].values()))

    def test_policy_preview_hash_conflict_backup_and_extra_dependency(self):
        self.file('custom/map.bin')
        initial = snapshots.inspect(self.project)
        self.assertIsNone(initial['policy_sha256'])
        self.assertTrue(snapshots.configure(self.project, include_paths=['custom'])['dry_run'])
        self.assertFalse(self.project.path(snapshots.POLICY).exists())
        result = snapshots.configure(self.project, include_paths=['custom'], dry_run=False)
        info = snapshots.inspect(self.project)
        self.assertIn('custom', info['by_root'])
        with self.assertRaisesRegex(ValueError, '哈希'):
            snapshots.configure(self.project, dry_run=False)
        self.project.restore(result['transaction'], False)
        self.assertIsNone(snapshots.inspect(self.project)['policy_sha256'])
        self.assertTrue((self.root / 'custom/map.bin').exists())

    def test_full_mode_exclusion_and_required_resource_protection(self):
        self.file('reference/nested/foo.bin')
        self.file('server/cache.bin')
        result = snapshots.configure(self.project, mode='full', exclude_paths=['reference'])
        self.assertIn('server', result['snapshot']['by_root'])
        self.assertNotIn('reference', result['snapshot']['by_root'])
        for path in ['rooms', 'rooms/Room1/Room1.yy', 'test.yyp']:
            with self.assertRaises(ValueError):
                snapshots.configure(self.project, exclude_paths=[path])

    def test_invalid_configuration_does_not_write_and_readonly_protection(self):
        for path in ['../secret', 'C:/secret', '/secret', '.', 'rooms/*', '.codex', 'reference/../rooms']:
            with self.assertRaises(ValueError):
                snapshots.configure(self.project, include_paths=[path])
        for values in ['', {}, ['rooms', 'ROOMS'], ['missing']]:
            with self.assertRaises(ValueError):
                snapshots.configure(self.project, include_paths=values)
        self.project.writable = False
        self.assertTrue(snapshots.configure(self.project)['dry_run'])
        with self.assertRaises(ValueError):
            snapshots.configure(self.project, dry_run=False)

    def test_missing_registered_and_included_files_fail_before_copy(self):
        meta = self.project.read('test.yyp')
        meta['IncludedFiles'] = [{'name': 'missing.bin', 'filePath': 'datafiles'}]
        (self.root / 'test.yyp').write_bytes(encode(meta))
        with self.assertRaisesRegex(ValueError, 'Included File'):
            snapshots.plan(self.project)
        meta.pop('IncludedFiles')
        (self.root / 'test.yyp').write_bytes(encode(meta))
        (self.root / 'rooms/Room1/Room1.yy').unlink()
        with self.assertRaisesRegex(ValueError, '登记资源'):
            snapshots.plan(self.project)

    def test_selected_symlink_rejected_but_unselected_tree_not_opened(self):
        with tempfile.TemporaryDirectory() as other:
            path = self.root / 'reference'
            try:
                path.symlink_to(other, target_is_directory=True)
            except OSError:
                self.skipTest('host cannot create symlinks')
            self.assertNotIn('reference', snapshots.plan(self.project)['by_root'])
            with self.assertRaises(ValueError):
                snapshots.configure(self.project, include_paths=['reference'])

    def test_limits_pagination_and_cancel(self):
        result = snapshots.inspect(self.project, limit=1)
        self.assertEqual(len(result['files']), 1)
        self.assertEqual(result['next_offset'], 1)
        def cancel():
            raise InterruptedError('cancelled')
        with self.assertRaises(InterruptedError):
            snapshots.plan(self.project, check_cancel=cancel)

    def test_actual_copy_does_not_link_source_and_disk_failure_leaves_no_files(self):
        fixture = fixtures.RuntimeTests('test_stop_preview_and_owned_process_only')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        (fixture.root / 'unregistered.bin').write_bytes(b'do not copy')
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            with patch('morx_gms2_mcp.runtime.shutil.disk_usage', return_value=shutil._ntuple_diskusage(1, 1, 0)):
                with self.assertRaisesRegex(ValueError, '空间不足'):
                    fixture.runtime._copy_project(target, 'probe', None)
            self.assertEqual(list(target.iterdir()), [])
            fixture.runtime._copy_project(target, 'probe', None)
            self.assertFalse((target / 'unregistered.bin').exists())
            copied = target / 'rooms/Room1/Room1.yy'
            source = fixture.root / 'rooms/Room1/Room1.yy'
            before = source.read_bytes()
            copied.write_bytes(b'changed')
            self.assertEqual(source.read_bytes(), before)
