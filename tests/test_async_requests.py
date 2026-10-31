"""长事件只提交一次，状态查询只收割回执；MCP错误保持isError及可解析数据。"""
import asyncio
import json
import os
import threading
import time
import unittest
from unittest.mock import patch
from morx_gms2_mcp.runtime import RuntimeRequestError, atomic_json
from morx_gms2_mcp.server import build_server
from tests import test_runtime as fixtures


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.RuntimeTests('test_stop_preview_and_owned_process_only')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.launch();self.fixture.ready()
        self.rt=self.fixture.runtime;self.sid=self.fixture.session_id

    def complete(self,request_id,ok=True):
        self.rt._file('request.json').unlink(missing_ok=True)
        atomic_json(self.rt._file('response.json'),{'session':self.sid,'request':request_id,
                    'ok':ok,'data':{'instances':1} if ok else {'error':'probe failed'},'frame':9})

    def test_async_submits_once_status_returns_same_cached_response(self):
        with patch('morx_gms2_mcp.runtime.atomic_json',wraps=atomic_json) as write:
            queued=self.rt.trigger_event(self.sid,'obj_probe','User',dry_run=False,asynchronous=True)
            request_id=queued['request_id']
            self.assertEqual(self.rt.request_status(self.sid,request_id)['state'],'pending')
            self.complete(request_id)
            self.assertEqual(self.rt.request_status(self.sid,request_id)['state'],'completed')
            self.rt._file('response.json').unlink()
            self.assertTrue(self.rt.request_status(self.sid,request_id)['result']['ok'])
            self.assertEqual(write.call_count,1)
        self.assertIsNone(self.rt.pending)

    def test_timeout_provides_original_id_and_late_result_is_queryable(self):
        with self.assertRaises(RuntimeRequestError) as raised:
            self.rt.request(self.sid,'event',{},timeout=.01)
        error=raised.exception.details
        self.assertTrue(error['execution_unknown'])
        self.assertEqual(error['code'],'request_timeout')
        self.assertEqual(self.rt.status()['last_request_error']['diagnostic_id'],error['diagnostic_id'])
        with self.assertRaisesRegex(ValueError,'尚未确认'):
            self.rt.request(self.sid,'event',{})
        self.complete(error['request_id'])
        result=self.rt.request_status(self.sid,error['request_id'])
        self.assertEqual(result['state'],'completed')
        self.assertTrue(result['wait_timed_out'])

    def test_stale_activity_is_visible_but_does_not_claim_readiness(self):
        queued=self.rt.request(self.sid,'event',{},asynchronous=True)
        atomic_json(self.rt._file('ready.json'),{'session':self.sid,'bridge':self.rt.session['bridge'],
            'frame':10,'activity':{'request':queued['request_id'],'state':'executing'}})
        old=time.time()-11;os.utime(self.rt._file('ready.json'),(old,old))
        status=self.rt.request_status(self.sid,queued['request_id'])
        self.assertEqual(status['state'],'pending')
        self.assertEqual(status['bridge_diagnostics']['activity']['state'],'executing')
        self.assertFalse(status['bridge_diagnostics']['ready'])
        self.assertFalse(status['bridge_diagnostics']['accepting_requests'])

    def test_recent_event_completion_waits_for_fresh_heartbeat_without_replay(self):
        queued=self.rt.request(self.sid,'event',{},asynchronous=True)
        self.complete(queued['request_id'])
        self.rt.request_status(self.sid,queued['request_id'])
        old=time.time()-11;os.utime(self.rt._file('ready.json'),(old,old))
        def heartbeat():
            time.sleep(.1);self.fixture.ready()
        thread=threading.Thread(target=heartbeat);thread.start();self.addCleanup(thread.join)
        self.fixture.respond({'globals':{'score':1}},command='state')
        result=self.rt.inspect(self.sid,scope='globals',variables=['score'])
        self.assertEqual(result['data']['globals']['score'],1)
        self.assertEqual(len(self.rt.request_history),2)

    def test_failed_result_unknown_id_and_stop_do_not_erase_confirmed_result(self):
        with self.assertRaises(ValueError):self.rt.request_status(self.sid,'missing')
        queued=self.rt.request(self.sid,'event',{},asynchronous=True)
        self.complete(queued['request_id'],False)
        self.assertEqual(self.rt.request_status(self.sid,queued['request_id'])['state'],'failed')
        pending=self.rt.request(self.sid,'event',{},asynchronous=True)
        self.rt.stop(self.sid,False)
        self.assertEqual(self.rt.request_status(self.sid,pending['request_id'])['state'],'stopped_unknown')
        self.assertEqual(self.rt.request_status(self.sid,queued['request_id'])['state'],'failed')

    def test_history_is_bounded_and_other_session_cannot_query(self):
        for _ in range(20):
            queued=self.rt.request(self.sid,'event',{},asynchronous=True)
            self.complete(queued['request_id']);self.rt.request_status(self.sid,queued['request_id'])
        self.assertEqual(len(self.rt.request_history),16)
        with self.assertRaises(ValueError):self.rt.request_status('other',queued['request_id'])

    def test_fastmcp_runtime_error_is_json_and_has_error_flag(self):
        from morx_gms2_mcp.sessions import RuntimePool
        pool=RuntimePool(self.fixture.project,self.fixture.options);pool.sessions[self.sid]=self.rt
        with patch('morx_gms2_mcp.server.RuntimePool',return_value=pool):
            server=build_server(self.fixture.root,runtime_options=self.fixture.options)
        async def check():
            result=await server.call_tool('game_request_status',{'session_id':self.sid,'request_id':'missing'})
            self.assertTrue(result.isError)
            data=json.loads(result.content[0].text)
            self.assertEqual(data,result.structuredContent)
            self.assertEqual(data['error']['tool'],'game_request_status')
            self.assertIn('diagnostic_id',data['error'])
        asyncio.run(check())

    def test_request_write_error_and_invalid_response_keep_diagnostics_and_original_id(self):
        with patch('morx_gms2_mcp.runtime.atomic_json',side_effect=PermissionError('test request lock')):
            with self.assertRaises(RuntimeRequestError) as raised:
                self.rt.request(self.sid,'event',{},asynchronous=True)
        request_id=raised.exception.details['request_id']
        self.assertEqual(raised.exception.details['code'],'request_io_error')
        self.assertEqual(self.rt.request_status(self.sid,request_id)['state'],'pending')
        atomic_json(self.rt._file('response.json'),{'session':self.sid,'request':request_id,'ok':'invalid','data':{}})
        status=self.rt.status()
        self.assertEqual(status['last_request_error']['code'],'invalid_response')
        self.assertEqual(self.rt.request_status(self.sid,request_id)['state'],'invalid_response')
