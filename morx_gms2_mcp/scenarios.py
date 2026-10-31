"""有界场景检查与本地证据；只比较返回数据，不求值 GML 或表达式。"""
from copy import deepcopy
import hashlib
import json
import math
import re
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

from . import __version__
from .display import integer
from .storage import compact_encode
from .runtime_bridge import sources


def scalar(value):
    if isinstance(value, dict) and value.get('type') == 'int64' and set(value) == {'type', 'value'}:
        text = value['value']
        if not isinstance(text, str) or not re.fullmatch('-?[0-9]{1,19}', text):
            raise ValueError('无效 int64 比较值')
        value = int(text)
        if not -(2**63) <= value < 2**63:
            raise ValueError('int64 比较值超限')
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, str) and len(value) <= 1024:
        return value
    raise ValueError('断言只比较有限数字、布尔、短字符串、null 或精确 int64')


def resolve(data, path):
    """使用响应的字段/索引列表，区分缺失与预算截断。"""
    uncertain = False
    for part in path:
        if isinstance(data, dict):
            uncertain = uncertain or bool(data.get('truncated'))
            if not isinstance(part, str) or part not in data:
                unknown = uncertain or data.get('type') in ('truncated', 'complex', 'non_finite') or 'error' in data
                return None, 'unavailable' if unknown else 'missing'
            data = data[part]
        elif isinstance(data, list) and type(part) is int and 0 <= part < len(data):
            data = data[part]
        else:
            return None, 'unavailable' if uncertain else 'missing'
    if isinstance(data, dict) and data.get('type') in ('missing', 'undefined'):
        return None, 'missing'
    if isinstance(data, dict) and (data.get('type') in ('truncated', 'complex', 'non_finite') or 'error' in data):
        return None, 'unavailable'
    if isinstance(data, str) and len(data) >= 1024:
        return None, 'unavailable'
    return data, 'present'


def compare(data, path, operator, expected):
    expected = scalar(expected)
    actual, availability = resolve(data, path)
    passed = False
    if operator == 'exists':
        passed = availability != 'unavailable' and (availability == 'present') == expected
    elif availability == 'present':
        try:
            actual = scalar(actual)
        except ValueError:
            return {'passed': False, 'availability': 'unavailable', 'path': path,
                    'operator': operator, 'expected': expected}
        numeric = type(actual) in (int, float) and type(expected) in (int, float)
        compatible = numeric or type(actual) is type(expected)
        if operator == 'eq':
            passed = compatible and actual == expected
        elif operator == 'ne':
            passed = compatible and actual != expected
        elif numeric:
            passed = {'lt': lambda: actual < expected, 'lte': lambda: actual <= expected,
                      'gt': lambda: actual > expected, 'gte': lambda: actual >= expected}[operator]()
    def exact(value):
        return {'type': 'int64', 'value': str(value)} if type(value) is int and abs(value) > 9007199254740991 else value
    return {'passed': passed, 'availability': availability, 'actual': exact(actual),
            'operator': operator, 'expected': exact(expected), 'path': path}


