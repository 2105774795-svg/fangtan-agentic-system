"""Reusable local conversation runtime: durable turns, audio, streaming and archives.

Project scope follows the unauthenticated platform. Legacy memoir authentication
is unchanged; only its pure evidence/formatting functions are reused.
"""
import asyncio
import hashlib
import json
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

import imageio_ffmpeg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import asr_dashscope
import config
import job_queue
import platform_ai
import store
import employee_auth
from agents import memoir_agent
from agents.errors import ERROR_MESSAGES, classify, diagnostic
from agents.tools import enqueue_book
from memory import repository as memory_store
from memoir_editor import chapters
from memoir_editor import biography
from memoir_editor.models import BookRequest, BookSettings
from conversation_policies import POLICIES


async def require_project_access(pid: str, request: Request):
    # AUTH=false 继续是本机单人模式；开启员工认证后不得按猜到的项目ID读记忆。
    if config.AUTH_ENABLED:
        user = getattr(request.state, "user", None) or await employee_auth.current_user(request)
        with store.get() as db:
            row = db.execute("SELECT owner_id FROM projects WHERE id=?", (pid,)).fetchone()
            if not row or row["owner_id"] != user["id"]:
                raise HTTPException(404, "项目不存在或无权访问")


router = APIRouter(prefix="/api/platform/projects/{pid}", tags=["conversations"],
                   dependencies=[Depends(require_project_access)])
_audio_locks = {}


def init():
    with store.get() as db:
        for sql in ("""CREATE TABLE IF NOT EXISTS conversation_sessions(
            id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,
            archive TEXT NOT NULL DEFAULT '',archive_task TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,finished_at REAL)""",
            """CREATE TABLE IF NOT EXISTS conversation_turns(
            id TEXT PRIMARY KEY,session_id TEXT NOT NULL,seq INTEGER NOT NULL,role TEXT NOT NULL,
            client_id TEXT NOT NULL,text TEXT NOT NULL,output TEXT NOT NULL DEFAULT '',task_id TEXT NOT NULL DEFAULT '',
            control TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,UNIQUE(session_id,role,client_id),UNIQUE(session_id,seq))""",
            """CREATE TABLE IF NOT EXISTS conversation_audio(
            id TEXT PRIMARY KEY,session_id TEXT NOT NULL,turn_key TEXT NOT NULL,part_index INTEGER NOT NULL,
            digest TEXT NOT NULL,path TEXT NOT NULL,transcript TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'pending',
            UNIQUE(session_id,turn_key,part_index))""",
            """CREATE TABLE IF NOT EXISTS conversation_people(project_id TEXT PRIMARY KEY,preferred_name TEXT NOT NULL,source_turn TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS conversation_archive_versions(session_id TEXT NOT NULL,revision INTEGER NOT NULL,document TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(session_id,revision))""",
            "CREATE INDEX IF NOT EXISTS conversation_project ON conversation_sessions(project_id,created_at)"):
            db.execute(sql)
        memory_store.init(db)


def _project(db, pid):
    import research_platform
    return research_platform._read(db, pid)


def _session(db, pid, sid):
    _project(db, pid)
    row = db.execute("SELECT * FROM conversation_sessions WHERE id=? AND project_id=?", (sid, pid)).fetchone()
    if not row:
        raise HTTPException(404, "对谈不存在")
    return dict(row)


def _turn(db, sid, role, text, client_id, control=""):
    tid = uuid.uuid4().hex
    seq = db.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM conversation_turns WHERE session_id=?", (sid,)).fetchone()[0]
    db.execute("INSERT INTO conversation_turns(id,session_id,seq,role,client_id,text,control,created_at) VALUES(?,?,?,?,?,?,?,?)",
               (tid, sid, seq, role, client_id, text, control, time.time()))
    return tid


def _enqueue(db, pid, kind, payload):
    tid, now = uuid.uuid4().hex, time.time()
    db.execute("""INSERT INTO tasks(id,kind,owner_id,resource_id,payload,max_attempts,available_at,created_at,updated_at)
                  VALUES(?,?, -1,?,?,?,?,?,?)""", (tid, kind, pid, json.dumps(payload), 2 if kind == 'conversation_reply' else 1, now, now, now))
    return tid


