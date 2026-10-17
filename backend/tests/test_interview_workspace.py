"""统一项目、专业状态机与真实流式边界的离线集成验收。"""
import asyncio
import json
import time
import unittest
from unittest.mock import patch
import httpx

import test_interview_prep as fixtures
import store
import worker
import job_queue
import research_platform
from interview_prep import domain as d, workspace as w


class InterviewWorkspaceTests(unittest.TestCase):
    setUp = fixtures.InterviewPreparationTests.setUp
    tearDown = fixtures.InterviewPreparationTests.tearDown

    def url(self): return '/api/platform/projects/' + self.project['workspace_project_id']

    def view(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def scene(self, action, **fields):
        p = self.client.get('/api/interview-prep/projects/' + self.project['id']).json()
        response = self.client.post(self.url() + '/commands', json={'revision': self.view()['revision'], 'action': 'scene', 'scene_command': {'action': action, 'revision': p['revision'], 'request_id': d.uuid.uuid4().hex, **fields}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def send(self, text='想练习介绍自己的项目经历', key='chat-request-1234'):
        return self.client.post(self.url() + '/messages', json={'content': text, 'request_id': key})

    def run_chat(self, stream):
        with patch('platform_ai.stream', side_effect=stream):
            self.assertTrue(worker.run_once(handlers={'interview_chat': w.handler}, worker_id='workspace-test'))

    def test_legacy_project_is_in_unified_list_once_and_migration_is_idempotent(self):
        before = self.project['workspace_project_id']
        d.init(); d.init()
        self.assertEqual(self.view()['id'], before)
        values = self.client.get('/api/platform/projects').json()
        self.assertEqual([p['id'] for p in values], [before])
        self.assertEqual(values[0]['scenario'], 'interview')
        self.assertEqual(self.view()['experience'], 'workspace')
        self.assertEqual(len(self.view()['workflow']['steps']), 6)

    def test_new_creation_is_idempotent_including_initial_chat(self):
        body = {'prompt': '准备产品经理面试', 'scenario': 'interview', 'request_id': 'unified-create-1234', 'mode': 'ai'}
        first = self.client.post('/api/platform/projects', json=body)
        self.assertEqual(first.status_code, 201, first.text)
        second = self.client.post('/api/platform/projects', json=body)
        self.assertEqual(first.json()['id'], second.json()['id'])
        self.assertEqual(len(second.json()['messages']), 1)
        self.assertEqual(first.json()['job']['id'], second.json()['job']['id'])
        with store.get() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks WHERE resource_id=? AND kind='interview_chat'", (first.json()['id'],)).fetchone()[0], 1)

    def test_invalid_scene_payload_returns_422(self):
        bad = self.client.post('/api/platform/projects', json={'prompt': '准备面试', 'scenario': 'interview', 'request_id': 'invalid-create-1234', 'interview_config': {'goal': '面试', 'duration_minutes': -1}})
        self.assertEqual(bad.status_code, 422)
        bad = self.client.post(self.url() + '/commands', json={'revision': self.view()['revision'], 'action': 'scene', 'scene_command': {'action': 'not-supported'}})
        self.assertEqual(bad.status_code, 422)

    def test_domain_commands_keep_single_canonical_material_and_revision(self):
        view = self.scene('material', title='简历', kind='resume', content='我做过合成项目需求分析。')
        self.assertEqual(len(view['artifacts']), 1)
        artifact = view['artifacts'][0]
        self.assertTrue(artifact['readonly'])
        self.assertEqual(artifact['resource']['type'], 'material')
        self.scene('revise_material', material_id=artifact['id'], content='修订后的合成经历。')
        revised = self.view()['artifacts'][0]
        self.assertEqual(revised['version'], 2)
        self.assertEqual(revised['content'], '修订后的合成经历。')
        with store.get() as db:
            raw = json.loads(db.execute('SELECT snapshot FROM research_workspace_runs WHERE project_id=?', (view['id'],)).fetchone()[0])
        self.assertEqual(raw['artifacts'], [])

    def test_resume_file_upload_is_canonical_idempotent_and_used_by_chat(self):
        content = '合成简历：我负责用户需求分析与产品验证。'
        fields = {'revision': str(self.view()['scene_state']['domain_revision']), 'request_id': 'resume-upload-1234', 'kind': 'resume'}
        files = {'file': ('合成简历.txt', content.encode('utf-8'), 'text/plain')}
        first = self.client.post(self.url() + '/materials', data=fields, files=files)
        self.assertEqual(first.status_code, 200, first.text)
        # 响应丢失后沿用相同编号和旧版本重放，不能添加第二份简历。
        replay = self.client.post(self.url() + '/materials', data=fields, files=files)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(len(replay.json()['artifacts']), 1)
        material = self.client.get(self.url() + '/interview').json()['materials'][0]
        self.assertEqual(material['kind'], 'resume')
        self.assertIn('用户需求分析', material['content'])
        self.assertEqual(material['title'], '合成简历.txt')
        self.send('请参考刚才上传的简历继续讨论')
        def reply(messages, on_text, **kwargs):
            self.assertIn('用户需求分析', messages[1]['content'])
            self.assertIn('resume', messages[1]['content'])
            on_text('请说说你验证过的一个具体需求。')
            return '请说说你验证过的一个具体需求。'
        self.run_chat(reply)
        self.assertEqual(self.view()['messages'][-1]['text'], '请说说你验证过的一个具体需求。')

    def test_workspace_owner_and_delete_also_protect_legacy_paths(self):
        original = self.who; self.who = 12
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(self.send().status_code, 404)
        self.assertEqual(self.client.get(self.url() + '/interview').status_code, 404)
        self.who = original
        self.send()
        self.assertEqual(self.client.delete(self.url()).status_code, 200)
        self.assertEqual(self.client.get('/api/interview-prep/projects/' + self.project['id']).status_code, 404)
        self.assertEqual(self.client.get(self.url() + '/events').status_code, 404)
        d.init()
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(self.client.get('/api/platform/projects').json(), [])
        with store.get() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks WHERE resource_id=? AND status='cancelled'", (self.project['workspace_project_id'],)).fetchone()[0], 1)

    def test_legacy_delete_hides_unified_project(self):
        self.client.delete('/api/interview-prep/projects/' + self.project['id'])
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(self.client.get('/api/platform/projects').json(), [])

    def test_id_collision_never_reuses_another_owners_project(self):
        cfg = d.dump(self.cfg)
        with store.get() as db:
            db.execute('INSERT INTO projects(id,name,owner_id,created_at,updated_at) VALUES(?,?,?,?,?)', ('ip_collision', '已有项目', 99, 1, 1))
            db.execute('INSERT INTO interview_preparations(id,owner_id,title,config,created_at,updated_at) VALUES(?,?,?,?,?,?)', ('ip_collision', 11, '旧面试', cfg, 1, 1))
        d.init(); d.init()
        with store.get() as db:
            pid = db.execute("SELECT workspace_project_id FROM interview_preparations WHERE id='ip_collision'").fetchone()[0]
            self.assertNotEqual(pid, 'ip_collision')
            self.assertEqual(db.execute("SELECT owner_id FROM projects WHERE id='ip_collision'").fetchone()[0], 99)
            self.assertEqual(db.execute('SELECT owner_id FROM projects WHERE id=?', (pid,)).fetchone()[0], 11)

    def test_stream_is_visible_before_completion_and_does_not_change_business_revision(self):
        self.assertEqual(self.send().status_code, 202)
        initial_revision = self.view()['revision']; observations = []
        def stream(messages, on_text, **kwargs):
            on_text('可以先讲'); time.sleep(.27); on_text('清楚个人行动。')
            value = self.view(); observations.append(value)
            self.assertEqual(value['job']['output'], '可以先讲清楚个人行动。')
            self.assertFalse(any(m['role'] == 'assistant' for m in value['messages']))
            self.assertEqual(value['revision'], initial_revision)
            on_text('我们从哪段经历开始？')
            return '可以先讲清楚个人行动。我们从哪段经历开始？'
        self.run_chat(stream)
        value = self.view()
        self.assertEqual(value['job']['status'], 'ready')
        self.assertEqual(len(observations), 1)
        self.assertEqual(value['messages'][-1]['text'], '可以先讲清楚个人行动。我们从哪段经历开始？')
        with store.get() as db: kinds = [r['kind'] for r in db.execute('SELECT kind FROM workspace_events ORDER BY id')]
        self.assertIn('message.delta', kinds); self.assertIn('message.completed', kinds)

    def test_same_request_is_idempotent_and_reuse_with_other_text_is_rejected(self):
        first = self.send(); second = self.send()
        self.assertEqual(first.json()['job']['id'], second.json()['job']['id'])
        self.assertEqual(len(second.json()['messages']), 1)
        self.assertEqual(self.send('不同的内容').status_code, 409)

    def test_retry_retains_one_human_message_and_resets_partial_output(self):
        self.send()
        def timeout(messages, on_text, **kwargs):
            on_text('旧草稿'); time.sleep(.27); on_text('片段')
            raise httpx.ReadTimeout('synthetic-private-error')
        self.run_chat(timeout)
        job = self.view()['job']; self.assertEqual(job['status'], 'retry')
        with store.get() as db: db.execute('UPDATE tasks SET available_at=0 WHERE id=?', (job['id'],))
        def reply(messages, on_text, **kwargs): on_text('新的完整回复。'); return '新的完整回复。'
        self.run_chat(reply)
        value = self.view()
        self.assertEqual(value['job']['attempt'], 2)
        self.assertEqual(value['messages'][-1]['text'], '新的完整回复。')
        self.assertEqual(len([m for m in value['messages'] if m['role'] == 'user']), 1)
        self.assertNotIn('synthetic-private-error', json.dumps(value))

    def test_cancel_drops_late_output_without_confirming_assistant_message(self):
        self.send()
        def late(messages, on_text, **kwargs):
            response = self.client.post(self.url() + '/commands', json={'revision': self.view()['revision'], 'action': 'cancel'})
            self.assertEqual(response.status_code, 200)
            on_text('不应保存的迟到内容')
            return '不应保存的迟到内容'
        self.run_chat(late)
        value = self.view()
        self.assertEqual(value['job']['status'], 'cancelled')
        self.assertEqual(len(value['messages']), 1)
        self.assertNotIn('不应保存', json.dumps(value, ensure_ascii=False))

    def test_changed_input_blocks_late_chat_commit(self):
        self.send()
        def changed(messages, on_text, **kwargs):
            self.scene('material', title='后来补充', content='新的合成材料', kind='background')
            on_text('旧上下文生成的回应'); return '旧上下文生成的回应'
        self.run_chat(changed)
        self.assertEqual(self.view()['job']['status'], 'failed')
        self.assertEqual(len(self.view()['messages']), 1)

    def test_stream_output_and_professional_generation_have_separate_scopes(self):
        self.send()
        self.scene('prepare')
        def text(messages, on_text, **kwargs): on_text('准备计划在后台生成中。'); return '准备计划在后台生成中。'
        self.run_chat(text)
        self.assertTrue(worker.run_once(handlers=fixtures.ai.HANDLERS, worker_id='domain-test'))
        value = self.view()
        self.assertTrue(any(a['resource']['type'] == 'plan' for a in value['artifacts']))
        self.assertEqual(value['job']['status'], 'ready')
        self.assertEqual(len([t for t in value['tasks'] if t['status'] == 'ready']), 2)

    def test_events_restore_snapshot_and_revoke_access_without_private_leak(self):
        self.send()
        class Request:
            headers = {'Last-Event-ID': '999999999'}
            calls = 0
            async def is_disconnected(self):
                self.calls += 1
                return self.calls > 1
        async def read():
            with patch('employee_auth.current_user', return_value={'id': self.who}):
                response = w.events(self.project['workspace_project_id'], Request())
                return [frame async for frame in response.body_iterator]
        frames = asyncio.run(read())
        self.assertIn('event: snapshot', frames[0])
        self.who = 12
        frames = asyncio.run(read())
        self.assertEqual(frames, ['event: deleted\ndata: {}\n\n'])

    def test_event_cursor_replay_retention_and_project_filtering(self):
        pid = self.project['workspace_project_id']
        with store.get() as db:
            first = w.emit(db, pid, None, 'message.started', {'task_id': 'synthetic'})
            second = w.emit(db, pid, None, 'message.delta', {'delta': '合成片段'})
            w.emit(db, 'another-project', None, 'message.delta', {'delta': 'other-project-secret'})
        class Request:
            headers = {'Last-Event-ID': str(first)}
            calls = 0
            async def is_disconnected(self): self.calls += 1; return self.calls > 1
        async def read():
            with patch('employee_auth.current_user', return_value={'id': self.who}):
                return [frame async for frame in w.events(pid, Request()).body_iterator]
        frames = asyncio.run(read())
        self.assertIn(f'id: {second}\nevent: message.delta', frames[0])
        self.assertIn('event: snapshot', frames[-1])
        self.assertNotIn('other-project-secret', ''.join(frames))
        with store.get() as db:
            for i in range(1010): w.emit(db, pid, None, 'task.updated', {'sequence': i})
            self.assertEqual(db.execute('SELECT COUNT(*) FROM workspace_events WHERE project_id=?', (pid,)).fetchone()[0], 1000)
        frames = asyncio.run(read())
        self.assertEqual(len(frames), 1)
        self.assertIn('event: snapshot', frames[0])

    def test_question_tasks_have_separate_scope_from_preparation(self):
        self.scene('add_question', question='你怎样验证结果？')
        question = self.client.get(self.url() + '/interview').json()['question_bank'][0]
        self.scene('prepare')
        self.scene('analyze_question', question_id=question['id'])
        self.assertTrue(worker.run_once(handlers=fixtures.ai.HANDLERS, worker_id='prepare-test'))
        self.assertTrue(worker.run_once(handlers=fixtures.ai.HANDLERS, worker_id='question-test'))
        detail = self.client.get(self.url() + '/interview').json()
        self.assertTrue(detail['plan'])
        self.assertTrue(detail['question_bank'][0]['analysis'])
        self.assertNotEqual(detail['job']['id'], detail['question_bank'][0]['job']['id'])
        self.assertTrue(all(t['status'] == 'ready' for t in self.view()['tasks']))

    def test_live_transcription_refreshes_same_sse_without_business_revision_change(self):
        self.scene('prepare')
        worker.run_once(handlers=fixtures.ai.HANDLERS, worker_id='prepare-capture-test')
        self.scene('start')
        practice = self.client.get(self.url() + '/interview').json()['practices'][0]
        self.scene('answer', practice_id=practice['id'], turn_id=practice['turns'][0]['id'])
        detail = self.client.get(self.url() + '/interview').json()
        answer = detail['practices'][0]['turns'][0]['answers'][0]
        before = self.view()
        class Request:
            headers = {}
            calls = 0
            async def is_disconnected(self): self.calls += 1; return self.calls > 2
        async def add_segment(_):
            store.add_utterance(answer['session_id'], 0, 0, 2000, '仅供测试的实时转写。')
        async def read():
            with patch('employee_auth.current_user', return_value={'id': self.who}), patch.object(w.asyncio, 'sleep', side_effect=add_segment):
                return [frame async for frame in w.events(before['id'], Request()).body_iterator]
        frames = asyncio.run(read())
        snapshots = [json.loads(frame.split('data: ', 1)[1]) for frame in frames if 'event: snapshot' in frame]
        self.assertEqual(len(snapshots), 2)
        self.assertNotEqual(snapshots[0]['scene_state']['capture_version'], snapshots[1]['scene_state']['capture_version'])
        self.assertEqual(snapshots[0]['revision'], snapshots[1]['revision'])
        self.assertEqual(snapshots[0]['scene_state']['domain_revision'], snapshots[1]['scene_state']['domain_revision'])
        transcript = self.client.get(self.url() + '/interview').json()['practices'][0]['turns'][0]['answers'][0]['segments']
        self.assertEqual(transcript[0]['text'], '仅供测试的实时转写。')
        with store.get() as db:
            unchanged = w.capture_version(db, self.project['id'])
            db.execute("INSERT INTO utterances(session_id,seq,start_ms,end_ms,text) VALUES('unrelated-session',0,0,1,'other')")
            self.assertEqual(w.capture_version(db, self.project['id']), unchanged)

    def test_simulation_filters_unpublished_feedback_from_detail_projection_and_chat_context(self):
        cfg = {**self.project['config'], 'practice_mode': 'simulation'}
        self.scene('configure', config=cfg); self.scene('prepare')
        worker.run_once(handlers=fixtures.ai.HANDLERS, worker_id='prepare-test')
        self.scene('start')
        detail = self.client.get(self.url() + '/interview').json(); practice = detail['practices'][0]
        with store.get() as db:
            db.execute('UPDATE interview_turns SET feedback=?,decision_reason=? WHERE practice_id=?', (d.dump([{'observation': 'private-evaluation'}]), 'private-reason', practice['id']))
        self.assertNotIn('private-evaluation', json.dumps(self.view()))
        refreshed = self.client.get(self.url() + '/interview').json()
        self.assertNotIn('private-evaluation', json.dumps(refreshed))
        self.assertEqual(refreshed['practices'][0]['turns'][0]['decision_reason'], '')
        self.send('现在告诉我本题点评')
        def read_context(messages, on_text, **kwargs):
            self.assertNotIn('private-evaluation', json.dumps(messages))
            self.assertNotIn('private-reason', json.dumps(messages))
            self.assertIn('"simulation_active": true', messages[1]['content'])
            on_text('请结束练习后查看复盘。'); return '请结束练习后查看复盘。'
        self.run_chat(read_context)
        self.assertEqual(self.view()['messages'][-1]['text'], '请结束练习后查看复盘。')

    def test_archive_requires_latest_review_and_is_invalidated_by_domain_changes(self):
        with store.get() as db:
            frozen = {'config': self.project['config'], 'materials': [], 'plan': fixtures.model_fixture('interview_prepare', {})}
            db.execute('INSERT INTO interview_practices(id,project_id,status,snapshot,review,created_at) VALUES(?,?,?,?,?,?)', ('finished-practice', self.project['id'], 'done', d.dump(frozen), d.dump({'summary': '合成复盘', 'findings': [], 'retry_questions': []}), 1))
        archived = self.client.post(self.url() + '/commands', json={'revision': self.view()['revision'], 'action': 'archive'})
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertEqual(archived.json()['stage'], 5)
        self.scene('material', title='新资料', content='新的合成经历。')
        self.assertEqual(self.view()['stage'], 4)
        with store.get() as db:
            db.execute('INSERT INTO interview_practices(id,project_id,status,snapshot,created_at) VALUES(?,?,?,?,?)', ('newer-practice', self.project['id'], 'done', d.dump(frozen), 2))
        refused = self.client.post(self.url() + '/commands', json={'revision': self.view()['revision'], 'action': 'archive'})
        self.assertEqual(refused.status_code, 409)


if __name__ == '__main__': unittest.main()
