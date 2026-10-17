"""Additive schema and atomic state transitions for personal interview practice."""
import hashlib
import json
import time
import uuid
from fastapi import HTTPException
import config
import store
import job_queue

TEMPLATES = {
    'job': {'name': '求职面试', 'criteria': ['回答切题', '个人贡献', '事实与结果', '思考与取舍', '表达结构']},
    'thesis': {'name': '论文答辩', 'criteria': ['研究问题', '方法与依据', '结果与创新', '局限与解释', '表达结构']},
}
def effective_config(cfg):
    return {'followup_mode':'adaptive','include_candidate_questions':True,'company_name':'','role_name':'',
            'company_background':'','job_requirements':'','background_source':'','interview_stage':'',**cfg}

ACTIVE = {'queued', 'running', 'retry'}

def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)

def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()

def init():
    with store.get() as db:
        job_queue.init(db)
        statements = '''
        CREATE TABLE IF NOT EXISTS interview_preparations(
            id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, title TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1, config TEXT NOT NULL, plan TEXT,
            plan_stale INTEGER NOT NULL DEFAULT 0, job_id TEXT, deleted_at REAL DEFAULT 0,
            created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS interview_materials(
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,
            kind TEXT NOT NULL, content TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS interview_practices(
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL,
            snapshot TEXT NOT NULL, job_id TEXT, review TEXT, review_stale INTEGER DEFAULT 0,
            source_practice_id TEXT, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS interview_turns(
            id TEXT PRIMARY KEY, practice_id TEXT NOT NULL, position INTEGER NOT NULL,
            question TEXT NOT NULL, target TEXT NOT NULL, parent_id TEXT,
            status TEXT NOT NULL DEFAULT 'waiting', feedback TEXT,
            UNIQUE(practice_id,position));
        CREATE TABLE IF NOT EXISTS interview_answers(
            id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, session_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'recording', transcript TEXT NOT NULL DEFAULT '',
            version INTEGER DEFAULT 1, segments TEXT NOT NULL DEFAULT '[]',
            original_transcript TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS interview_reviews(
            id TEXT PRIMARY KEY, practice_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            content TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS interview_requests(
            project_id TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            PRIMARY KEY(project_id,request_id));
        CREATE INDEX IF NOT EXISTS idx_interview_owner ON interview_preparations(owner_id,updated_at);
        CREATE INDEX IF NOT EXISTS idx_interview_practices ON interview_practices(project_id,created_at);
        '''
        for statement in statements.split(';'):
            if statement.strip(): db.execute(statement)
        db.execute('''CREATE TABLE IF NOT EXISTS interview_question_bank(
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, question TEXT NOT NULL,
            previous_answer TEXT NOT NULL DEFAULT '', context TEXT NOT NULL DEFAULT '',
            analysis TEXT, analysis_stale INTEGER DEFAULT 0, job_id TEXT, created_at REAL NOT NULL)''')
        turn_columns = {r['name'] for r in db.execute('PRAGMA table_info(interview_turns)')}
        if 'feedback_notice' not in turn_columns:
            db.execute("ALTER TABLE interview_turns ADD COLUMN feedback_notice TEXT NOT NULL DEFAULT ''")
        if 'turn_kind' not in turn_columns:
            db.execute("ALTER TABLE interview_turns ADD COLUMN turn_kind TEXT NOT NULL DEFAULT 'question'")
        if 'decision_reason' not in turn_columns:
            db.execute("ALTER TABLE interview_turns ADD COLUMN decision_reason TEXT NOT NULL DEFAULT ''")
        practice_columns = {r['name'] for r in db.execute('PRAGMA table_info(interview_practices)')}
        if 'candidate_review' not in practice_columns:
            db.execute('ALTER TABLE interview_practices ADD COLUMN candidate_review TEXT')
        material_columns = {r['name'] for r in db.execute('PRAGMA table_info(interview_materials)')}
        if 'history' not in material_columns:
            db.execute("ALTER TABLE interview_materials ADD COLUMN history TEXT NOT NULL DEFAULT '[]'")
        columns = {r['name'] for r in db.execute('PRAGMA table_info(sessions)')}
        if 'purpose' not in columns:
            db.execute("ALTER TABLE sessions ADD COLUMN purpose TEXT NOT NULL DEFAULT 'research_capture'")
        from . import workspace
        workspace.init(db)

