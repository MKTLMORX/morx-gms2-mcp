"""对已安装版本做只读协议检查，不创建或修改游戏资源。"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check(project, writable):
    args = ['-m', 'morx_gms2_mcp', 'serve', '--project', str(project)]
    if writable:
        args.append('--write')
    params = StdioServerParameters(command=sys.executable, args=args,
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'})
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            result = {'tools': names}
            for name in ('server_info', 'inspect_project'):
                response = await session.call_tool(name, arguments={})
                if response.isError:
                    raise RuntimeError(str(response.content))
                result[name] = json.loads(response.content[0].text)
            result['ok'] = len(names) == 35 and {'inspect_sprite', 'configure_sprite', 'set_sprite_animation',
                'inspect_display_settings', 'configure_windows_display', 'configure_room_display',
                'runtime_status', 'run_game', 'capture_game', 'inspect_game', 'read_code', 'update_code'
                }.issubset(names) and result['inspect_project']['ok']
            return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--write', action='store_true', help='验证可写启动模式，但只调用读取工具')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = asyncio.run(check(args.project.resolve(), args.write))
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(output + '\n', encoding='utf-8')
    print(output)
    raise SystemExit(0 if result['ok'] else 1)