def _job(db, tid):
    row = db.execute("SELECT id,status,stage,progress,error,attempts,max_attempts FROM tasks WHERE id=?", (tid,)).fetchone()
    if not row:
        return None
    value = dict(row)
    if value["error"]:
        code = value['error'][6:] if value['error'].startswith('agent:') else ''
        value['error_code'] = code if code in ERROR_MESSAGES else ''
        value["error"] = ERROR_MESSAGES.get(code, "暂时没能完成，原始内容已经保存，可以重试。")
    if value['status'] == 'ready' and db.execute("SELECT 1 FROM memoir_agent_diagnostics WHERE task_id=? AND code='reply_fallback' LIMIT 1", (tid,)).fetchone():
        value['notice'] = '本轮使用简短系统接续，未生成完整 AI 回复；您的讲述已保存，可以继续讲。'
    return value


def _snapshot(db, pid, sid):
    session = _session(db, pid, sid)
    turns = []
    for row in db.execute("SELECT * FROM conversation_turns WHERE session_id=? ORDER BY seq", (sid,)):
        item = dict(row)
        item["job"] = _job(db, item.pop("task_id"))
        turns.append(item)
    session["archive_job"] = _job(db, session.pop("archive_task"))
    session["archive"] = json.loads(session["archive"]) if session["archive"] else None
    session["audio"] = [dict(r) for r in db.execute("SELECT id,turn_key,part_index,status,transcript FROM conversation_audio WHERE session_id=? ORDER BY turn_key,part_index", (sid,))]
    for item in turns:
        item["audio_ids"] = [r["id"] for r in session["audio"] if item["role"] == "human" and r["turn_key"] == item["client_id"]]
    person = db.execute("SELECT preferred_name FROM conversation_people WHERE project_id=?", (pid,)).fetchone()
    session["preferred_name"] = person["preferred_name"] if person else ""
    session["turns"] = turns
    plan = db.execute("SELECT plan,revision FROM memoir_agent_plans WHERE project_id=? AND session_id=?", (pid, sid)).fetchone()
    session["agent"] = {"plan": json.loads(plan["plan"]), "revision": plan["revision"]} if plan else None
    return session


class Start(BaseModel):
    kind: str = "memoir"
    request_id: str = Field(min_length=8, max_length=80)
    new_session: bool = False


@router.post("/conversations")
def start(pid: str, body: Start):
    if body.kind not in POLICIES:
        raise HTTPException(422, "不支持的对谈方式")
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        project = _project(db, pid)
        sid = uuid.uuid5(uuid.NAMESPACE_URL, f"conversation:{pid}:{body.request_id}").hex
        if db.execute("SELECT 1 FROM conversation_sessions WHERE id=?", (sid,)).fetchone():
            return _snapshot(db, pid, sid)
        if not body.new_session:
            row = db.execute("SELECT id FROM conversation_sessions WHERE project_id=? AND kind=? AND status='live' ORDER BY created_at DESC LIMIT 1", (pid, body.kind)).fetchone()
            if row:
                return _snapshot(db, pid, row["id"])
        elif db.execute("SELECT 1 FROM conversation_sessions WHERE project_id=? AND status='live'", (pid,)).fetchone():
            raise HTTPException(409, "请先结束当前对谈，再开始新的一场")
        db.execute("INSERT INTO conversation_sessions(id,project_id,kind,status,created_at) VALUES(?,?,?,'live',?)", (sid, pid, body.kind, time.time()))
        # Older memoir workspaces may still contain an intake job. It is not the
        # conversational path, and must not continue questioning a hidden form.
        if project["scenario"] == "memoir":
            db.execute("UPDATE tasks SET status='cancelled',stage='cancelled',updated_at=?,finished_at=? WHERE kind='research_platform' AND resource_id=? AND status IN ('queued','running','retry')", (time.time(), time.time(), pid))
        previous = db.execute("SELECT archive FROM conversation_sessions WHERE project_id=? AND archive<>'' AND kind=? ORDER BY created_at DESC LIMIT 1", (pid, body.kind)).fetchone()
        opening = POLICIES[body.kind]["opening"]
        if previous:
            opening = "很高兴又和您聊天。您愿意接着上次的故事讲，还是聊一段新的回忆？" if body.kind == "memoir" else "欢迎继续。您想接着上次的经历，还是换一个话题？"
        _turn(db, sid, "assistant", opening, "opening")
        return _snapshot(db, pid, sid)


@router.get("/conversations/{sid}")
def detail(pid: str, sid: str):
    with store.get() as db:
        return _snapshot(db, pid, sid)


