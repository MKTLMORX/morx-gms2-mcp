"""Android开发桥接仅修改副本，原生Manifest属性与IncludedFile描述可追溯。"""
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from morx_gms2_mcp import android, android_bridge, schemas
from morx_gms2_mcp.storage import encode, parse_yy
from tests import test_android_toolchain as fixtures


class AndroidBridgeTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ToolchainTests('test_native_runtime_requires_real_ndk_even_for_vm')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.root, self.project = fixture.root, fixture.project

    def test_clone_bridge_changes_no_source_and_packaged_marker_is_validated(self):
        (self.root/'rooms/Room1/Room1.yy').write_bytes(encode(schemas.room('Room1',640,360)))
        original = {path.relative_to(self.root):path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary)/'copy'
            shutil.copytree(self.root,copied,ignore=shutil.ignore_patterns('.codex'))
            result = android_bridge.install(copied,self.project.yyp.name)
            self.assertEqual(result['transport'],'adb_run_as')
            self.assertTrue((copied/'objects/sys_morx_runtime/Create_0.gml').is_file())
            create=(copied/'objects/sys_morx_runtime/Create_0.gml').read_text(encoding='utf-8')
            self.assertIn('game_save_id + "morx_mcp/"',create)
            self.assertEqual(result['protocol'],2)
            options=parse_yy((copied/android.OPTIONS).read_text(encoding='utf-8'))
            self.assertIn('android:debuggable="true"',options['option_android_application_tag_inject'])
            meta=parse_yy((copied/self.project.yyp.name).read_text(encoding='utf-8'))
            self.assertIn(android_bridge.MARKER_FILE,[row['name'] for row in meta['IncludedFiles']])
            self.assertFalse((copied/'.codex').exists())
            archive_path=Path(temporary)/'fixture.zip'
            with zipfile.ZipFile(archive_path,'w') as archive:
                archive.writestr('assets/'+android_bridge.MARKER_FILE,json.dumps(result))
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(android_bridge.marker(archive),result)
            result['protocol']=1
            with zipfile.ZipFile(archive_path,'w') as archive:
                archive.writestr('assets/'+android_bridge.MARKER_FILE,json.dumps(result))
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaisesRegex(ValueError,'协议'):android_bridge.marker(archive)
        self.assertEqual(original,{path.relative_to(self.root):path.read_bytes() for path in self.root.rglob('*') if path.is_file()})
