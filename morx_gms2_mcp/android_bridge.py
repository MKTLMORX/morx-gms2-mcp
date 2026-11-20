"""仅在显式开发构建的隔离副本安装桥接；ADB run-as传输，不开放网络端口。"""
import json
import shutil
from . import android
from .runtime import link_free
from .resources import Edit, Project
from .storage import encode, parse_yy

MARKER_FILE = 'morx_android_bridge.json'
PROTOCOL = 2


def install(copied, yyp_name):
    from .cli import bind
    if not link_free(copied): raise ValueError('开发副本路径不能含链接')
    copied = copied.resolve(strict=True)
    bind(copied)
    clone = Project(copied, writable=True)
    clone.install_runtime_bridge(False)
    with clone.lock:
        _, _, key, _, _ = clone._bridge_object(Edit(clone))
    options_path = copied / android.OPTIONS
    options = parse_yy(options_path.read_text(encoding='utf-8-sig'))
    injection = options.get('option_android_application_tag_inject', '') or ''
    # debug属性只由本次显式开发构建设置，正式源选项不会被写回。
    if 'debuggable' in injection or 'testOnly' in injection:
        raise ValueError('已有调试应用属性，不能确认开发构建设置')
    options['option_android_application_tag_inject'] = injection + ' android:debuggable="true"'
    options_path.write_bytes(encode(options))
    marker = {'protocol': PROTOCOL, 'bridge': key, 'transport': 'adb_run_as',
              'development_only': True, 'network_port': None, 'sandbox': 'files/morx_mcp'}
    meta_path = copied / yyp_name
    meta = parse_yy(meta_path.read_text(encoding='utf-8-sig'))
    included = meta.setdefault('IncludedFiles', [])
    if any(row.get('name', '').casefold() == MARKER_FILE for row in included):
        raise ValueError('项目已占用开发桥接描述文件名')
    included.append({'$GMIncludedFile': '', '%Name': MARKER_FILE, 'CopyToMask': 8,
                     'filePath': 'datafiles', 'name': MARKER_FILE,
                     'resourceType': 'GMIncludedFile', 'resourceVersion': '2.0'})
    meta_path.write_bytes(encode(meta))
    (copied / 'datafiles').mkdir(exist_ok=True)
    (copied / 'datafiles' / MARKER_FILE).write_bytes(encode(marker))
    # 本工具的绑定及事务备份只辅助制作副本，不作为构建输入保留。
    private = copied / '.codex'
    if not private.resolve().is_relative_to(copied) or not link_free(private):
        raise ValueError('开发副本配置清理边界不匹配')
    shutil.rmtree(private)
    return marker


def marker(archive, *, require_current=True):
    candidates = [row for row in archive.infolist() if row.filename in
                  ('assets/' + MARKER_FILE, 'base/assets/' + MARKER_FILE)]
    if not candidates:
        return None
    if len(candidates) != 1 or candidates[0].file_size > 4096:
        raise ValueError('开发桥接描述文件无效')
    data = json.loads(archive.read(candidates[0]).decode('utf-8-sig'))
    import re
    if (not isinstance(data, dict) or type(data.get('protocol')) is not int or not 1 <= data['protocol'] <= 65535
            or data.get('development_only') is not True or data.get('transport') != 'adb_run_as'
            or data.get('network_port') is not None or data.get('sandbox') != 'files/morx_mcp'
            or not isinstance(data.get('bridge'), str) or not re.fullmatch('[a-f0-9]{32}', data['bridge'])):
        raise ValueError('Android开发桥接描述文件结构无效')
    if require_current and data['protocol'] != PROTOCOL:
        raise ValueError('不支持的Android开发桥接协议：APK=' + str(data['protocol'])
                         + '，当前=' + str(PROTOCOL) + '；请重新构建开发APK')
    return data
