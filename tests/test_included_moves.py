"""测试迁移已登记随包文件和工程清单的事务边界。"""
from unittest.mock import patch

from morx_gms2_mcp.storage import encode
from tests import test_core


class IncludedMoveTests(test_core.ProjectFixture):
    def setUp(self):
        super().setUp()
        self.moves = [
            {'source_path': 'datafiles/old/atlas/card.png', 'destination_path': 'datafiles/new/card.png'},
            {'source_path': 'datafiles/fonts/body.ttf', 'destination_path': 'datafiles/new/fonts/ui.ttf'},
        ]
        self.sources = [row['source_path'] for row in self.moves]
        self.targets = [row['destination_path'] for row in self.moves]
        meta = self.project.metadata()
        meta['IncludedFiles'] = []
        for path, mask, raw in zip(self.sources, [0x200008, -1], [b'png test payload', b'font test payload']):
            source = self.root / path
            source.parent.mkdir(parents=True)
            source.write_bytes(raw)
            folder, name = path.rsplit('/', 1)
            meta['IncludedFiles'].append({'$GMIncludedFile': '', '%Name': name, 'resourceType': 'GMIncludedFile',
                'resourceVersion': '2.0', 'CopyToMask': mask, 'filePath': folder, 'name': name, 'tags': ['keep']})
        raw = encode(meta).replace(b'\n}\n', b',\n}\n').replace(b'\n', b'\r\n')
        self.project.yyp.write_bytes(b'\xef\xbb\xbf' + raw)
        self.paths = [self.project.yyp.name, *self.sources, *self.targets]

    def test_batch_preview_commit_and_reopen_for_restore(self):
        before = self.project.snapshot(self.paths)
        preview = self.project.move_included_files(self.moves)
        self.assertIsNone(preview['transaction'])
        self.assertEqual(self.project.snapshot(self.paths), before)
        self.assertEqual([(row['old_runtime_path'], row['runtime_path'], row['platform_mask']) for row in preview['moves']],
                         [('old/atlas/card.png', 'new/card.png', 0x200008), ('fonts/body.ttf', 'new/fonts/ui.ttf', -1)])
        result = self.project.move_included_files(self.moves, preview['plan_sha256'], False)
        self.assertTrue(result['readback_verified'])
        self.assertEqual(len(self.project.transactions()), 1)
        after = self.project.snapshot(self.paths)
        self.assertTrue(after[self.project.yyp.name].startswith(b'\xef\xbb\xbf'))
        self.assertTrue(after[self.project.yyp.name].endswith(b',\r\n}\r\n'))
        for source, target in zip(self.sources, self.targets):
            self.assertIsNone(after[source])
            self.assertEqual(after[target], before[source])
        rows = self.project.metadata()['IncludedFiles']
        self.assertEqual([(row['filePath'], row['name'], row['%Name'], row['CopyToMask'], row['tags']) for row in rows],
                         [('datafiles/new', 'card.png', 'card.png', 0x200008, ['keep']),
                          ('datafiles/new/fonts', 'ui.ttf', 'ui.ttf', -1, ['keep'])])
        # Recovery must be authorized by the saved transaction in a fresh connection.
        reopened = type(self.project)(self.root)
        reopened.restore(result['transaction'], False)
        self.assertEqual(reopened.snapshot(self.paths), before)

    def test_source_or_manifest_change_invalidates_preview(self):
        original = self.project.snapshot(self.paths)
        plan = self.project.move_included_files(self.moves)
        with self.assertRaisesRegex(ValueError, '请先预览'):
            self.project.move_included_files(self.moves, dry_run=False)
        for relative in (self.sources[0], self.project.yyp.name):
            self.project.path(relative).write_bytes(original[relative] + b'\r\n')
            modified = self.project.snapshot(self.paths)
            with self.assertRaisesRegex(ValueError, '重新预览'):
                self.project.move_included_files(self.moves, plan['plan_sha256'], False)
            self.assertEqual(self.project.snapshot(self.paths), modified)
            self.project.path(relative).write_bytes(original[relative])
        self.assertEqual(self.project.transactions(), [])

    def test_path_scope_and_occupied_target_are_rejected(self):
        before = self.project.snapshot(self.paths)
        target = self.root / self.targets[1]
        target.parent.mkdir(parents=True)
        target.write_bytes(b'outside edit')
        with self.assertRaisesRegex(ValueError, '目标文件已存在'):
            self.project.move_included_files(self.moves)
        self.assertEqual(target.read_bytes(), b'outside edit')
        for source, destination in [('datafiles/missing.png', 'datafiles/new/missing.png'),
                                    (self.sources[0], 'datafiles/../card.png'),
                                    (self.sources[0], 'datafiles/new/card.ttf'),
                                    (self.sources[0], self.sources[0]),
                                    (self.sources[0], 'datafiles/new/NUL.png')]:
            with self.subTest(source=source, destination=destination), self.assertRaises(ValueError):
                self.project.move_included_files([{'source_path': source, 'destination_path': destination}])
        with self.assertRaises(ValueError):
            self.project._check_write_path(self.targets[0])
        self.assertEqual(self.project.snapshot(self.paths[:-1]), {k: before[k] for k in self.paths[:-1]})
        self.assertEqual(self.project.transactions(), [])

    def test_failed_second_unlink_rolls_back_manifest_and_first_move(self):
        before = self.project.snapshot(self.paths)
        plan = self.project.move_included_files(self.moves)
        write = self.project._write
        def fail(relative, data, **kwargs):
            if relative == self.sources[1] and data is None:
                raise PermissionError('owned fixture lock')
            write(relative, data, **kwargs)
        with patch.object(self.project, '_write', side_effect=fail):
            with self.assertRaises(PermissionError):
                self.project.move_included_files(self.moves, plan['plan_sha256'], False)
        self.assertEqual(self.project.snapshot(self.paths), before)
        self.assertEqual(self.project.transactions()[0]['status'], 'rolled_back')

    def test_target_created_during_commit_is_preserved(self):
        before = self.project.snapshot(self.paths)
        plan = self.project.move_included_files(self.moves)
        write = self.project._write
        def racing_write(relative, data, **kwargs):
            if relative == self.targets[1] and data is not None:
                target = self.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'outside edit')
            write(relative, data, **kwargs)
        with patch.object(self.project, '_write', side_effect=racing_write):
            with self.assertRaisesRegex(ValueError, '目标已被外部创建'):
                self.project.move_included_files(self.moves, plan['plan_sha256'], False)
        self.assertEqual(self.project.snapshot(self.paths), {**before, self.targets[1]: b'outside edit'})
