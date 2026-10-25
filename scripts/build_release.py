"""只按公开源码白名单打包，避免带入工作区和个人游戏内容。"""
import hashlib
import re
import sys
from pathlib import Path
import zipfile


def main():
    # 英文 Windows 的输出编码可能为 cp1252；日志不可使已成功的打包失败。
    sys.stdout.reconfigure(errors='backslashreplace')
    root = Path(__file__).resolve().parents[1]
    version = re.search(r'__version__\s*=\s*[\'"]([^\'"]+)',
                        (root / 'morx_gms2_mcp/__init__.py').read_text(encoding='utf-8')).group(1)
    files = [root / name for name in ('pyproject.toml', 'LICENSE', 'README.md', 'CHANGELOG.md',
        'CONTRIBUTING.md', 'SECURITY.md', '.gitignore', 'MANIFEST.in',
        'docs/setup.md', 'docs/reference.md', 'docs/assets/workflow.svg')]
    for folder, pattern in [('morx_gms2_mcp', '*.py'), ('tests', '*.py'), ('examples', '*.py'),
                            ('examples', '*.json'), ('examples', '*.toml'), ('scripts', '*.py'),
                            ('.github/workflows', '*.yml')]:
        files.extend((root / folder).glob(pattern))
    dest = root / 'dist'
    dest.mkdir(exist_ok=True)
    archive = dest / f'morx-gms2-mcp-{version}-source.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as out:
        for path in sorted(set(files)):
            out.write(path, f'morx-gms2-mcp-{version}/' + path.relative_to(root).as_posix())
    artifacts = sorted(p for p in dest.iterdir() if p.suffix in ('.whl', '.zip') or p.name.endswith('.tar.gz'))
    (dest / 'SHA256SUMS.txt').write_text(''.join(
        hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in artifacts), encoding='utf-8')
    print(f'源码包：{archive}；文件数：{len(set(files))}')


if __name__ == '__main__':
    main()
