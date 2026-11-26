"""固定项目边界、进程互斥、操作日志及保守恢复。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from contextlib import AbstractContextManager
from pathlib import Path, PurePosixPath


def replace_retry(temp, path, attempts=20, delay=0.01):
    """替换目标文件；Windows 上目标可能正被读取，短暂重试再报错。"""
    for attempt in range(attempts):
        try:
            os.replace(temp, path)
            return
        except OSError as exc:
            transient = isinstance(exc, PermissionError) or \
                getattr(exc, 'winerror', None) in (5, 32)
            if not transient or attempt + 1 == attempts:
                raise
            time.sleep(delay)


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def included_file_path(relative):
    """规范的 datafiles 内部路径，适用于登记、迁移和恢复。"""
    from .snapshots import relative as snapshot_path

    path = PurePosixPath(snapshot_path(relative))
    devices = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
               *(f'LPT{i}' for i in range(1, 10))}
    if (len(path.parts) < 2 or path.parts[0] != 'datafiles'
            or any(part.endswith((' ', '.')) or re.search(r'[<>"|\x00-\x1f]', part)
                   or part.split('.', 1)[0].upper() in devices for part in path.parts)):
        raise ValueError('须提供 datafiles 下的文件相对路径：' + relative)
    return path


class FileConflict(ValueError):
    def __init__(self, code, conflicts, transaction=None):
        names = '、'.join(row['path'] for row in conflicts[:5])
        extra = f' 等共 {len(conflicts)} 个文件' if len(conflicts) > 5 else ''
        message = ('写后回读发现文件与预期不符：' if code == 'write_conflict'
                   else '编译前复核发现文件与预期不符：') + names + extra
        if transaction:
            message += '；已保留事务备份 ' + transaction
        super().__init__(message)
        self.details = {'code': code, 'message': message, 'conflicts': conflicts[:64],
                        'conflict_count': len(conflicts), 'diagnostic_id': uuid.uuid4().hex}
        if transaction:
            self.details['transaction'] = transaction


def encode(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def compact_encode(data):
    """单行 JSON；游戏内 GML 文本读取按行处理时使用。"""
    return (json.dumps(data, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')


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
        binding_path = self.path('.codex/gms2-resource-binding.json')
        if not binding_path.is_file():
            raise ValueError('缺少项目绑定文件；禁止隐式绑定其他项目')
        binding = json.loads(binding_path.read_text(encoding='utf-8-sig'))
        if Path(binding['project']).resolve() != self.yyp or binding.get('format') != 1:
            raise ValueError('项目绑定与启动目标不一致')
        self.lock = ProjectLock(self.path('.codex/gms2-resource.lock'))
        self.journal = self.path('.codex/gms2-resource-backups')
        self._recent_files = {}
        self._recent_guard = threading.Lock()

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

    def _write(self, relative, data, *, exclusive=False):
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
            if exclusive:
                # Windows rename refuses an existing target. Elsewhere link does the same.
                if os.name == 'nt':
                    os.rename(temp, p)
                else:
                    os.link(temp, p)
            else:
                replace_retry(temp, p)
        finally:
            temp.unlink(missing_ok=True)

    def _manifest(self, txid):
        if not re.fullmatch('[a-f0-9]{32}', txid):
            raise ValueError('无效事务编号')
        rel = f'.codex/gms2-resource-backups/{txid}/manifest.json'
        data = self.read(rel)
        if data.get('project') != str(self.yyp) or data.get('version') != 1:
            raise ValueError('备份不属于当前项目或版本不受支持')
        included_paths = data.get('included_move_paths', [])
        if (not isinstance(included_paths, list) or len(included_paths) > 256
                or any(not isinstance(path, str) for path in included_paths)
                or not set(included_paths).issubset({item['path'] for item in data['files']})):
            raise ValueError('备份随包文件路径不完整')
        for item in data['files']:
            self._check_write_path(item['path'], included_paths)
        return rel, data

    def transactions(self):
        result = []
        for path in sorted(self.journal.glob('*/manifest.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            rel, data = self._manifest(path.parent.name)
            result.append({'id': path.parent.name, 'status': data['status'],
                           'files': [i['path'] for i in data['files']]})
            if data['status'] == 'write_conflict':
                result[-1]['conflicts'] = data.get('conflicts', [])
                result[-1]['conflict_count'] = data.get('conflict_count', len(result[-1]['conflicts']))
        return result

    def _remember_changes(self, rows, field='after_sha256'):
        with self._recent_guard:
            for row in rows:
                if not row['path'].startswith('.codex/'):
                    self._recent_files[row['path']] = row[field]

    def build_expectations(self, transactions=None):
        """默认复核此服务写过的工程文件；也支持从指定备份恢复校验基线。"""
        if transactions is None:
            with self._recent_guard:
                return self._recent_files.copy()
        if (not isinstance(transactions, list) or len(transactions) > 128
                or any(not isinstance(txid, str) or not re.fullmatch('[a-f0-9]{32}', txid) for txid in transactions)
                or len(set(transactions)) != len(transactions)):
            raise ValueError('expected_transactions 须为最多128个不重复的事务号，按提交顺序传入')
        result = {}
        with self.lock:
            for txid in transactions:
                _, manifest = self._manifest(txid)
                if manifest['status'] not in ('committed', 'write_conflict'):
                    raise ValueError('事务不能作为编译基线：' + txid)
                for row in manifest['files']:
                    if not row['path'].startswith('.codex/'):
                        result[row['path']] = row['after_sha256']
        return result

    def _file_digest(self, relative, check_cancel):
        path = self.path(relative)
        hasher = hashlib.sha256()
        try:
            with path.open('rb') as file:
                before = os.fstat(file.fileno())
                for chunk in iter(lambda: file.read(1024 * 1024), b''):
                    check_cancel()
                    hasher.update(chunk)
        except FileNotFoundError:
            return None
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError('文件在回读期间改变')
        return hasher.hexdigest()

    def verify_files(self, expected, copied=None, *, transaction=None, check_cancel=lambda: None):
        conflicts = []
        for relative, wanted in expected.items():
            check_cancel()
            row = {'path': relative, 'expected_sha256': wanted}
            try:
                actual = self._file_digest(relative, check_cancel)
            except (InterruptedError, TimeoutError):
                raise
            except (ValueError, OSError):
                conflicts.append({**row, 'reason': 'unreadable'})
                continue
            if actual != wanted:
                conflicts.append({**row, 'actual_sha256': actual, 'reason': 'disk_changed'})
            elif copied is not None and copied.get(relative) != wanted:
                conflicts.append({**row, 'actual_sha256': copied.get(relative), 'reason': 'snapshot_changed'})
        if conflicts:
            raise FileConflict('write_conflict' if transaction else 'source_conflict', conflicts, transaction)

    def _check_write_path(self, relative, included_paths=()):
        p = self.path(relative)
        parts = PurePosixPath(relative).parts
        first = parts[0]
        if relative in (self.yyp.name, self.yyp.with_suffix('.resource_order').name,
                        'options/windows/options_windows.yy', 'options/android/options_android.yy',
                        '.codex/morx-snapshot.json', '.codex/morx-android-toolchain.json',
                        '.codex/morx-android-signing.json'):
            return p
        if len(parts) == 4 and parts[:3] == ('options', 'android', 'splash'):
            from .android import SPLASH_PATHS
            if relative not in SPLASH_PATHS:
                raise ValueError('仅允许指定的 Android 横屏和竖屏启动图路径')
            return p
        if len(parts) == 5 and parts[:3] == ('options', 'android', 'morx_icons'):
            from .android import DENSITIES
            if parts[3] not in ('legacy', 'foreground', 'background') or parts[4] not in {d + '.png' for d in DENSITIES}:
                raise ValueError('仅允许固定 Android 密度图标路径')
            return p
        if len(parts) == 4 and parts[:3] == ('options', 'windows', 'icons'):
            from .exports import file_name
            file_name(parts[-1], '.ico')
            return p
        if first == 'datafiles':
            if relative in included_paths:
                included_file_path(relative)
                return p
            if len(parts) < 3 or parts[1] != 'fonts':
                raise ValueError('只允许写入 datafiles/fonts：' + relative)
            return p
        if first == 'extensions':
            from .extensions import relative_file
            from .resources import name_check
            if len(parts) < 3:
                raise ValueError('扩展路径须位于独立资源目录')
            name_check(parts[1])
            relative_file('/'.join(parts[2:]))
            return p
        if first not in {'scripts', 'objects', 'rooms', 'sprites', 'sounds', 'shaders', 'tilesets'}:
            raise ValueError('不是本服务支持的资源路径：' + relative)
        return p

    def commit(self, changes, before, dry_run=False, recovery=False, *, included_move_paths=()):
        with self.lock:
            if not self.writable and not dry_run:
                raise ValueError('服务以只读模式启动')
            if not set(changes).issubset(before):
                raise ValueError('变更缺少原文件快照')
            for rel in changes:
                self._check_write_path(rel, included_move_paths)
            changes = {rel: value for rel, value in changes.items() if value != before[rel]}
            if changes and not dry_run and not recovery and any(
                    t['status'] in ('prepared', 'writing', 'recovery_required') for t in self.transactions()):
                raise ValueError('存在未完成事务，先检查并恢复备份')
            for rel, expected in before.items():
                actual = self.snapshot([rel])[rel]
                if actual != expected:
                    raise ValueError('文件被外部修改，停止写入：' + rel)
            summary = [{'path': rel, 'action': 'delete' if new is None else 'create' if before[rel] is None else 'update',
                        'before_sha256': digest(before[rel]), 'after_sha256': digest(new)} for rel, new in changes.items()]
            if dry_run or not changes:
                return {'project': str(self.yyp), 'dry_run': dry_run, 'changed': list(changes), 'files': summary, 'transaction': None}
            txid = uuid.uuid4().hex
            directory = f'.codex/gms2-resource-backups/{txid}'
            manifest = {'version': 1, 'project': str(self.yyp), 'status': 'prepared', 'files': summary}
            if included_move_paths:
                manifest['included_move_paths'] = sorted(set(included_move_paths) & changes.keys())
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
                    if rel in included_move_paths and before[rel] is None:
                        try:
                            self._write(rel, data, exclusive=True)
                        except FileExistsError as exc:
                            raise ValueError('提交时目标已被外部创建：' + rel) from exc
                    else:
                        self._write(rel, data)
                    written.append(rel)
                self.verify_files({row['path']: row['after_sha256'] for row in summary}, transaction=txid)
                manifest['status'] = 'committed'
                self._write(f'{directory}/manifest.json', encode(manifest))
            except FileConflict as exc:
                # Files may now belong to the IDE. Preserve disk contents and both backups.
                manifest['status'] = 'write_conflict'
                manifest['conflicts'] = exc.details['conflicts']
                manifest['conflict_count'] = exc.details['conflict_count']
                self._write(f'{directory}/manifest.json', encode(manifest))
                self._remember_changes(summary)
                raise
            except Exception as exc:
                failed = []
                for rel in reversed(written):
                    try:
                        if self.snapshot([rel])[rel] != changes[rel]:
                            raise ValueError('回滚前检测到外部修改')
                        if rel in included_move_paths and changes[rel] is None:
                            self._write(rel, before[rel], exclusive=True)
                        else:
                            self._write(rel, before[rel])
                    except Exception:
                        failed.append(rel)
                manifest['status'] = 'recovery_required' if failed else 'rolled_back'
                manifest['error'] = str(exc)
                manifest['restore_failed'] = failed
                self._write(f'{directory}/manifest.json', encode(manifest))
                raise
            self._remember_changes(summary)
            return {'project': str(self.yyp), 'dry_run': False, 'changed': list(changes), 'readback_verified': True,
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
            result = self.commit(changes, before, dry_run=dry_run, recovery=True,
                                 included_move_paths=manifest.get('included_move_paths', ()))
            if not dry_run:
                manifest['status'] = 'restored'
                self._write(manifest_rel, encode(manifest))
                # 恢复本身的日志保留供审计，不再次作为可撤销的资源操作。
                if result['transaction']:
                    undo_rel, undo = self._manifest(result['transaction'])
                    undo['status'] = 'restored'
                    undo['restores'] = txid
                    self._write(undo_rel, encode(undo))
                self._remember_changes(manifest['files'], 'before_sha256')
            return result
