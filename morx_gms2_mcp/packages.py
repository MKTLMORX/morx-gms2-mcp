"""独立异步 Android Package 任务；编译快照可清理，明确交付的成品保留。"""
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from uuid import uuid4

from . import android, android_artifacts, android_signing, android_toolchain, android_bridge, snapshots
from .android_process import run_command
from .display import integer
from .exports import file_name
from .runtime import link_free, utc_time
from .resources import Edit, walk_layers
from .runtime_bridge import OBJECT as BRIDGE_OBJECT, LAYER as BRIDGE_LAYER
from .storage import FileConflict, digest, encode, parse_yy

TERMINAL = {'succeeded', 'failed', 'cancelled', 'timed_out'}


class PackageManager:
    def __init__(self, project, options, build_gate=None):
        self.project, self.options = project, options
        self.guard = threading.RLock()
        self.gate = build_gate or threading.Lock()
        self.jobs = {}

    def _check(self, job):
        if job['cancel'].is_set():
            raise InterruptedError('构建已取消')
        if time.monotonic() >= job['deadline']:
            raise TimeoutError('构建达到截止时间，含排队、复制与验证')

    def _phase(self, job, phase):
        with self.guard:
            job.update(phase=phase, phase_started=time.monotonic())

    def _log(self, job, line):
        # 只输出受管任务的有界日志；原始账号配置与签名值不进入状态对象。
        for value in (str(self.project.root), str(Path.home()), str(job['directory'])):
            line = line.replace(value, '<local>').replace(value.replace('\\','/'), '<local>')
        with self.guard:
            if re.search(r'(?i)(?:SDK|NDK|JDK) path was not properly configured', line):
                job['failure_code'] = 'toolchain_not_configured'
            if re.search(r'(?i)JSON file reader encountered|Cannot load project or resource',line):
                job['failure_code'] = 'resource_load_failed'
            if re.search(r'(?i)Could not resolve .*|Could not GET .*|UnknownHostException',line):
                job['failure_code'] = 'dependency_resolution_failed'
            if line == '[authorization failure]': job['failure_code'] = 'authorization_failed'
            if re.search(r'(?i)(?:pre gradle|gradlew|> Task |FAILURE: Build)', line):
                self._phase(job, 'gradle')
            if re.search(r'(?i)(?:password was incorrect|keystore was tampered|failed to read key|validateSigning.*FAILED)', line):
                job['signing_failure'] = True
            job['cursor'] += 1
            job['logs'].append({'cursor': job['cursor'], 'phase': job['phase'], 'text': line[:4096]})

    def _roots(self, chain, output_directory):
        build_root = android_toolchain.absolute(chain['paths']['build_root']['path'], 'build_root')
        export_root = android_toolchain.absolute(chain['paths']['export_root']['path'], 'export_root')
        output = android_toolchain.absolute(output_directory, 'output_directory')
        if not output.is_relative_to(export_root):
            raise ValueError('output_directory 须位于配置的export_root之内（可等于该目录）')
        protected = [self.project.root, Path.home(), Path(os.environ.get('SystemRoot', 'C:/Windows'))]
        protected += [Path(value) for value in (self.options.runtime_root, self.options.user_directory) if value]
        protected += [Path(chain['paths'][key]['path']) for key in ('sdk_root', 'ndk_root', 'jdk_root')
                      if chain['paths'][key]['path']]
        for root in (build_root, output):
            for forbidden in protected:
                if root == forbidden or (forbidden != Path.home() and root.is_relative_to(forbidden)) or forbidden.is_relative_to(root):
                    raise ValueError('构建/交付目录不能覆盖项目、工具链、账号目录或其父目录')
        return build_root, output

    @staticmethod
    def _free_bytes(path):
        while not path.exists() and path != path.parent:
            path = path.parent
        try:
            return shutil.disk_usage(path).free
        except OSError:
            return None

    def _strip_managed_bridge(self, copied):
        """只从本任务副本剔除带本工具标记的测试物体，源项目与用户GML保持原样。"""
        meta_path = copied / self.project.yyp.name
        meta = parse_yy(meta_path.read_text(encoding='utf-8-sig'))
        refs = [item['id'] for item in meta.get('resources', []) if item['id']['name'] == BRIDGE_OBJECT]
        if not refs:
            return False
        with self.project.lock:
            managed_ref, _, _, _, _ = self.project._bridge_object(Edit(self.project))
        if len(refs) != 1 or refs[0] != managed_ref:
            raise ValueError('副本中的桥接引用不匹配，不能生成不带调试桥接的发行包')
        for item in meta.get('resources', []):
            ref = item['id']
            if not ref['path'].startswith('rooms/'):
                continue
            path = copied / ref['path']
            room = parse_yy(path.read_text(encoding='utf-8-sig'))
            removed = set()
            for layer in walk_layers(room.get('layers', [])):
                original = layer.get('instances', [])
                kept = []
                for instance in original:
                    if instance.get('objectId') == managed_ref:
                        removed.add(instance['name'])
                    else:
                        kept.append(instance)
                if kept != original:
                    layer['instances'] = kept
            if removed:
                room['layers'] = [layer for layer in room.get('layers', [])
                                  if layer.get('name') != BRIDGE_LAYER or layer.get('instances') or layer.get('layers')]
                room['instanceCreationOrder'] = [row for row in room.get('instanceCreationOrder', []) if row.get('name') not in removed]
                path.write_bytes(encode(room))
        meta['resources'] = [item for item in meta['resources'] if item['id'] != managed_ref]
        meta_path.write_bytes(encode(meta))
        order_path = copied / self.project.yyp.with_suffix('.resource_order').name
        if order_path.is_file():
            def prune(value):
                if isinstance(value, list):
                    return [prune(row) for row in value if not (isinstance(row, dict) and row.get('path') == managed_ref['path'])]
                if isinstance(value, dict):
                    return {key: prune(child) for key, child in value.items()}
                return value
            order_path.write_bytes(encode(prune(parse_yy(order_path.read_text(encoding='utf-8-sig')))))
        directory = copied / 'objects' / BRIDGE_OBJECT
        if not directory.resolve().is_relative_to(copied.resolve()) or directory.name != BRIDGE_OBJECT or not link_free(directory):
            raise ValueError('副本桥接清理路径无效')
        shutil.rmtree(directory)
        return True

    def build(self, output_directory, artifact_type='apk', backend='VM', configuration='Default',
              signing_profile='development', artifact_name=None, expected_output_sha256=None,
              max_seconds=1800, allow_downloads=False, dry_run=True, development_bridge=False,
              expected_transactions=None):
        integer(max_seconds, 'max_seconds', 1, 7200)
        if type(allow_downloads) is not bool:
            raise ValueError('allow_downloads 须为布尔值')
        if type(development_bridge) is not bool:
            raise ValueError('development_bridge 须为布尔值')
        if development_bridge and (artifact_type != 'apk' or signing_profile != 'development'):
            raise ValueError('开发桥接仅允许development签名APK，不能加入发行签名或AAB')
        if not isinstance(configuration, str) or not re.fullmatch('[A-Za-z0-9_ -]{1,64}', configuration):
            raise ValueError('configuration 须为明确的项目配置名')
        chain = android_toolchain.inspect(self.project, self.options, backend, artifact_type)
        build_root, output = self._roots(chain, output_directory)
        name = artifact_name or self.project.yyp.stem + '.' + artifact_type
        file_name(name, '.' + artifact_type)
        target = output / name
        if not link_free(target):
            raise ValueError('交付文件不能是链接或目录联接')
        actual = android_artifacts.checksum(target) if target.is_file() else None
        if target.exists() and not target.is_file():
            raise ValueError('目标产物路径不是普通文件')
        if actual != expected_output_sha256:
            raise ValueError('交付目标哈希不匹配；覆盖现有成品须先检查其SHA256')
        with self.project.lock:
            expected_files = self.project.build_expectations(expected_transactions)
            self.project.verify_files(expected_files)
            validation = self.project.validate()
            if not validation['ok']:
                chain['issues'].append({'code': 'project_invalid', 'errors': validation['errors'][:10]})
                chain['ready'] = False
            bridge = self.project.bridge_status()
            if bridge['installed']:
                self.project._bridge_object(Edit(self.project))
            meta = self.project.metadata()
            configurations = {'Default'}
            def config_names(node):
                if isinstance(node, dict):
                    if isinstance(node.get('name'), str): configurations.add(node['name'])
                    for child in node.get('children', []): config_names(child)
            config_names(meta.get('configs', {}))
            if configuration not in configurations:
                raise ValueError('项目构建配置不存在')
            plan = snapshots.plan(self.project)
            # 构建私有配置及密钥不能通过额外依赖、IncludedFile或完整副本模式进入包。
            forbidden_suffixes = {'.jks', '.keystore', '.p12', '.dpapi'}
            for relative in plan['files']:
                if Path(relative).suffix.lower() in forbidden_suffixes or Path(relative).name in ('licence.plist', 'local_settings.json'):
                    raise ValueError('快照包含签名或本机账号材料，拒绝构建')
        signing = android_signing.inspect(self.project, self.options)
        profile = next((row for row in signing['profiles'] if row['name'] == signing_profile), None)
        if profile is None or not profile['keystore_exists'] or not profile['password_available']:
            chain['issues'].append({'code': 'signing_profile_incomplete', 'profile': signing_profile,
                'next': '显式创建独立development签名或引用用户已有正式签名；不自动轮换密钥'})
            chain['ready'] = False
        # 当前无下载隔离保证时不能在无许可下启动可能自动补全 SDK 的 Gradle。
        if not allow_downloads:
            chain['issues'].append({'code': 'downloads_not_authorized',
                'next': 'Gradle可能下载缺失依赖；确认后显式allow_downloads=true，本工具不安装SDK/NDK'})
            chain['ready'] = False
        preview = {'dry_run': dry_run, 'platform': 'Android', 'artifact_type': artifact_type, 'backend': backend,
            'configuration': configuration, 'signing_profile': signing_profile, 'output_path': str(target),
            'expected_output_sha256': actual, 'max_seconds': max_seconds, 'includes_queue': True,
            'development_bridge': development_bridge, 'debuggable': development_bridge,
            'remove_managed_runtime_bridge_from_copy': bridge['installed'],
            'snapshot': {key: plan[key] for key in ('file_count', 'total_bytes', 'by_root', 'mode')},
            'storage': {'build_free_bytes': self._free_bytes(build_root), 'output_free_bytes': self._free_bytes(output),
                        'build_reserve_bytes': plan['total_bytes'] * 2 + 1024**3,
                        'download_bytes': None, 'cache_reused': True,
                        'note': '余量为保守预检，Gradle依赖和最终产物体积须实测；不复制SDK或全量缓存'},
            'toolchain': chain, 'allow_downloads': allow_downloads, 'ready': chain['ready'],
            'source_check_files': len(expected_files)}
        if dry_run:
            return preview
        if not self.options.enabled:
            raise ValueError('打包需 --allow-run；不会自动启用或下载工具链')
        if not chain['ready']:
            return {**preview, 'submitted': False, 'state': 'preflight_failed'}
        with self.guard:
            if sum(job['state'] not in TERMINAL for job in self.jobs.values()) >= 4:
                raise ValueError('最多4个排队/运行构建任务；查询或取消现有任务')
            for old_id, old in list(self.jobs.items()):
                if len(self.jobs) < 32: break
                if old['state'] in TERMINAL and old.get('cleaned'): del self.jobs[old_id]
            if len(self.jobs) >= 32:
                raise ValueError('构建历史已满且存在未清理任务')
            job_id = uuid4().hex
            job = {'id': job_id, 'state': 'queued', 'phase': 'queued', 'phase_started': time.monotonic(),
                   'deadline': time.monotonic() + max_seconds, 'created_at': utc_time(),
                   'expires_at': utc_time(time.time() + max_seconds), 'cancel': threading.Event(),
                   'logs': deque(maxlen=500), 'cursor': 0, 'directory': build_root / job_id,
                   'build_root': build_root, 'copied_files': 0, 'copied_bytes': 0, 'cleaned': False,
                   'request': preview, 'chain': chain, 'target': target, 'process': None, 'expected_files': expected_files,
                   'directory_created': False, 'signing_configuration_sha256': signing['configuration_sha256'],
                   'keystore_sha256': android_artifacts.checksum(Path(android_signing.registry(self.project)[0]['profiles'][signing_profile]['keystore']))}
            self.jobs[job_id] = job
            thread = threading.Thread(target=self._worker, args=(job,), daemon=True)
            job['thread'] = thread
            thread.start()
        return {'job_id': job_id, 'state': 'queued', 'submitted': True,
                'next': '查询build_status/get_build_output，不重复发起构建'}

    def _worker(self, job):
        acquired = False
        try:
            while not self.gate.acquire(timeout=.1): self._check(job)
            acquired = True
            self._check(job)
            job['state'] = 'running'
            request, directory = job['request'], job['directory']
            directory.mkdir(parents=True, exist_ok=False)
            job['directory_created'] = True
            (directory / 'morx-build.json').write_bytes(encode({'job': job['id']}))
            self._phase(job, 'copying')
            copied = directory / 'project'
            with self.project.lock:
                check_cancel = lambda: self._check(job)
                self.project.verify_files(job['expected_files'], check_cancel=check_cancel)
                plan = snapshots.plan(self.project, check_cancel=lambda: self._check(job))
                for relative in plan['files']:
                    if Path(relative).suffix.lower() in ('.jks', '.keystore', '.p12', '.dpapi') or Path(relative).name in ('licence.plist', 'local_settings.json'):
                        raise ValueError('源项目在排队期间加入了禁止复制的本机材料')
                if plan['total_bytes'] != request['snapshot']['total_bytes'] or plan['file_count'] != request['snapshot']['file_count']:
                    raise ValueError('源项目复制范围已改变，请重新预览')
                if shutil.disk_usage(directory).free < plan['total_bytes'] * 2 + 1024**3:
                    raise ValueError('构建临时磁盘空间不足，至少保留快照两倍与1GiB构建余量')
                for relative, size in plan['files'].items():
                    self._check(job)
                    destination = copied / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    path = self.project.path(relative)
                    before = path.stat()
                    count = 0
                    checksum = hashlib.sha256()
                    with path.open('rb') as source, destination.open('xb') as target:
                        for block in iter(lambda: source.read(1024 * 1024), b''):
                            self._check(job); count += len(block)
                            if count > size: raise ValueError('复制时源文件大小改变：' + relative)
                            target.write(block); job['copied_bytes'] += len(block)
                            checksum.update(block)
                    after = path.stat()
                    if count != size or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                        raise ValueError('复制期间源文件改变：' + relative)
                    job['copied_files'] += 1
                    job.setdefault('source_manifest', []).append({'path': relative, 'bytes': count, 'sha256': checksum.hexdigest()})
                source_manifest = sorted(job.pop('source_manifest'), key=lambda row: row['path'])
                job['source_manifest_sha256'] = digest(encode(source_manifest))
                copied_hashes = {row['path']: row['sha256'] for row in source_manifest if row['path'] in job['expected_files']}
                job['stripped_managed_bridge'] = self._strip_managed_bridge(copied)
                if request['development_bridge']:
                    job['bridge_marker'] = android_bridge.install(copied, self.project.yyp.name)
            data, signing_raw = android_signing.registry(self.project)
            if digest(signing_raw) != job['signing_configuration_sha256']:
                raise ValueError('签名配置在排队期间发生变化，请重新预览')
            row = data['profiles'][request['signing_profile']]
            if android_artifacts.checksum(Path(row['keystore'])) != job['keystore_sha256']:
                raise ValueError('签名文件在排队期间发生变化，请重新预览')
            store, key = android_signing.passwords(row)
            license_path = Path(self.options.user_directory) / 'licence.plist'
            encoded_store = android_signing.igor_password(store, license_path)
            encoded_key = android_signing.igor_password(key, license_path)
            private_user = directory / 'user'; private_user.mkdir()
            settings = {native: job['chain']['paths'][name]['path'] or '' for name, native in android_toolchain.NATIVE_PATHS.items()}
            prefix = 'machine.Platform Settings.Android.Keystore.'
            settings.update({prefix+'filename': row['keystore'], prefix+'alias': row['alias'],
                             prefix+'keystore_password': encoded_store, prefix+'keystore_alias_password': encoded_key})
            (private_user / 'local_settings.json').write_bytes(encode(settings))
            candidate = directory / ('candidate.' + request['artifact_type'])
            runtime = Path(self.options.runtime_root)
            args = [str(runtime / 'bin/igor/windows/x64/Igor.exe'), '/rp='+str(runtime), '/uf='+str(private_user),
                    '/lf='+str(license_path), '/project='+str(copied / self.project.yyp.name),
                    '/cache='+str(directory / 'cache'), '/temp='+str(directory / 'temp'),
                    '/runtime='+request['backend'], '/config='+request['configuration'], '/tf='+str(candidate),
                    '--', 'Android', 'Package']
            self._phase(job, 'compiling')
            self.project.verify_files(job['expected_files'], copied_hashes, check_cancel=check_cancel)
            result = run_command(args, timeout=max(.01, job['deadline']-time.monotonic()), cancel=job['cancel'],
                capture_limit=0, cwd=directory, env={**os.environ, 'GRADLE_USER_HOME': job['chain']['paths']['gradle_user_home']['path'],
                                     'GRADLE_OPTS': os.environ.get('GRADLE_OPTS', '') + ' -Dorg.gradle.daemon=false -Dorg.gradle.project.android.builder.sdkDownload=false'},
                on_line=lambda line: self._log(job, line), on_started=lambda process: job.update(process=process),
                private_values=(store, key, encoded_store, encoded_key, row['keystore']))
            self._check(job)
            if result['exit_code']:
                job.setdefault('failure_code', 'signing_failed' if job.get('signing_failure') else 'gradle_failed' if job['phase'] == 'gradle' else 'gml_compile_failed')
                raise ValueError('Igor Android Package失败；请按游标查看受管构建日志')
            if not candidate.is_file():
                job['failure_code'] = 'output_missing'
                raise ValueError('Igor退出0但未生成本次独立产物')
            self._phase(job, 'verifying')
            artifact = android_artifacts.inspect(candidate, job['chain'], cancel=job['cancel'])
            job['verified_candidate'] = {key: artifact[key] for key in ('package_name', 'versionName', 'versionCode', 'min_sdk', 'target_sdk', 'abis', 'signature')}
            if bool(artifact.get('development_bridge')) != request['development_bridge']:
                raise ValueError('最终产物开发桥接与本次显式选择不一致')
            if request['development_bridge'] and (artifact['development_bridge'] != job['bridge_marker'] or not artifact['debuggable']):
                raise ValueError('开发包桥接或原生debuggable属性未通过成包验证')
            source_options = android.settings(parse_yy((copied / android.OPTIONS).read_text(encoding='utf-8-sig')))
            expected_abis = sorted(value for name, value in android.ABIS.items() if source_options.get('arch_' + name))
            if artifact['package_name'] != android.package_name(source_options) or artifact['abis'] != expected_abis:
                raise ValueError('成包包名或ABI与本次隔离项目配置不一致')
            for name, field in (('minimum_sdk', 'min_sdk'), ('target_sdk', 'target_sdk')):
                if source_options.get(name) and artifact[field] != int(source_options[name]):
                    job['failure_code'] = 'artifact_metadata_mismatch'
                    raise ValueError('成包SDK与本次配置不一致：' + name + '，项目=' + str(source_options[name]) + '，成包=' + str(artifact[field]))
            self._check(job)
            self._phase(job, 'delivering')
            target = job['target']
            if not link_free(target):
                raise ValueError('交付文件路径被更改为链接，拒绝写入')
            current = android_artifacts.checksum(target) if target.is_file() else None
            if current != request['expected_output_sha256']:
                raise ValueError('交付目标在构建期间被修改，拒绝覆盖')
            target.parent.mkdir(parents=True, exist_ok=True)
            # 先在交付目录写入完整临时文件，跨磁盘时仍只替换最终单个文件。
            staged = target.with_name(target.name + '.morx-' + job['id'] + '.tmp')
            staged_identity = None
            try:
                with candidate.open('rb') as source, staged.open('xb') as destination:
                    info = os.fstat(destination.fileno())
                    staged_identity = (info.st_dev, info.st_ino)
                    for block in iter(lambda: source.read(1024 * 1024), b''):
                        self._check(job); destination.write(block)
                self._check(job)
                if android_artifacts.checksum(staged) != artifact['sha256']:
                    raise ValueError('交付文件校验失败')
                if (android_artifacts.checksum(target) if target.is_file() else None) != current:
                    raise ValueError('交付目标发生并发修改')
                if not link_free(target):
                    raise ValueError('交付路径含链接，拒绝替换')
                os.replace(staged, target)
            finally:
                if staged_identity is not None and staged.exists():
                    info = staged.lstat()
                    if (info.st_dev, info.st_ino) == staged_identity and link_free(staged):
                        staged.unlink()
            artifact['path'] = str(target)
            job.update(state='succeeded', artifact=artifact)
        except InterruptedError:
            job.update(state='cancelled', failure_code='cancelled')
        except TimeoutError:
            job.update(state='timed_out', failure_code='build_timeout')
        except FileConflict as exc:
            job.update(state='failed', failure_code='source_conflict', error={'type': type(exc).__name__, **exc.details})
        except Exception as exc:
            job.update(state='failed', failure_code=job.get('failure_code', 'build_failed'),
                       error={'type': type(exc).__name__, 'message': str(exc)[:2048]})
        finally:
            self._phase(job, 'cleanup')
            self._cleanup(job)
            job['finished_at'] = utc_time()
            self._phase(job, 'finished')
            if acquired: self.gate.release()

    def status(self, job_id):
        with self.guard:
            if job_id not in self.jobs: raise ValueError('构建任务不存在或已淘汰')
            job = self.jobs[job_id]
            return {'job_id': job_id, **{key: job[key] for key in ('state', 'phase', 'created_at', 'expires_at',
                    'copied_files', 'copied_bytes', 'source_manifest_sha256', 'cleaned', 'failure_code', 'error', 'artifact', 'verified_candidate', 'cleanup_error', 'finished_at') if key in job},
                    'remaining_seconds': max(0, round(job['deadline']-time.monotonic(), 1)) if job['state'] not in TERMINAL else 0,
                    'latest_log_cursor': job['cursor']}

    def output(self, job_id, after=0, limit=100):
        integer(after, 'after', 0, 2**63-1); integer(limit, 'limit', 1, 200)
        with self.guard:
            self.status(job_id); job = self.jobs[job_id]
            rows = [row for row in job['logs'] if row['cursor'] > after][:limit]
            return {'job_id': job_id, 'lines': rows, 'next_cursor': rows[-1]['cursor'] if rows else after,
                    'truncated': bool(job['logs'] and after < job['logs'][0]['cursor']-1)}

    def cancel(self, job_id, dry_run=True):
        with self.guard:
            result = self.status(job_id)
            if not dry_run and result['state'] not in TERMINAL: self.jobs[job_id]['cancel'].set()
            return {'job_id': job_id, 'dry_run': dry_run, 'state': result['state'],
                    'cancel_requested': not dry_run and result['state'] not in TERMINAL}

    def _cleanup(self, job):
        try:
            directory = job['directory']
            if directory.exists():
                if not job['directory_created']:
                    raise ValueError('不是本任务创建的目录')
                marker = json.loads((directory / 'morx-build.json').read_text(encoding='utf-8-sig'))
                if (directory.name != job['id'] or marker != {'job': job['id']} or not link_free(directory)
                        or not os.path.samefile(directory.resolve().parent, job['build_root'])):
                    raise ValueError('构建清理所有权或路径校验失败')
                # marker最后删除，使文件占用导致的半途失败仍可校验并重试。
                for path in directory.iterdir():
                    if path.name == 'morx-build.json': continue
                    if not link_free(path): raise ValueError('清理目录出现链接')
                    if path.is_dir(): shutil.rmtree(path)
                    else: path.unlink()
                (directory / 'morx-build.json').unlink()
                directory.rmdir()
            job['cleaned'] = not directory.exists()
            job.pop('cleanup_error', None)
        except Exception as exc:
            job['cleaned'] = False
            job['cleanup_error'] = type(exc).__name__

    def cleanup(self, job_id, dry_run=True):
        with self.guard:
            result = self.status(job_id)
            job = self.jobs[job_id]
            if job['thread'].is_alive() or result['phase'] != 'finished':
                raise ValueError('任务尚未收尾，请先取消并等待finished再清理')
            if not dry_run: self._cleanup(job)
            return {**self.status(job_id), 'dry_run': dry_run, 'deliverable_retained': True}

    def close(self):
        for job in self.jobs.values():
            if job['state'] not in TERMINAL: job['cancel'].set()
        for job in self.jobs.values(): job['thread'].join(15)
