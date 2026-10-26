"""原生瓦片编码、资源依赖与事务回归；仅生成临时素材。"""
from copy import deepcopy
import unittest

from PIL import Image

from morx_gms2_mcp import schemas, tiles
from morx_gms2_mcp.storage import encode
from tests.test_core import ProjectFixture


class TileEncodingTests(unittest.TestCase):
    def test_mixed_runs_flags_and_signed_empty(self):
        data = {'SerialiseWidth': 4, 'SerialiseHeight': 2, 'TileDataFormat': 1,
                'TileCompressedData': [-2, -2147483648, 3, 1, 0x10000002, 0x60000003, -3, 2]}
        expected = [-2147483648, -2147483648, 1, 0x10000002, 0x60000003, 2, 2, 2]
        before = deepcopy(data)
        self.assertEqual(tiles.read(data), (4, 2, expected))
        self.assertEqual(data, before)
        self.assertTrue(tiles.empty(-2147483648))
        self.assertTrue(tiles.empty(2147483648))
        tiles.write(data, expected)
        self.assertEqual(tiles.read(data)[2], expected)
        self.assertNotIn('TileSerialiseData', data)

    def test_encoding_roundtrips_and_preserves_metadata(self):
        samples = [[1], [1, 2, 3, 4], [3] * 12, [1, 2, 2, 3, 4, 5, 5],
                   [2147483648, -2147483648, 0, 0x70000001, 1]]
        for form in (0, 1):
            for values in samples:
                with self.subTest(form=form, values=values):
                    data = {'SerialiseWidth': len(values), 'SerialiseHeight': 1,
                            'TileDataFormat': form, 'future': {'keep': True}}
                    tiles.write(data, values)
                    expected = [v - (1 << 32) if v >= 1 << 31 else v for v in values] if form else values
                    self.assertEqual(tiles.read(data)[2], expected)
                    self.assertEqual(data['TileDataFormat'], form)
                    self.assertEqual(data['future'], {'keep': True})

    def test_rejects_malformed_and_oversized_data(self):
        base = {'SerialiseWidth': 2, 'SerialiseHeight': 2, 'TileDataFormat': 1,
                'TileCompressedData': [-4, 1]}
        invalid = [None, {}, {**base, 'SerialiseWidth': True},
                   {**base, 'SerialiseHeight': 0}, {**base, 'SerialiseWidth': tiles.MAX_CELLS},
                   {**base, 'TileDataFormat': 2}, {**base, 'TileDataFormat': True},
                   {**base, 'TileSerialiseData': [1] * 4}]
        for raw in ([], [-4], [4, 1], [-5, 1], [True, 1], [-4, True], [-4, 1.5],
                    [-4, 1 << 31], [-4, 1 << 32], [-4, -(1 << 31) - 1], [-3, 1], [0] * 9):
            invalid.append({**base, 'TileCompressedData': raw})
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                tiles.read(data)
        for raw in ([1], [1, 2, 3, True], [1, 2, 3, 1 << 32]):
            with self.assertRaises(ValueError):
                tiles.read({'SerialiseWidth': 2, 'SerialiseHeight': 2, 'TileSerialiseData': raw})


class TileProjectTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        self.png = self.root / 'sheet.png'
        Image.new('RGBA', (16, 16), (80, 120, 200, 255)).save(self.png)
        self.project.import_sprite('spr_sheet', [str(self.png)])
        self.project.create_tileset('ts_sheet', 'spr_sheet', 8, 8)
        self.project.create_tile_layer('Room1', 'Tiles', 'ts_sheet', width=4, height=2)
        self.room_path = 'rooms/Room1/Room1.yy'

    def compressed_room(self):
        room = self.project.read(self.room_path)
        layer = next(layer for layer in room['layers'] if layer['name'] == 'Tiles')
        layer['tiles'] = {'SerialiseWidth': 4, 'SerialiseHeight': 2, 'TileDataFormat': 1,
                          'TileCompressedData': [-2, -2147483648, 3, 1, 0x10000002, 0x60000003, -3, 2]}
        self.project.path(self.room_path).write_bytes(encode(room))

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def test_compressed_map_inspection_edit_preview_conflict_and_restore(self):
        self.compressed_room()
        info = self.project.inspect_tilemap('Room1', 'Tiles', include_cells=True)
        self.assertEqual(info['non_empty'], 6)
        self.assertEqual([c['tile'] for c in info['cells']], [1, 2, 3, 2, 2, 2])
        self.assertTrue(info['cells'][1]['flip_x'])
        self.assertTrue(info['cells'][2]['flip_y'])
        self.assertTrue(info['cells'][2]['rotate'])
        self.assertTrue(self.project.validate()['ok'], self.project.validate())
        before = self.snapshot()
        original = before[self.room_path]
        changes = [{'x': 0, 'y': 0, 'tile': 4, 'rotate': True}, {'x': 3, 'y': 1, 'tile': 0}]
        self.project.set_tilemap_cells('Room1', 'Tiles', changes, info['room_sha256'], True)
        self.assertEqual(before, self.snapshot())
        result = self.project.set_tilemap_cells('Room1', 'Tiles', changes, info['room_sha256'], False)
        room = self.project.read(self.room_path)
        data = next(layer['tiles'] for layer in room['layers'] if layer['name'] == 'Tiles')
        self.assertEqual(data['TileDataFormat'], 1)
        self.assertNotIn('TileSerialiseData', data)
        self.assertEqual(tiles.read(data)[2], [0x40000004, -2147483648, 1, 0x10000002,
                                             0x60000003, 2, 2, -2147483648])
        self.assertTrue(self.project.validate()['ok'])
        with self.assertRaises(ValueError):
            self.project.set_tilemap_cells('Room1', 'Tiles', changes, info['room_sha256'], False)
        self.project.restore(result['transaction'], dry_run=False)
        self.assertEqual(self.project.path(self.room_path).read_bytes(), original)

    def test_invalid_compressed_map_rejects_edit_without_writes(self):
        self.compressed_room()
        room = self.project.read(self.room_path)
        next(layer for layer in room['layers'] if layer['name'] == 'Tiles')['tiles']['TileCompressedData'] = [-9, 1]
        self.project.path(self.room_path).write_bytes(encode(room))
        before = self.snapshot()
        self.assertFalse(self.project.validate()['ok'])
        with self.assertRaises(ValueError):
            self.project.inspect_tilemap('Room1', 'Tiles')
        with self.assertRaises(ValueError):
            self.project.set_tilemap_cells('Room1', 'Tiles', [{'x': 0, 'y': 0, 'tile': 1}])
        self.assertEqual(before, self.snapshot())

    def test_tileset_sprite_resize_rejected_without_writes(self):
        self.project.create_tileset('ts_other', 'spr_sheet', 4, 4)
        replacement = self.root / 'larger.png'
        Image.new('RGBA', (32, 16), (200, 120, 80, 255)).save(replacement)
        info = self.project.inspect_sprite('spr_sheet')
        before = self.snapshot()
        for preview in (True, False):
            with self.assertRaisesRegex(ValueError, 'ts_sheet, ts_other'):
                self.project.replace_sprite_images('spr_sheet', [str(replacement)], info['sha256'],
                    info['image_sha256'], dry_run=preview, allow_resize=True)
            self.assertEqual(before, self.snapshot())
        self.assertTrue(self.project.validate()['ok'])
        Image.new('RGBA', (16, 16), (200, 120, 80, 255)).save(replacement)
        result = self.project.replace_sprite_images('spr_sheet', [str(replacement)], info['sha256'],
            info['image_sha256'], dry_run=False)
        self.assertFalse(result['resized'])
        self.assertTrue(self.project.validate()['ok'])
