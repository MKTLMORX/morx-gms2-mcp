"""创建/导入原生 Android 扩展定义；源码仅复制，不执行或改写。"""
import json
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET

from .android import ANDROID_NS
from .exports import expected
from .resources import Edit, name_check, group_check
from .snapshots import relative as snapshot_relative
from .runtime import link_free
from .storage import digest, encode, parse_yy

SUFFIXES = {'.yy', '.gml', '.ext', '.java', '.jar', '.aar', '.xml', '.json', '.png', '.jpg', '.jpeg',
            '.webp', '.txt', '.md', '.pro', '.properties', '.gradle', '.so'}
NATIVE_TEXT = ('androidactivityinject','androidclassname','androidcodeinjection','androidinject','androidmanifestinject',
               'androidsourcedir','author','classname','description','extensionVersion','gradleinject','helpfile',
               'HTML5CodeInjection','installdir','iosCocoaPodDependencies','iosCocoaPods','ioscodeinjection',
               'iosdelegatename','iosplistinject','license','maccompilerflags','maclinkerflags','macsourcedir',
               'optionsFile','packageId','productId','sourcedir','tvosCocoaPodDependencies','tvosCocoaPods','tvoscodeinjection',
               'tvosmaccompilerflags','tvosmaclinkerflags','tvosplistinject')
TYPE_IDS = {'string': 1, 'real': 2}