class SceneTests:
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock = threading.RLock()
        self.record = None
        self.worker = None
        self.cancel = threading.Event()

    @property
    def active(self):
        return self.record is not None and self.record['status'] == 'running'

    def normalize(self, steps):
        if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
            raise ValueError('场景须包含 1～32 步')
        if len(compact_encode(steps)) > 128 * 1024:
            raise ValueError('测试定义超过 128 KiB')
        result = []
        for step in steps:
            if not isinstance(step, dict):
                raise ValueError('测试步骤须为对象')
            action = step.get('action')
            if action in ('assert', 'wait_until'):
                allowed = {'action', 'observe', 'path', 'operator', 'expected', 'timeout_seconds', 'poll_ms'}
                unknown = set(step) - allowed
                if unknown:
                    raise ValueError('断言包含未知参数：' + ', '.join(sorted(unknown))
                                     + '；允许参数：' + ', '.join(sorted(allowed)))
                observe = step.get('observe', {})
                if not isinstance(observe, dict) or observe.get('action') not in ('state', 'audio'):
                    raise ValueError('observe 须为 state 或 audio 只读步骤')
                query = self.normalize([observe])[0]
                path = step.get('path')
                if (not isinstance(path, list) or not 1 <= len(path) <= 24
                        or any(not (isinstance(p, str) and 0 < len(p) <= 128)
                               and not (type(p) is int and 0 <= p <= 10000) for p in path)):
                    raise ValueError('path 须为 1～24 个字段名/非负索引，不接受表达式')
                operator = step.get('operator', 'eq')
                if operator not in ('eq', 'ne', 'lt', 'lte', 'gt', 'gte', 'exists') or 'expected' not in step:
                    raise ValueError('断言须提供 expected；operator 允许 eq/ne/lt/lte/gt/gte/exists')
                expected = scalar(step['expected'])
                if operator == 'exists' and type(expected) is not bool:
                    raise ValueError('exists 的 expected 须为布尔值')
                if operator in ('lt', 'lte', 'gt', 'gte') and type(expected) not in (int, float):
                    raise ValueError('大小比较须为数值')
                seconds, interval = step.get('timeout_seconds', 10), step.get('poll_ms', 100)
                integer(seconds, 'timeout_seconds', 1, 30)
                integer(interval, 'poll_ms', 50, 1000)
                result.append(dict(action=action, observe=query, path=path, operator=operator,
                                   expected=step['expected'], timeout_seconds=seconds, poll_ms=interval))
            elif action == 'audio':
                if set(step) - {'action', 'sound_names', 'voices'}:
                    raise ValueError('音频查询包含未知参数')
                result.append({'action': action, **self.runtime._audio_args(step.get('sound_names'), step.get('voices'))})
            elif action == 'mouse':
                result.append(self.runtime._mouse_step(step))
            else:
                result.append(self.runtime._sequence_step(step))
        if sum(s.get('frames', 0) for s in result) > 1800:
            raise ValueError('测试总等待/输入帧数超过 1800')
        if sum(s['action'] == 'capture' for s in result) > 3:
            raise ValueError('最多显式截图 3 次，另留一次失败截图')
        return result

    def start(self, session_id, steps, stop_on_failure=True, max_seconds=120, dry_run=True):
        steps = self.normalize(deepcopy(steps))
        if not any(s['action'] in ('assert', 'wait_until') for s in steps):
            raise ValueError('场景测试至少包含一项 assert 或 wait_until；纯动作请用 run_game_sequence')
        integer(max_seconds, 'max_seconds', 1, 120)
        if type(stop_on_failure) is not bool:
            raise ValueError('stop_on_failure 须为布尔值')
        rt = self.runtime
        with rt.guard, self.lock:
            session = rt._require_session(session_id)
            if self.active:
                raise ValueError('已有场景测试运行中；先查询 game_test_status 或 stop_game')
            if dry_run:
                return {'dry_run': True, 'steps': steps, 'stop_on_failure': stop_on_failure, 'max_seconds': max_seconds}
            if not rt.interaction_guard.acquire(blocking=False):
                raise ValueError('键鼠动作尚未完成，不能开始场景测试')
            rt.interaction_guard.release()
            if rt.pending or not rt._ready():
                raise ValueError('场景测试需要就绪且无待确认请求的会话')
            test_id = uuid4().hex
            self.cancel = threading.Event()
            self.record = {'test_id': test_id, 'session_id': session_id, 'status': 'running',
                'started_at': datetime.now(timezone.utc).isoformat(), 'server_version': __version__,
                'source_snapshot_sha256': session.get('source_snapshot_sha256'),
                'bridge_sha256': hashlib.sha256(compact_encode(sources('0'*32))).hexdigest(),
                'definition': steps, 'results': [], 'images': [], 'stop_on_failure': stop_on_failure,
                'max_seconds': max_seconds}
            self.worker = threading.Thread(target=self._run, args=(self.record,), daemon=True)
            try:
                self.worker.start()
            except Exception:
                self.record['status'] = 'error'
                self.record['error'] = '测试线程启动失败'
                raise
            return {'dry_run': False, 'test_id': test_id, 'session_id': session_id, 'status': 'running',
                    'next': '查询 game_test_status；完成后可 save_game_test_report，勿重复投递测试'}

    def _observe(self, session_id, query, timeout=15):
        action = query['action']
        result = self.runtime.request(session_id, action, {k: v for k, v in query.items() if k != 'action'}, timeout=timeout)
        data = result.get('data')
        if not isinstance(data, dict) or 'error' in data:
            raise ValueError('观察请求未返回有效状态：' + str(data.get('error') if isinstance(data, dict) else 'invalid data'))
        return result

    def _step(self, record, step, deadline):
        rt, session_id = self.runtime, record['session_id']
        action = step['action']
        if action in ('assert', 'wait_until'):
            until = min(deadline, time.monotonic() + step['timeout_seconds'])
            attempts = 0
            last = {}
            while True:
                if self.cancel.is_set():
                    raise ValueError('场景测试已取消')
                remaining = until - time.monotonic()
                if remaining <= 0:
                    return {**last, 'passed': False, 'timed_out': True, 'attempts': attempts}
                observed = self._observe(session_id, step['observe'], min(15, remaining))
                attempts += 1
                comparison = compare(observed.get('data'), step['path'], step['operator'], step['expected'])
                result = {**comparison, 'attempts': attempts, 'frame': observed.get('frame'), 'observation': observed.get('data')}
                last = result
                if comparison['passed'] or action == 'assert':
                    return result
                if time.monotonic() >= until:
                    return {**result, 'timed_out': True}
                self.cancel.wait(min(step['poll_ms'] / 1000, max(0, until-time.monotonic())))
        if action == 'mouse':
            args = {k: v for k, v in step.items() if k not in ('action', 'mouse_action')}
            return rt.mouse(session_id, step['mouse_action'], dry_run=False, **args)
        if action == 'audio':
            return self._observe(session_id, step)
        result = rt.run_sequence(session_id, [step], dry_run=False)
        if not result['ok']:
            raise ValueError(str(result['results'][-1].get('error', '步骤失败')))
        item = result['results'][0]
        if 'image_index' in item:
            item['image_index'] += len(record['images'])
        self._images(record, result.pop('pictures', []))
        return item

    def _images(self, record, images):
        with self.lock:
            for image in images:
                if len(record['images']) >= 4 or len(image) > 8 * 1024 * 1024:
                    raise ValueError('测试截图超过四张或单图 8 MiB 限制')
                record['images'].append(image)

    def _run(self, record):
        rt = self.runtime
        start = time.monotonic()
        deadline = start + record['max_seconds']
        rt.test_deadline = deadline
        status = 'passed'
        try:
            for index, step in enumerate(record['definition'], 1):
                if self.cancel.is_set():
                    status = 'cancelled'
                    break
                if time.monotonic() >= deadline:
                    status = 'timed_out'
                    break
                result = self._step(record, step, deadline)
                if len(compact_encode(result)) > 256 * 1024:
                    raise ValueError('单步结果超过 256 KiB，请缩小状态查询范围')
                with self.lock:
                    record['results'].append({'index': index, 'action': step['action'], 'result': result})
                if result.get('passed') is False:
                    if result.get('timed_out'):
                        status = 'timed_out'
                    elif status == 'passed':
                        status = 'failed'
                    with self.lock:
                        record.setdefault('failed_step', index)
                    if record['stop_on_failure']:
                        break
        except Exception as exc:
            status = 'cancelled' if self.cancel.is_set() else 'timed_out' if time.monotonic() >= deadline else 'error'
            with self.lock:
                record['error'] = rt._redact(str(exc)[:2048])
                record.setdefault('failed_step', len(record['results']) + 1)
        finally:
            rt.test_deadline = time.monotonic() + 5
            evidence = {}
            try:
                with rt.guard:
                    rt._require_session(record['session_id'])
                evidence['runtime_status'] = rt.status()
                evidence['logs'] = rt.output(record['session_id'], max(0, rt.sequence-100), 100)
                if status != 'passed' and not self.cancel.is_set() and not rt.pending and rt._ready():
                    evidence['state'] = rt.request(record['session_id'], 'state',
                        {'object_name': '', 'variables': [], 'limit': 1, 'scope': 'instances'}, timeout=2).get('data')
                    capture = rt.request(record['session_id'], 'capture', timeout=3)
                    evidence['failure_image_index'] = len(record['images'])
                    self._images(record, [capture['image']])
                elif status != 'passed':
                    evidence['capture_skipped'] = '会话已停止、桥接未就绪或请求待确认；不重发命令'
            except Exception as exc:
                evidence['collection_error'] = rt._redact(str(exc)[:1024])
            with rt.guard, self.lock:
                rt.test_deadline = None
                record['evidence'] = evidence
                record['elapsed_seconds'] = round(time.monotonic() - start, 3)
                record['finished_at'] = datetime.now(timezone.utc).isoformat()
                record['status'] = 'cancelled' if self.cancel.is_set() else status
                record['passed'] = record['status'] == 'passed'

    def result(self, test_id, include_images=False):
        with self.lock:
            if not self.record or self.record['test_id'] != test_id:
                raise ValueError('测试编号不匹配；内存仅保留最近一次测试，请先导出再开启下一次')
            result = deepcopy({k: v for k, v in self.record.items() if k != 'images'})
            result['image_count'] = len(self.record['images'])
            if include_images:
                result['pictures'] = list(self.record['images'])
            return result

    def save(self, test_id, dry_run=True):
        with self.lock:
            result = self.result(test_id, True)
        if result['status'] == 'running':
            raise ValueError('测试尚未完成')
        project = self.runtime.project
        if not project.writable and not dry_run:
            raise ValueError('保存测试报告需要 --write')
        relative = '.codex/morx-test-results/' + test_id
        target = project.path(relative)
        if target.exists():
            raise ValueError('报告目录已存在，不覆盖')
        if dry_run:
            return {'dry_run': True, 'path': relative, 'image_count': result['image_count']}
        images = result.pop('pictures')
        result['images'] = [f'capture-{i+1}.png' for i in range(len(images))]
        # 只创建固定编号目录；部分保存保留在目录内并显式失败，不覆盖已有证据。
        target.parent.mkdir(parents=True, exist_ok=True)
        project.path(relative).mkdir(exist_ok=False)
        for name, image in zip(result['images'], images):
            with project.path(relative + '/' + name).open('xb') as out:
                out.write(image)
        with project.path(relative + '/report.json').open('xb') as out:
            out.write(json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8'))
        summary = '# 场景测试报告\n\n' + f"结果：{result['status']}\n\n测试：{test_id}\n\n"
        summary += f"完成步骤：{len(result['results'])}/{len(result['definition'])}\n\n"
        summary += '详细结果、源码快照哈希、桥接哈希和日志见 [report.json](report.json)。\n'
        for name in result['images']:
            summary += f'\n![测试截图]({name})\n'
        with project.path(relative + '/report.md').open('xb') as out:
            out.write(summary.encode('utf-8'))
        return {'dry_run': False, 'path': relative, 'files': ['report.json', 'report.md', *result['images']]}