def owned(db, pid, owner):
    row = db.execute('SELECT * FROM interview_preparations WHERE id=? AND owner_id=? AND deleted_at=0', (pid, owner)).fetchone()
    if not row: raise HTTPException(404, '面试准备项目不存在')
    if row['workspace_project_id']:
        linked = db.execute('''SELECT 1 FROM research_workspace_runs w JOIN projects p ON p.id=w.project_id
            WHERE w.project_id=? AND w.deleted_at=0 AND p.owner_id=?''', (row['workspace_project_id'], owner)).fetchone()
        if not linked: raise HTTPException(404, '项目不存在或已删除')
    return dict(row)

def snapshot(db, pid, owner):
    project = owned(db, pid, owner)
    project['plan_stale'] = bool(project['plan_stale'])
    project['config'] = effective_config(json.loads(project['config']))
    project['plan'] = json.loads(project['plan']) if project['plan'] else None
    project['materials'] = [dict(r) for r in db.execute('SELECT * FROM interview_materials WHERE project_id=? ORDER BY created_at,id', (pid,))]
    project['question_bank'] = []
    for row in db.execute('SELECT * FROM interview_question_bank WHERE project_id=? ORDER BY created_at,id',(pid,)):
        item = dict(row)
        item['analysis'] = json.loads(item['analysis']) if item['analysis'] else None
        item['analysis_stale'] = bool(item['analysis_stale'])
        item['job'] = public_job(db,item['job_id'])
        project['question_bank'].append(item)
    project['practices'] = []
    for row in db.execute('SELECT * FROM interview_practices WHERE project_id=? ORDER BY created_at DESC,id', (pid,)):
        practice = dict(row)
        frozen = json.loads(practice.pop('snapshot'))
        practice['config'] = effective_config(frozen['config'])
        practice['candidate_review'] = json.loads(practice['candidate_review']) if practice['candidate_review'] else None
        practice['material_versions'] = [{'id': m['id'], 'version': m['version'], 'title': m['title']} for m in frozen['materials']]
        practice['review_stale'] = bool(practice['review_stale'])
        practice['review'] = json.loads(practice['review']) if practice['review'] else None
        practice['turns'] = []
        for turn_row in db.execute('SELECT * FROM interview_turns WHERE practice_id=? ORDER BY position', (practice['id'],)):
            turn = dict(turn_row)
            turn['feedback'] = json.loads(turn['feedback']) if turn['feedback'] else None
            turn['answers'] = []
            for answer_row in db.execute('SELECT * FROM interview_answers WHERE turn_id=? ORDER BY created_at,id', (turn['id'],)):
                answer = dict(answer_row); answer['segments'] = json.loads(answer['segments'])
                answer['chunk_ids'] = [r['chunk_id'] for r in db.execute('SELECT chunk_id FROM audio_receipts WHERE session_id=? ORDER BY seq', (answer['session_id'],))]
                if answer['status'] == 'recording':
                    answer['segments'] = [dict(r) for r in db.execute('SELECT id,seq,start_ms,end_ms,text FROM utterances WHERE session_id=? ORDER BY seq', (answer['session_id'],))]
                turn['answers'].append(answer)
            practice['turns'].append(turn)
        practice['job'] = public_job(db, practice['job_id'])
        # Simulation feedback is private until the practice has ended.
        if frozen['config']['practice_mode'] == 'simulation' and practice['status'] != 'done':
            practice['candidate_review'] = None
            for turn in practice['turns']:
                turn['feedback'] = None
                turn['feedback_notice'] = ''
                turn['decision_reason'] = ''
                turn['target'] = ''
        project['practices'].append(practice)
    project['job'] = public_job(db, project['job_id'])
    return project

def public_job(db, jid):
    row = db.execute('SELECT id,kind,status,stage,progress,error FROM tasks WHERE id=?', (jid or '',)).fetchone()
    if not row: return None
    result = dict(row)
    raw_error = result.pop('error', '')
    if result['status'] == 'failed':
        evidence_errors = {'feedback_quote_not_in_answer','invalid_audio_evidence','quote_not_in_audio_segments','wrong_turn_evidence'}
        result['error'] = ('AI 反馈未能准确对应回答原文，已保存的回答和录音不受影响；可以重试生成。' if raw_error in evidence_errors else '本次生成未完成，回答和录音已保存；请重试，持续失败时再检查模型连接。')
    return result

