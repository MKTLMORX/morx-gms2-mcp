"""固定项目边界、进程互斥、操作日志及保守恢复。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from contextlib import AbstractContextManager
from pathlib import Path, PurePosixPath


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def encode(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def parse_yy(text):
    text = text.lstrip('\ufeff')
    out, quoted, escaped = [], False, False
    for i, char in enumerate(text):
        if quoted:
            out.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        if char == ',' and text[i + 1:].lstrip().startswith(('}', ']')):
            continue
        out.append(char)
    return json.loads(''.join(out))


class ProjectLock(AbstractContextManager):
    """同一服务可重入，不同服务进程同时写入时立即拒绝。IDE 不参与此锁。"""
    def __init__(self, path):
        self.path, self.depth = path, 0
        self.thread = threading.RLock()

    def __enter__(self):
        self.thread.acquire()
        try:
            if self.depth == 0:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.file = self.path.open('a+b')
                if self.file.tell() == 0:
                    self.file.write(b'0')
                    self.file.flush()
                self.file.seek(0)
                try:
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    self.file.close()
                    raise ValueError('其他 MCP 进程正在操作该项目，请稍后重试') from exc
            self.depth += 1
            return self
        except BaseException:
            self.thread.release()
            raise

    def __exit__(self, *args):
        self.depth -= 1
        try:
            if self.depth == 0:
                self.file.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
                self.file.close()
        finally:
            self.thread.release()


class Storage:
    def __init__(self, root: Path, writable=True):
        self.root = root.resolve(strict=True)
        if any(p.casefold() == '参考项目' for p in self.root.parts):
            raise ValueError('参考项目只读，资源服务拒绝绑定')
        projects = list(self.root.glob('*.yyp'))
        if len(projects) != 1:
            raise ValueError('目录必须恰好包含一个 yyp')
        self.yyp = self.path(projects[0].name)
        self.writable = writable
        if not (self.root / '.mcp-probe-project').is_file():
            binding_path = self.path('.codex/gms2-resource-binding.json')
            if not binding_path.is_file():
                raise ValueError('缺少项目绑定文件；禁止隐式绑定其他项目')
            binding = json.loads(binding_path.read_text(encoding='utf-8-sig'))
            if Path(binding['project']).resolve() != self.yyp or binding.get('format') != 1:
                raise ValueError('项目绑定与启动目标不一致')
        self.lock = ProjectLock(self.path('.codex/gms2-resource.lock'))
        self.journal = self.path('.codex/gms2-resource-backups')

    def path(self, relative):
        if not isinstance(relative, str) or '\\' in relative or ':' in relative:
            raise ValueError('路径必须是项目内的正斜线相对路径')
        parts = PurePosixPath(relative).parts
        if not parts or PurePosixPath(relative).is_absolute() or any(p in ('..', '.') for p in parts):
            raise ValueError('禁止绝对路径或父目录跳转')
        current = self.root
        for part in parts:
            current = current / part
            if current.is_symlink() or (current.exists() and current.resolve() != current.absolute()):
                raise ValueError('不通过符号链接或目录联接操作资源')
        p = current.resolve()
        if not p.is_relative_to(self.root) or p == self.root:
            raise ValueError('路径越出绑定项目')
        return p

    def read(self, relative):
        return parse_yy(self.path(relative).read_text(encoding='utf-8-sig'))

    def snapshot(self, names):
        return {n: self.path(n).read_bytes() if self.path(n).is_file() else None for n in names}

    def _write(self, relative, data):
        p = self.path(relative)
        if data is None:
            p.unlink(missing_ok=True)
            parent = p.parent
            while parent != self.root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        temp = p.with_name(p.name + '.mcp-tmp-' + uuid.uuid4().hex)
        try:
            with temp.open('wb') as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, p)
        finally:
            temp.unlink(missing_ok=True)

    def _manifest(self, txid):
        if not re.fullmatch('[a-f0-9]{32}', txid):
            raise ValueError('无效事务编号')
        rel = f'.codex/gms2-resource-backups/{txid}/manifest.json'
        data = self.read(rel)
        if data.get('project') != str(self.yyp) or data.get('version') != 1:
            raise ValueError('备份不属于当前项目或版本不受支持')
        for item in data['files']:
            self._check_write_path(item['path'])
        return rel, data

    def transactions(self):
        result = []
        for path in sorted(self.journal.glob('*/manifest.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            rel, data = self._manifest(path.parent.name)
            result.append({'id': path.parent.name, 'status': data['status'],
                           'files': [i['path'] for i in data['files']]})
        return result

    def _check_write_path(self, relative):
        p = self.path(relative)
        first = PurePosixPath(relative).parts[0]
        if relative not in (self.yyp.name, self.yyp.with_suffix('.resource_order').name,
                             'options/windows/options_windows.yy') and first not in {
                'scripts', 'objects', 'rooms', 'sprites', 'sounds', 'shaders'}:
            raise ValueError('不是本服务支持的资源路径：' + relative)
        return p

    def commit(self, changes, before, dry_run=False, recovery=False):
        with self.lock:
            if not self.writable and not dry_run:
                raise ValueError('服务以只读模式启动')
            if not set(changes).issubset(before):
                raise ValueError('变更缺少原文件快照')
            for rel in changes:
                self._check_write_path(rel)
            if not recovery and any(t['status'] in ('prepared', 'writing', 'recovery_required') for t in self.transactions()):
                raise ValueError('存在未完成事务，先检查并恢复备份')
            for rel, expected in before.items():
                actual = self.snapshot([rel])[rel]
                if actual != expected:
                    raise ValueError('文件被外部修改，停止写入：' + rel)
            changes = {rel: value for rel, value in changes.items() if value != before[rel]}
            summary = [{'path': rel, 'action': 'delete' if new is None else 'create' if before[rel] is None else 'update',
                        'before_sha256': digest(before[rel]), 'after_sha256': digest(new)} for rel, new in changes.items()]
            if dry_run or not changes:
                return {'project': str(self.yyp), 'dry_run': dry_run, 'changed': list(changes), 'files': summary, 'transaction': None}
            txid = uuid.uuid4().hex
            directory = f'.codex/gms2-resource-backups/{txid}'
            manifest = {'version': 1, 'project': str(self.yyp), 'status': 'prepared', 'files': summary}
            for rel in changes:
                if before[rel] is not None:
                    self._write(f'{directory}/before/{rel}', before[rel])
                if changes[rel] is not None:
                    self._write(f'{directory}/after/{rel}', changes[rel])
            self._write(f'{directory}/manifest.json', encode(manifest))
            written = []
            try:
                manifest['status'] = 'writing'
                self._write(f'{directory}/manifest.json', encode(manifest))
                for rel, data in changes.items():
                    if self.snapshot([rel])[rel] != before[rel]:
                        raise ValueError('提交时检测到外部修改：' + rel)
                    self._write(rel, data)
                    written.append(rel)
                manifest['status'] = 'committed'
                self._write(f'{directory}/manifest.json', encode(manifest))
            except Exception as exc:
                failed = []
                for rel in reversed(written):
                    try:
                        if self.snapshot([rel])[rel] != changes[rel]:
                            raise ValueError('回滚前检测到外部修改')
                        self._write(rel, before[rel])
                    except Exception:
                        failed.append(rel)
                manifest['status'] = 'recovery_required' if failed else 'rolled_back'
                manifest['error'] = str(exc)
                manifest['restore_failed'] = failed
                self._write(f'{directory}/manifest.json', encode(manifest))
                raise
            return {'project': str(self.yyp), 'dry_run': False, 'changed': list(changes),
                    'files': summary, 'transaction': txid, 'backup': str(self.path(directory))}

    def restore(self, txid, dry_run=True):
        with self.lock:
            manifest_rel, manifest = self._manifest(txid)
            if manifest['status'] in ('restored', 'rolled_back'):
                return {'changed': [], 'transaction': None, 'status': manifest['status'], 'dry_run': dry_run}
            # 恢复旧事务会破坏后续依赖，仅允许最新尚未撤销的事务。
            live = [t for t in self.transactions() if t['status'] not in ('restored', 'rolled_back')]
            if not live or live[0]['id'] != txid:
                raise ValueError('请先撤销更新的事务，禁止越过后续操作恢复')
            changes = {}
            before = self.snapshot(i['path'] for i in manifest['files'])
            for item in reversed(manifest['files']):
                rel = item['path']
                if digest(before[rel]) not in (item['before_sha256'], item['after_sha256']):
                    raise ValueError('文件已被 IDE 或用户修改，拒绝覆盖：' + rel)
                old = None
                if item['before_sha256'] is not None:
                    old = self.path(f'.codex/gms2-resource-backups/{txid}/before/{rel}').read_bytes()
                    if digest(old) != item['before_sha256']:
                        raise ValueError('备份内容损坏：' + rel)
                changes[rel] = old
            result = self.commit(changes, before, dry_run=dry_run, recovery=True)
            if not dry_run:
                manifest['status'] = 'restored'
                self._write(manifest_rel, encode(manifest))
                # 恢复本身的日志保留供审计，不再次作为可撤销的资源操作。
                if result['transaction']:
                    undo_rel, undo = self._manifest(result['transaction'])
                    undo['status'] = 'restored'
                    undo['restores'] = txid
                    self._write(undo_rel, encode(undo))
            return result
