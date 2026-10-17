"""安装后入口：绑定、诊断、客户端配置与 stdio 服务。"""
import argparse
import json
import sys
from pathlib import Path
from . import __version__
from .storage import encode, parse_yy


def root_path(value):
    path = Path(value).resolve(strict=True)
    return path.parent if path.is_file() and path.suffix == '.yyp' else path


def bind(root):
    # 与服务端统一规范路径，兼容 Windows 短路径及包含 .. 的调用路径。
    root = Path(root).resolve(strict=True)
    if any(p.casefold() == '参考项目' for p in root.parts):
        raise ValueError('参考项目只读，拒绝绑定')
    projects = list(root.glob('*.yyp'))
    if len(projects) != 1 or projects[0].is_symlink():
        raise ValueError('须指定恰含一个真实 yyp 文件的项目目录')
    data = parse_yy(projects[0].read_text(encoding='utf-8-sig'))
    if data.get('resourceType') != 'GMProject' or data.get('$GMProject') != 'v1':
        raise ValueError('仅支持现代 GMProject v1 项目')
    directory = root / '.codex'
    if directory.exists() and directory.resolve() != directory:
        raise ValueError('配置目录不能是符号链接或目录联接')
    directory.mkdir(exist_ok=True)
    dest = directory / 'gms2-resource-binding.json'
    raw = encode({'format': 1, 'project': str(projects[0].resolve())})
    if dest.exists():
        if dest.is_symlink() or dest.read_bytes() != raw:
            raise ValueError('已有不同绑定，不自动覆盖；请核对绑定文件')
    else:
        with dest.open('xb') as file:
            file.write(raw)
    return {'bound': str(projects[0]), 'binding': str(dest)}


def client_config(root, client, write, runtime_options=None):
    args = ['-m', 'morx_gms2_mcp', 'serve', '--project', str(root)]
    if write:
        args.append('--write')
    if runtime_options and runtime_options.enabled:
        args.append('--allow-run')
        for flag, value in (('--runtime-root', runtime_options.runtime_root),
                            ('--user-directory', runtime_options.user_directory)):
            if value:
                args.extend([flag, value])
    env = {'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
    if client == 'claude':
        return json.dumps({'mcpServers': {'morx-gms2-mcp': {'type': 'stdio',
            'command': sys.executable, 'args': args, 'env': env, 'timeout': 180000}}},
            ensure_ascii=False, indent=2)
    quote = lambda text: json.dumps(text, ensure_ascii=False)
    lines = ['[mcp_servers.morx-gms2-mcp]', 'command = ' + quote(sys.executable),
        'args = [' + ', '.join(quote(a) for a in args) + ']', 'startup_timeout_sec = 30', 'tool_timeout_sec = 180',
        '[mcp_servers.morx-gms2-mcp.env]']
    lines += [f'{key} = {quote(value)}' for key, value in env.items()]
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='GameMaker 制作与运行观察 MCP；固定项目，默认只读，运行须单独启用。')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('bind', 'check', 'serve', 'client-config'):
        sub = commands.add_parser(name)
        sub.add_argument('--project', '--project-root', dest='project', required=True)
        if name in ('serve', 'client-config'):
            sub.add_argument('--write', action='store_true', help='允许显式 dry_run=false 的资源变更')
            sub.add_argument('--allow-run', action='store_true', help='允许构建及运行受信任项目的隔离副本')
            sub.add_argument('--runtime-root', help='已安装的 GameMaker Runtime 根目录')
            sub.add_argument('--user-directory', help='已登录 GameMaker 的本机用户目录；不复制许可证')
        if name == 'client-config':
            sub.add_argument('--client', choices=('claude', 'codex'), required=True)
    args = parser.parse_args(argv)
    try:
        root = root_path(args.project)
        from .runtime import RuntimeOptions
        runtime_options = RuntimeOptions(getattr(args, 'allow_run', False),
            getattr(args, 'runtime_root', None), getattr(args, 'user_directory', None))
        if args.command == 'bind':
            print(json.dumps(bind(root), ensure_ascii=False))
        elif args.command == 'client-config':
            print(client_config(root, args.client, args.write, runtime_options))
        elif args.command == 'check':
            from .resources import Project
            result = Project(root, writable=False).validate()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if not result['ok']:
                raise SystemExit(1)
        else:
            from .server import build_server
            build_server(root, args.write, runtime_options).run(transport='stdio')
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f'错误：{exc}\n')
