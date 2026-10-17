"""合成原话与模型工具调用回归；禁止真实云调用。"""
import json
import unittest
from unittest.mock import patch

import test_conversation_engine as base_tests
import conversation_engine as engine
import store
import worker
from memory import repository, retrieval
from memoir_editor import chapters


def tool(name, arguments, key='tool-1'):
    return {'id': key, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}


def response(message='您刚到厂里的第一天是什么样的？', **values):
    return {'role': 'assistant', 'content': None, 'tool_calls': [tool('respond', {'action': 'follow_up', 'message': message, **values})]}


class MemoirAgentTests(unittest.TestCase):
    setUp = base_tests.ConversationTests.setUp
    send = base_tests.ConversationTests.send
    run_reply = base_tests.ConversationTests.run_reply

    def execute(self, replies):
        with patch('agents.model.complete', side_effect=replies) as mock:
            worker.run_once(handlers={'conversation_reply': engine.reply_job}, worker_id='agent-test')
        return mock

    def human(self):
        return self.client.get(self.url).json()['turns'][-2]

    def test_tool_results_are_returned_and_plan_is_saved(self):
        self.send('那年我和二哥去了南方。')
        mock = self.execute([
            {'role': 'assistant', 'content': '内部调度不能朗读', 'tool_calls': [tool('search_memories', {'query': '二哥'})]},
            response(plan={'topic': '第一次外出工作', 'open_threads': ['刚到南方的生活'], 'skipped_topics': []}),
        ])
        value = self.client.get(self.url).json()
        self.assertEqual(value['turns'][-1]['job']['status'], 'ready')
        self.assertNotIn('内部调度', json.dumps(value, ensure_ascii=False))
        self.assertEqual(value['agent']['plan']['topic'], '第一次外出工作')
        self.assertEqual(mock.call_args_list[1].args[0][-1]['role'], 'tool')
        with store.get() as db:
            run = db.execute('SELECT * FROM memoir_agent_runs').fetchone()
            self.assertEqual(len(json.loads(run['checkpoint'])), 2)
            self.assertTrue(run['decision'])

    def test_memory_requires_real_human_evidence_and_cannot_confirm_itself(self):
        self.send('1980年我第一次去工厂工作。')
        human = self.human()
        candidate = {'category': 'event', 'title': '进厂', 'description': '第一次进厂工作', 'date_text': '1980年', 'evidence': [{'turn_id': human['id'], 'quote': human['text']}]}
        self.execute([{'role': 'assistant', 'content': None, 'tool_calls': [tool('save_memory_candidates', {'memories': [candidate]})]}, response()])
        memory = self.client.get(self.base + '/conversation-archive').json()['memories'][0]
        self.assertEqual(memory['status'], 'candidate')
        with store.get() as db:
            with self.assertRaises(ValueError):
                repository.save_candidate(db, self.project['id'], {**candidate, 'evidence': [{'turn_id': self.session['turns'][0]['id'], 'quote': self.session['turns'][0]['text']}]})
            with self.assertRaises(ValueError):
                repository.save_candidate(db, self.project['id'], {**candidate, 'date_text': '1981年'})

    def test_invalid_tool_rolls_back_and_model_can_recover(self):
        self.send('母亲带我赶集。')
        human = self.human()
        good = {'title': '赶集', 'description': '和母亲赶集', 'evidence': [{'turn_id': human['id'], 'quote': human['text']}]}
        self.execute([{'role': 'assistant', 'content': None, 'tool_calls': [tool('save_memory_candidates', {'memories': [good, {**good, 'title': '错误', 'date_text': '1980年'}]})]}, response()])
        self.assertEqual(self.client.get(self.base + '/conversation-archive').json()['memories'], [])

    def test_loop_budget_finishes_without_unbounded_calls(self):
        self.send('我小时候住在村里。')
        mock = self.execute([{'role': 'assistant', 'content': None, 'tool_calls': [tool('search_memories', {'query': '村里'}, str(i))]} for i in range(4)])
        self.assertLessEqual(mock.call_count, 4)
        self.assertEqual(self.client.get(self.url).json()['turns'][-1]['text'], '您可以接着往下讲，我在听。')

    def test_multiple_questions_are_repaired_without_saving_invalid_reply(self):
        self.send('我小时候住在村里。')
        self.execute([response('您住在哪里？什么时候搬家？'), response('您小时候住的地方是什么样的？')])
        value = self.client.get(self.url).json()
        self.assertEqual(value['turns'][-1]['job']['status'], 'ready')
        self.assertEqual(value['turns'][-1]['text'], '您小时候住的地方是什么样的？')
        self.assertEqual(value['turns'][-1]['output'], '')

    def test_cross_project_tool_scope_cannot_be_overridden(self):
        self.send('我和母亲赶集。')
        human = self.human()
        other = self.client.post('/api/platform/projects', json={'prompt': '另一份口述史', 'mode': 'demo', 'request_id': 'other-memory-project'}).json()
        with store.get() as db:
            self.assertEqual(retrieval.search(db, other['id'], '赶集')['originals'], [])
            with self.assertRaises(ValueError):
                repository.sources(db, other['id'], [human['id']])
        self.execute([{'role': 'assistant', 'content': None, 'tool_calls': [tool('search_memories', {'query': '赶集', 'project_id': other['id']})]}, response()])
        self.assertEqual(self.client.get(self.url).json()['turns'][-1]['job']['status'], 'ready')

    def test_memory_confirm_dispute_and_correction_preserve_versions(self):
        self.send('我大约1980年进厂，也可能是1981年。')
        human = self.human()
        evidence = [{'turn_id': human['id'], 'quote': human['text']}]
        with store.get() as db:
            first = repository.save_candidate(db, self.project['id'], {'title': '进厂', 'description': '进厂工作', 'date_text': '1980年', 'evidence': evidence})
            second = repository.save_candidate(db, self.project['id'], {'title': '工厂工作', 'description': '进厂时间需确认', 'date_text': '1981年', 'evidence': evidence})
        self.assertEqual(first['status'], 'uncertain')
        url = self.base + '/conversation-memories/' + first['id'] + '/review'
        self.assertEqual(self.client.post(url, json={'revision': 1, 'action': 'dispute', 'note': '年份记不准'}).json()['status'], 'disputed')
        self.assertEqual(self.client.post(url, json={'revision': 1, 'action': 'confirm'}).status_code, 409)
        changed = self.client.post(self.base + '/conversation-memories/' + second['id'] + '/review', json={'revision': 1, 'action': 'confirm', 'replaces': first['id']}).json()
        self.assertEqual(changed['status'], 'confirmed')
        self.assertIn(first['id'], changed['related_ids'])
        with store.get() as db:
            rows = repository.memories(db, self.project['id'])
            self.assertEqual(next(m for m in rows if m['id'] == first['id'])['status'], 'superseded')
            self.assertGreaterEqual(db.execute('SELECT COUNT(*) FROM memoir_memory_versions').fetchone()[0], 2)

    def test_deleted_project_cannot_receive_late_agent_results(self):
        self.send('我在村里长大。')
        def deleting(*args):
            self.client.delete(self.base)
            return response()
        self.execute(deleting)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        with store.get() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM memoir_agent_runs').fetchone()[0], 0)

    def test_worker_release_resumes_tool_checkpoint(self):
        self.send('小时候我和母亲赶集。')
        first = {'role': 'assistant', 'content': None, 'tool_calls': [tool('search_memories', {'query': '赶集'})]}
        self.execute([first, worker.WorkerStopping('test interruption')])
        self.assertEqual(self.client.get(self.url).json()['turns'][-1]['job']['status'], 'retry')
        mock = self.execute([response()])
        self.assertEqual(mock.call_count, 1)
        if engine.config.KIMI_MODEL.startswith('kimi-k3'):
            self.assertEqual(mock.call_args.args[0][-1]['role'], 'user')
            self.assertIn('completed_tool_results', mock.call_args.args[0][-1]['content'])
        else:
            self.assertEqual(mock.call_args.args[0][-1]['role'], 'tool')
        self.assertEqual(self.client.get(self.url).json()['turns'][-1]['job']['status'], 'ready')

    def test_authenticated_api_rejects_another_employees_project(self):
        with patch.object(engine.config, 'AUTH_ENABLED', True), patch('employee_auth.current_user', return_value={'id': 42, 'name': '合成员工'}):
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.assertEqual(self.client.get(self.base + '/conversation-book').status_code, 404)

    def test_authenticated_project_ownership_is_bound_by_server(self):
        with patch.object(engine.config, 'AUTH_ENABLED', True):
            with patch('employee_auth.current_user', return_value={'id': 42, 'name': '合成人物甲'}):
                project = self.client.post('/api/platform/projects', json={'prompt': '另一份口述史', 'mode': 'ai', 'request_id': 'owned-memoir-project'}).json()
                self.assertEqual(self.client.get('/api/platform/projects/' + project['id']).status_code, 200)
                self.assertEqual(self.client.post('/api/platform/projects/' + project['id'] + '/conversations', json={'request_id': 'owned-memoir-session'}).status_code, 200)
            with patch('employee_auth.current_user', return_value={'id': 43, 'name': '合成人物乙'}):
                self.assertEqual(self.client.get('/api/platform/projects/' + project['id']).status_code, 404)
                self.assertEqual(self.client.delete('/api/platform/projects/' + project['id']).status_code, 404)
                self.assertNotIn(project['id'], [p['id'] for p in self.client.get('/api/platform/projects').json()])

    def book_model(self, system, value):
        if system == chapters.SCENE_SYSTEM:
            return {'scenes': [{'title': '童年的往事', 'summary': t['text'], 'missing': ['您还记得那里的样子吗？'],
                                'evidence': [{'turn_id': t['turn_id'], 'quote': t['text'][:1200]}]} for t in value['human_turns']], 'deferred_turn_ids': []}
        if system == chapters.PLAN_SYSTEM:
            return {'chapters': [{'chapter_id': value['existing_chapters'][0]['id'] if value['existing_chapters'] else '',
                                 'title': '童年的生活', 'scene_ids': [s['id'] for s in value['new_scenes']]}]}
        if system == chapters.AUDIT_SYSTEM:
            return {'issues': []}
        if system == chapters.bio.CONTINUITY_SYSTEM:
            return {'issues': []}
        if isinstance(value, dict):
            value = value['human_turns']
        return {'paragraphs': [{'text': '我记得童年的生活。', 'evidence': [{'turn_id': t['turn_id'], 'quote': t['text']}]} for t in value]}

    def make_book(self, model=None):
        result = self.client.post(self.base + '/conversation-book', json={})
        self.assertEqual(result.status_code, 200)
        with patch('memoir_editor.chapters._chat', side_effect=model or self.book_model):
            worker.run_once(handlers={'conversation_book': chapters.job})
        return self.client.get(self.base + '/conversation-book').json()

    def test_book_covers_cross_session_sources_and_retains_edits_as_proposal(self):
        self.send('我小时候和母亲赶集。'); self.run_reply()
        self.client.post(self.url + '/finish', json={})
        second = self.client.post(self.base + '/conversations', json={'request_id': 'book-next-session', 'new_session': True}).json()
        self.client.post(self.base + '/conversations/' + second['id'] + '/turns', json={'client_id': 'second-story-turn', 'text': '后来我和二哥去了南方。'})
        value = self.make_book()
        self.assertEqual(value['job']['status'], 'ready')
        self.assertEqual(value['book']['coverage']['source_turns'], 2)
        self.assertEqual(value['book']['coverage']['uncited_turn_ids'], [])
        revision = value['book']['revision']
        edited = self.client.post(self.base + '/conversation-book/review', json={'revision': revision, 'action': 'edit', 'chapter_index': 0, 'paragraph_index': 0, 'text': '这是本人修改过的章节。'})
        self.assertEqual(edited.status_code, 200)
        self.client.post(self.base + '/conversation-book/settings', json={'revision': 0, 'action': 'settings', 'voice_note': '保留本人的直爽口吻。'})
        regenerated = self.make_book()['book']
        self.assertEqual(regenerated['chapters'][0]['paragraphs'][0]['text'], '这是本人修改过的章节。')
        self.assertIsNotNone(regenerated['pending_draft'])
        adopted = self.client.post(self.base + '/conversation-book/review', json={'revision': regenerated['revision'], 'action': 'adopt'})
        self.assertEqual(adopted.status_code, 200)
        self.assertNotIn('pending_draft', adopted.json()['book'])
        self.assertGreaterEqual(len(self.client.get(self.base + '/conversation-book/versions').json()['versions']), 2)

    def test_book_rejects_missing_plan_sources_and_invalid_quotes(self):
        self.send('我在村里长大。')
        def missing(system, block):
            if system == chapters.PLAN_SYSTEM: return {'chapters': [{'title': '童年', 'scene_ids': []}]}
            return self.book_model(system, block)
        value = self.make_book(missing)
        self.assertEqual(value['job']['status'], 'failed')
        self.assertIsNone(value['book'])
        def invented(system, block):
            if system in {chapters.PLAN_SYSTEM, chapters.SCENE_SYSTEM, chapters.AUDIT_SYSTEM}:
                return self.book_model(system, block)
            return {'paragraphs': [{'text': '编造', 'evidence': [{'turn_id': 'wrong-id', 'quote': '虚构'}]}]}
        self.assertEqual(self.make_book(invented)['job']['status'], 'failed')

    def test_book_rejects_late_results_after_source_change(self):
        self.send('我在村里长大。')
        def changed(system, block):
            if system == chapters.AUDIT_SYSTEM and 'paragraphs' in block:
                with store.get() as db:
                    db.execute("UPDATE conversation_turns SET text='新的本人更正原话' WHERE role='human'")
            return self.book_model(system, block)
        value = self.make_book(changed)
        self.assertEqual(value['job']['status'], 'failed')
        self.assertIsNone(value['book'])

    def test_quote_coverage_flags_uncited_turns(self):
        from memoir_editor.validation import coverage
        value = coverage([{'id': 'one'}, {'id': 'two'}], [{'paragraphs': [{'evidence': [{'turn_id': 'one', 'quote': '原话'}]}]}])
        self.assertEqual(value['uncited_turn_ids'], ['two'])

    def test_book_retry_reuses_validated_checkpoints(self):
        self.send('我小时候和母亲赶集。')
        calls = []
        def interrupted(system, block):
            calls.append(system)
            if system == chapters.AUDIT_SYSTEM and 'paragraphs' in block:
                raise RuntimeError('synthetic interruption')
            return self.book_model(system, block)
        self.assertEqual(self.make_book(interrupted)['job']['status'], 'failed')
        calls.clear()
        def resumed(system, block):
            calls.append(system)
            return self.book_model(system, block)
        self.assertEqual(self.make_book(resumed)['job']['status'], 'ready')
        self.assertNotIn(chapters.PLAN_SYSTEM, calls)

    def test_context_budget_keeps_latest_full_source(self):
        from agents.context import build
        self.send('这是一段很长的本人原话。' * 800)
        value = self.client.get(self.url).json()
        with store.get() as db:
            material = build(db, self.project['id'], self.session['id'], value['turns'][-1]['id'])
        self.assertLessEqual(len(json.dumps(material, ensure_ascii=False)), 20000)
        self.assertEqual(material['recent_dialogue'][-1]['text'], value['turns'][-2]['text'])

    def test_memory_review_marks_book_stale_and_blocks_review(self):
        self.send('1980年我进厂工作。')
        human = self.human()
        book = self.make_book()['book']
        with store.get() as db:
            memory = repository.save_candidate(db, self.project['id'], {'title': '进厂', 'description': '进厂工作', 'date_text': '1980年', 'evidence': [{'turn_id': human['id'], 'quote': human['text']}]})
        self.client.post(self.base + '/conversation-memories/' + memory['id'] + '/review', json={'revision': 1, 'action': 'confirm'})
        self.assertTrue(self.client.get(self.base + '/conversation-book').json()['book']['stale'])
        self.assertEqual(self.client.post(self.base + '/conversation-book/review', json={'revision': book['revision'], 'action': 'review'}).status_code, 409)

    def test_semantic_audit_repair_is_bounded(self):
        self.send('小时候我和母亲赶集。')
        audits = []
        def rejected(system, value):
            if system == chapters.AUDIT_SYSTEM and 'paragraphs' in value:
                audits.append(1)
                return {'issues': ['正文添加了原话没有的年份']}
            return self.book_model(system, value)
        self.assertEqual(self.make_book(rejected)['job']['status'], 'failed')
        self.assertEqual(len(audits), 2)
        self.assertIsNone(self.client.get(self.base + '/conversation-book').json()['book'])

    def test_book_writer_receives_human_corrections(self):
        self.send('我大约1980年进厂，也可能是1981年。')
        human = self.human()
        with store.get() as db:
            memory = repository.save_candidate(db, self.project['id'], {'title': '进厂', 'description': '进厂年份需核对', 'date_text': '1980年', 'evidence': [{'turn_id': human['id'], 'quote': human['text']}]})
        self.client.post(self.base + '/conversation-memories/' + memory['id'] + '/review', json={'revision': 1, 'action': 'dispute', 'note': '年份还不能确定'})
        seen = []
        def model(system, value):
            if isinstance(value, dict) and 'memory_reviews' in value:
                seen.extend(value['memory_reviews'])
            return self.book_model(system, value)
        self.assertEqual(self.make_book(model)['job']['status'], 'ready')
        self.assertTrue(any(m['status'] == 'disputed' and m['note'] == '年份还不能确定' for m in seen))


if __name__ == '__main__':
    unittest.main()