def _java_text(raw):
    # 保留换行与括号位置；不把注释或字符串里的方法样例视为声明。
    text = raw.decode('utf-8-sig')
    return re.sub(r'/\*.*?\*/|//[^\r\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                  lambda match: re.sub(r'[^\r\n]', ' ', match[0]), text, flags=re.S)


def _java_body(files, class_name):
    simple = class_name.rsplit('.', 1)[-1]
    candidates = [raw for path, raw in files.items() if PurePosixPath(path).name == simple + '.java']
    if len(candidates) != 1:
        raise ValueError('AndroidSource须有唯一同名Java类文件：' + simple + '.java')
    text = _java_text(candidates[0])
    classes = list(re.finditer(r'\bclass\s+' + re.escape(simple) + r'\b[^{};]*\{', text))
    if len(classes) != 1:
        raise ValueError('AndroidSource缺少唯一登记的Java类')
    prefix = text[:classes[0].start()]
    if prefix.count('{') != prefix.count('}'):
        raise ValueError('登记Java类必须是同名文件中的顶层类')
    start = classes[0].end(); depth = 1
    for end in range(start, len(text)):
        if text[end] == '{': depth += 1
        elif text[end] == '}': depth -= 1
        if depth == 0: return text[start:end]
    raise ValueError('登记Java类的括号不完整')


def native_order(value):
    # GMSC对版本化扩展子对象的读取依赖原生属性顺序；只调整对象键，不改值或数组顺序。
    if isinstance(value,dict):
        return {key:native_order(item) for key,item in sorted(value.items(),key=lambda row:row[0].casefold())}
    if isinstance(value,list): return [native_order(item) for item in value]
    return value


def relative_file(value):
    path = PurePosixPath(value)
    if (not value or '\\' in value or ':' in value or path.is_absolute()
            or any(part.startswith('.') or part.endswith((' ', '.')) or re.search(r'[<>:"|?*\x00-\x1f]', part) for part in path.parts)
            or path.suffix.lower() not in SUFFIXES):
        raise ValueError('扩展文件路径或类型不受支持：' + str(value))
    if len(path.parts) > 1 and path.parts[0] != 'AndroidSource':
        raise ValueError('首轮仅导入根目录原生声明/GML及AndroidSource伴随文件')
    return value


def _injections(data):
    changes = []
    for field in ('androidcodeinjection', 'androidmanifestinject', 'androidactivityinject', 'androidinject'):
        text = data.get(field, '') or ''
        if not isinstance(text, str) or len(text.encode('utf-8')) > 128 * 1024 or '<!DOCTYPE' in text or '<!ENTITY' in text:
            raise ValueError('扩展注入须为有界XML，不接受DTD或实体声明')
        if not text.strip(): continue
        try:
            root = ET.fromstring(f'<root xmlns:android="{ANDROID_NS}">{text}</root>')
        except ET.ParseError as exc:
            raise ValueError('扩展注入XML无效：' + field) from exc
        for node in root:
            content = ET.tostring(node, encoding='unicode')
            changes.append({'field': field, 'tag': node.tag, 'attributes': dict(node.attrib),
                            'content': content[:4096], 'truncated': len(content) > 4096,
                            'content_sha256': digest(content.encode('utf-8'))})
    if len(changes) > 64:
        raise ValueError('扩展注入节点超过64项')
    return changes


def _declaration(data, files, allow_void=True):
    if (data.get('resourceType') != 'GMExtension' or data.get('resourceVersion') != '2.0'
            or data.get('$GMExtension') != ''):
        raise ValueError('首轮支持原生GMExtension 2.0空版本标记；不把未知格式重写为v1')
    name_check(data.get('name', ''))
    invalid = [key for key in NATIVE_TEXT if not isinstance(data.get(key),str)]
    invalid += [key for key in ('androidProps','exportToGame','hasConvertedCodeInjection','html5Props','iosProps','tvosProps') if type(data.get(key)) is not bool]
    invalid += [key for key in ('androidPermissions','IncludedResources','iosSystemFrameworkEntries','iosThirdPartyFrameworkEntries',
                               'options','tvosSystemFrameworkEntries','tvosThirdPartyFrameworkEntries') if not isinstance(data.get(key),list)]
    if invalid: raise ValueError('原生扩展缺少字段或类型不符：'+', '.join(invalid))
    if data.get('%Name', data['name']) != data['name'] or data.get('ConfigValues'):
        raise ValueError('扩展名称或配置覆盖不受支持')
    if data.get('copyToTargets', -1) != -1 and not (type(data.get('copyToTargets')) is int and data['copyToTargets'] & 8):
        raise ValueError('扩展不支持Android目标')
    if any(data.get(field) for field in ('androidsourcedir', 'sourcedir', 'macsourcedir', 'installdir')):
        raise ValueError('扩展不能依赖外部源目录或安装脚本目录')
    definitions = data.get('files')
    if not isinstance(definitions, list) or not 1 <= len(definitions) <= 128:
        raise ValueError('扩展files须为1～128项')
    root_mask = data.get('copyToTargets', -1)
    supported_mask = data.get('supportedTargets', -1)
    if type(root_mask) is not int or type(supported_mask) is not int or root_mask < -1 or supported_mask < -1:
        raise ValueError('扩展平台掩码须为-1或非负整数')
    effective_mask = root_mask & supported_mask
    java_class = data.get('androidclassname') or ''
    java = ''
    if data.get('androidProps'):
        if not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*', java_class):
            raise ValueError('Android扩展类名无效')
        java = _java_body(files, java_class)
    functions, names, file_targets = [], set(), []
    for file in definitions:
        if (not isinstance(file, dict) or file.get('resourceType') != 'GMExtensionFile'
                or file.get('resourceVersion') != '2.0' or file.get('$GMExtensionFile') not in ('', 'v1')
                or file.get('kind') not in (2, 4) or file.get('init') or file.get('final') or file.get('ProxyFiles')):
            raise ValueError('首轮只导入无自动初始化/代理文件的GML或Android占位扩展文件')
        if (not isinstance(file.get('constants'),list) or not isinstance(file.get('functions'),list)
                or not isinstance(file.get('origname'),str) or type(file.get('uncompress')) is not bool
                or (file['$GMExtensionFile']=='v1' and type(file.get('usesRunnerInterface')) is not bool)
                or type(file.get('copyToTargets')) is not int):
            raise ValueError('原生扩展文件缺少声明字段或类型不符')
        filename = relative_file(file.get('filename', ''))
        if file.get('name', filename) != filename or file.get('%Name', filename) != filename:
            raise ValueError('扩展文件名称与原生filename不一致')
        if '/' in filename or (file['kind'] == 2 and (not filename.endswith('.gml') or filename not in files)):
            raise ValueError('GML扩展文件缺失或文件名不是原生根目录结构')
        if file['kind'] == 4 and not filename.endswith('.ext'):
            raise ValueError('Android函数声明须使用原生.ext占位文件')
        if file['kind'] == 4 and not data.get('androidProps'):
            raise ValueError('占位函数缺少Android平台绑定')
        file_mask = file['copyToTargets']
        if file_mask < -1: raise ValueError('文件平台掩码须为-1或非负整数')
        file_targets.append({'filename': filename, 'kind': file['kind'], 'copy_to_targets': file_mask,
                             'effective_targets_mask': effective_mask & file_mask})
        for function in file.get('functions', []):
            name = function.get('name', ''); external = function.get('externalName') or name
            name_check(name); name_check(external)
            if (not isinstance(function.get('help'),str) or not isinstance(function.get('documentation'),str)
                    or type(function.get('hidden')) is not bool):
                raise ValueError('原生扩展函数缺少help/documentation/hidden字段')
            args = function.get('args', [])
            if (function.get('resourceType') != 'GMExtensionFunction' or function.get('resourceVersion') != '2.0'
                    or function.get('$GMExtensionFunction') not in ('', 'v1') or function.get('%Name', name) != name
                    or name.casefold() in names or not isinstance(args, list) or len(args) > 16
                    or any(type(value) is not int or value not in (1, 2) for value in args)
                    or type(function.get('returnType')) is not int or function.get('returnType') not in (1, 2)
                    or type(function.get('argCount', 0)) is not int or function.get('argCount', 0) not in (0, len(args))):
                raise ValueError('扩展函数重复或参数/返回类型不受支持：' + name)
            names.add(name.casefold())
            if len(names)>256: raise ValueError('扩展函数超过256项')
            if function.get('kind', file['kind']) != file['kind']:
                raise ValueError('扩展函数kind与来源文件不一致')
            if file['kind'] == 4:
                methods = re.finditer(r'\bpublic\s+(?:(?:static|final|synchronized)\s+)*(String|double|void)\s+' +
                                     re.escape(external) + r'\s*\(([^)]*)\)', java)
                compatible = False
                for method in methods:
                    # 仅匹配登记类本身，不误认辅助类/内部类中的同名方法。
                    prefix = java[:method.start()]
                    if prefix.count('{') != prefix.count('}'): continue
                    result, arguments = method.groups()
                    parameters = [] if not arguments.strip() else arguments.split(',')
                    types = [re.sub(r'\bfinal\s+', '', value.strip()).split()[0] if value.strip() else '' for value in parameters]
                    if types == [('String' if value == 1 else 'double') for value in args] and (
                            (allow_void and result == 'void') or result == ('String' if function['returnType'] == 1 else 'double')):
                        compatible = True
                if not compatible:
                    raise ValueError('Java公开方法与声明签名不匹配：' + external)
            else:
                source = files[filename].decode('utf-8-sig')
                source = re.sub(r'/\*.*?\*/|//[^\r\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', ' ', source, flags=re.S)
                declarations = re.findall(r'\bfunction\s+' + re.escape(external) + r'\s*\(([^)]*)\)', source)
                if len(declarations)!=1:
                    raise ValueError('首轮GML扩展须有唯一显式function声明：'+external)
                parameters = [value.strip() for value in declarations[0].split(',')] if declarations[0].strip() else []
                if len(parameters)!=len(args) or any(not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',value) for value in parameters):
                    raise ValueError('GML函数参数与原生声明不匹配：'+external)
            functions.append({'name': name, 'external_name': external, 'args': args, 'return_type': function['returnType'],
                              'argument_types': [('string' if value == 1 else 'real') for value in args],
                              'return_type_name': 'string' if function['returnType'] == 1 else 'real',
                              'help': function['help'], 'source': filename, 'copy_to_targets': file_mask,
                              'effective_targets_mask': effective_mask & file_mask,
                              'java_signature_checked': file['kind'] == 4})
    permissions = []
    for item in data.get('androidPermissions', []):
        value = item if isinstance(item, str) else item.get('name') if isinstance(item, dict) else None
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', value):
            raise ValueError('扩展权限名称无效')
        permissions.append(value)
    return {'name': data['name'], 'resource_version': data['resourceVersion'], 'version_marker': data['$GMExtension'],
            'android_class': java_class, 'functions': functions, 'permissions': sorted(set(permissions)),
            'copy_to_targets': root_mask, 'supported_targets': supported_mask,
            'effective_targets_mask': effective_mask, 'android_enabled': bool(effective_mask & 8),
            'android_only': effective_mask == 8, 'file_targets': file_targets,
            'manifest_and_build_injections': _injections(data), 'compiled': False}


def inventory(source_path):
    source = Path(source_path)
    if not link_free(source):
        raise ValueError('扩展源不能通过链接或目录联接读取')
    source = source.resolve(strict=True)
    if source.is_file():
        descriptor = source; directory = source.parent
    else:
        directory = source; candidates = list(source.glob('*.yy'))
        if len(candidates) != 1: raise ValueError('扩展目录须恰好有一个原生.yy声明')
        descriptor = candidates[0]
    if descriptor.suffix != '.yy' or descriptor.stat().st_size > 2 * 1024**2:
        raise ValueError('扩展声明须为最多2MiB的.yy')
    files = {}; total = 0; names = set()
    for path in directory.rglob('*'):
        if not link_free(path): raise ValueError('扩展源含链接或目录联接')
        if path.is_dir(): continue
        relative = relative_file(path.relative_to(directory).as_posix())
        if relative.casefold() in names:
            raise ValueError('扩展文件存在Windows大小写冲突')
        names.add(relative.casefold())
        if len(files) >= 512:
            raise ValueError('扩展超过128MiB或512文件')
        before = path.stat()
        with path.open('rb') as stream:
            raw = stream.read(128 * 1024**2 - total + 1)
        after = path.stat()
        if len(raw) > 128 * 1024**2 - total or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError('扩展超过128MiB或读取时发生并发修改')
        total += len(raw)
        files[relative] = raw
    data = parse_yy(files[descriptor.name].decode('utf-8-sig'))
    info = _declaration(data, files)
    if descriptor.name != data['name'] + '.yy':
        raise ValueError('原生扩展声明文件名须与资源名一致')
    manifest = [{'path': path, 'sha256': digest(raw), 'bytes': len(raw)} for path, raw in sorted(files.items())]
    return data, files, {**info, 'source_sha256': digest(files[descriptor.name]),
                        'manifest_sha256': digest(encode(manifest)), 'files': manifest, 'total_bytes': total}


def inspect(project, name=None, source_path=None, offset=0, limit=20):
    from .display import integer
    integer(offset, 'offset', 0, 512); integer(limit, 'limit', 0, 100)
    if (name is None) == (source_path is None):
        raise ValueError('明确选择已登记name或本地source_path之一')
    if name is not None:
        name_check(name)
        with project.lock:
            rows = [row['id'] for row in project.metadata()['resources'] if row['id']['name'] == name]
            if len(rows) != 1 or rows[0]['path'] != f'extensions/{name}/{name}.yy':
                raise ValueError('扩展未唯一登记')
            source_path = str(project.path(rows[0]['path']))
    _, _, result = inventory(source_path)
    rows = result['files']
    functions = result['functions']
    return {**result, 'file_count': len(rows), 'files': rows[offset:offset+limit],
            'file_targets': result['file_targets'][offset:offset+limit],
            'function_count': len(functions), 'functions': functions[offset:offset+limit],
            'function_next_offset': offset+limit if limit and offset+limit<len(functions) else None,
            'next_offset': offset+limit if limit and offset+limit < len(rows) else None}


def import_extension(project, source_path, expected_project_sha256, expected_manifest_sha256, group='扩展', dry_run=True):
    data, files, information = inventory(source_path)
    if information['manifest_sha256'] != expected_manifest_sha256:
        raise ValueError('扩展源文件已改变，请重新inspect_extension并预览')
    return _register(project, data, files, information, expected_project_sha256, group, dry_run)


def _register(project, data, files, information, expected_project_sha256, group, dry_run, source_before=None):
    if not isinstance(group, str): raise ValueError('分组须为字符串')
    group_check(group)
    with project.lock:
        edit = Edit(project)
        expected(edit.raw(project.yyp.name), expected_project_sha256, '项目')
        if source_before: edit.before.update(source_before)
        meta = edit.load(project.yyp.name)
        if meta.get('ConfigValues'):
            raise ValueError('项目含配置覆盖，暂不导入扩展')
        known = {row['id']['path']: row['id']['name'] for row in meta['resources']}
        for reference in data.get('IncludedResources', []):
            if not isinstance(reference, dict) or known.get(reference.get('path')) != reference.get('name'):
                raise ValueError('扩展IncludedResources引用未在目标项目登记')
        incoming = {row['name'].casefold() for row in information['functions']}
        if incoming & ({value.casefold() for value in known.values()} | {data['name'].casefold()}):
            raise ValueError('扩展函数与资源名称重名')
        for entry in meta['resources']:
            other = edit.load(entry['id']['path'])
            if other.get('resourceType') == 'GMExtension':
                existing = {function['name'].casefold() for file in other.get('files', []) for function in file.get('functions', [])}
                if incoming & existing: raise ValueError('扩展函数与现有扩展重名')
                if (data.get('androidProps') and other.get('androidProps') and
                        other.get('androidclassname', '').casefold() == data['androidclassname'].casefold()):
                    raise ValueError('Android扩展类与现有扩展重名')
        name = data['name']
        relative = project._register(edit, 'extensions', name, native_order(data), group)
        for filename, raw in files.items():
            if filename != name + '.yy': edit.new(f'extensions/{name}/' + filename, raw)
        registration = {'resource': {'name': name, 'path': relative}, 'group': group,
                        'project_sha256_before': digest(edit.before[project.yyp.name]),
                        'project_sha256_after': digest(edit.changes[project.yyp.name])}
        return {**edit.commit(dry_run), 'name': name, 'path': relative,
                'registration': registration,
                'android_class': information['android_class'],
                'copy_to_targets': information['copy_to_targets'], 'supported_targets': information['supported_targets'],
                'android_only': information['android_only'],
                'manifest_sha256': information['manifest_sha256'], 'functions': information['functions'][:20],
                'function_count':len(information['functions']), 'functions_truncated':len(information['functions'])>20,
                'permissions': information['permissions'], 'manifest_and_build_injections': information['manifest_and_build_injections'],
                'compiled': False, 'source_files_modified': False}


def _creation_sources(project, sources):
    if not isinstance(sources, list) or not 1 <= len(sources) <= 32:
        raise ValueError('sources须为1～32个项目内明确Java相对路径')
    files, before, manifest = {}, {}, []
    seen = set(); total = 0
    for relative in sources:
        snapshot_relative(relative)
        path = project.path(relative)
        if path.suffix != '.java' or not path.is_file():
            raise ValueError('创建Android扩展只接受现有项目内.java源码')
        name_check(path.stem)
        destination = 'AndroidSource/Java/' + path.name
        if destination.casefold() in seen: raise ValueError('Java目标文件名重复或大小写冲突')
        seen.add(destination.casefold())
        info = path.stat()
        if info.st_size > 2 * 1024**2: raise ValueError('单个Java源超过2MiB')
        with path.open('rb') as stream: raw = stream.read(2 * 1024**2 + 1)
        after = path.stat()
        total += len(raw)
        if len(raw) > 2 * 1024**2 or total > 8 * 1024**2:
            raise ValueError('Java源合计超过8MiB')
        if (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or len(raw) != info.st_size:
            raise ValueError('读取期间Java源发生变化')
        # 验证编码，但保留BOM/CRLF等原始字节。
        raw.decode('utf-8-sig')
        before[relative] = raw; files[destination] = raw
        manifest.append({'source_path': relative, 'destination_path': destination, 'sha256': digest(raw), 'bytes': len(raw)})
    manifest.sort(key=lambda row: row['source_path'])
    return files, before, manifest


def _creation_definition(name, android_class, functions):
    name_check(name); name_check(android_class)
    if not isinstance(functions, list) or not 1 <= len(functions) <= 32:
        raise ValueError('functions须为1～32项{name,external_name,args,return_type,help}声明')
    declarations = []
    for row in functions:
        if not isinstance(row, dict) or set(row) - {'name', 'external_name', 'args', 'return_type', 'help'}:
            raise ValueError('函数只接受name/external_name/args/return_type/help')
        function = row.get('name', ''); external = row.get('external_name', function)
        if not isinstance(function,str) or not isinstance(external,str): raise ValueError('函数名与external_name须为字符串')
        name_check(function); name_check(external)
        args = row.get('args', [])
        returns = row.get('return_type')
        if (not isinstance(args, list) or len(args) > 16 or any(not isinstance(value,str) or value not in TYPE_IDS for value in args)
                or not isinstance(returns,str) or returns not in TYPE_IDS):
            raise ValueError('args最多16项，参数和return_type仅支持string/real')
        help_text = row.get('help', function + '(' + ', '.join(args) + ')')
        if not isinstance(help_text,str) or len(help_text.encode('utf-8')) > 1024 or '\0' in help_text:
            raise ValueError('help须为最多1024字节的字符串，不含NUL')
        declarations.append({'$GMExtensionFunction':'', '%Name':function, 'name':function, 'externalName':external,
            'args':[TYPE_IDS[value] for value in args], 'argCount':0, 'returnType':TYPE_IDS[returns], 'kind':4,
            'help':help_text, 'hidden':False, 'documentation':'', 'resourceType':'GMExtensionFunction','resourceVersion':'2.0'})
    data = {key:'' for key in NATIVE_TEXT}
    data.update({'$GMExtension':'', '%Name':name, 'name':name, 'resourceType':'GMExtension','resourceVersion':'2.0',
        'androidclassname':android_class, 'androidProps':True, 'androidPermissions':[], 'copyToTargets':8, 'supportedTargets':8,
        'extensionVersion':'1.0.0', 'exportToGame':True, 'hasConvertedCodeInjection':True,
        'html5Props':False, 'iosProps':False, 'tvosProps':False, 'tvosclassname':None, 'tvosdelegatename':None,
        'IncludedResources':[], 'options':[], 'iosSystemFrameworkEntries':[], 'iosThirdPartyFrameworkEntries':[],
        'tvosSystemFrameworkEntries':[], 'tvosThirdPartyFrameworkEntries':[],
        'files':[{'$GMExtensionFile':'v1', '%Name':name+'.ext', 'name':name+'.ext', 'filename':name+'.ext', 'kind':4,
            'constants':[], 'functions':declarations, 'copyToTargets':8, 'init':'','final':'','ProxyFiles':[], 'origname':'',
            'uncompress':False, 'usesRunnerInterface':False, 'resourceType':'GMExtensionFile','resourceVersion':'2.0'}]})
    return native_order(data)


def create(project, name, android_class, sources, functions, expected_project_sha256,
           expected_manifest_sha256=None, targets=None, group='扩展', dry_run=True):
    if targets is not None and targets != ['Android']:
        raise ValueError('create_extension首轮只支持targets=["Android"]')
    with project.lock:
        expected(project.yyp.read_bytes(), expected_project_sha256, '项目')
        files, before, manifest = _creation_sources(project, sources)
        manifest_sha = digest(encode(manifest))
        if expected_manifest_sha256 is not None or not dry_run:
            expected(encode(manifest), expected_manifest_sha256, 'Java源清单')
        data = _creation_definition(name, android_class, functions)
        information = _declaration(data, files, allow_void=False)
        information['manifest_sha256'] = manifest_sha
        result = _register(project, data, files, information, expected_project_sha256, group, dry_run, before)
        # 创建声明数量上限32，预览返回全部签名；import仍保留有界摘要。
        return {**result, 'targets':['Android'], 'source_manifest':manifest, 'manifest_sha256':manifest_sha,
                'functions':information['functions'], 'functions_truncated':False,
                'placeholder':{'filename':name+'.ext','kind':4,'copy_to_targets':8,'physical_file_required':False},
                'next':'预览后以相同参数、项目哈希和本次manifest_sha256显式执行；inspect_extension按name读回'}