def busy(db, jid):
    job = public_job(db, jid)
    return job and job['status'] in ACTIVE

def touch(db, pid):
    db.execute('UPDATE interview_preparations SET revision=revision+1,updated_at=? WHERE id=?', (time.time(), pid))
    db.execute('UPDATE projects SET updated_at=? WHERE id=(SELECT workspace_project_id FROM interview_preparations WHERE id=?)', (time.time(), pid))

def enqueue(db, project, kind, practice_id=None, question_id=None):
    if not config.KIMI_API_KEY and kind != 'interview_next_turn': raise HTTPException(503, '请先配置 KIMI_API_KEY，再生成准备方案或模拟面试')
    jid, now = uuid.uuid4().hex, time.time()
    payload = {'project_id': project['id'], 'owner_id': project['owner_id'], 'practice_id': practice_id, 'question_id':question_id}
    db.execute('''INSERT INTO tasks(id,kind,owner_id,resource_id,payload,max_attempts,available_at,created_at,updated_at)
                  VALUES(?,?,?,?,?,1,?,?,?)''', (jid, kind, project['owner_id'], project['id'], dump(payload), now, now, now))
    if question_id: db.execute('UPDATE interview_question_bank SET job_id=? WHERE id=?',(jid,question_id))
    if practice_id: db.execute('UPDATE interview_practices SET job_id=? WHERE id=?', (jid, practice_id))
    elif not question_id: db.execute('UPDATE interview_preparations SET job_id=? WHERE id=?', (jid, project['id']))
    return jid

def practice_owned(db, project, practice_id):
    row = db.execute('SELECT * FROM interview_practices WHERE id=? AND project_id=?', (practice_id, project['id'])).fetchone()
    if not row: raise HTTPException(404, '练习不存在')
    return dict(row)

def create(owner, title, request_id, cfg):
    pid = 'ip_' + uuid.uuid5(uuid.NAMESPACE_URL, f'interview:{owner}:{request_id}').hex
    now = time.time()
    with store.get() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM interview_preparations WHERE id=?', (pid,)).fetchone()
        if row and row['deleted_at']: raise HTTPException(410, '项目已删除，请重新创建')
        if row and (row['title'] != title or row['config'] != dump(cfg)):
            raise HTTPException(409, '请求编号已用于其他项目')
        db.execute('INSERT OR IGNORE INTO interview_preparations(id,owner_id,title,config,created_at,updated_at) VALUES(?,?,?,?,?,?)', (pid, owner, title, dump(cfg), now, now))
        from . import workspace
        workspace.attach(db, dict(db.execute('SELECT * FROM interview_preparations WHERE id=?', (pid,)).fetchone()))
        return snapshot(db, pid, owner)

