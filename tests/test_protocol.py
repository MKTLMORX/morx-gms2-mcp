"""真实 MCP stdio 握手、工具元数据、预览、执行、错误与恢复。"""
import asyncio
import json
import os
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from tests.test_core import ProjectFixture


class ProtocolTests(ProjectFixture):
    def run_protocol(self, writable):
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
                    self.assertEqual(len(tools), 18)
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
                    self.assertTrue((await call('inspect_project', {}))['ok'])
        asyncio.run(run())

    def test_writable_protocol(self):
        self.run_protocol(True)

    def test_readonly_protocol(self):
        self.run_protocol(False)
