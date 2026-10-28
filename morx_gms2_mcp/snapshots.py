"""按注册依赖规划独立副本；不缓存、不链接源文件，不删除用户文件。"""
import json
import os
from pathlib import Path, PurePosixPath

from .storage import digest, encode

POLICY = '.codex/morx-snapshot.json'
DEFAULT = {'version': 1, 'mode': 'dependencies', 'include_paths': [], 'exclude_paths': []}
PRIVATE = {'.git', '.codex', '.venv', 'venv', 'dist', 'build', 'node_modules', '__pycache__'}


def relative(value):
    if (not isinstance(value, str) or not value or len(value) > 512 or '\\' in value
            or ':' in value or any(c in value for c in '*?[]\x00')
            or any(p in ('', '.', '..') for p in value.split('/'))):
        raise ValueError('快照路径须为项目内明确的文件/目录，不接受通配符、绝对路径或父目录')
    parts = PurePosixPath(value).parts
    if not parts or any(p.startswith('.') or p.casefold() in PRIVATE for p in parts):
        raise ValueError('快照不包含隐藏目录、工具环境或构建缓存')
    return value


def policy(project):
    path = project.path(POLICY)
    raw = path.read_bytes() if path.exists() else None
    if raw is not None and len(raw) > 32768:
        raise ValueError('快照配置超过 32 KiB')
    data = json.loads(raw.decode('utf-8-sig')) if raw is not None else dict(DEFAULT)
    if (not isinstance(data, dict) or set(data) != set(DEFAULT) or data['version'] != 1
            or data['mode'] not in ('dependencies', 'full')):
        raise ValueError('不支持的快照配置')
    for field in ('include_paths', 'exclude_paths'):
        values = data[field]
        if not isinstance(values, list) or len(values) > 64:
            raise ValueError(field + ' 须为最多 64 个明确路径')
        for value in values:
            relative(value)
        if len({v.casefold() for v in values}) != len(values):
            raise ValueError('快照配置路径重复')
    return data, raw


def under(name, root):
    return name.casefold() == root.casefold() or name.casefold().startswith(root.casefold() + '/')