@router.get("/conversation-archive")
def archive(pid: str):
    with store.get() as db:
        project = _project(db, pid)
        sessions = [_snapshot(db, pid, row["id"]) for row in db.execute("SELECT id FROM conversation_sessions WHERE project_id=? ORDER BY created_at DESC", (pid,)).fetchall()]
        book = chapters.snapshot(db, pid)
        return {"title": project["title"], "sessions": sessions, "legacy_artifacts": project["artifacts"],
                "memories": memory_store.memories(db, pid), "book": book["book"], "book_job": book["job"],
                "book_settings": book["settings"], "book_gaps": book["gaps"]}


class MemoryReview(BaseModel):
    revision: int = Field(ge=1)
    action: str
    note: str = Field(default="", max_length=1000)
    replaces: str | None = Field(default=None, max_length=80)


@router.post("/conversation-memories/{mid}/review")
def review_memory(pid: str, mid: str, body: MemoryReview):
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        return memory_store.review(db, pid, mid, body.revision, body.action, body.note, body.replaces)


@router.get("/conversation-book")
def read_book(pid: str):
    with store.get() as db:
        return chapters.snapshot(db, pid)


@router.post("/conversation-book")
def generate_book(pid: str, body: BookRequest = BookRequest()):
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        if not chapters.inputs(db, pid):
            raise HTTPException(409, "请先留下本人讲述")
        if body.mode == 'polish' and not biography.settings(db, pid)['style_approved']:
            raise HTTPException(409, '请先阅读样章并认可写法，再进行全书统稿')
        return enqueue_book(db, pid, mode=body.mode)


@router.post("/conversation-book/settings")
def update_book_settings(pid: str, body: BookSettings):
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        return biography.change_settings(db, pid, body)


class BookReview(BaseModel):
    revision: int = Field(ge=1)
    action: str
    chapter_index: int | None = Field(default=None, ge=0)
    paragraph_index: int | None = Field(default=None, ge=0)
    text: str = Field(default="", max_length=5000)
    chapter_id: str = Field(default="", max_length=80)


@router.post("/conversation-book/review")
def review_book(pid: str, body: BookReview):
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        return chapters.review(db, pid, body)


@router.get("/conversation-book/versions")
def book_versions(pid: str):
    with store.get() as db:
        memory_store.require_project(db, pid)
        return {"versions": [{"revision": r["revision"], "created_at": r["created_at"], "document": json.loads(r["document"])}
                             for r in db.execute("SELECT * FROM memoir_book_versions WHERE project_id=? ORDER BY revision DESC LIMIT 20", (pid,))]}


@router.get("/conversations/{sid}/events")
async def events(pid: str, sid: str, request: Request):
    detail(pid, sid)
    async def generate():
        last, ticks = None, 0
        while not await request.is_disconnected():
            try:
                data = await run_in_threadpool(detail, pid, sid)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                yield "event: deleted\ndata: {}\n\n"
                return
            encoded = json.dumps(data, ensure_ascii=False)
            if encoded != last:
                yield "event: snapshot\ndata: " + encoded + "\n\n"
                last = encoded
            ticks += 1
            if ticks % 30 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(.4)
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


class Submit(BaseModel):
    client_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    text: str = Field(default="", max_length=12000)
    audio_ids: list[str] = Field(default_factory=list, max_length=240)


