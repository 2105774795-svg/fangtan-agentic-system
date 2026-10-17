"""Reusable dialogue integration tests; models/ASR isolated from cloud services."""
import io
import json
import sys
import tempfile
import unittest
import uuid
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import conversation_engine as engine
import research_platform as platform
import store
import worker
import job_queue


class ConversationTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for obj, name, value in ((store, 'DATA_DIR', root), (store, 'DB_PATH', root / 'test.db'),
                                  (engine.config, 'DATA_DIR', root), (engine.config, 'KIMI_API_KEY', 'test-key')):
            self.enterContext(patch.object(obj, name, value))
        store.init(); platform.init(); engine.init()
        app = FastAPI(); app.include_router(platform.router); app.include_router(engine.router)
        self.client = self.enterContext(TestClient(app))
        self.project = self.client.post('/api/platform/projects', json={'prompt': '我想讲讲童年往事', 'mode': 'ai', 'request_id': 'dialogue-project'}).json()
        self.base = '/api/platform/projects/' + self.project['id']
        self.session = self.client.post(self.base + '/conversations', json={'request_id': 'dialogue-session'}).json()
        self.url = self.base + '/conversations/' + self.session['id']

    def send(self, text, key='human-turn-001'):
        return self.client.post(self.url + '/turns', json={'client_id': key, 'text': text})

    def run_reply(self, text='您提到母亲经常带您赶集，那时最盼着吃什么？'):
        def stream(messages, callback, **kwargs):
            callback(text[:5]); callback(text[5:])
            return text
        with patch('conversation_engine.platform_ai.stream', side_effect=stream), patch('agents.model.complete', return_value={'role': 'assistant', 'content': text}):
            worker.run_once(handlers={'conversation_reply': engine.reply_job}, worker_id='conversation-fixture')

    def archive_model(self, system, block):
        evidence = [{'turn_id': block[0]['turn_id'], 'quote': block[0]['text']}]
        if 'events' in system:
            return {'events': [{'title': '童年赶集', 'description': '本人讲到和母亲赶集的经历。', 'date_text': '未提及', 'status': 'candidate', 'evidence': evidence}]}
        return {'paragraphs': [{'text': '小时候，母亲经常带我去赶集。', 'evidence': evidence}]}

    def test_memoir_routes_directly_without_intake_or_authentication(self):
        self.assertEqual(self.project['scenario'], 'memoir')
        self.assertEqual(self.project['experience'], 'conversation')
        self.assertIsNone(self.project.get('job'))
        self.assertEqual(len(self.session['turns']), 1)
        self.assertIn('称呼', self.session['turns'][0]['text'])
        resumed = self.client.post(self.base + '/conversations', json={'request_id': 'new-page-request'}).json()
        self.assertEqual(resumed['id'], self.session['id'])

    def test_turn_idempotency_streaming_reply_and_name_memory(self):
        self.assertEqual(self.send('叫我老李就行').status_code, 200)
        self.assertEqual(self.send('叫我老李就行').status_code, 200)
        self.assertEqual(self.send('不同的原话').status_code, 409)
        self.run_reply('老李，您小时候最常吃的一道饭是什么？')
        value = self.client.get(self.url).json()
        self.assertEqual(value['preferred_name'], '老李')
        self.assertEqual(len(value['turns']), 3)
        self.assertEqual(value['turns'][-1]['job']['status'], 'ready')
        self.assertEqual(value['turns'][-1]['output'], '')

    def test_failed_reply_keeps_human_turn_and_retry_has_no_duplicates(self):
        self.send('小时候母亲带我赶集。')
        with patch('agents.model.complete', side_effect=RuntimeError('private cloud message')):
            worker.run_once(handlers=engine.HANDLERS)
        value = self.client.get(self.url).json()
        self.assertNotIn('private cloud', json.dumps(value))
        self.assertEqual(value['turns'][-1]['job']['status'], 'failed')
        self.assertEqual(self.send('另一段话', 'human-turn-002').status_code, 409)
        self.client.post(self.url + '/retry', json={})
        self.run_reply()
        value = self.client.get(self.url).json()
        self.assertEqual(len(value['turns']), 3)
        self.assertEqual(value['turns'][1]['text'], '小时候母亲带我赶集。')

    def test_finish_archives_only_human_sources_and_can_start_next_session(self):
        self.send('小时候母亲经常带我去赶集。'); self.run_reply()
        result = self.client.post(self.url + '/finish', json={})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['status'], 'done')
        with patch('memoir.handlers._chat', side_effect=self.archive_model):
            worker.run_once(handlers=engine.HANDLERS)
        archive = self.client.get(self.base + '/conversation-archive').json()['sessions'][0]
        self.assertEqual(archive['archive_job']['status'], 'ready')
        self.assertEqual(archive['archive']['status'], 'draft')
        self.assertEqual(archive['archive']['events'][0]['date_text'], '未提及')
        self.assertEqual(archive['archive']['paragraphs'][0]['evidence'][0]['turn_id'], archive['turns'][1]['id'])
        self.assertEqual(self.send('结束后新话', 'new-finished-turn').status_code, 409)

    def test_archive_review_preserves_raw_turns_and_versions(self):
        self.send('小时候母亲经常带我去赶集。'); self.run_reply()
        self.client.post(self.url + '/finish', json={})
        with patch('memoir.handlers._chat', side_effect=self.archive_model):
            worker.run_once(handlers=engine.HANDLERS)
        edited = self.client.post(self.url + '/archive-review', json={'revision': 1, 'action': 'edit', 'paragraph_index': 0, 'text': '这是本人校对后的故事。'})
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.json()['archive']['revision'], 2)
        self.assertTrue(edited.json()['archive']['paragraphs'][0]['edited'])
        self.assertEqual(edited.json()['turns'][1]['text'], '小时候母亲经常带我去赶集。')
        self.assertEqual(self.client.post(self.url + '/archive-review', json={'revision': 1, 'action': 'review'}).status_code, 409)
        reviewed = self.client.post(self.url + '/archive-review', json={'revision': 2, 'action': 'review'}).json()
        self.assertEqual(reviewed['archive']['status'], 'reviewed')
        with store.get() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM conversation_archive_versions WHERE session_id=?', (self.session['id'],)).fetchone()[0], 2)

    def test_voice_control_and_quoted_story_are_distinct(self):
        self.send('母亲当时说，今天先聊到这里。')
        self.run_reply()
        self.assertEqual(self.client.get(self.url).json()['status'], 'live')
        self.assertEqual(self.send('暂停一下', 'pause-turn-001').json()['turns'][-1]['control'], 'pause')
        value = self.send('今天先聊到这里', 'finish-turn-001').json()
        self.assertEqual(value['status'], 'done')
        self.assertEqual(self.client.post(self.url + '/finish', json={}).status_code, 200)
        next_session = self.client.post(self.base + '/conversations', json={'request_id': 'second-session-001', 'new_session': True}).json()
        self.assertNotEqual(next_session['id'], self.session['id'])

    def test_foreign_session_and_audio_cannot_cross_project(self):
        other = self.client.post('/api/platform/projects', json={'prompt': '另一个人', 'mode': 'demo', 'request_id': 'different-project'}).json()
        foreign = '/api/platform/projects/' + other['id'] + '/conversations/' + self.session['id']
        self.assertEqual(self.client.get(foreign).status_code, 404)
        self.assertEqual(self.client.post(foreign + '/finish', json={}).status_code, 404)

    def test_policy_can_be_reused_for_interview(self):
        other = self.client.post('/api/platform/projects', json={'prompt': '面试', 'mode': 'demo', 'request_id': 'interview-project'}).json()
        result = self.client.post('/api/platform/projects/' + other['id'] + '/conversations', json={'kind': 'interview', 'request_id': 'interview-session'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['kind'], 'interview')
        self.assertIn('面试', result.json()['turns'][0]['text'])

    def test_hallucinated_archive_evidence_is_not_saved(self):
        self.send('小时候母亲带我赶集。'); self.run_reply()
        self.client.post(self.url + '/finish', json={})
        with patch('memoir.handlers._chat', return_value={'paragraphs': [{'text': '编造', 'evidence': [{'turn_id': 'not-a-human-turn', 'quote': '不存在的原话'}]}]}):
            worker.run_once(handlers=engine.HANDLERS)
        value = self.client.get(self.url).json()
        self.assertIsNone(value['archive'])
        self.assertEqual(value['archive_job']['status'], 'failed')

    def test_delete_project_cancels_reply_and_hides_dialogue(self):
        self.send('小时候母亲带我赶集。')
        task_id = self.client.get(self.url).json()['turns'][-1]['job']['id']
        self.client.delete(self.base)
        self.assertEqual(job_queue.get(task_id)['status'], 'cancelled')
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_silent_audio_and_failure_retry_preserve_original_once(self):
        raw = io.BytesIO()
        with wave.open(raw, 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000); audio.writeframes(bytes(32000))
        chunk = uuid.uuid4().hex
        def upload():
            return self.client.post(self.url + '/audio', data={'chunk_id': chunk, 'turn_key': 'audio-turn-001', 'part_index': 0}, files={'audio': ('audio.wav', raw.getvalue(), 'audio/wav')})
        with patch('asr_dashscope.transcribe', side_effect=RuntimeError('cloud failed')):
            self.assertEqual(upload().status_code, 502)
        self.assertEqual(self.client.get(self.url + '/audio/' + chunk).status_code, 200)
        self.assertEqual(self.client.get(self.url + '/audio/' + chunk + '?original=true').content, raw.getvalue())
        self.assertEqual(self.client.post(self.url + '/finish', json={}).status_code, 409)
        with patch('asr_dashscope.transcribe', return_value=''):
            self.assertTrue(upload().json()['no_speech'])
        with patch('asr_dashscope.transcribe', side_effect=AssertionError('must be idempotent')):
            self.assertEqual(upload().status_code, 200)
        response = self.client.post(self.url + '/turns', json={'client_id': 'audio-turn-001', 'audio_ids': [chunk]})
        self.assertTrue(response.json()['no_speech'])
        result = self.client.post(self.url + '/finish', json={}).json()
        self.assertEqual(result['status'], 'done')
        self.assertIsNone(result['archive_job'])
