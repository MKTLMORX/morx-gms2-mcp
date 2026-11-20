"""ADB契约回归：所有设备返回为夹具，不连接设备或启动应用。"""
from collections import deque
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import zipfile
from unittest.mock import patch
from PIL import Image

from morx_gms2_mcp.android_devices import AndroidManager, serial_check, display_rotation
from morx_gms2_mcp import android_artifacts, android_bridge
from tests import test_android_artifacts as artifact_fixtures


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.manager = AndroidManager(None, None)
        self.addCleanup(self.manager.close)
        self.artifact = {'path': 'fixture.apk', 'artifact_type': 'apk', 'sha256': 'a'*64,
                         'package_name': 'org.example.probe', 'versionCode': 100, 'versionName':'1.0',
                         'min_sdk':23, 'abis':['arm64-v8a'], 'signature':{'verified':True, 'certificate_sha256':['b'*64]},
                         'debuggable':True, 'launchable_activity':'org.example.probe.RunnerActivity', 'development_bridge':None}

    def row(self):
        timer = threading.Timer(100, lambda: None)
        row = {'id':'owned', 'serial':'probe-device', 'package':'org.example.probe', 'pid':12345,
               'state':'running', 'component':'org.example.probe/.RunnerActivity', 'bridge':None,
               'created_at':'fixture', 'expires_at':'fixture', 'deadline':time.monotonic()+90,
               'timer':timer, 'lock':threading.RLock(), 'touch':None, 'logs':deque(maxlen=200),
               'seen':deque(maxlen=500), 'cursor':0}
        self.manager.sessions['owned'] = row
        return row

    def upgrade_fixture(self, old_protocol=1, old_certificate=None, old_version=100):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name).resolve(strict=True)
        chain={'paths':{'sdk_root':{'path':str(root)}},
               'components':{'java':'java.exe','keytool':'keytool.exe'},
               'dependencies':{'build_tools':{'effective_value':'35.0.0'}}}
        old,new=root/'old.apk',root/'new.apk'
        for path,protocol,certificate,version in ((old,old_protocol,old_certificate or 'b'*64,old_version),
                                                   (new,2,'b'*64,100)):
            with zipfile.ZipFile(path,'w') as archive:
                archive.writestr('AndroidManifest.xml',b'owned fixture')
                archive.writestr('lib/arm64-v8a/libprobe.so',b'owned fixture')
                archive.writestr('assets/'+android_bridge.MARKER_FILE,json.dumps(artifact_fixtures.bridge_marker(protocol)))
                archive.writestr('assets/fixture-certificate.txt',certificate)
                archive.writestr('assets/fixture-version.txt',str(version))
        state={'installed':old,'writes':[]}
        def official(arguments,**kwargs):
            with zipfile.ZipFile(arguments[-1]) as archive:
                certificate=archive.read('assets/fixture-certificate.txt').decode()
                version=archive.read('assets/fixture-version.txt').decode()
            if 'aapt2.exe' in arguments[0]:
                return {'exit_code':0,'output':artifact_fixtures.BADGING.replace("versionCode='2003004'", "versionCode='"+version+"'")}
            return {'exit_code':0,'output':'certificate SHA-256 digest: '+certificate}
        def adb(arguments,serial=None,**kwargs):
            self.assertEqual(serial,'probe-device')
            if arguments==['shell','pm','path','org.example.probe']:
                return {'exit_code':0,'output':'package:/data/app/probe/base.apk\n'}
            if arguments[:2]==['pull','/data/app/probe/base.apk']:
                Path(arguments[2]).write_bytes(state['installed'].read_bytes())
            elif arguments==['install','-r',str(new)]:
                state['writes'].append(arguments);state['installed']=new
            else:raise AssertionError(arguments)
            return {'exit_code':0,'output':''}
        for context in (patch.object(self.manager,'_device',return_value={'api':35,'abis':['arm64-v8a']}),
                        patch.object(self.manager,'_chain',return_value=chain),
                        patch.object(self.manager,'_adb',side_effect=adb),
                        patch('morx_gms2_mcp.android_artifacts.run_command',side_effect=official)):
            context.start();self.addCleanup(context.stop)
        return old,new,state

    def test_real_parser_accepts_legacy_installed_identity_and_upgrades_once(self):
        old,new,state=self.upgrade_fixture()
        expected=android_artifacts.checksum(new)
        preview=self.manager.install('probe-device',str(new),expected,allow_upgrade=True)
        self.assertTrue(preview['ready']);self.assertEqual(state['writes'],[])
        self.assertEqual(preview['installed']['development_bridge_compatibility']['protocol'],1)
        self.assertFalse(preview['installed']['development_bridge_compatibility']['runtime_supported'])
        self.assertTrue(preview['artifact']['development_bridge_compatibility']['runtime_supported'])
        result=self.manager.install('probe-device',str(new),expected,allow_upgrade=True,dry_run=False)
        self.assertTrue(result['installed_verified']);self.assertTrue(result['preserve_data'])
        self.assertEqual(state['writes'],[['install','-r',str(new)]])
        self.assertEqual(self.manager.installs[('probe-device','org.example.probe')]['sha256'],expected)
        # 能核对旧包身份不代表可以运行或安装旧协议候选。
        with self.assertRaisesRegex(ValueError,'待安装/运行APK.*协议'):
            self.manager._inspect(str(old),android_artifacts.checksum(old))

    def test_legacy_identity_still_blocks_signature_conflict_and_downgrade(self):
        for values in ({'old_certificate':'c'*64},{'old_version':101}):
            old,new,state=self.upgrade_fixture(**values)
            with self.subTest(values=values):
                preview=self.manager.install('probe-device',str(new),android_artifacts.checksum(new),allow_upgrade=True)
                self.assertFalse(preview['ready'])
                with self.assertRaisesRegex(ValueError,'安装预检未通过'):
                    self.manager.install('probe-device',str(new),android_artifacts.checksum(new),True,False)
                self.assertEqual(state['writes'],[])

    def test_install_errors_identify_candidate_or_installed_package(self):
        old,new,state=self.upgrade_fixture()
        with self.assertRaisesRegex(ValueError,'待安装/运行APK校验失败'):
            self.manager.install('probe-device',str(old),android_artifacts.checksum(old),True)
        with patch('morx_gms2_mcp.android_artifacts.run_command',side_effect=[{'exit_code':0,'output':artifact_fixtures.BADGING},
                {'exit_code':1,'output':'invalid signature'}]):
            with self.assertRaisesRegex(ValueError,'设备已安装APK校验失败.*官方产物工具验证失败'):
                self.manager._installed('probe-device','org.example.probe')
        self.assertEqual(state['writes'],[])

    def test_device_authorization_and_serial_never_select_implicitly(self):
        def adb(arguments, serial=None, **kwargs):
            if arguments == ['devices','-l']:
                return {'output':'List of devices attached\nprobe-device device product:probe model:Probe\nuntrusted unauthorized\nlost offline\n'}
            self.assertEqual(serial, 'probe-device')
            return {'output':'35' if arguments[-1]=='ro.build.version.sdk' else 'arm64-v8a,armeabi-v7a'}
        with patch.object(self.manager, '_adb', side_effect=adb):
            rows = self.manager.devices()['devices']
            self.assertEqual(rows[0]['api'], 35)
            self.assertEqual(rows[1]['state'], 'unauthorized')
            with self.assertRaises(ValueError): self.manager._device('untrusted')
        for value in ('', None, '-s', 'serial;echo', 'a b'):
            with self.subTest(value=value), self.assertRaises(ValueError): serial_check(value)

    def test_install_signature_conflict_upgrade_and_downgrade_never_uninstall(self):
        installed = {key:self.artifact[key] for key in ('package_name','versionName','versionCode','signature')}
        with patch.object(self.manager, '_device', return_value={'api':35,'abis':['arm64-v8a']}), patch.object(
                self.manager, '_inspect', return_value=self.artifact), patch.object(self.manager, '_installed', return_value=installed), patch.object(self.manager,'_adb') as adb:
            result = self.manager.install('probe-device','fixture.apk','a'*64)
            self.assertFalse(result['ready'])
            with self.assertRaises(ValueError): self.manager.install('probe-device','fixture.apk','a'*64,False,False)
            installed['signature'] = {'certificate_sha256':['c'*64]}
            with self.assertRaises(ValueError): self.manager.install('probe-device','fixture.apk','a'*64,True,False)
            installed['signature'] = self.artifact['signature']; installed['versionCode'] = 101
            with self.assertRaises(ValueError): self.manager.install('probe-device','fixture.apk','a'*64,True,False)
            adb.assert_not_called()

    def test_existing_process_is_not_taken_over_and_replacement_not_stopped(self):
        self.manager.installs[('probe-device','org.example.probe')] = {'sha256':'a'*64}
        with patch.object(self.manager,'_device'), patch.object(self.manager,'_inspect',return_value=self.artifact), patch.object(self.manager,'_pid',return_value=54321):
            with self.assertRaisesRegex(ValueError, '已经运行'): self.manager.run('probe-device','fixture.apk','a'*64, dry_run=False)
        self.row()
        with patch.object(self.manager, '_pid', return_value=54321), patch.object(self.manager,'_adb') as adb:
            result = self.manager.stop('owned', False)
            self.assertEqual(result['exit_reason'], 'process_exited_or_replaced')
            adb.assert_not_called()

    def test_touch_error_releases_and_preview_leaves_contact_unchanged(self):
        row = self.row()
        with patch.object(self.manager,'_pid',return_value=12345), patch.object(self.manager,'_foreground',return_value=True), patch.object(
                self.manager,'_metrics',return_value={'width':640,'height':360}), patch.object(self.manager,'_adb',return_value={'output':'motionevent'}) as adb:
            self.manager.input('owned','down',10,20)
            self.assertIsNone(row['touch'])
            self.manager.input('owned','down',10,20,dry_run=False)
            self.assertEqual(row['touch'],(10,20))
            with self.assertRaises(ValueError): self.manager.input('owned','move',999,20,dry_run=False)
            self.assertIsNone(row['touch'])
            self.assertIn('UP', adb.call_args.args[0])
            with self.assertRaises(ValueError): self.manager.input('owned','pinch',10,20)

    def test_bridge_payload_and_request_ids_match_existing_gml_protocol(self):
        row = self.row(); row['bridge']={'bridge':'b'*32}
        row['bridge_handshake'] = True
        sent=[]
        def write(row, name, data): sent.append(data)
        def read(row, name):
            if name=='ready.json': return {'session':'owned','bridge':'b'*32,'channel':'gml_file',
                                          'protocol':2,'storage':'game_save_id','frame':60}
            return {'session':'owned','request':sent[-1]['request'],'ok':True,'data':{'room':'Room1'}}
        with patch.object(self.manager,'_pid',return_value=12345), patch.object(self.manager,'_foreground',return_value=True), patch.object(
                self.manager,'_write',side_effect=write), patch.object(self.manager,'_read',side_effect=read):
            result=self.manager.inspect('owned',['score'])
        self.assertEqual(sent[-1]['command'],'state')
        self.assertEqual(sent[-1]['args']['variables'],['score'])
        self.assertEqual(result['result']['data']['room'],'Room1')

    def test_capture_requires_app_bridge_and_never_falls_back_to_display(self):
        row = self.row()
        with patch.object(self.manager,'_pid',return_value=12345), patch.object(self.manager,'_adb') as adb:
            with self.assertRaisesRegex(ValueError, '开发桥接'): self.manager.capture('owned')
            adb.assert_not_called()

    def test_logcat_is_pid_scoped_bounded_cursor_and_redacted(self):
        row = self.row()
        output='1793550000.1 12345 100 E Probe: FATAL EXCEPTION\n1793550000.2 54321 100 E Other: private\n1793550000.3 12345 100 E Probe: token=private\n'
        with patch.object(self.manager,'_pid',return_value=12345), patch.object(self.manager,'_adb',return_value={'output':output}) as adb:
            first=self.manager.logs('owned')
            self.assertEqual(len(first['lines']),2)
            self.assertEqual(first['lines'][0]['category'],'java_crash')
            self.assertNotIn('token=private',str(first))
            self.assertIn('--pid=12345',adb.call_args.args[0])
            self.assertEqual(self.manager.logs('owned',first['next_cursor'])['lines'],[])
        row['state']='stopped'

    def test_run_as_write_uses_fixed_sandbox_and_encoded_json_only(self):
        row = self.row()
        with patch.object(self.manager,'_adb') as adb:
            self.manager._write(row,'session.json',{'bridge':'a'*32,'session':'owned'})
            command=adb.call_args.args[0]
            self.assertEqual(command[:4],['shell','run-as','org.example.probe','sh'])
            self.assertIn('files/morx_mcp/session.json.tmp', command[-1])
            self.assertNotIn('"bridge"', command[-1])
            with self.assertRaises(ValueError):self.manager._write(row,'../outside',{})
        row['state']='stopped'

    def test_release_failure_remains_visible_until_successful_retry(self):
        row=self.row(); row['touch']=(10,20)
        with patch.object(self.manager,'_adb',side_effect=ValueError('offline')):
            with self.assertRaises(ValueError):self.manager._release(row)
        self.assertEqual(row['touch'],(10,20))
        self.assertEqual(row['touch_release_error'],'ValueError')
        with patch.object(self.manager,'_adb'):
            self.manager._release(row)
        self.assertIsNone(row['touch']);self.assertNotIn('touch_release_error',row)
        row['state']='stopped'

    def test_capture_reads_app_image_and_removes_only_this_request_file(self):
        row=self.row();row['bridge']={'bridge':'b'*32}
        stream=io.BytesIO();Image.new('RGB',(320,180),(20,40,60)).save(stream,format='PNG')
        request='c'*32
        with patch.object(self.manager,'_pid',return_value=12345),patch.object(self.manager,'_request',return_value={
                'request':request,'data':{'capture':{'raw_size':[320,180]}}}),patch.object(
                self.manager,'_read',return_value=stream.getvalue()),patch.object(self.manager,'_foreground',return_value=True),patch.object(
                self.manager,'_metrics',return_value={'width':640,'height':360,'density_dpi':240}),patch.object(self.manager,'_adb') as adb:
            result=self.manager.capture('owned')
        self.assertEqual(result['raw_size'],[320,180])
        self.assertEqual(result['image'],stream.getvalue())
        self.assertFalse(result['includes_other_apps'])
        self.assertEqual(adb.call_args.args[0][-1],'files/morx_mcp/capture_'+request+'.png')
        row['state']='stopped'

    def test_foreground_reads_api35_display_fields_and_legacy_activity_fallback(self):
        row = self.row()
        samples = [
            '  mCurrentFocus=Window{ab u0 org.example.probe/org.example.probe.RunnerActivity}',
            '  mCurrentFocus = Window{ab u0 org.example.probe/.RunnerActivity}',
            '  mFocusedApp=ActivityRecord{ab u0 org.example.probe/.RunnerActivity t8}',
            '  WINDOW MANAGER WINDOWS (dumpsys window windows)\n',
        ]
        for sample in samples:
            def adb(args, *unused, **kwargs):
                if args == ['shell','dumpsys','window']: return {'output':sample}
                self.assertEqual(args, ['shell','dumpsys','activity','activities'])
                return {'output':'  topResumedActivity=ActivityRecord{cd u0 org.example.probe/.RunnerActivity t8}'}
            with self.subTest(sample=sample), patch.object(self.manager, '_adb', side_effect=adb):
                self.assertTrue(self.manager._foreground(row))
        with patch.object(self.manager, '_adb', side_effect=[{'output':''},{'output':
                '  mResumedActivity: ActivityRecord{a u0 org.example.probe/.RunnerActivity t8}'}]):
            self.assertTrue(self.manager._foreground(row))
        row['state']='stopped'

    def test_foreground_never_overrides_null_overlay_conflicts_or_other_display(self):
        row = self.row()
        for output in ('mCurrentFocus=null',
                       'mCurrentFocus=Window{a u0 com.android.systemui/.Dialog}',
                       'mCurrentFocus=Window{a u0 org.example.probe/.RunnerActivity}\n'
                       'mCurrentFocus=Window{b u0 org.example.other/.Main}',
                       'mCurrentFocus=unrecognized'):
            with self.subTest(output=output), patch.object(self.manager,'_adb',return_value={'output':output}) as adb:
                self.assertFalse(self.manager._foreground(row)); self.assertEqual(adb.call_count,1)
        for window, activity in (
                ('mFocusedApp=ActivityRecord{a u0 org.example.other/.Main}',
                 'topResumedActivity=ActivityRecord{a u0 org.example.probe/.RunnerActivity}'),
                ('Window #1 Window{a u0 org.example.probe/.RunnerActivity}', ''),
                ('', 'topResumedActivity=ActivityRecord{a u0 org.example.other/.Main}\n'
                 'mResumedActivity=ActivityRecord{b u0 org.example.probe/.RunnerActivity}')):
            with patch.object(self.manager,'_adb',side_effect=[{'output':window},{'output':activity}]):
                self.assertFalse(self.manager._foreground(row))
        row['state']='stopped'

    def test_bridge_readiness_requires_matching_gml_handshake_and_progress(self):
        row=self.row();row['bridge']={'bridge':'b'*32};row['bridge_probe']='c'*32
        ready={'session':'owned','bridge':'b'*32,'protocol':2,'channel':'gml_file','storage':'game_save_id','frame':0}
        response={'session':'owned','request':'c'*32,'ok':True,'data':{
            'bridge':'b'*32,'protocol':2,'channel':'gml_file','storage':'game_save_id','frame':1}}
        def read(row,name): return ready if name=='ready.json' else response
        with patch.object(self.manager,'_read',side_effect=read), patch.object(self.manager,'_pid',return_value=12345):
            self.assertFalse(self.manager.status('owned')['bridge_ready'])
            ready['frame']=60.0;response['data']['frame']=1.0;response['request']='d'*32
            self.assertEqual(self.manager.status('owned')['bridge_status']['reason'],'awaiting_gml_handshake')
            response['request']='c'*32
            self.assertTrue(self.manager.status('owned')['bridge_ready'])
            row['bridge_progress_at']-=11
            self.assertFalse(self.manager.status('owned')['bridge_ready'])
            ready['frame']=120
            self.assertTrue(self.manager.status('owned')['bridge_ready'])
            ready['frame']=1
            self.assertEqual(self.manager.status('owned')['bridge_status']['reason'],'gml_heartbeat_stale')
        row['state']='stopped'

    def test_bridge_rejects_java_only_or_foreign_heartbeat_before_sending_command(self):
        row=self.row();row['bridge']={'bridge':'b'*32};row['bridge_handshake']=True
        for ready in ({'session':'owned','frame':0},
                      {'session':'owned','bridge':'a'*32,'channel':'gml_file','protocol':2,'frame':60},
                      {'session':'old','bridge':'b'*32,'channel':'gml_file','protocol':2,'frame':60}):
            with patch.object(self.manager,'_read',return_value=ready),patch.object(self.manager,'_foreground',return_value=True),patch.object(self.manager,'_write') as write:
                with self.assertRaisesRegex(ValueError,'尚未就绪'):self.manager._request(row,'state',{})
                write.assert_not_called()
        row['state']='stopped'

    def test_launch_stages_exactly_one_readonly_handshake_before_start(self):
        self.artifact['development_bridge']={'bridge':'b'*32,'protocol':2}
        self.manager.installs[('probe-device','org.example.probe')]={'sha256':'a'*64}
        with patch.object(self.manager,'_device'),patch.object(self.manager,'_inspect',return_value=self.artifact),patch.object(
                self.manager,'_pid',side_effect=[None,12345]),patch.object(self.manager,'_write') as write,patch.object(self.manager,'_adb'):
            result=self.manager.run('probe-device','fixture.apk','a'*64,dry_run=False)
        calls=write.call_args_list
        self.assertEqual([call.args[1] for call in calls],['session.json','request.json'])
        self.assertEqual(calls[1].args[2]['command'],'ping')
        self.assertEqual(calls[1].args[2]['session'],result['session_id'])
        self.manager.sessions[result['session_id']]['state']='stopped'
        self.manager.sessions[result['session_id']]['timer'].cancel()

    def test_cropped_capture_is_rejected_and_own_file_is_still_removed(self):
        row=self.row();row['bridge']={'bridge':'b'*32}
        stream=io.BytesIO();Image.new('RGB',(1024,576)).save(stream,format='PNG')
        with patch.object(self.manager,'_pid',return_value=12345),patch.object(self.manager,'_request',return_value={
                'request':'c'*32,'data':{'capture':{'raw_size':[1280,576]}}}),patch.object(
                self.manager,'_read',return_value=stream.getvalue()),patch.object(self.manager,'_adb') as adb:
            with self.assertRaisesRegex(ValueError,'裁切'):self.manager.capture('owned')
            self.assertEqual(adb.call_args.args[0][-1],'files/morx_mcp/capture_'+'c'*32+'.png')
        row['state']='stopped'

    def test_bridge_read_keeps_remote_exit_code_and_handles_initially_missing_file(self):
        row=self.row()
        with patch.object(self.manager,'_adb',return_value={'exit_code':1,'output':'cat: ready.json: No such file'}) as adb:
            self.assertIsNone(self.manager._read(row,'ready.json'))
            self.assertEqual(adb.call_args.args[0],['shell','run-as','org.example.probe','cat','files/morx_mcp/ready.json'])
        with patch.object(self.manager,'_adb',return_value={'exit_code':0,'output':'\ufeff{"session":"owned","frame":60.0}\r\n'}):
            self.assertEqual(self.manager._read(row,'ready.json')['frame'],60)
        for text in ('','\r\n','\ufeff'):
            with patch.object(self.manager,'_adb',return_value={'exit_code':0,'output':text}):
                self.assertIsNone(self.manager._read(row,'ready.json'))
        with patch.object(self.manager,'_adb',return_value={'exit_code':0,'output':'{broken'}):
            with self.assertRaisesRegex(ValueError,'无效数据'):self.manager._read(row,'ready.json')
        row['state']='stopped'

    def test_display_rotation_ignores_nested_fields_and_uses_effective_default_display(self):
        record=('DisplayInfo{"Built-in Screen", displayId 0, modes [{id=1, width=1280, height=576}], '
                'hdrCapabilities HdrCapabilities{rotation 3, value=0}, rotation 1, state ON}')
        self.assertEqual(display_rotation('mDisplayInfo='+record),1)
        self.assertEqual(display_rotation('mBaseDisplayInfo=DisplayInfo{displayId 0, rotation 0}\n'
                                         'mDisplayInfo='+record+'\n'
                                         'mDisplayInfo=DisplayInfo{displayId 2, rotation 3}'),1)
        row=self.row()
        with patch.object(self.manager,'_adb',side_effect=[{'output':'Physical size: 576x1280'},
                {'output':'Physical density: 240'},{'output':'mDisplayInfo='+record}]):
            metrics=self.manager._metrics(row)
        self.assertEqual((metrics['width'],metrics['height'],metrics['rotation']),(1280,576,1))
        for value in ('DisplayInfo{displayId 1, rotation 0}', 'DisplayInfo{displayId 0, rotation 4}',
                      'DisplayInfo{displayId 0, modes [{rotation 1}]}',
                      'mDisplayInfo=DisplayInfo{displayId 0, rotation 0}\nmDisplayInfo=DisplayInfo{displayId 0, rotation 1}'):
            with self.subTest(value=value),self.assertRaises(ValueError):display_rotation(value)
        row['state']='stopped'

    def test_tap_has_observable_duration_and_failed_atomic_input_releases(self):
        row=self.row()
        with patch.object(self.manager,'_pid',return_value=12345),patch.object(self.manager,'_foreground',return_value=True),patch.object(
                self.manager,'_metrics',return_value={'width':1280,'height':576}),patch.object(self.manager,'_adb') as adb:
            self.manager.input('owned','tap',640,288,duration_ms=80)
            adb.assert_not_called()
            result=self.manager.input('owned','tap',640,288,duration_ms=80,dry_run=False)
            self.assertEqual(adb.call_args.args[0],['shell','input','touchscreen','swipe','640','288','640','288','80'])
            self.assertFalse(result['touch_down'])
        for action in ('tap','swipe'):
            with patch.object(self.manager,'_pid',return_value=12345),patch.object(self.manager,'_foreground',return_value=True),patch.object(
                    self.manager,'_metrics',return_value={'width':1280,'height':576}),patch.object(
                    self.manager,'_adb',side_effect=[ValueError('command failed'),{'output':''}]) as adb:
                with self.assertRaisesRegex(ValueError,'command failed'):
                    self.manager.input('owned',action,640,288,end_x=650,end_y=288,dry_run=False)
                self.assertIn('UP',adb.call_args.args[0]);self.assertIsNone(row['touch'])
        row['state']='stopped'

    def test_crash_log_can_be_collected_after_exit_without_reading_reused_pid(self):
        row=self.row()
        def adb(arguments,*args,**kwargs):
            if arguments[:2]==['shell','ps']:return {'output':'NAME\n'}
            return {'output':'1793550000.1 12345 100 E Probe: FATAL EXCEPTION\n'}
        with patch.object(self.manager,'_pid',return_value=None),patch.object(self.manager,'_adb',side_effect=adb):
            result=self.manager.logs('owned')
        self.assertEqual(row['state'],'exited')
        self.assertEqual(result['lines'][0]['category'],'java_crash')
        self.assertTrue(row['final_logs_collected'])
        row.pop('final_logs_collected');row['logs'].clear();row['state']='exited'
        with patch.object(self.manager,'_adb',return_value={'output':'NAME\nother.application\n'}) as call:
            self.assertEqual(self.manager.logs('owned')['lines'],[])
            self.assertEqual(call.call_count,1)

    def test_stop_still_stops_original_if_log_read_fails_but_never_replacement(self):
        self.row()
        with patch.object(self.manager,'_pid',return_value=12345),patch.object(self.manager,'_collect_logs',side_effect=ValueError('log unavailable')),patch.object(self.manager,'_adb') as adb:
            result=self.manager.stop('owned',False)
        self.assertEqual(result['state'],'stopped')
        self.assertEqual(adb.call_args.args[0],['shell','am','force-stop','org.example.probe'])
        self.row()
        with patch.object(self.manager,'_pid',side_effect=[12345,54321]),patch.object(self.manager,'_collect_logs'),patch.object(self.manager,'_adb') as adb:
            result=self.manager.stop('owned',False)
        self.assertEqual(result['exit_reason'],'process_exited_or_replaced')
        adb.assert_not_called()
