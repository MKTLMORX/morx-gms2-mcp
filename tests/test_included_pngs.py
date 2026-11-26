"""PNG 登记保留项目路径和目标平台，不修改文件本身。"""
from PIL import Image
from tests import test_core


class IncludedPngTests(test_core.ProjectFixture):
    def png(self, path, color='red'):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGBA', (12, 18), color).save(file)
        return file

    def test_register_distinct_paths_platform_masks_and_undo(self):
        rows = [{'path': 'datafiles/atlas/tile/card.png', 'platforms': ['Windows']},
                {'path': 'datafiles/atlas/detail/card.png', 'platforms': ['Android']},
                {'path': 'datafiles/atlas/shared.png', 'platforms': ['Windows', 'Android']}]
        originals = {self.png(row['path'], color): None for row, color in zip(rows, ['red', 'green', 'blue'])}
        originals = {file: file.read_bytes() for file in originals}
        yyp = self.project.yyp.read_bytes()
        preview = self.project.register_png_included_files(rows)
        self.assertIsNone(preview['transaction'])
        self.assertEqual(self.project.yyp.read_bytes(), yyp)
        self.assertEqual([row['runtime_path'] for row in preview['included_files']],
                         ['atlas/tile/card.png', 'atlas/detail/card.png', 'atlas/shared.png'])
        result = self.project.register_png_included_files(rows, preview['plan_sha256'], False)
        self.assertEqual(result['changed'], [self.project.yyp.name])
        native = self.project.metadata()['IncludedFiles']
        self.assertEqual([(row['filePath'], row['name'], row['CopyToMask']) for row in native],
            [('datafiles/atlas/tile', 'card.png', 0x100040),
             ('datafiles/atlas/detail', 'card.png', 0x200008),
             ('datafiles/atlas', 'shared.png', 0x300048)])
        self.assertTrue(all(row['already_registered'] for row in self.project.register_png_included_files(rows)['included_files']))
        with self.assertRaisesRegex(ValueError, '已登记'):
            self.project.register_png_included_files([{**rows[0], 'platforms': ['Android']}])
        self.project.restore(result['transaction'], False)
        self.assertEqual(self.project.yyp.read_bytes(), yyp)
        self.assertEqual({file: file.read_bytes() for file in originals}, originals)

    def test_pins_sources_and_requires_fresh_preview(self):
        path = 'datafiles/atlas/a.png'
        source = self.png(path)
        items = [{'path': path, 'platforms': ['Windows']}]
        preview = self.project.register_png_included_files(items)
        self.png(path, 'blue')
        changed = source.read_bytes()
        yyp = self.project.yyp.read_bytes()
        with self.assertRaisesRegex(ValueError, '重新预览'):
            self.project.register_png_included_files(items, preview['plan_sha256'], False)
        self.assertEqual(source.read_bytes(), changed)
        self.assertEqual(self.project.yyp.read_bytes(), yyp)
        self.assertEqual(self.project.transactions(), [])

    def test_invalid_item_rejects_entire_batch(self):
        path = 'datafiles/atlas/a.png'
        self.png(path)
        bad_image = self.root / 'datafiles/atlas/not_png.png'
        bad_image.write_bytes(b'not a PNG')
        before = self.project.yyp.read_bytes()
        good = {'path': path, 'platforms': ['Windows']}
        for invalid in [{'path': 'datafiles/../outside.png', 'platforms': ['Android']},
                        {'path': 'outside.png', 'platforms': ['Android']},
                        {'path': 'datafiles/atlas/not_png.png', 'platforms': ['Android']},
                        {'path': 'datafiles/atlas/not_png.png', 'platforms': ['Linux']},
                        {'path': path.upper(), 'platforms': ['Android']}, good]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.project.register_png_included_files([good, invalid])
        self.assertEqual(self.project.yyp.read_bytes(), before)
        self.assertEqual(self.project.transactions(), [])