def command(pid, owner, body):
    with store.get() as db:
        db.execute('BEGIN IMMEDIATE')
        project = owned(db, pid, owner)
        request_hash = digest({k: v for k,v in body.items() if k not in {'revision', 'request_id'}})
        old = db.execute('SELECT fingerprint FROM interview_requests WHERE project_id=? AND request_id=?', (pid, body['request_id'])).fetchone()
        if old:
            if old['fingerprint'] != request_hash: raise HTTPException(409, '请求编号已用于其他操作')
            return snapshot(db, pid, owner)
        if project['revision'] != body['revision']: raise HTTPException(409, '项目已更新，请刷新后重试')
        action = body['action']; cfg = effective_config(json.loads(project['config']))
        if action == 'add_question':
            question = body.get('question','').strip()
            if not question: raise HTTPException(422,'请填写真实遇到的面试问题')
            if db.execute('SELECT COUNT(*) FROM interview_question_bank WHERE project_id=?',(pid,)).fetchone()[0]>=30:
                raise HTTPException(422,'每个项目最多保存30道历史问题')
            db.execute('INSERT INTO interview_question_bank(id,project_id,question,previous_answer,context,created_at) VALUES(?,?,?,?,?,?)',
                       (uuid.uuid4().hex,pid,question,body.get('previous_answer','').strip(),body.get('question_context','').strip(),time.time()))
            db.execute('UPDATE interview_preparations SET plan_stale=1 WHERE id=?',(pid,))
            cancel_job(db,project['job_id'])
        elif action in {'delete_question','analyze_question'}:
            question = bank_question(db,pid,body.get('question_id'))
            if action=='delete_question':
                cancel_job(db,question['job_id'])
                cancel_job(db,project['job_id'])
                db.execute('DELETE FROM interview_question_bank WHERE id=?',(question['id'],))
                db.execute('UPDATE interview_preparations SET plan_stale=1 WHERE id=?',(pid,))
            else:
                if busy(db,question['job_id']): raise HTTPException(409,'请等待本题分析完成或取消')
                enqueue(db,project,'interview_question_analysis',question_id=question['id'])
        elif action == 'material':
            content = body.get('content', '').strip()
            if not content or len(content) > 50000: raise HTTPException(422, '每份材料需为 1–50000 字')
            total = db.execute('SELECT COALESCE(SUM(length(content)),0) FROM interview_materials WHERE project_id=?', (pid,)).fetchone()[0]
            if total + len(content) > 100000: raise HTTPException(422, '项目材料合计最多 100000 字，请拆分')
            db.execute('INSERT INTO interview_materials(id,project_id,title,kind,content,version,created_at) VALUES(?,?,?,?,?,1,?)', (uuid.uuid4().hex, pid, body.get('title') or '准备材料', body.get('kind') or 'background', content, time.time()))
            db.execute('UPDATE interview_preparations SET plan_stale=1 WHERE id=?', (pid,))
            cancel_job(db, project['job_id'])
        elif action == 'delete_material':
            changed = db.execute('DELETE FROM interview_materials WHERE id=? AND project_id=?', (body.get('material_id'),pid)).rowcount
            if not changed: raise HTTPException(404, '材料不存在')
            db.execute('UPDATE interview_preparations SET plan_stale=1 WHERE id=?', (pid,))
            cancel_job(db, project['job_id'])
        elif action == 'revise_material':
            row = db.execute('SELECT * FROM interview_materials WHERE id=? AND project_id=?', (body.get('material_id'), pid)).fetchone()
            content = body.get('content', '').strip()
            total = db.execute('SELECT COALESCE(SUM(length(content)),0) FROM interview_materials WHERE project_id=? AND id!=?', (pid, body.get('material_id'))).fetchone()[0]
            if not row: raise HTTPException(404, '材料不存在')
            if not content or len(content)>50000 or total+len(content)>100000: raise HTTPException(422, '材料正文为空或超过容量限制')
            history = json.loads(row['history'])
            history.append({'version':row['version'], 'content':row['content'], 'at':time.time()})
            db.execute('UPDATE interview_materials SET content=?,version=version+1,history=? WHERE id=?', (content, dump(history), row['id']))
            db.execute('UPDATE interview_preparations SET plan_stale=1 WHERE id=?', (pid,))
            cancel_job(db, project['job_id'])
        elif action == 'configure':
            cfg = body['config']
            db.execute('UPDATE interview_preparations SET config=?,plan_stale=1 WHERE id=?', (dump(cfg), pid))
            cancel_job(db, project['job_id'])
        elif action == 'prepare':
            if busy(db, project['job_id']): raise HTTPException(409, '准备方案正在生成')
            if not cfg.get('goal', '').strip(): raise HTTPException(422,'请先说明这次想准备的面试')
            enqueue(db, project, 'interview_prepare')
        elif action in {'start', 'retry_practice','practice_question'}:
            if action=='start' and (not project['plan'] or project['plan_stale']): raise HTTPException(409, '请先生成有效的准备方案')
            if db.execute("SELECT 1 FROM interview_practices WHERE project_id=? AND status='active'", (pid,)).fetchone(): raise HTTPException(409, '请先结束当前练习')
            plan = json.loads(project['plan']) if project['plan'] else {'summary':'历史问题专项练习','questions':[]}
            materials = [dict(r) for r in db.execute('SELECT id,title,kind,content,version FROM interview_materials WHERE project_id=? ORDER BY created_at,id', (pid,))]
            source = None
            if action == 'retry_practice':
                previous = practice_owned(db, project, body.get('practice_id'))
                if previous['status'] != 'done' or not previous['review'] or previous['review_stale']: raise HTTPException(409, '请先完成复盘')
                review = json.loads(previous['review'])
                # Review-time additions can be practiced immediately, even though
                # adding them invalidates the general preparation plan.
                additions = [{'question':q['question'], 'target':'复盘补充的历史问题'} for q in db.execute(
                    'SELECT question FROM interview_question_bank WHERE project_id=? ORDER BY created_at DESC,id', (pid,))]
                questions = []
                seen = set()
                for question in additions + review['retry_questions']:
                    key = question['question'].strip()
                    if key and key not in seen:
                        questions.append(question)
                        seen.add(key)
                if not questions: raise HTTPException(422, '请先补充要重练的问题')
                plan = {**plan, 'questions': questions}
                source = previous['id']
            if action=='practice_question':
                question = bank_question(db,pid,body.get('question_id'))
                plan = {'summary':'针对已遇到的面试问题重新作答','questions':[{'question':question['question'],'target':'历史问题重练'}]}
            frozen = {'config': cfg, 'materials': materials, 'plan': plan, 'criteria_version': 1,'question_bank':[dict(q) for q in db.execute('SELECT id,question,previous_answer,context,analysis FROM interview_question_bank WHERE project_id=? ORDER BY created_at,id',(pid,))]}
            practice_id = uuid.uuid4().hex
            db.execute('INSERT INTO interview_practices(id,project_id,status,snapshot,source_practice_id,created_at) VALUES(?,?,\'active\',?,?,?)', (practice_id, pid, dump(frozen), source, time.time()))
            add_turn(db, practice_id, plan['questions'][0], 0)
        else:
            practice = practice_owned(db, project, body.get('practice_id'))
            if action == 'cancel':
                cancel_job(db, practice['job_id'])
            elif action == 'answer':
                if practice['status'] != 'active' or busy(db, practice['job_id']): raise HTTPException(409, '当前不能作答')
                turn = db.execute('SELECT * FROM interview_turns WHERE id=? AND practice_id=?', (body.get('turn_id'), practice['id'])).fetchone()
                if not turn or turn['status'] != 'waiting': raise HTTPException(409, '本题已完成')
                if db.execute("SELECT 1 FROM interview_answers WHERE turn_id=? AND status IN ('recording','confirming')", (turn['id'],)).fetchone(): raise HTTPException(409, '已有未完成回答，请继续或放弃后重录')
                aid, sid = uuid.uuid4().hex, uuid.uuid4().hex[:12]
                db.execute('''INSERT INTO sessions(id,subject,scene,outline,status,created_at,subject_source,owner_id,purpose)
                              VALUES(?,?,?,?,'live',?,'user',?,'interview_answer')''', (sid, project['title'], '面试作答', '[]', time.time(), owner))
                db.execute('INSERT INTO interview_answers(id,turn_id,session_id,created_at) VALUES(?,?,?,?)', (aid, turn['id'], sid, time.time()))
            elif action in {'complete_answer','prepare_answer'}:
                if practice['status'] != 'active': raise HTTPException(409, '练习已结束')
                answer = db.execute('''SELECT a.* FROM interview_answers a JOIN interview_turns t ON a.turn_id=t.id
                                        WHERE a.id=? AND t.practice_id=?''', (body.get('answer_id'), practice['id'])).fetchone()
                if not answer or answer['status'] != 'recording': raise HTTPException(409, '回答不存在或已提交')
                chunk_ids = body.get('chunk_ids', [])
                receipts = db.execute('SELECT * FROM audio_receipts WHERE session_id=?', (answer['session_id'],)).fetchall()
                if not chunk_ids or len(set(chunk_ids)) != len(chunk_ids) or set(chunk_ids) != {r['chunk_id'] for r in receipts} or any(r['status'] != 'ready' for r in receipts):
                    raise HTTPException(409, '录音分片尚未全部上传和转写，请重试上传后提交')
                segments = [dict(r) for r in db.execute('SELECT id,seq,start_ms,end_ms,text FROM utterances WHERE session_id=? ORDER BY seq,id', (answer['session_id'],))]
                text = '\n'.join(r['text'] for r in segments)
                if not text.strip(): raise HTTPException(422, '未识别到有效语音，请继续录音或放弃本次回答后重录')
                db.execute("UPDATE interview_answers SET status=?,transcript=?,original_transcript=?,segments=? WHERE id=?", ('confirming' if action == 'prepare_answer' else 'ready', text, text, dump(segments), answer['id']))
                db.execute("UPDATE sessions SET status='done',finished_at=? WHERE id=?", (time.time(), answer['session_id']))
                if action == 'complete_answer':
                    db.execute("UPDATE interview_turns SET status='answered' WHERE id=?", (answer['turn_id'],))
                    enqueue(db, project, 'interview_next_turn', practice['id'])
            elif action == 'confirm_answer':
                if practice['status'] != 'active' or busy(db, practice['job_id']): raise HTTPException(409, '当前不能确认回答')
                answer = db.execute('''SELECT a.* FROM interview_answers a JOIN interview_turns t ON a.turn_id=t.id
                                     WHERE a.id=? AND t.practice_id=? AND a.status='confirming' ''', (body.get('answer_id'),practice['id'])).fetchone()
                if not answer: raise HTTPException(409, '请先完成录音并核对转写')
                text = body.get('content', '').strip() or answer['transcript']
                if len(text)>20000: raise HTTPException(422, '校对后的回答最多 20000 字')
                db.execute("UPDATE interview_answers SET status='ready',transcript=?,version=version+? WHERE id=?", (text,int(text!=answer['transcript']),answer['id']))
                db.execute("UPDATE interview_turns SET status='answered' WHERE id=?", (answer['turn_id'],))
                enqueue(db, project, 'interview_next_turn', practice['id'])
            elif action == 'discard_answer':
                answer = db.execute('''SELECT a.* FROM interview_answers a JOIN interview_turns t ON a.turn_id=t.id
                                      WHERE a.id=? AND t.practice_id=?''', (body.get('answer_id'), practice['id'])).fetchone()
                if not answer or answer['status'] not in {'recording','confirming'}: raise HTTPException(409, '只能放弃未提交的回答')
                db.execute("UPDATE interview_answers SET status='discarded' WHERE id=?", (answer['id'],))
                db.execute("UPDATE sessions SET status='done',finished_at=? WHERE id=?", (time.time(), answer['session_id']))
            elif action == 'candidate_questions':
                if practice['status']!='active': raise HTTPException(409,'练习已结束')
                frozen_cfg = effective_config(json.loads(practice['snapshot'])['config'])
                if frozen_cfg['template']!='job': raise HTTPException(422,'论文答辩不使用求职反问环节')
                if db.execute("SELECT 1 FROM interview_answers a JOIN interview_turns t ON a.turn_id=t.id WHERE t.practice_id=? AND a.status IN ('recording','confirming')",(practice['id'],)).fetchone():
                    raise HTTPException(409,'请先完成并确认当前回答')
                if db.execute("SELECT 1 FROM interview_turns WHERE practice_id=? AND turn_kind='candidate_questions'",(practice['id'],)).fetchone():
                    raise HTTPException(409,'本轮已经进入过反问环节')
                cancel_job(db,practice['job_id'])
                db.execute("UPDATE interview_turns SET status='skipped' WHERE practice_id=? AND status='waiting'",(practice['id'],))
                open_candidate_questions(db,practice['id'])
            elif action == 'finish':
                if db.execute("SELECT 1 FROM interview_answers a JOIN interview_turns t ON a.turn_id=t.id WHERE t.practice_id=? AND a.status IN ('recording','confirming')", (practice['id'],)).fetchone(): raise HTTPException(409, '请先提交或放弃当前回答')
                cancel_job(db, practice['job_id'])
                db.execute("UPDATE interview_practices SET status='done' WHERE id=?", (practice['id'],))
            elif action == 'next':
                if practice['status'] != 'active' or busy(db, practice['job_id']): raise HTTPException(409, '请等待当前任务完成')
                if db.execute("SELECT 1 FROM interview_turns WHERE practice_id=? AND status='waiting'", (practice['id'],)).fetchone(): raise HTTPException(409, '请先完成当前题')
                enqueue(db, project, 'interview_next_turn', practice['id'])
            elif action == 'review':
                if practice['status'] != 'done' or busy(db, practice['job_id']): raise HTTPException(409, '请先结束练习或等待任务完成')
                if not db.execute("SELECT 1 FROM interview_answers a JOIN interview_turns t ON a.turn_id=t.id WHERE t.practice_id=? AND a.status='ready'", (practice['id'],)).fetchone(): raise HTTPException(422, '尚无有效回答')
                enqueue(db, project, 'interview_review', practice['id'])
            elif action == 'correct':
                if practice['status'] != 'done': raise HTTPException(409, '请结束练习后校对转写')
                answer = db.execute('''SELECT a.* FROM interview_answers a JOIN interview_turns t ON a.turn_id=t.id WHERE a.id=? AND t.practice_id=? AND a.status='ready' ''', (body.get('answer_id'), practice['id'])).fetchone()
                content = body.get('content', '').strip()
                if not answer or not content or len(content) > 20000: raise HTTPException(422, '校对内容无效，最多 20000 字')
                cancel_job(db, practice['job_id'])
                db.execute('UPDATE interview_answers SET transcript=?,version=version+1 WHERE id=?', (content, answer['id']))
                db.execute('UPDATE interview_turns SET feedback=NULL WHERE id=?', (answer['turn_id'],))
                db.execute('UPDATE interview_practices SET review_stale=1 WHERE id=?', (practice['id'],))
            else: raise HTTPException(422, '不支持的操作')
        if action in {'configure','material','revise_material','delete_material'}:
            db.execute('UPDATE interview_question_bank SET analysis_stale=1 WHERE project_id=?',(pid,))
        touch(db, pid)
        db.execute('INSERT INTO interview_requests VALUES(?,?,?)', (pid, body['request_id'], request_hash))
        return snapshot(db, pid, owner)

