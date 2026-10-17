"""真实失败路径的离线回归：不放宽引用，不调用云服务。"""
import copy
import json
import unittest
from unittest.mock import patch
import httpx

import test_memoir_agent as fixtures
from agents import model, tools
from agents.errors import AgentFailure
import conversation_engine as engine
import store
import worker


class ModelAdapterTests(unittest.TestCase):
    def request(self, response, status=200, available=None):
        captured = []
        def transport(request):
            captured.append(json.loads(request.content))
            return httpx.Response(status, json=response, request=request)
        real_client = httpx.Client
        def client(**kwargs): return real_client(transport=httpx.MockTransport(transport), **kwargs)
        with patch.object(model.config, 'KIMI_API_KEY', 'synthetic-secret'), patch.object(model.config, 'KIMI_MODEL', 'kimi-k3'), patch.object(model.httpx, 'Client', side_effect=client):
            value = model.complete([{'role': 'user', 'content': '合成问题'}], available or tools.definitions())
        return value, captured

    def test_k3_preserves_complete_message_and_has_reasoning_budget(self):
        message = {'role': 'assistant', 'content': None, 'reasoning_content': '仅供测试的内部内容', 'tool_calls': [fixtures.tool('search_memories', {'query': '村里'})]}
        result, payloads = self.request({'choices': [{'finish_reason': 'tool_calls', 'message': message}]})
        self.assertEqual(result, message)
        self.assertEqual(payloads[0]['max_completion_tokens'], 4096)
        self.assertNotIn('max_tokens', payloads[0])
        self.assertEqual(payloads[0]['reasoning_effort'], 'low')

    def test_final_only_forces_respond(self):
        final = [t for t in tools.definitions() if t['function']['name'] == 'respond']
        _, payloads = self.request({'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '我在听。'}}]}, available=final)
        self.assertEqual(payloads[0]['tool_choice']['function']['name'], 'respond')

    def test_incomplete_and_malformed_have_safe_error_codes(self):
        for response, code in [({'choices': [{'finish_reason': 'length'}]}, 'model_incomplete'), ({'private': 'provider-private'}, 'model_response'), ({'choices': [None]}, 'model_response'), ({'choices': [{'finish_reason': 'content_filter'}]}, 'model_request')]:
            with self.subTest(code=code), self.assertRaises(AgentFailure) as caught: self.request(response)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn('private', str(caught.exception))

    def test_http_errors_do_not_leak_provider_or_auth(self):
        for status, code, retryable in [(401, 'model_auth', False), (400, 'model_request', False), (429, 'model_busy', True), (503, 'model_busy', True)]:
            with self.subTest(status=status), self.assertRaises(AgentFailure) as caught: self.request({'error': 'provider-private synthetic-secret'}, status)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(caught.exception.retryable, retryable)
            self.assertNotIn('synthetic-secret', str(caught.exception))

    def test_memory_schema_contains_evidence_and_date_constraints(self):
        schema = tools.Candidates.model_json_schema()
        self.assertIn('Evidence', schema['$defs'])
        self.assertIn('Candidate', schema['$defs'])
        self.assertIn('evidence.quote', schema['$defs']['Candidate']['properties']['date_text']['description'])
        with self.assertRaises(ValueError): tools.Candidate.model_validate({'title': '进厂', 'description': '合成', 'status': 'confirmed', 'evidence': [{'turn_id': 'source', 'quote': '原话'}]})


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.MemoirAgentTests.setUp
    send = fixtures.MemoirAgentTests.send
    human = fixtures.MemoirAgentTests.human
    execute = fixtures.MemoirAgentTests.execute

    def snapshot(self): return self.client.get(self.url).json()

    def codes(self):
        with store.get() as db: return [r['code'] for r in db.execute('SELECT code FROM memoir_agent_diagnostics ORDER BY id')]

    def test_invalid_date_skips_memory_tool_and_still_replies(self):
        self.send('我和母亲去赶集。'); human = self.human()
        item = {'title': '赶集', 'description': '和母亲赶集', 'date_text': '1980年', 'evidence': [{'turn_id': human['id'], 'quote': human['text']}]}
        calls = self.execute([{'role': 'assistant', 'content': None, 'tool_calls': [fixtures.tool('save_memory_candidates', {'memories': [item]})]}, fixtures.response('您记得集市上最热闹的地方吗？')])
        self.assertEqual(self.snapshot()['turns'][-1]['job']['status'], 'ready')
        self.assertEqual(self.client.get(self.base + '/conversation-archive').json()['memories'], [])
        self.assertEqual([t['function']['name'] for t in calls.call_args_list[1].args[1]], ['respond'])
        self.assertIn('memory_date', self.codes())
        with store.get() as db:
            checkpoint = json.loads(db.execute('SELECT checkpoint FROM memoir_agent_runs').fetchone()[0])
        result = json.loads(checkpoint[-1]['content'])
        self.assertEqual(result['code'], 'memory_date')
        self.assertFalse(result['saved'])

    def test_rewritten_quote_is_rejected_but_reply_is_not_lost(self):
        self.send('母亲带我去过集市。'); human = self.human()
        item = {'title': '赶集', 'description': '和母亲赶集', 'evidence': [{'turn_id': human['id'], 'quote': '母亲经常带我赶集。'}]}
        self.execute([{'role': 'assistant', 'content': None, 'tool_calls': [fixtures.tool('save_memory_candidates', {'memories': [item]})]}, fixtures.response()])
        self.assertEqual(self.snapshot()['turns'][-1]['job']['status'], 'ready')
        self.assertIn('memory_quote', self.codes())
        self.assertEqual(self.client.get(self.base + '/conversation-archive').json()['memories'], [])

    def test_tool_failure_defers_remaining_tools_in_same_batch(self):
        self.send('母亲带我去过集市。'); human = self.human()
        invalid = {'title': '赶集', 'description': '赶集', 'date_text': '1980年', 'evidence': [{'turn_id': human['id'], 'quote': human['text']}]}
        valid = {**invalid, 'date_text': '未提及'}
        self.execute([{'role': 'assistant', 'content': None, 'tool_calls': [fixtures.tool('save_memory_candidates', {'memories': [invalid]}, 'first'), fixtures.tool('save_memory_candidates', {'memories': [valid]}, 'second')]}, fixtures.response()])
        self.assertEqual(self.snapshot()['turns'][-1]['job']['status'], 'ready')
        self.assertEqual(self.client.get(self.base + '/conversation-archive').json()['memories'], [])
        with store.get() as db:
            checkpoint = json.loads(db.execute('SELECT checkpoint FROM memoir_agent_runs').fetchone()[0])
        self.assertEqual([m['tool_call_id'] for m in checkpoint if m['role'] == 'tool'], ['first', 'second'])
        self.assertTrue(all(json.loads(m['content'])['saved'] is False for m in checkpoint if m['role'] == 'tool'))

    def test_k3_reasoning_is_returned_in_memory_only(self):
        self.send('我小时候住在村里。')
        first = {'role': 'assistant', 'content': None, 'reasoning_content': 'synthetic-private-reasoning', 'tool_calls': [fixtures.tool('search_memories', {'query': '村里'})]}
        seen = []
        def complete(messages, schemas):
            seen.append(copy.deepcopy(messages))
            return first if len(seen) == 1 else fixtures.response()
        with patch.object(engine.config, 'KIMI_MODEL', 'kimi-k3'): self.execute(complete)
        self.assertTrue(any(m.get('reasoning_content') == 'synthetic-private-reasoning' for m in seen[1]))
        with store.get() as db:
            stored = db.execute('SELECT checkpoint FROM memoir_agent_runs').fetchone()[0]
            diagnostics = [dict(r) for r in db.execute('SELECT * FROM memoir_agent_diagnostics')]
        self.assertNotIn('synthetic-private-reasoning', stored)
        self.assertNotIn('synthetic-private-reasoning', json.dumps(self.snapshot()))
        self.assertNotIn('synthetic-private-reasoning', json.dumps(diagnostics))

    def test_bad_final_json_repairs_once_then_system_continuation_is_labelled(self):
        self.send('我小时候住在村里。')
        invalid = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'final', 'function': {'name': 'respond', 'arguments': '{unfinished'}}]}
        calls = self.execute([invalid, invalid])
        turn = self.snapshot()['turns'][-1]
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(turn['job']['status'], 'ready')
        self.assertIn('系统接续', turn['job']['notice'])
        self.assertNotIn('{unfinished', turn['text'])
        self.assertIn('reply_fallback', self.codes())

    def test_incomplete_output_is_repaired_without_exposing_partial(self):
        self.send('我小时候住在村里。')
        calls = self.execute([AgentFailure('model_incomplete'), fixtures.response('您小时候的村子是什么样的？')])
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(self.snapshot()['turns'][-1]['job']['status'], 'ready')
        self.assertIn('model_incomplete', self.codes())

    def test_invalid_optional_plan_does_not_destroy_valid_reply(self):
        self.send('我小时候住在村里。')
        self.execute([fixtures.response(plan={'unknown': '不支持的字段'})])
        self.assertEqual(self.snapshot()['turns'][-1]['job']['status'], 'ready')
        self.assertIn('plan_invalid', self.codes())

    def test_mixed_final_and_side_effect_never_executes_the_side_effect(self):
        self.send('我小时候住在村里。')
        value = fixtures.response(); value['tool_calls'].append(fixtures.tool('create_story_draft', {'title': '不该创建'}, 'side-effect'))
        self.execute([value, fixtures.response()])
        self.assertEqual(self.snapshot()['turns'][-1]['job']['status'], 'ready')
        with store.get() as db: self.assertEqual(db.execute("SELECT COUNT(*) FROM tasks WHERE kind='conversation_book'").fetchone()[0], 0)

    def test_timeout_retries_same_task_once_without_duplicating_human_turn(self):
        self.send('母亲带我赶集。')
        self.execute([httpx.ReadTimeout('secret-provider-response')])
        before = self.snapshot()['turns'][-1]['job']
        self.assertEqual(before['status'], 'retry')
        self.assertEqual(before['error_code'], 'model_timeout')
        with store.get() as db: db.execute('UPDATE tasks SET available_at=0 WHERE id=?', (before['id'],))
        self.execute([fixtures.response()])
        value = self.snapshot()
        self.assertEqual(value['turns'][-1]['job']['id'], before['id'])
        self.assertEqual(value['turns'][-1]['job']['attempts'], 2)
        self.assertEqual(value['turns'][-1]['job']['status'], 'ready')
        self.assertEqual(len([t for t in value['turns'] if t['role'] == 'human']), 1)
        self.assertNotIn('secret-provider-response', json.dumps(value))

    def test_second_timeout_exhausts_budget_and_auth_is_not_retried(self):
        self.send('母亲带我赶集。')
        for _ in range(2):
            self.execute([AgentFailure('model_timeout', retryable=True)])
            with store.get() as db: db.execute("UPDATE tasks SET available_at=0 WHERE kind='conversation_reply'")
        task = self.snapshot()['turns'][-1]['job']
        self.assertEqual(task['status'], 'failed'); self.assertEqual(task['attempts'], 2)
        self.client.post(self.url + '/retry', json={})
        self.execute([AgentFailure('model_auth')])
        task = self.snapshot()['turns'][-1]['job']
        self.assertEqual(task['status'], 'failed'); self.assertEqual(task['attempts'], 1)
        self.assertEqual(task['error_code'], 'model_auth')

    def test_checkpoint_resume_does_not_replay_memory_side_effect(self):
        self.send('母亲带我赶集。'); human = self.human()
        item = {'title': '赶集', 'description': '和母亲赶集', 'evidence': [{'turn_id': human['id'], 'quote': human['text']}]}
        first = {'role': 'assistant', 'content': None, 'reasoning_content': 'private-reasoning', 'tool_calls': [fixtures.tool('save_memory_candidates', {'memories': [item]})]}
        with patch.object(engine.config, 'KIMI_MODEL', 'kimi-k3'):
            self.execute([first, worker.WorkerStopping('synthetic stop')])
            seen = []
            def resume(messages, schemas): seen.extend(copy.deepcopy(messages)); return fixtures.response()
            self.execute(resume)
        self.assertTrue(any('completed_tool_results' in (m.get('content') or '') for m in seen))
        self.assertFalse(any(m['role'] == 'tool' for m in seen))
        self.assertEqual(len(self.client.get(self.base + '/conversation-archive').json()['memories']), 1)
        self.assertEqual(self.snapshot()['turns'][-1]['job']['status'], 'ready')

    def test_diagnostics_are_removed_on_project_delete(self):
        self.send('我小时候住在村里。')
        self.execute([AgentFailure('model_auth')])
        self.assertIn('model_auth', self.codes())
        self.client.delete(self.base)
        with store.get() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM memoir_agent_diagnostics').fetchone()[0], 0)


if __name__ == '__main__': unittest.main()
