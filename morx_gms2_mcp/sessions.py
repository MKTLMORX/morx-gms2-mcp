"""多会话路由；每个 Runtime 独立状态，准备/构建共用一个有界排队槽。"""
import threading

from .runtime import Runtime, RuntimeOptions


class RuntimePool:
    ROUTED = {'stop', 'restart', 'output', 'errors', 'request', 'request_status', 'save_capture', 'inspect',
              'inspect_audio', 'set_variables', 'spawn', 'destroy', 'input', 'trigger_event',
              'mouse', 'wait', 'run_sequence'}

    def __init__(self, project, options=None):
        self.project = project
        self.options = options or RuntimeOptions()
        self.guard = threading.RLock()
        self.build_gate = threading.BoundedSemaphore(1)
        self.sessions = {}
        self.closed = False
        self.idle = Runtime(project, self.options)

    def target(self, session_id=None):
        with self.guard:
            if session_id is not None:
                if session_id not in self.sessions:
                    raise ValueError('运行会话不匹配；查询 list_game_sessions')
                return self.sessions[session_id]
            active = [rt for rt in self.sessions.values() if not rt.session.get('stopped')
                      or rt.session.get('stopping') or rt.session.get('cleanup_failed')]
            if len(active) > 1:
                raise ValueError('存在多个会话，必须明确 session_id；查询 list_game_sessions')
            return active[0] if active else next(reversed(self.sessions.values()), self.idle)

    def run(self, dry_run=True, compile_only=False, new_session=False, launch_mode='igor'):
        if type(new_session) is not bool:
            raise ValueError('new_session 须为布尔值')
        with self.guard:
            if self.closed:
                raise ValueError('运行服务已关闭')
            active = [r for r in self.sessions.values() if not r.session.get('stopped')
                      or r.session.get('stopping') or r.session.get('cleanup_failed') or r.tests.active]
            if active and not new_session:
                raise ValueError('已有会话；先 stop_game，或明确 new_session=true 新建独立会话')
            if len(active) >= 8:
                raise ValueError('最多保有8个活动或待清理会话，请先清理')
            rt = Runtime(self.project, self.options)
            rt.build_gate = self.build_gate
            if not dry_run:
                result = rt.run(False, compile_only, launch_mode)
                self.sessions[result['session_id']] = rt
                # 有界历史，不移除尚有进程、待清理目录或运行中测试的会话。
                for sid, old in list(self.sessions.items()):
                    if len(self.sessions) <= 32:
                        break
                    if old.session.get('stopped') and not old.session.get('stopping') and not old.session.get('cleanup_failed') and not old.tests.active:
                        del self.sessions[sid]
                return {**result, 'new_session': new_session, 'build_concurrency': 1}
        return {**rt.run(True, compile_only, launch_mode), 'new_session': new_session, 'max_sessions': 8}

    def status(self, wait_seconds=0, session_id=None):
        return self.target(session_id).status(wait_seconds)

    def listing(self, include_history=True, summary=True):
        if type(include_history) is not bool or type(summary) is not bool:
            raise ValueError('include_history 和 summary 须为布尔值')
        with self.guard:
            entries = list(self.sessions.items())
        rows = []
        for sid, rt in entries:
            try:
                status = rt.status()
                if not include_history and status['state'] == 'stopped' and not rt.session.get('cleanup_failed'):
                    continue
                if summary:
                    status = {key: status[key] for key in ('session_id', 'state', 'compiled', 'build_status',
                        'bridge_ready', 'build_pid', 'runner_pid', 'exit_code', 'error_count',
                        'compile_error_count', 'runtime_log_error_count', 'lifecycle', 'stop_reason',
                        'stop_source', 'launch_mode') if key in status}
                    status['cleanup_failed'] = bool(rt.session.get('cleanup_failed'))
                rows.append(status)
            except (ValueError, OSError) as exc:
                rows.append({'session_id': sid, 'status_error': rt._redact(str(exc))})
        return {'sessions': rows, 'max_sessions': 8, 'build_concurrency': 1,
                'include_history': include_history, 'summary': summary}

    def test_start(self, session_id, *args, **kwargs):
        return self.target(session_id).tests.start(session_id, *args, **kwargs)

    def test_target(self, test_id):
        with self.guard:
            entries = list(self.sessions.values())
        for rt in entries:
            with rt.tests.lock:
                if rt.tests.record and rt.tests.record['test_id'] == test_id:
                    return rt.tests
        raise ValueError('测试编号不匹配；每个会话仅保留最近一次测试，请先导出')

    def test_result(self, test_id, include_images=False):
        return self.test_target(test_id).result(test_id, include_images)

    def test_save(self, test_id, dry_run=True):
        return self.test_target(test_id).save(test_id, dry_run)

    def __getattr__(self, name):
        if name not in self.ROUTED:
            raise AttributeError(name)
        def dispatch(session_id, *args, **kwargs):
            return getattr(self.target(session_id), name)(session_id, *args, **kwargs)
        return dispatch

    def close(self):
        with self.guard:
            self.closed = True
            entries = list(self.sessions.values())
        # 先取消所有排队准备，避免逐个停止期间又启动别的 Runner。
        for rt in entries:
            if rt.session.get('cancel'):
                rt.session['cancel'].set()
        for rt in entries:
            rt.close()