@router.post("/conversations/{sid}/turns")
def submit(pid: str, sid: str, body: Submit):
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        session = _session(db, pid, sid)
        prior = db.execute("SELECT * FROM conversation_turns WHERE session_id=? AND role='human' AND client_id=?", (sid, body.client_id)).fetchone()
        if prior:
            if body.text.strip() and prior["text"] != body.text.strip():
                raise HTTPException(409, "同一轮请求对应不同内容，请重新发送")
            return _snapshot(db, pid, sid)
        if session["status"] != "live":
            raise HTTPException(409, "这场对谈已经结束")
        if body.text.strip() and body.audio_ids:
            raise HTTPException(422, "语音和文字请分轮提交")
        text = body.text.strip()
        if body.audio_ids:
            if len(set(body.audio_ids)) != len(body.audio_ids):
                raise HTTPException(422, "音频片段重复")
            rows = db.execute("SELECT * FROM conversation_audio WHERE session_id=? AND turn_key=? ORDER BY part_index", (sid, body.client_id)).fetchall()
            if set(body.audio_ids) != {r["id"] for r in rows} or [r["part_index"] for r in rows] != list(range(len(rows))) or any(r["status"] != "ready" for r in rows):
                raise HTTPException(409, "语音片段尚未完成转写，可以离开后重试")
            text = "\n".join(r["transcript"] for r in rows if r["transcript"].strip())
        if not text:
            return {**_snapshot(db, pid, sid), "no_speech": True}
        size = db.execute("SELECT COALESCE(SUM(LENGTH(text)),0) FROM conversation_turns WHERE session_id IN (SELECT id FROM conversation_sessions WHERE project_id=?) AND role='human'", (pid,)).fetchone()[0]
        if len(text) > 12000 or size + len(text) > 200000:
            raise HTTPException(422, "讲述超过本档案处理上限，原音保留，请另建档案")
        last = db.execute("SELECT * FROM conversation_turns WHERE session_id=? ORDER BY seq DESC LIMIT 1", (sid,)).fetchone()
        if last and last["task_id"] and _job(db, last["task_id"])["status"] in {"queued", "running", "retry", "failed"}:
            raise HTTPException(409, "请等待当前回应完成，或重试后继续")
        # Only an explicit, short control utterance changes the conversation state;
        # quoted stories about somebody saying goodbye are not control commands.
        compact = re.sub(r"[，。！!？?、\s]", "", text)
        control = "finish" if compact in {"今天先聊到这里", "今天就聊到这里", "今天不聊了", "结束聊天", "结束对话", "不聊了"} else "pause" if compact in {"暂停", "休息一下", "先歇一会", "暂停一下", "我还没说完"} else ""
        human_id = _turn(db, sid, "human", text, body.client_id, control)
        name = re.fullmatch(r"(?:您可以|你可以)?(?:叫我|我叫|称呼我)([\u4e00-\u9fff·]{1,10}?)(?:就行|就好|吧)?[，。！!\s]*", text)
        if name:
            db.execute("INSERT OR REPLACE INTO conversation_people VALUES(?,?,?)", (pid, name.group(1), human_id))
        if control:
            _turn(db, sid, "assistant", "好，今天先聊到这里。我会把您讲过的故事保存下来。" if control == "finish" else "好的，不着急，我们先停一停。", "reply:" + body.client_id, control)
            if control == "finish":
                _finish(db, pid, sid)
        else:
            aid = _turn(db, sid, "assistant", "", "reply:" + body.client_id)
            task_id = _enqueue(db, pid, "conversation_reply", {"pid": pid, "sid": sid, "turn_id": aid})
            db.execute("UPDATE conversation_turns SET task_id=? WHERE id=?", (task_id, aid))
        db.execute("UPDATE projects SET updated_at=? WHERE id=?", (time.time(), pid))
        return _snapshot(db, pid, sid)


def _finish(db, pid, sid):
    db.execute("UPDATE conversation_sessions SET status='done',finished_at=? WHERE id=?", (time.time(), sid))
    if db.execute("SELECT 1 FROM conversation_turns WHERE session_id=? AND role='human' AND control=''", (sid,)).fetchone():
        tid = _enqueue(db, pid, "conversation_archive", {"pid": pid, "sid": sid})
        db.execute("UPDATE conversation_sessions SET archive_task=? WHERE id=?", (tid, sid))


@router.post("/conversations/{sid}/finish")
def finish(pid: str, sid: str):
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        session = _session(db, pid, sid)
        if session["status"] == "live":
            if db.execute("SELECT 1 FROM conversation_audio WHERE session_id=? AND status='pending'", (sid,)).fetchone():
                raise HTTPException(409, "还有语音待转写，录音已保存，可稍后重试")
            if db.execute("SELECT 1 FROM conversation_audio a WHERE a.session_id=? AND a.transcript<>'' AND NOT EXISTS(SELECT 1 FROM conversation_turns t WHERE t.session_id=a.session_id AND t.client_id=a.turn_key AND t.role='human')", (sid,)).fetchone():
                raise HTTPException(409, "这段语音尚未提交，请先发送或重试；原音已保留")
            db.execute("UPDATE tasks SET status='cancelled',stage='cancelled',updated_at=?,finished_at=? WHERE kind='conversation_reply' AND resource_id=? AND id IN (SELECT task_id FROM conversation_turns WHERE session_id=?) AND status IN ('queued','running','retry')", (time.time(), time.time(), pid, sid))
            _finish(db, pid, sid)
        return _snapshot(db, pid, sid)