def cancel_job(db, jid):
    if jid:
        db.execute("UPDATE tasks SET status='cancelled',stage='已取消',finished_at=?,updated_at=? WHERE id=? AND status IN ('queued','running','retry')", (time.time(), time.time(), jid))

def add_turn(db, practice_id, question, position, parent_id=None, turn_kind='question', reason=''):
    db.execute('INSERT INTO interview_turns(id,practice_id,position,question,target,parent_id,turn_kind,decision_reason) VALUES(?,?,?,?,?,?,?,?)',
               (uuid.uuid4().hex,practice_id,position,question['question'],question['target'],parent_id,turn_kind,reason))

def bank_question(db,pid,qid):
    row=db.execute('SELECT * FROM interview_question_bank WHERE id=? AND project_id=?',(qid,pid)).fetchone()
    if not row: raise HTTPException(404,'历史问题不存在')
    return dict(row)

def open_candidate_questions(db,practice_id):
    position=db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM interview_turns WHERE practice_id=?',(practice_id,)).fetchone()[0]
    add_turn(db,practice_id,{'question':'面试接近尾声，你有什么想了解或向我们提问的？请说出你会问的问题。','target':'求职反问：双向选择与信息获取'},position,turn_kind='candidate_questions')

def advance_or_close(db,practice_id,cfg):
    exists=db.execute("SELECT 1 FROM interview_turns WHERE practice_id=? AND turn_kind='candidate_questions'",(practice_id,)).fetchone()
    if cfg['template']=='job' and cfg.get('include_candidate_questions',True) and not exists:
        open_candidate_questions(db,practice_id)
    else:
        db.execute("UPDATE interview_practices SET status='done' WHERE id=?",(practice_id,))

