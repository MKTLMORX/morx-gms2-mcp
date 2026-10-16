"""真实 MCP stdio 握手、工具元数据、预览、执行、错误与恢复。"""
import asyncio
import json
import os
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image
from tests.test_core import ProjectFixture


class ProtocolTests(ProjectFixture):
    def run_protocol(self, writable):
        png = self.root / 'protocol.png'
        Image.new('RGBA', (4, 4), (255, 100, 0, 255)).save(png)
        self.project.import_sprite('spr_protocol', [str(png), str(png)])
        sprite_file = self.root / 'sprites/spr_protocol/spr_protocol.yy'
        original = sprite_file.read_bytes()
        async def run():
            args = ['-m', 'morx_gms2_mcp', 'serve', '--project', str(self.root)]
            if writable:
                args.append('--write')
            params = StdioServerParameters(command=sys.executable, args=args,
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    init = await session.initialize()
                    self.assertEqual(init.serverInfo.name, 'morx-gms2-mcp')
                    tools = (await session.list_tools()).tools
                    self.assertEqual(len(tools), 21)
                    self.assertTrue({'inspect_sprite', 'configure_sprite', 'set_sprite_animation'}
                                    .issubset({tool.name for tool in tools}))
                    for tool in tools:
                        self.assertIsNotNone(tool.annotations)
                        if not tool.annotations.readOnlyHint:
                            self.assertTrue(tool.inputSchema['properties']['dry_run']['default'])
                    async def call(name, args, error=False):
                        result = await session.call_tool(name, arguments=args)
                        self.assertEqual(bool(result.isError), error, str(result))
                        return json.loads(result.content[0].text) if not error else None
                    self.assertEqual((await call('server_info', {}))['writable'], writable)
                    preview = await call('create_script', {'name': 'scr_protocol', 'code': '// 测试'})
                    self.assertTrue(preview['dry_run'])
                    self.assertFalse((self.root / 'scripts').exists())
                    if writable:
                        result = await call('create_script', {'name': 'scr_protocol', 'code': '// 测试', 'dry_run': False})
                        await call('create_script', {'name': 'scr_protocol', 'dry_run': False}, True)
                        await call('restore_backup', {'transaction_id': result['transaction'], 'dry_run': False})
                        self.assertFalse((self.root / 'scripts').exists())
                    else:
                        await call('create_script', {'name': 'scr_protocol', 'dry_run': False}, True)
                    sprite = await call('inspect_sprite', {'name': 'spr_protocol'})
                    order = [f['frame_id'] for f in sprite['frames']][::-1]
                    options = {'name': 'spr_protocol', 'speed': 24, 'origin_x': -1,
                               'expected_sha256': sprite['sha256']}
                    animation = {'name': 'spr_protocol', 'frame_order': order,
                                 'frame_durations': [2, 1], 'expected_sha256': sprite['sha256']}
                    self.assertTrue((await call('configure_sprite', options))['dry_run'])
                    self.assertTrue((await call('set_sprite_animation', animation))['dry_run'])
                    self.assertEqual(sprite_file.read_bytes(), original)
                    if writable:
                        result = await call('configure_sprite', {**options, 'dry_run': False})
                        configured = await call('inspect_sprite', {'name': 'spr_protocol'})
                        self.assertEqual(configured['speed'], 24)
                        self.assertEqual(configured['origin']['x'], -1)
                        await call('set_sprite_animation', {**animation, 'dry_run': False}, True)
                        await call('restore_backup', {'transaction_id': result['transaction'], 'dry_run': False})
                        result = await call('set_sprite_animation', {**animation, 'dry_run': False})
                        changed = await call('inspect_sprite', {'name': 'spr_protocol'})
                        self.assertEqual([f['frame_id'] for f in changed['frames']], order)
                        self.assertEqual(changed['length'], 3)
                        await call('restore_backup', {'transaction_id': result['transaction'], 'dry_run': False})
                    else:
                        await call('configure_sprite', {**options, 'dry_run': False}, True)
                        await call('set_sprite_animation', {**animation, 'dry_run': False}, True)
                    self.assertEqual(sprite_file.read_bytes(), original)
                    self.assertTrue((await call('inspect_project', {}))['ok'])
        asyncio.run(run())

    def test_writable_protocol(self):
        self.run_protocol(True)

    def test_readonly_protocol(self):
        self.run_protocol(False)
