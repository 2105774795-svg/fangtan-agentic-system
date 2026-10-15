import hashlib
import json
import sys
import tempfile
import threading
import unittest
import wave
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import kimi
import job_queue
import store
import worker


class FieldWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for item in (patch.object(store, 'DB_PATH', root / 'test.db'), patch.object(store, 'DATA_DIR', root),
                     patch.object(app.config, 'DATA_DIR', root), patch.object(app.config, 'UPLOAD_DIR', root / 'uploads'),
                     patch.object(app.config, 'AUTH_ENABLED', False), patch('app._schedule_followup')):
            item.start(); self.addCleanup(item.stop)
        store.init()
        self.client = self.enterContext(TestClient(app.app))
        self.location = {'city':'重庆市', 'market':'双福国际农贸城'}
        self.params = {'subject':'水果档口经营变化', 'interviewee_type':'商户', 'industry':'食品饮料', 'location':self.location}

    def test_outline_task_is_persistent_deduplicated_and_survives_client_reconnect(self):
        with patch('app.threading.Thread') as thread:
            first = self.client.post('/api/outlines/jobs', json=self.params)
            second = self.client.post('/api/outlines/jobs', json=self.params)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json()['job_id'], second.json()['job_id'])
        self.assertEqual(thread.call_count, 0)
        job_id = first.json()['job_id']
        self.assertEqual(len(job_queue.find(kind='outline', resource_id=job_id)), 1)
        self.assertEqual(job_queue.get(job_id)['status'], 'queued')
        questions = ['这个市场客源有什么变化？'] * 6
        brief = {'background':'双福国际农贸城的具体业态和经营情况尚待实地核实。',
                 'background_note':'采访前参考，未经实时检索核实。', 'questions':questions}
        research = {**brief, 'status':'unavailable', 'sources':[], 'retrieved_at':''}
        with patch('app.kimi.gen_outline_brief', return_value=brief) as generate, patch('place_research.research', return_value=research):
            self.assertTrue(worker.run_once())
        self.assertIn('双福国际农贸城', generate.call_args.args[-1])
        self.assertEqual(first.json()['settings']['location'], self.location)
        with TestClient(app.app) as reconnected:
            result = reconnected.get('/api/outlines/jobs/' + job_id).json()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['questions'], questions)
        self.assertEqual(result['background'], brief['background'])
        self.assertEqual(result['background_note'], brief['background_note'])
        self.assertEqual(self.client.post('/api/outlines/jobs', json=self.params).json()['job_id'], job_id)

    def test_specific_location_is_required_and_interrupted_jobs_can_retry(self):
        self.assertEqual(self.client.post('/api/outlines/jobs', json={**self.params,'location':{'city':'重庆'}}).status_code, 400)
        self.assertEqual(self.client.post('/api/outlines/jobs', json={**self.params,'location':{'market':'双福国际农贸城'}}).status_code, 400)
        too_fine = {'city':'重庆市', 'district':'江津区', 'town':'双福街道', 'address':'某路1号'}
        self.assertEqual(self.client.post('/api/outlines/jobs', json={**self.params,'location':too_fine}).status_code, 400)
        with patch('app.threading.Thread'):
            job_id = self.client.post('/api/outlines/jobs', json=self.params).json()['job_id']
        # API 重连不能把仍在队列中的任务标成失败；终态失败才允许重新提交。
        store.init()
        self.assertEqual(self.client.get('/api/outlines/jobs/' + job_id).json()['status'], 'generating')
        claimed = job_queue.claim('test-worker')
        job_queue.fail(job_id, claimed['lease_owner'], '测试失败', retryable=False)
        self.assertEqual(self.client.get('/api/outlines/jobs/' + job_id).json()['status'], 'failed')
        with patch('app.threading.Thread'):
            new_id = self.client.post('/api/outlines/jobs', json=self.params).json()['job_id']
        self.assertNotEqual(job_id, new_id)

    def test_new_location_excludes_removed_fields_but_history_remains_readable(self):
        legacy = {**self.location, 'district':'江津区', 'town':'双福街道', 'address':'旧记录地址'}
        sid = self.client.post('/api/sessions/', json={**self.params,'location':legacy}).json()['session_id']
        self.assertEqual(json.loads(store.get_session_summary(sid)['location']), self.location)
        store.create_session('legacy-location', '旧访谈', '现场录音', '商户', [], location=legacy)
        store.init()
        saved = store.get_session_summary('legacy-location')['location']
        self.assertEqual(json.loads(saved), legacy)
        self.assertIn('江津区', app._location_text(saved))
        self.assertIn('双福街道', app._location_text(saved))
        self.assertIn('旧记录地址', app._location_text(saved))

    def test_new_brief_cache_does_not_reuse_question_only_result(self):
        params = app._outline_params(self.params, True)
        old_fingerprint = hashlib.sha256(json.dumps({'settings':params, 'model':app.config.KIMI_OUTLINE_MODEL,
            'prompt':kimi.OUTLINE_SYSTEM}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        store.create_outline_job('question-only', 0, old_fingerprint, params)
        store.finish_outline_job('question-only', ['旧问题？'] * 6)
        with patch('app.threading.Thread'):
            result = self.client.post('/api/outlines/jobs', json=self.params)
        self.assertEqual(result.status_code, 202)
        self.assertNotEqual(result.json()['job_id'], 'question-only')
        self.assertEqual(result.json()['background'], '')

    def test_sync_endpoint_also_returns_background_without_losing_questions(self):
        brief = {'background':'地点的经营特点待核实。', 'background_note':'仅供采访前参考。',
                 'questions':[f'问题{i}？' for i in range(10)]}
        research = {**brief, 'status':'unavailable', 'sources':[], 'retrieved_at':''}
        with patch('app.kimi.gen_outline_brief', return_value=brief) as generate, patch('place_research.research', return_value=research):
            result = self.client.post('/api/outlines/generate', json=self.params)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json(), {**brief,'count':10,'sources':[],'claims':[],
                                         'research_status':'unavailable','retrieved_at':''})
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(self.client.post('/api/outlines/generate', json={**self.params,'location':{}}).status_code, 400)

    def test_selected_outline_replaces_generic_coverage_and_is_recovered(self):
        questions = ['本市场最近一个月客流变化多少？', '这个档口租金一个月多少？']
        sid = self.client.post('/api/sessions/', json={**self.params,'outline':questions}).json()['session_id']
        self.assertEqual(json.loads(store.get_session_summary(sid)['location']), self.location)
        initial = self.client.get(f'/api/sessions/{sid}/state').json()['coverage']
        self.assertEqual(initial['mode'], 'outline')
        self.assertEqual([dim['item'] for dim in initial['dims']], questions)
        self.assertEqual(self.client.post(f'/api/sessions/{sid}/outline_asked', json={'item':questions[0]}).status_code, 200)
        marked = self.client.get(f'/api/sessions/{sid}/state').json()['coverage']
        self.assertEqual(marked['ratio'], 0)
        self.assertEqual(marked['engine'], 'ai')
        self.assertTrue(marked['dims'][0]['manual_asked'])
        self.assertEqual(self.client.post(f'/api/sessions/{sid}/outline_asked', json={'item':'未勾选的问题'}).status_code, 400)

    def wave(self):
        body = BytesIO()
        with wave.open(body, 'wb') as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
            output.writeframes(b'\0\0' * 32000)
        return body.getvalue()

    def test_audio_retry_is_idempotent_and_timing_uses_actual_recorded_duration(self):
        sid = self.client.post('/api/sessions/', json=self.params).json()['session_id']
        audio = self.wave()
        with patch('app.asr_dashscope.transcribe', return_value='现场录音测试') as transcribe:
            first = self.client.post(f'/api/sessions/{sid}/audio', files={'audio':('chunk.wav',audio,'audio/wav')}, data={'chunk_id':'fixed-chunk-one'})
            retry = self.client.post(f'/api/sessions/{sid}/audio', files={'audio':('chunk.wav',audio,'audio/wav')}, data={'chunk_id':'fixed-chunk-one'})
            next_part = self.client.post(f'/api/sessions/{sid}/audio', files={'audio':('chunk.wav',audio,'audio/wav')}, data={'chunk_id':'fixed-chunk-two'})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(retry.json()['seq'], first.json()['seq'])
        self.assertEqual(next_part.status_code, 200)
        self.assertEqual(transcribe.call_count, 2)
        rows = store.list_export_utterances(sid)
        self.assertEqual([(r['start_ms'],r['end_ms']) for r in rows], [(0,2000),(2000,4000)])
        conflict = self.client.post(f'/api/sessions/{sid}/audio', files={'audio':('chunk.wav',audio+b'other','audio/wav')}, data={'chunk_id':'fixed-chunk-one'})
        self.assertEqual(conflict.status_code, 409)

    def test_failed_asr_can_retry_same_saved_chunk_without_duplicating_audio(self):
        sid = self.client.post('/api/sessions/', json=self.params).json()['session_id']
        audio = self.wave()
        with patch('app.asr_dashscope.transcribe', side_effect=RuntimeError('临时失败')), patch.object(app.logger,'exception'):
            failed = self.client.post(f'/api/sessions/{sid}/audio', files={'audio':('chunk.wav',audio,'audio/wav')}, data={'chunk_id':'retry-same-chunk'})
        self.assertEqual(failed.status_code, 502)
        with patch('app.asr_dashscope.transcribe', return_value='录音恢复成功'):
            result = self.client.post(f'/api/sessions/{sid}/audio', files={'audio':('chunk.wav',audio,'audio/wav')}, data={'chunk_id':'retry-same-chunk'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(list((app.config.DATA_DIR/'recordings'/sid).glob('*.wav'))), 1)
        self.assertEqual(len(store.list_export_utterances(sid)), 1)


class FastPromptTests(unittest.TestCase):
    def test_k3_outline_uses_low_effort_and_has_enough_output_budget(self):
        with patch.object(kimi,'KIMI_API_KEY','test-only'), patch.object(kimi,'KIMI_OUTLINE_MODEL','kimi-k3'), patch('kimi.httpx.Client') as client:
            reply = client.return_value.__enter__.return_value.post.return_value
            reply.json.return_value = {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
                'background':'地点的具体业态和经营特点尚待实地核实。', 'background_note':'请现场确认当地经营变化。',
                'questions':[f'这个市场的具体问题{i}？' for i in range(10)]})}}]}
            result = kimi.gen_outline('水果批发','商户','食品饮料','价格','重庆市 · 双福国际农贸城')
            payload = client.return_value.__enter__.return_value.post.call_args.kwargs['json']
        self.assertEqual(len(result),10)
        self.assertEqual(payload['reasoning_effort'],'low')
        self.assertGreaterEqual(payload['max_tokens'],8192)
        self.assertIn('双福国际农贸城', payload['messages'][1]['content'])

    def test_followup_task_does_not_block_transcript_processing(self):
        sid = 'followup-nonblocking-test'
        app.FOLLOWUP_LOCKS.pop(sid,None);app.FOLLOWUP_STARTED.pop(sid,None)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def slow(*args):
            entered.set();release.wait(3);finished.set();return None
        with patch('app.store.recent_suggestion_time', return_value=0), patch('app.store.list_utterances', return_value=[]), \
             patch('app._session_field', return_value=''), patch('app._outline_remaining', return_value=[]), \
             patch('app.kimi.gen_followup', side_effect=slow), patch('app.store.get_session_summary', return_value={'status':'live'}):
            app._schedule_followup(sid, [])
            try:
                self.assertTrue(entered.wait(1));self.assertFalse(finished.is_set())
            finally:
                release.set();app.FOLLOWUP_LOCKS[sid].acquire(timeout=3);app.FOLLOWUP_LOCKS[sid].release()


if __name__ == '__main__': unittest.main()