@router.post("/conversations/{sid}/retry")
def retry(pid: str, sid: str):
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        session = _session(db, pid, sid)
        row = db.execute("SELECT id,task_id FROM conversation_turns WHERE session_id=? ORDER BY seq DESC LIMIT 1", (sid,)).fetchone()
        tid = session["archive_task"] if session["status"] == "done" else row["task_id"] if row else ""
        task = _job(db, tid)
        if not task or task["status"] not in {"failed", "cancelled"}:
            return _snapshot(db, pid, sid)
        kind = "conversation_archive" if session["status"] == "done" else "conversation_reply"
        new_id = _enqueue(db, pid, kind, {"pid": pid, "sid": sid, **({"turn_id": row["id"]} if kind.endswith("reply") else {})})
        if kind.endswith("reply"):
            db.execute("UPDATE conversation_turns SET task_id=?,output='' WHERE id=?", (new_id, row["id"]))
        else:
            db.execute("UPDATE conversation_sessions SET archive_task=? WHERE id=?", (new_id, sid))
        return _snapshot(db, pid, sid)


class ArchiveReview(BaseModel):
    revision: int = Field(ge=1)
    action: str
    paragraph_index: int | None = Field(default=None, ge=0)
    text: str = Field(default="", max_length=5000)


@router.post("/conversations/{sid}/archive-review")
def archive_review(pid: str, sid: str, body: ArchiveReview):
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        session = _session(db, pid, sid)
        if not session["archive"]:
            raise HTTPException(409, "故事尚未完成整理")
        document = json.loads(session["archive"])
        revision = document.get("revision", 1)
        if revision != body.revision:
            raise HTTPException(409, "故事已在另一页面修改，请刷新后重试")
        if body.action == "edit":
            if body.paragraph_index is None or body.paragraph_index >= len(document["paragraphs"]) or not body.text.strip():
                raise HTTPException(422, "请填写有效的故事正文")
            document["paragraphs"][body.paragraph_index].update(text=body.text.strip(), edited=True)
            document["status"] = "draft"
        elif body.action == "review":
            document["status"] = "reviewed"
        else:
            raise HTTPException(422, "不支持的审阅操作")
        document["revision"] = revision + 1
        db.execute("INSERT INTO conversation_archive_versions VALUES(?,?,?,?)", (sid, revision, session["archive"], time.time()))
        db.execute("UPDATE conversation_sessions SET archive=? WHERE id=?", (json.dumps(document, ensure_ascii=False), sid))
        return _snapshot(db, pid, sid)


def _save_audio(pid, sid, chunk_id, turn_key, index, raw):
    digest = hashlib.sha256(raw).hexdigest()
    with _audio_locks.setdefault(sid, threading.Lock()):
        with store.get() as db:
            session = _session(db, pid, sid)
            old = db.execute("SELECT * FROM conversation_audio WHERE id=?", (chunk_id,)).fetchone()
            if old:
                if old["session_id"] != sid or old["turn_key"] != turn_key or old["digest"] != digest or old["part_index"] != index:
                    raise HTTPException(409, "分片编号不匹配")
                if old["status"] == "ready":
                    return {"id": chunk_id, "text": old["transcript"], "no_speech": not old["transcript"]}
            if session["status"] != "live":
                raise HTTPException(409, "本场对谈已结束")
            if not old and db.execute("SELECT 1 FROM conversation_audio WHERE session_id=? AND turn_key=? AND part_index=?", (sid, turn_key, index)).fetchone():
                raise HTTPException(409, "该顺序的音频分片已经保存")
            if db.execute("SELECT 1 FROM conversation_turns WHERE session_id=? AND role='human' AND client_id=?", (sid, turn_key)).fetchone():
                raise HTTPException(409, "这一轮讲述已经提交，请开始新一轮")
        root = config.DATA_DIR / "conversations" / sid
        root.mkdir(parents=True, exist_ok=True)
        source, wav = root / (chunk_id + ".source"), root / (chunk_id + ".wav")
        if not old:
            source.write_bytes(raw)
            try:
                subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-v", "error", "-y", "-i", str(source), "-ac", "1", "-ar", "16000", "-f", "wav", str(wav)], capture_output=True, check=True, timeout=60)
            except (subprocess.SubprocessError, OSError):
                raise HTTPException(422, "音频暂时无法解码，本机分片保留，可重新录制或改用文字") from None
            with store.get() as db:
                db.execute("BEGIN IMMEDIATE")
                _session(db, pid, sid)
                db.execute("INSERT INTO conversation_audio(id,session_id,turn_key,part_index,digest,path) VALUES(?,?,?,?,?,?)", (chunk_id, sid, turn_key, index, digest, str(wav)))
        try:
            text = asr_dashscope.transcribe(str(wav)).strip()
        except Exception:
            raise HTTPException(502, "录音已经保存，转写暂未完成；可以离开后重试，不会重复保存") from None
        with store.get() as db:
            _session(db, pid, sid)
            db.execute("UPDATE conversation_audio SET transcript=?,status='ready' WHERE id=?", (text, chunk_id))
        return {"id": chunk_id, "text": text, "no_speech": not text}