def task_input(db, project, practice_id=None, question_id=None):
    if practice_id:
        practice = practice_owned(db, project, practice_id)
        data = json.loads(practice['snapshot'])
        data['config'] = effective_config(data['config'])
        data['turns'] = []
        for row in db.execute('SELECT * FROM interview_turns WHERE practice_id=? ORDER BY position', (practice_id,)):
            turn = dict(row)
            turn['answers'] = [dict(a) for a in db.execute("SELECT id,transcript,version,segments FROM interview_answers WHERE turn_id=? AND status='ready' ORDER BY created_at", (turn['id'],))]
            for answer in turn['answers']: answer['segments'] = json.loads(answer['segments'])
            data['turns'].append(turn)
        data['status'] = practice['status']
        return data
    data = {'config':effective_config(json.loads(project['config'])),
            'materials':[{k:r[k] for k in ('id','title','kind','content','version')} for r in db.execute('SELECT * FROM interview_materials WHERE project_id=? ORDER BY created_at,id',(project['id'],))],
            'question_bank':[dict(q) for q in db.execute('SELECT id,question,previous_answer,context FROM interview_question_bank WHERE project_id=? ORDER BY created_at,id',(project['id'],))]}
    if question_id: data['historical_question'] = bank_question(db,project['id'],question_id)
    return data
