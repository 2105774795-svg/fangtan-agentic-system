"""Explicit paid model smoke test with synthetic text and an isolated D: database."""
import json
import os
import sys
import uuid
from pathlib import Path
root = Path(__file__).resolve().parents[1]
os.environ['FANGTAN_DATA_DIR'] = str(Path('D:/codex-deps/tmp') / ('conversation-live-' + uuid.uuid4().hex))
os.environ['FANGTAN_AUTH_ENABLED'] = 'false'
os.environ['TEAMFLOW_USAGE_URL'] = ''
sys.path.insert(0, str(root / 'backend'))
from fastapi import FastAPI
from fastapi.testclient import TestClient
import store
import research_platform as platform
import conversation_engine as engine
import worker
store.init(); platform.init(); engine.init()
app = FastAPI(); app.include_router(platform.router); app.include_router(engine.router)
with TestClient(app) as client:
 def post(url, body):
  response = client.post(url, json=body)
  assert response.status_code < 300, (response.status_code, response.text)
  return response.json()
 project = post('/api/platform/projects', {'prompt': '口述史合成材料联调', 'scenario': 'memoir', 'mode': 'ai', 'request_id': uuid.uuid4().hex})
 base = '/api/platform/projects/' + project['id']
 session = post(base + '/conversations', {'request_id': uuid.uuid4().hex})
 url = base + '/conversations/' + session['id']
 post(url + '/turns', {'client_id': uuid.uuid4().hex, 'text': '以下是合成测试材料：小时候母亲常带我去赶集，我最喜欢集市上卖的糖葫芦。具体是哪一年记不清了。'})
 worker.run_once(handlers=engine.HANDLERS)
 session = client.get(url).json()
 assert session['turns'][-1]['job']['status'] == 'ready', session['turns'][-1]['job']
 post(url + '/finish', {})
 worker.run_once(handlers=engine.HANDLERS)
 session = client.get(url).json()
 assert session['archive_job']['status'] == 'ready', session['archive_job']
 assert session['archive']['paragraphs']
 result = {'success': True, 'model': engine.config.KIMI_MODEL, 'reply_saved': True, 'paragraphs': len(session['archive']['paragraphs']), 'events': len(session['archive']['events']), 'references_validated': True, 'data_dir': os.environ['FANGTAN_DATA_DIR'], 'audio': 'not tested: synthetic text only'}
 output = root / 'data/qa/conversation/live-result.json'; output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
 print(json.dumps(result, ensure_ascii=True))