@router.post("/conversations/{sid}/audio")
async def audio(pid: str, sid: str, chunk_id: str = Form(...), turn_key: str = Form(...), part_index: int = Form(...), audio: UploadFile = File(...)):
    if not re.fullmatch(r"[a-f0-9-]{32,36}", chunk_id) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", turn_key) or not 0 <= part_index < 240:
        raise HTTPException(422, "分片参数无效")
    raw = await audio.read(10 * 1024 * 1024 + 1)
    await audio.close()
    if not raw or len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, "音频分片为空或超过10MB")
    return await run_in_threadpool(_save_audio, pid, sid, chunk_id, turn_key, part_index, raw)


@router.get("/conversations/{sid}/audio/{chunk_id}")
def recording(pid: str, sid: str, chunk_id: str, original: bool = False):
    with store.get() as db:
        _session(db, pid, sid)
        row = db.execute("SELECT path FROM conversation_audio WHERE id=? AND session_id=?", (chunk_id, sid)).fetchone()
        if not row:
            raise HTTPException(404, "录音不存在")
    path = Path(row["path"]).resolve()
    if path.parent != (config.DATA_DIR / "conversations" / sid).resolve() or not path.is_file():
        raise HTTPException(404, "录音不存在")
    if original:
        source = path.with_suffix(".source")
        if not source.is_file():
            raise HTTPException(404, "原始文件不存在")
        with source.open("rb") as stream:
            signature = stream.read(12)
        mime, extension = ("audio/wav", "wav") if signature.startswith(b"RIFF") else ("audio/mp4", "mp4") if b"ftyp" in signature else ("audio/ogg", "ogg") if signature.startswith(b"OggS") else ("audio/webm", "webm")
        return FileResponse(source, media_type=mime, filename=chunk_id + "." + extension, headers={"Cache-Control": "no-store"})
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})


def reply_job(payload, ctx):
    pid, sid, tid = payload["pid"], payload["sid"], payload["turn_id"]
    with store.get() as db:
        session = _session(db, pid, sid)
        project = _project(db, pid)
        current = db.execute("SELECT * FROM conversation_turns WHERE id=? AND session_id=?", (tid, sid)).fetchone()
        if not current or current["task_id"] != ctx.task_id or session["status"] != "live":
            raise ValueError("对谈轮次已变更")
        if current["text"]:
            return {"saved": True}
        recent = [dict(r) for r in db.execute("SELECT role,text FROM conversation_turns WHERE session_id=? AND seq<? ORDER BY seq DESC LIMIT 16", (sid, current["seq"]))][::-1]
        person = db.execute("SELECT preferred_name FROM conversation_people WHERE project_id=?", (pid,)).fetchone()
        memories = [json.loads(r["archive"]) for r in db.execute("SELECT archive FROM conversation_sessions WHERE project_id=? AND kind=? AND archive<>'' ORDER BY created_at DESC LIMIT 4", (pid, session["kind"]))]
    messages = [{"role": "system", "content": POLICIES[session["kind"]]["system"]}, {"role": "user", "content": json.dumps({"topic": project["prompt"], "preferred_name": person["preferred_name"] if person else "", "prior_stories": memories}, ensure_ascii=False)}]
    messages += [{"role": "user" if r["role"] == "human" else "assistant", "content": r["text"]} for r in recent]
    output, last = [], 0.0
    def on_text(fragment):
        nonlocal last
        ctx.check_cancelled()
        output.append(fragment)
        if time.monotonic() - last > .15:
            with store.get() as db:
                ctx.check_cancelled(db)
                _session(db, pid, sid)
                db.execute("UPDATE conversation_turns SET output=? WHERE id=? AND task_id=?", ("".join(output), tid, ctx.task_id))
            last = time.monotonic()
    try:
        if session["kind"] == "memoir":
            decision = memoir_agent.run(pid, sid, tid, ctx)
            text = decision.message
            # 工具与内部决策永不进入语音流；最终文本通过校验才对外展示。
            on_text(text)
        else:
            text = platform_ai.stream(messages, on_text, max_tokens=700).strip()
        if not text or len(text) > 1000 or text.count("？") + text.count("?") > 1:
            raise ValueError("回应应一次只问一个问题")
        with store.get() as db:
            db.execute("BEGIN IMMEDIATE")
            ctx.check_cancelled(db)
            latest_session = _session(db, pid, sid)
            if latest_session["status"] != "live":
                raise ValueError("对谈已结束")
            db.execute("UPDATE conversation_turns SET text=?,output='' WHERE id=? AND task_id=?", (text, tid, ctx.task_id))
        return {"saved": True}
    except job_queue.TaskUnavailable:
        raise
    except Exception as cause:
        failure = classify(cause)
        with store.get() as db:
            # 仅在仍持有任务租约时写诊断，取消或迟到结果不能继续保存。
            ctx.check_cancelled(db)
            diagnostic(db, pid, ctx.task_id, failure.code, failure.stage)
        raise failure from None


