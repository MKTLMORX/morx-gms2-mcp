"""原生扩展格式、源清单哈希、签名和注册事务，使用独立自建Java/GML夹具。"""
import copy
import asyncio
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from morx_gms2_mcp import extensions
from morx_gms2_mcp.storage import digest, encode
from tests.test_core import ProjectFixture


def extension_fixture(name='MorxProbe'):
    function = {'$GMExtensionFunction': '', '%Name': 'morx_probe_inc', 'name': 'morx_probe_inc',
                'resourceType': 'GMExtensionFunction', 'resourceVersion': '2.0', 'externalName': 'morx_probe_inc',
                'kind': 4, 'argCount': 0, 'args': [2], 'returnType': 2,'help':'','hidden':False,'documentation':''}
    filename = name + '.ext'
    data = {'$GMExtension': '', '%Name': name, 'name': name, 'resourceType': 'GMExtension', 'resourceVersion': '2.0',
            'androidProps': True, 'androidclassname': name, 'androidPermissions': [], 'copyToTargets': 8,
            'extensionVersion': '1.0.0', 'androidcodeinjection': '', 'IncludedResources': [],
            'unknown_native': {'keep': True}, 'files': [{'$GMExtensionFile': 'v1', '%Name': filename, 'name': filename,
                'filename': filename, 'kind': 4, 'resourceType': 'GMExtensionFile', 'resourceVersion': '2.0',
                'init': '', 'final': '', 'ProxyFiles': [], 'functions': [function], 'constants':[],
                'copyToTargets':-1,'origname':'','uncompress':False,'usesRunnerInterface':False}]}
    # 原生扩展格式中的空平台字段也必须存在；不是来自第三方扩展的实现代码。
    for key in ('androidactivityinject','androidinject','androidmanifestinject','androidsourcedir','author','classname',
                'description','gradleinject','helpfile','HTML5CodeInjection','installdir','iosCocoaPodDependencies','iosCocoaPods',
                'ioscodeinjection','iosdelegatename','iosplistinject','license','maccompilerflags','maclinkerflags','macsourcedir',
                'optionsFile','packageId','productId','sourcedir','tvosCocoaPodDependencies','tvosCocoaPods','tvoscodeinjection',
                'tvosmaccompilerflags','tvosmaclinkerflags','tvosplistinject'):
        data[key]=''
    for key in ('iosSystemFrameworkEntries','iosThirdPartyFrameworkEntries','options','tvosSystemFrameworkEntries','tvosThirdPartyFrameworkEntries'):
        data[key]=[]
    for key in ('html5Props','iosProps','tvosProps'): data[key]=False
    data.update(exportToGame=True,hasConvertedCodeInjection=True,supportedTargets=-1,tvosclassname=None,tvosdelegatename=None)
    return data


class ExtensionTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name) / 'MorxProbe'
        self.source.mkdir()
        self.descriptor = self.source / 'MorxProbe.yy'
        self.descriptor.write_bytes(encode(extension_fixture()))
        java = self.source / 'AndroidSource/Java/MorxProbe.java'
        java.parent.mkdir(parents=True)
        java.write_text('public class MorxProbe { public double morx_probe_inc(double value) { return value+1; } }', encoding='utf-8')
        self.java = java

    def project_sha(self):
        return digest(self.project.yyp.read_bytes())

    def import_source(self, dry_run=True):
        info = extensions.inspect(self.project, source_path=str(self.source))
        return extensions.import_extension(self.project, str(self.source), self.project_sha(), info['manifest_sha256'], dry_run=dry_run)

    def test_preview_import_restore_and_version_marker_are_native(self):
        raw = self.descriptor.read_bytes(); java = self.java.read_bytes(); source_yyp = self.project.yyp.read_bytes()
        preview = self.import_source()
        self.assertTrue(preview['dry_run'])
        self.assertFalse((self.root / 'extensions').exists())
        result = self.import_source(False)
        data = self.project.read('extensions/MorxProbe/MorxProbe.yy')
        self.assertEqual(data['$GMExtension'], '')
        self.assertEqual(data['unknown_native'], {'keep': True})
        self.assertEqual((self.root / 'extensions/MorxProbe/AndroidSource/Java/MorxProbe.java').read_bytes(), java)
        self.assertTrue(extensions.inspect(self.project, name='MorxProbe')['functions'][0]['java_signature_checked'])
        self.assertTrue(self.project.validate()['ok'])
        self.project.restore(result['transaction'], False)
        self.assertEqual(self.project.yyp.read_bytes(), source_yyp)
        self.assertFalse((self.root / 'extensions').exists())
        self.assertEqual(self.descriptor.read_bytes(), raw)

    def test_source_manifest_and_project_hash_conflicts_never_partially_register(self):
        info = extensions.inspect(self.project, source_path=str(self.source))
        self.java.write_text(self.java.read_text(encoding='utf-8') + '\n// source changed\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '改变'):
            extensions.import_extension(self.project, str(self.source), self.project_sha(), info['manifest_sha256'], dry_run=False)
        info = extensions.inspect(self.project, source_path=str(self.source))
        with self.assertRaises(ValueError):
            extensions.import_extension(self.project, str(self.source), '0'*64, info['manifest_sha256'], dry_run=False)
        self.assertFalse((self.root / 'extensions').exists())

    def test_unknown_format_bad_java_signature_and_comment_only_method_are_rejected(self):
        original = extension_fixture()
        for edit in ({'$GMExtension': 'v1'}, {'androidsourcedir': 'C:/external'}, {'copyToTargets': 1}):
            data = copy.deepcopy(original); data.update(edit); self.descriptor.write_bytes(encode(data))
            with self.subTest(edit=edit), self.assertRaises(ValueError): self.import_source(False)
        self.descriptor.write_bytes(encode(original))
        for source in ('public class MorxProbe { public String morx_probe_inc(double value) {return "x";} }',
                       'public class MorxProbe { /* public double morx_probe_inc(double value) {return value;} */ }'):
            self.java.write_text(source, encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '签名'): self.import_source(False)
        self.assertFalse((self.root / 'extensions').exists())

    def test_untrusted_file_types_injections_and_missing_references_are_rejected(self):
        forbidden = self.source / 'install.exe'; forbidden.write_bytes(b'never execute')
        with self.assertRaises(ValueError): self.import_source(False)
        forbidden.unlink()
        data = extension_fixture(); data['androidcodeinjection'] = '<!DOCTYPE a><YYAndroidGradle/>'
        self.descriptor.write_bytes(encode(data))
        with self.assertRaises(ValueError): self.import_source(False)
        data = extension_fixture(); data['IncludedResources'] = [{'name': 'missing', 'path': 'scripts/missing/missing.yy'}]
        self.descriptor.write_bytes(encode(data))
        with self.assertRaisesRegex(ValueError, '引用'): self.import_source(False)

    def test_readonly_duplicate_name_and_existing_function_collision(self):
        self.project.writable = False
        self.assertTrue(self.import_source()['dry_run'])
        with self.assertRaisesRegex(ValueError, '只读'): self.import_source(False)
        self.project.writable = True
        self.import_source(False)
        with self.assertRaises(ValueError): self.import_source(False)
        replacement = extension_fixture('AnotherProbe')
        replacement['androidclassname'] = 'MorxProbe'
        self.descriptor.unlink(); self.descriptor = self.source / 'AnotherProbe.yy'; self.descriptor.write_bytes(encode(replacement))
        with self.assertRaisesRegex(ValueError, '重名'): self.import_source(False)

    def test_gml_companion_and_manifest_preview(self):
        data = extension_fixture(); data['androidProps'] = False; data['androidclassname'] = ''
        file = data['files'][0]; file.update(kind=2, name='probe.gml', filename='probe.gml', **{'%Name': 'probe.gml'})
        file['functions'][0]['kind'] = 2
        data['androidcodeinjection'] = '<YYAndroidManifestApplicationInject><meta-data android:name="probe" android:value="1"/></YYAndroidManifestApplicationInject>'
        self.descriptor.write_bytes(encode(data))
        self.java.unlink()
        (self.source / 'probe.gml').write_text('function morx_probe_inc(value) { return value+1; }', encoding='utf-8')
        info = extensions.inspect(self.project, source_path=str(self.source))
        self.assertFalse(info['functions'][0]['java_signature_checked'])
        self.assertEqual(info['manifest_and_build_injections'][0]['tag'], 'YYAndroidManifestApplicationInject')
        self.assertFalse(info['manifest_and_build_injections'][0]['truncated'])
        self.assertTrue(self.import_source()['dry_run'])


class ExtensionCreationTests(ProjectFixture):
    def setUp(self):
        super().setUp()
        self.source = self.root/'art/native/NativeProbe.java'
        self.source.parent.mkdir(parents=True)
        self.java = ('\ufeffpackage ${YYAndroidPackageName};\r\n'
                     '// 自建测试源码\r\npublic class NativeProbe extends ExtensionBase {\r\n'
                     'public String native_snapshot() { return "{}"; }\r\n'
                     'public double native_add(double value) { return value+1; }\r\n}\r\n').encode('utf-8')
        self.source.write_bytes(self.java)
        self.functions = [{'name':'native_snapshot','external_name':'native_snapshot','args':[],
                           'return_type':'string','help':'native_snapshot()'}]

    def request(self, **extra):
        arguments = {'name':'NativeExtension','android_class':'NativeProbe','sources':['art/native/NativeProbe.java'],
                     'functions':self.functions,'expected_project_sha256':digest(self.project.yyp.read_bytes())}
        arguments.update(extra)
        return extensions.create(self.project, **arguments)

    def execute(self, **extra):
        preview=self.request(**extra)
        return self.request(expected_manifest_sha256=preview['manifest_sha256'],dry_run=False,**extra)

    def test_preview_create_readback_and_restore_preserve_source_bytes_and_native_platform(self):
        original=self.project.yyp.read_bytes()
        preview=self.request()
        self.assertFalse((self.root/'extensions').exists())
        self.assertEqual(preview['targets'],['Android'])
        self.assertEqual(preview['source_manifest'][0]['sha256'],digest(self.java))
        self.assertEqual(preview['functions'][0]['args'],[])
        self.assertEqual(preview['functions'][0]['return_type'],1)
        self.assertEqual(preview['functions'][0]['help'],'native_snapshot()')
        self.assertEqual(preview['placeholder']['copy_to_targets'],8)
        self.assertEqual(preview['permissions'],[])
        self.assertEqual(preview['manifest_and_build_injections'],[])
        self.assertEqual(self.project.yyp.read_bytes(),original)
        result=self.request(expected_manifest_sha256=preview['manifest_sha256'],dry_run=False)
        data=self.project.read('extensions/NativeExtension/NativeExtension.yy')
        self.assertEqual(data['$GMExtension'],'')
        self.assertEqual(data['files'][0]['$GMExtensionFile'],'v1')
        self.assertEqual(data['files'][0]['copyToTargets'],8)
        self.assertEqual(data['supportedTargets'],8)
        self.assertEqual(data['files'][0]['init'],'')
        self.assertEqual(data['files'][0]['final'],'')
        inspected=extensions.inspect(self.project,name='NativeExtension')
        self.assertTrue(inspected['android_only'])
        self.assertEqual(inspected['android_class'],'NativeProbe')
        self.assertEqual(inspected['functions'][0]['help'],'native_snapshot()')
        self.assertEqual(inspected['functions'][0]['effective_targets_mask'],8)
        copied=self.root/'extensions/NativeExtension/AndroidSource/Java/NativeProbe.java'
        self.assertEqual(copied.read_bytes(),self.java)
        self.assertTrue(self.project.validate()['ok'])
        self.project.restore(result['transaction'],False)
        self.assertEqual(self.project.yyp.read_bytes(),original)
        self.assertFalse((self.root/'extensions').exists())
        self.assertEqual(self.source.read_bytes(),self.java)

    def test_missing_stale_source_and_project_hashes_never_register(self):
        preview=self.request()
        for expected_hash in (None,'0'*64,'not-a-hash'):
            with self.subTest(expected_hash=expected_hash),self.assertRaises(ValueError):
                self.request(expected_manifest_sha256=expected_hash,dry_run=False)
        self.source.write_bytes(self.java+b'// changed\r\n')
        with self.assertRaisesRegex(ValueError,'清单'):
            self.request(expected_manifest_sha256=preview['manifest_sha256'],dry_run=False)
        with self.assertRaisesRegex(ValueError,'项目'):
            self.request(expected_project_sha256='0'*64)
        self.assertFalse((self.root/'extensions').exists())

    def test_sources_are_bounded_project_java_paths_without_implicit_directory_copy(self):
        for sources in ([],['../outside.java'],[str(self.source)],['.codex/private.java'],['art/native'],
                        ['art/missing.java'],['art/native/NativeProbe.java']*2):
            with self.subTest(sources=sources),self.assertRaises(ValueError):self.request(sources=sources)
        too_large=self.source.parent/'Large.java';too_large.write_bytes(b' '* (2*1024**2+1))
        with self.assertRaisesRegex(ValueError,'2MiB'):self.request(sources=['art/native/Large.java'])
        self.assertFalse((self.root/'extensions').exists())

    def test_explicit_function_types_aliases_and_android_only_targets(self):
        function={'name':'read_native','external_name':'native_snapshot','return_type':'string'}
        preview=self.request(functions=[function])
        self.assertEqual(preview['functions'][0]['help'],'read_native()')
        self.assertEqual(preview['functions'][0]['argument_types'],[])
        number={'name':'native_add','args':['real'],'return_type':'real'}
        self.assertEqual(self.request(functions=[number])['functions'][0]['args'],[2])
        for target in ([],['Windows'],['Android','Android'],['Android','Windows']):
            with self.subTest(target=target),self.assertRaises(ValueError):self.request(targets=target)
        for functions in ([],[{'name':'native_snapshot','return_type':'String'}],
                          [{'name':'native_snapshot','return_type':'string','args':['int']}],
                          [{'name':'native_snapshot','return_type':'string','init':'run'}],
                          self.functions*2,[{'name':None,'return_type':'string'}]):
            with self.subTest(functions=functions),self.assertRaises(ValueError):self.request(functions=functions)
        self.assertFalse((self.root/'extensions').exists())

    def test_wrong_return_comment_helper_or_inner_class_method_is_not_the_binding(self):
        variants=(
            'public class NativeProbe { public double native_snapshot() { return 1; } }',
            'public class NativeProbe { /* public String native_snapshot() {return "";} */ }',
            'public class NativeProbe { public class Inner { public String native_snapshot() {return "";} } }',
            'public class NativeProbe { public void native_snapshot() {} }')
        for text in variants:
            self.source.write_text(text,encoding='utf-8')
            with self.subTest(text=text),self.assertRaisesRegex(ValueError,'签名'):self.request()
        helper=self.source.parent/'Helper.java'
        helper.write_text('public class Helper { public String native_snapshot(){return "";} }',encoding='utf-8')
        self.source.write_text('public class NativeProbe {}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'签名'):
            self.request(sources=['art/native/NativeProbe.java','art/native/Helper.java'])
        self.assertFalse((self.root/'extensions').exists())

    def test_readonly_duplicate_resources_classes_and_orphan_target_are_preserved(self):
        self.project.writable=False
        preview=self.request()
        with self.assertRaisesRegex(ValueError,'只读'):
            self.request(expected_manifest_sha256=preview['manifest_sha256'],dry_run=False)
        self.project.writable=True
        self.execute()
        with self.assertRaises(ValueError):self.request()
        alias=[{'name':'other_snapshot','external_name':'native_snapshot','return_type':'string'}]
        with self.assertRaisesRegex(ValueError,'类.*重名'):self.request(name='OtherExtension',functions=alias)
        resource_alias=[{'name':'Room1','external_name':'native_snapshot','return_type':'string'}]
        with self.assertRaisesRegex(ValueError,'资源名称'):self.request(name='NewExtension',functions=resource_alias)
        orphan=self.root/'extensions/Orphan/user.keep';orphan.parent.mkdir();orphan.write_bytes(b'keep')
        with self.assertRaises(ValueError):self.request(name='Orphan',android_class='NativeProbe')
        self.assertEqual(orphan.read_bytes(),b'keep')

    def test_commit_failure_rolls_back_all_registration_without_touching_source(self):
        preview=self.request();original_yyp=self.project.yyp.read_bytes()
        original_write=self.project._write;calls=0
        def fail(rel,data):
            nonlocal calls
            if not rel.startswith('.codex/'):
                calls+=1
                if calls==3:raise OSError('test commit failure')
            return original_write(rel,data)
        with patch.object(self.project,'_write',side_effect=fail):
            with self.assertRaises(OSError):
                self.request(expected_manifest_sha256=preview['manifest_sha256'],dry_run=False)
        self.assertEqual(self.project.yyp.read_bytes(),original_yyp)
        self.assertEqual(self.source.read_bytes(),self.java)
        self.assertFalse((self.root/'extensions').exists())
        self.assertEqual(self.project.transactions()[0]['status'],'rolled_back')

    def test_source_changed_after_read_before_commit_is_rejected(self):
        preview=self.request();original_commit=self.project.commit
        def changed(changes,before,**kwargs):
            self.source.write_bytes(self.java+b'// external edit\r\n')
            return original_commit(changes,before,**kwargs)
        with patch.object(self.project,'commit',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'外部修改'):
                self.request(expected_manifest_sha256=preview['manifest_sha256'],dry_run=False)
        self.assertFalse((self.root/'extensions').exists())
        self.assertTrue(self.source.read_bytes().endswith(b'// external edit\r\n'))

    def test_mcp_creation_preview_execution_readback_and_readonly_error(self):
        from morx_gms2_mcp.server import build_server
        async def verify():
            writable=build_server(self.root,writable=True)
            readonly=build_server(self.root,writable=False)
            async def call(server,name,args,error=False):
                result=await server.call_tool(name,args)
                if hasattr(result,'isError'):
                    self.assertEqual(bool(result.isError),error)
                    blocks=result.content
                else:blocks=result[0] if isinstance(result,tuple) else result
                return json.loads(blocks[0].text)
            args={'name':'NativeExtension','android_class':'NativeProbe','sources':['art/native/NativeProbe.java'],
                  'functions':self.functions,'expected_project_sha256':digest(self.project.yyp.read_bytes())}
            preview=await call(readonly,'create_extension',args)
            args.update(expected_manifest_sha256=preview['manifest_sha256'],dry_run=False)
            denied=await call(readonly,'create_extension',args,True)
            self.assertEqual(denied['error']['tool'],'create_extension')
            result=await call(writable,'create_extension',args)
            self.assertIsNotNone(result['transaction'])
            info=await call(writable,'inspect_extension',{'name':'NativeExtension'})
            self.assertTrue(info['android_only'])
            self.assertEqual(info['functions'][0]['return_type_name'],'string')
        asyncio.run(verify())
