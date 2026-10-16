"""检查公开仓库边界；只报告文件和规则，不输出可能敏感的匹配内容。"""
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


ROOT_FILES = {'README.md', 'LICENSE', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md',
              'pyproject.toml', 'MANIFEST.in', '.gitignore'}
RULES = {
    'private_windows_path': re.compile(r'(?i)\b[a-z]:[\\/]+(?:users|documents and settings)[\\/]+[^\s"\'<>]+'),
    'private_unix_path': re.compile(r'/(?:Users|home)/[^\s/"\'<>]+/'),
    'github_token': re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'),
    'api_key': re.compile(r'\bsk-[A-Za-z0-9_-]{20,}'),
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
}
EMAIL = re.compile(r'[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')


def public_email(value):
    return (value.endswith('@users.noreply.github.com') or value == 'noreply@github.com'
            or value.rsplit('@', 1)[-1] in ('example.com', 'example.org', 'example.net'))


def allowed_path(name):
    path = PurePosixPath(name)
    if name in ROOT_FILES:
        return True
    if len(path.parts) == 2:
        return ((path.parts[0] in ('morx_gms2_mcp', 'tests', 'scripts') and path.suffix == '.py')
                or (path.parts[0] == 'examples' and path.suffix in ('.py', '.json', '.toml')))
    return path.parts[:2] == ('.github', 'workflows') and len(path.parts) == 3 and path.suffix == '.yml'


def scan_text(text):
    rules = [name for name, pattern in RULES.items() if pattern.search(text)]
    if any(not public_email(value) for value in EMAIL.findall(text)):
        rules.append('private_email')
    return rules


def main():
    root = Path(__file__).resolve().parents[1]
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.PIPE)
    findings = []
    seen = set()
    def inspect(name, data, scope):
        if not allowed_path(name):
            findings.append({'scope': scope, 'file': name, 'rule': 'outside_public_allowlist'})
        try:
            content = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            findings.append({'scope': scope, 'file': name, 'rule': 'unexpected_binary'})
            return
        for rule in scan_text(content):
            findings.append({'scope': scope, 'file': name, 'rule': rule})
    try:
        # 暂存内容和工作区都检查，防止只扫描到其中一个版本。
        current = git('ls-files', '-z').decode('utf-8').strip('\0').split('\0')
        for name in filter(None, current):
            path = root / name
            if path.is_file():
                inspect(name, path.read_bytes(), 'worktree')
            inspect(name, git('show', ':' + name), 'index')
        commits = git('rev-list', '--all').decode().splitlines()
        for commit in commits:
            entries = git('ls-tree', '-rz', commit).decode('utf-8').strip('\0').split('\0')
            for entry in filter(None, entries):
                metadata, name = entry.split('\t', 1)
                _, kind, oid = metadata.split()
                if kind != 'blob':
                    findings.append({'scope': commit[:7], 'file': name, 'rule': 'unexpected_git_object'})
                    continue
                if (oid, name) not in seen:
                    inspect(name, git('cat-file', 'blob', oid), commit[:7])
                    seen.add((oid, name))
        for entry in git('log', '--all', '--format=%H%x09%ae%x09%ce').decode().splitlines():
            commit, author, committer = entry.split('\t')
            if not public_email(author) or not public_email(committer):
                findings.append({'scope': commit[:7], 'rule': 'private_commit_email'})
    except subprocess.CalledProcessError:
        print(json.dumps({'ok': False, 'error': 'Run this check inside a Git source checkout.'}))
        return 2
    print(json.dumps({'ok': not findings, 'tracked_files': len(current), 'commits': len(commits),
                      'history_file_versions': len(seen), 'findings': findings}, ensure_ascii=True, indent=2))
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