def archive_job(payload, ctx):
    from memoir.handlers import _chunks, _chat, _evidence, MEMORY_SYSTEM, CHAPTER_SYSTEM
    pid, sid = payload["pid"], payload["sid"]
    with store.get() as db:
        session = _session(db, pid, sid)
        if session["status"] != "done" or session["archive_task"] != ctx.task_id:
            raise ValueError("归档任务已变更")
        turns = [dict(r) for r in db.execute("SELECT id,text FROM conversation_turns WHERE session_id=? AND role='human' AND control='' ORDER BY seq", (sid,))]
    paragraphs, events = [], []
    for block in _chunks(turns):
        ctx.check_cancelled()
        sources = {r["turn_id"]: r["text"] for r in block}
        system = CHAPTER_SYSTEM if session["kind"] == "memoir" else CHAPTER_SYSTEM.replace("第一人称人生故事", "面试对话记录").replace("口述史整理者", "面试记录整理者")
        data = _chat(system, block)
        values = data.get("paragraphs")
        if not isinstance(values, list) or not 1 <= len(values) <= 30:
            raise ValueError("故事段落格式不正确")
        for value in values:
            if not isinstance(value.get("text"), str) or not 1 <= len(value["text"]) <= 5000:
                raise ValueError("故事正文格式不正确")
            paragraphs.append({"text": value["text"], "evidence": _evidence(value.get("evidence"), sources, 4)})
        if session["kind"] == "memoir":
            data = _chat(MEMORY_SYSTEM, block)
            values = data.get("events")
            if not isinstance(values, list) or len(values) > 20:
                raise ValueError("人生线索格式不正确")
            for value in values:
                evidence = _evidence(value.get("evidence"), sources, 3)
                date = value.get("date_text")
                if not isinstance(date, str) or not 1 <= len(date) <= 100 or (date != "未提及" and not any(date in q["quote"] for q in evidence)):
                    raise ValueError("事件时间没有原话依据")
                if any(not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= 3000 for key in ("title", "description")):
                    raise ValueError("人生线索格式不正确")
                events.append({"title": value["title"], "date_text": date, "description": value["description"], "status": "uncertain" if value.get("status") == "uncertain" or any(re.search("好像|大约|可能|左右|记不清", e["quote"]) for e in evidence) else "candidate", "evidence": evidence})
    document = {"paragraphs": paragraphs, "events": events, "status": "draft", "revision": 1, "created_at": time.time()}
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        ctx.check_cancelled(db)
        _session(db, pid, sid)
        db.execute("UPDATE conversation_sessions SET archive=? WHERE id=? AND archive_task=?", (json.dumps(document, ensure_ascii=False), sid, ctx.task_id))
        if session["kind"] == "memoir":
            for event in events:
                memory_store.save_candidate(db, pid, event)
            enqueue_book(db, pid)
    return {"saved": True}


HANDLERS = {"conversation_reply": reply_job, "conversation_archive": archive_job, "conversation_book": chapters.job}
