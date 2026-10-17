"""合成项目中的流式写入竞争与手动重试幂等回归，不访问真实数据或云模型。"""
import json
import threading
import time
import unittest
from unittest.mock import patch

import httpx

import store
import worker
from interview_prep import workspace as w, ai as interview_ai
import test_interview_workspace as fixtures


class InterviewWorkspaceRecoveryTests(unittest.TestCase):
    setUp = fixtures.InterviewWorkspaceTests.setUp
    tearDown = fixtures.InterviewWorkspaceTests.tearDown
    url = fixtures.InterviewWorkspaceTests.url
    view = fixtures.InterviewWorkspaceTests.view
    scene = fixtures.InterviewWorkspaceTests.scene
    send = fixtures.InterviewWorkspaceTests.send
    run_chat = fixtures.InterviewWorkspaceTests.run_chat

    def cancel(self):
        return self.client.post(self.url() + '/commands', json={
            'revision': self.view()['revision'], 'action': 'cancel'})

    def retry(self, key, task_id=None):
        body = {'revision': self.view()['revision'], 'action': 'retry', 'request_id': key}
        if task_id: body['chat_task_id'] = task_id
        return self.client.post(self.url() + '/commands', json=body)

    def test_retry_replay_returns_same_task_after_first_response_is_lost(self):
        first = self.send().json()['job']['id']
        self.assertEqual(self.cancel().status_code, 200)
        retried = self.retry('retry-idempotent-1234', first)
        replay = self.retry('retry-idempotent-1234', first)
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(retried.json()['job']['id'], replay.json()['job']['id'])
        self.assertEqual(len(replay.json()['messages']), 1)
        with store.get() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks WHERE kind='interview_chat'").fetchone()[0], 2)
            binding = json.loads(db.execute('SELECT fingerprint FROM interview_requests WHERE request_id=?', ('retry:retry-idempotent-1234',)).fetchone()[0])
        self.assertEqual(binding, {'source_task_id': first, 'task_id': retried.json()['job']['id']})

    def test_retry_replay_still_works_after_replacement_has_completed(self):
        source = self.send().json()['job']['id']
        self.cancel()
        initial = self.retry('retry-completed-1234', source)
        # Worker 会先收口原取消任务，再领取替代任务。
        self.assertTrue(worker.run_once(handlers={'interview_chat': w.handler}, worker_id='cancel-cleanup'))
        self.run_chat(lambda messages, on_text, **kwargs: (on_text('合成回复。'), '合成回复。')[1])
        replay = self.retry('retry-completed-1234', source)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()['job']['id'], initial.json()['job']['id'])
        self.assertEqual(replay.json()['job']['status'], 'ready')

    def test_retry_key_cannot_be_reused_for_another_source_task(self):
        source = self.send().json()['job']['id']
        self.cancel()
        first = self.retry('retry-conflict-1234', source)
        self.cancel()
        wrong = self.retry('retry-conflict-1234', first.json()['job']['id'])
        self.assertEqual(wrong.status_code, 409, wrong.text)
        with store.get() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks WHERE kind='interview_chat'").fetchone()[0], 2)

    def test_late_retry_cannot_restart_a_different_newer_failed_task(self):
        old = self.send().json()['job']['id']
        self.cancel()
        new = self.send('另一段合成问题', 'new-message-1234').json()['job']['id']
        self.cancel()
        response = self.retry('late-retry-1234', old)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.view()['job']['id'], new)
        self.assertEqual(self.view()['job']['status'], 'cancelled')

    def test_legacy_retry_without_source_is_idempotent(self):
        self.send()
        self.cancel()
        first = self.retry('legacy-retry-1234')
        replay = self.retry('legacy-retry-1234')
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(first.json()['job']['id'], replay.json()['job']['id'])

    def test_all_chat_writes_validate_lease_in_an_immediate_transaction(self):
        self.send()
        original = worker.TaskContext.check_cancelled
        transactions = []
        def checked(ctx, db=None):
            if db is not None: transactions.append(db.in_transaction)
            return original(ctx, db)
        def stream(messages, on_text, **kwargs):
            on_text('合成分片'); return '合成分片'
        with patch.object(worker.TaskContext, 'check_cancelled', checked):
            self.run_chat(stream)
        self.assertGreaterEqual(len(transactions), 3)
        self.assertTrue(all(transactions))
        self.assertEqual(self.view()['job']['status'], 'ready')

    def test_cancel_between_lease_check_and_flush_is_serialized_before_late_writes(self):
        task_id = self.send().json()['job']['id']
        original = worker.TaskContext.check_cancelled
        stream_started = False
        attempted = threading.Event()
        cancellation = []
        threads = []
        failures = []
        def cancel_after_check():
            try:
                with store.get() as db:
                    attempted.set()
                    db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task_id,))
                    cancellation.append(db.execute("SELECT COUNT(*) FROM workspace_events WHERE task_id=? AND kind='message.delta'", (task_id,)).fetchone()[0])
            except Exception as exc: failures.append(exc)
        def checked(ctx, db=None):
            result = original(ctx, db)
            if db is not None and stream_started and not threads:
                thread = threading.Thread(target=cancel_after_check)
                threads.append(thread); thread.start()
                self.assertTrue(attempted.wait(2))
                time.sleep(.05)
            return result
        def stream(messages, on_text, **kwargs):
            nonlocal stream_started
            stream_started = True
            time.sleep(.27)
            on_text('先于取消提交的合成草稿')
            for thread in threads: thread.join(5)
            return '不应成为正式回复'
        try:
            with patch.object(worker.TaskContext, 'check_cancelled', checked):
                self.run_chat(stream)
        finally:
            for thread in threads: thread.join(5)
        self.assertFalse(failures)
        self.assertTrue(threads and all(not thread.is_alive() for thread in threads))
        self.assertEqual(cancellation, [1], '取消应在已经校验过的分片提交之后生效，不能先取消再写入分片')
        value = self.view()
        self.assertEqual(value['job']['status'], 'cancelled')
        self.assertEqual(len(value['messages']), 1)

    def test_failed_stream_publishes_error_only_with_current_lease_and_input(self):
        self.send()
        original = worker.TaskContext.check_cancelled
        transactions = []
        def checked(ctx, db=None):
            if db is not None: transactions.append(db.in_transaction)
            return original(ctx, db)
        def timeout(messages, on_text, **kwargs):
            raise httpx.ReadTimeout('synthetic-private-detail')
        with patch.object(worker.TaskContext, 'check_cancelled', checked):
            self.run_chat(timeout)
        self.assertGreaterEqual(len(transactions), 2)
        self.assertTrue(all(transactions))
        self.assertEqual(self.view()['job']['status'], 'retry')
        self.assertNotIn('synthetic-private-detail', json.dumps(self.view()))

    def test_preparation_chat_cannot_continue_when_a_simulation_starts(self):
        self.scene('configure', config={**self.project['config'], 'practice_mode': 'simulation'})
        self.scene('prepare')
        self.assertTrue(worker.run_once(handlers=interview_ai.HANDLERS, worker_id='simulation-prepare'))
        self.send()
        def stream(messages, on_text, **kwargs):
            self.assertIn('"simulation_active": false', messages[1]['content'])
            self.scene('start')
            on_text('不应在模拟开始后发布的作答提示')
            return '不应在模拟开始后发布的作答提示'
        self.run_chat(stream)
        value = self.view()
        self.assertEqual(value['job']['status'], 'failed')
        self.assertNotIn('不应在模拟开始后', json.dumps(value, ensure_ascii=False))
        self.assertEqual(len(value['messages']), 1)

    def test_simulation_fingerprint_tracks_frozen_mode_but_ignores_question_and_recording_changes(self):
        self.scene('configure', config={**self.project['config'], 'practice_mode': 'simulation'})
        self.scene('prepare')
        self.assertTrue(worker.run_once(handlers=interview_ai.HANDLERS, worker_id='simulation-prepare'))
        value = self.scene('start')
        practice = value['scene_state']['practices'][0]
        with store.get() as db:
            p = w.preparation(db, value['id'])
            before = w.fingerprint(db, p)
            frozen = json.loads(db.execute('SELECT snapshot FROM interview_practices WHERE id=?', (practice['id'],)).fetchone()[0])
            db.execute("UPDATE interview_turns SET status='answered' WHERE practice_id=?", (practice['id'],))
            self.assertEqual(w.fingerprint(db, p), before)
            frozen['config']['practice_mode'] = 'coaching'
            db.execute('UPDATE interview_practices SET snapshot=? WHERE id=?', (json.dumps(frozen), practice['id']))
            self.assertNotEqual(w.fingerprint(db, p), before)


if __name__ == '__main__': unittest.main()