def plan(project, configuration=None, check_cancel=lambda: None):
    """只枚举选择的子树，避免扫描未登记的大型参考工程。"""
    data = configuration or policy(project)[0]
    meta = project.read(project.yyp.name)
    required = {project.yyp.name}
    roots = set()
    order = project.yyp.with_suffix('.resource_order').name
    if project.path(order).exists():
        required.add(order)
    for row in meta.get('resources', []):
        path = relative(row['id']['path'])
        if not project.path(path).is_file():
            raise ValueError('登记资源文件缺失：' + path)
        required.add(path)
        parent = str(PurePosixPath(path).parent)
        if parent == '.':
            raise ValueError('根目录资源无法安全推断伴随文件；请先整理到独立资源目录')
        roots.add(parent)
    # 配置图标、扩展伴随文件、字体和运行时数据不做扩展名猜测。
    for name in ('options', 'datafiles'):
        if project.path(name).exists():
            roots.add(name)
    for row in meta.get('IncludedFiles', []):
        path = relative(row['filePath'].rstrip('/') + '/' + row['name'])
        if not project.path(path).is_file():
            raise ValueError('Included File 缺失：' + path)
        required.add(path)
    extras = data['include_paths']
    excluded = data['exclude_paths']
    for name in extras:
        if not project.path(name).exists():
            raise ValueError('追加依赖不存在：' + name)
    for name in excluded:
        if any(under(p, name) or under(name, p) for p in required | roots):
            raise ValueError('不能排除登记资源、项目选项或数据文件：' + name)
        if any(under(p, name) for p in extras):
            raise ValueError('追加依赖同时被排除：' + name)
    selected = {}
    def excluded_path(name):
        return any(under(name, p) for p in excluded)
    def add_file(name):
        check_cancel()
        if excluded_path(name):
            return
        path = project.path(name)
        if not path.is_file():
            raise ValueError('快照依赖不是普通文件：' + name)
        selected[name] = path.stat().st_size
        if len(selected) > 100000:
            raise ValueError('项目运行副本超过 100000 文件')
    def add_tree(name):
        check_cancel()
        if excluded_path(name):
            return
        path = project.path(name)
        if path.is_file():
            add_file(name)
            return
        for directory, folders, files in os.walk(path, followlinks=False):
            check_cancel()
            rel_name = Path(directory).relative_to(project.root).as_posix()
            rel = project.path(rel_name)
            required_tree = any(under(rel_name, root) for root in roots)
            folders[:] = [f for f in folders if (required_tree or (not f.startswith('.') and f.casefold() not in PRIVATE))
                          and not excluded_path((rel / f).relative_to(project.root).as_posix())]
            for folder in folders:
                project.path((rel / folder).relative_to(project.root).as_posix())
            for file in files:
                if required_tree or not file.startswith('.'):
                    add_file((rel / file).relative_to(project.root).as_posix())
    for name in sorted(required):
        add_file(name)
    targets = roots | set(extras)
    if data['mode'] == 'full':
        targets |= {p.name for p in project.root.iterdir()
                    if not p.name.startswith('.') and p.name.casefold() not in PRIVATE}
    for name in sorted(targets):
        if not any(name != other and under(name, other) for other in targets):
            add_tree(name)
    total = sum(selected.values())
    if total > 2 * 1024**3:
        raise ValueError('项目运行副本超过 2 GiB；先检查复制预览及额外依赖')
    by_root = {}
    for name, size in selected.items():
        row = by_root.setdefault(name.split('/')[0], {'files': 0, 'bytes': 0})
        row['files'] += 1
        row['bytes'] += size
    omitted = sorted(p.name for p in project.root.iterdir() if p.name not in by_root)
    return {'mode': data['mode'], 'files': selected, 'file_count': len(selected), 'total_bytes': total,
            'by_root': by_root, 'omitted_root_entries': omitted,
            'include_paths': extras, 'exclude_paths': excluded}


def inspect(project, offset=0, limit=50):
    from .display import integer
    integer(offset, 'offset', 0, 100000)
    integer(limit, 'limit', 0, 200)
    with project.lock:
        data, raw = policy(project)
        result = plan(project, data)
        rows = sorted(result.pop('files').items())
        result.update(policy_sha256=digest(raw) if raw is not None else None,
                      files=[{'path': p, 'bytes': n} for p, n in rows[offset:offset+limit]],
                      offset=offset, next_offset=offset+limit if limit and offset+limit < len(rows) else None,
                      note='统计仅为源快照；编译缓存、输出与存档还需要额外空间。未选目录不会递归扫描。')
        return result


def configure(project, mode='dependencies', include_paths=None, exclude_paths=None,
              expected_sha256=None, dry_run=True):
    with project.lock:
        _, raw = policy(project)
        if expected_sha256 != (digest(raw) if raw is not None else None):
            raise ValueError('快照配置哈希不一致，请重新查询')
        data = {'version': 1, 'mode': mode, 'include_paths': [] if include_paths is None else include_paths,
                'exclude_paths': [] if exclude_paths is None else exclude_paths}
        if mode not in ('dependencies', 'full'):
            raise ValueError('mode 须为 dependencies 或 full')
        for field in ('include_paths', 'exclude_paths'):
            if not isinstance(data[field], list) or len(data[field]) > 64:
                raise ValueError(field + ' 须为最多64个明确路径')
            for path in data[field]:
                relative(path)
            if len(set(p.casefold() for p in data[field])) != len(data[field]):
                raise ValueError('快照配置路径重复')
        preview = plan(project, data)
        preview.pop('files')
        result = project.commit({POLICY: encode(data)}, {POLICY: raw}, dry_run=dry_run)
        return {**result, 'snapshot': preview}
