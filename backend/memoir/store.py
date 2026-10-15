"""时叙录私有表与不可覆盖的原始讲述；不创建旧访谈业务行。"""
import hashlib
import json
import os
import re
import stat
import time
import uuid
from pathlib import Path

import config
import store as main_store


MAX_SOURCE_CHARS = 200_000
MAX_TURNS = 10_000
KINDS = ("memoir_memory", "memoir_chapter")
EXTENSIONS = {"wav", "mp3", "m4a", "aac", "ogg", "webm", "mp4", "flac"}


def init(db=None):
    if db is None:
        with main_store.get() as connection:
            init(connection)
        return
    # execute 而非 executescript，允许调用者保留现有写事务。
    schema = """
    CREATE TABLE IF NOT EXISTS memoir_profiles(
      id TEXT PRIMARY KEY,owner_id INTEGER NOT NULL CHECK(owner_id>0),name TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',cloud_consented_at REAL NOT NULL,
      recording_consented_at REAL NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_memoir_profiles_owner ON memoir_profiles(owner_id,updated_at DESC);
    CREATE TABLE IF NOT EXISTS memoir_sessions(
      id TEXT PRIMARY KEY,profile_id TEXT NOT NULL,title TEXT NOT NULL,status TEXT NOT NULL,
      created_at REAL NOT NULL,finished_at REAL);
    CREATE INDEX IF NOT EXISTS idx_memoir_sessions_profile ON memoir_sessions(profile_id,created_at);
    CREATE TABLE IF NOT EXISTS memoir_turns(
      id TEXT PRIMARY KEY,profile_id TEXT NOT NULL,session_id TEXT NOT NULL,seq INTEGER NOT NULL,
      role TEXT NOT NULL CHECK(role IN ('human','assistant')),text TEXT NOT NULL,
      client_turn_id TEXT NOT NULL,audio_path TEXT NOT NULL DEFAULT '',audio_mime TEXT NOT NULL DEFAULT '',
      created_at REAL NOT NULL,UNIQUE(session_id,role,client_turn_id),UNIQUE(session_id,seq));
    CREATE INDEX IF NOT EXISTS idx_memoir_turns_profile ON memoir_turns(profile_id,created_at,session_id,seq);
    CREATE TABLE IF NOT EXISTS memoir_events(
      id TEXT PRIMARY KEY,profile_id TEXT NOT NULL,title TEXT NOT NULL,date_text TEXT NOT NULL,
      description TEXT NOT NULL,status TEXT NOT NULL,evidence TEXT NOT NULL,
      source_fingerprint TEXT NOT NULL,event_key TEXT NOT NULL,created_at REAL NOT NULL,confirmed_at REAL,
      UNIQUE(profile_id,event_key));
    CREATE TABLE IF NOT EXISTS memoir_chapters(
      id TEXT PRIMARY KEY,profile_id TEXT NOT NULL,title TEXT NOT NULL,status TEXT NOT NULL,
      paragraphs TEXT NOT NULL DEFAULT '[]',source_fingerprint TEXT NOT NULL,
      task_id TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,confirmed_at REAL);
    CREATE INDEX IF NOT EXISTS idx_memoir_chapters_profile ON memoir_chapters(profile_id,created_at DESC);
    """
    for statement in schema.split(";"):
        if statement.strip():
            db.execute(statement)


def owner(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("时叙录需要独立登录的个人账号")
    return value


def text(value, name, maximum, required=True):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"{name}不能为空或超出长度限制")
    return value.strip()


def _profile(db, profile_id, user_id):
    owner(user_id)
    row = db.execute("SELECT * FROM memoir_profiles WHERE id=? AND owner_id=?", (profile_id, user_id)).fetchone()
    if row is None:
        raise LookupError("人物档案不存在或无权访问")
    return dict(row)


def _session(db, profile_id, sid, user_id):
    _profile(db, profile_id, user_id)
    row = db.execute("SELECT * FROM memoir_sessions WHERE id=? AND profile_id=?", (sid, profile_id)).fetchone()
    if row is None:
        raise LookupError("讲述不存在或无权访问")
    return dict(row)


def create_profile(user_id, name, description="", *, cloud_consent=False, recording_consent=False):
    owner(user_id)
    if cloud_consent is not True or recording_consent is not True:
        raise ValueError("请单独同意云端文字处理和录音保存后创建时叙录")
    name, description = text(name, "人物称呼", 100), text(description, "人物介绍", 2000, False)
    pid, now = uuid.uuid4().hex, time.time()
    with main_store.get() as db:
        db.execute("INSERT INTO memoir_profiles VALUES(?,?,?,?,?,?,?,?)", (pid, user_id, name, description, now, now, now, now))
        return _profile(db, pid, user_id)


def list_profiles(user_id):
    owner(user_id)
    with main_store.get() as db:
        return [dict(row) for row in db.execute("SELECT * FROM memoir_profiles WHERE owner_id=? ORDER BY updated_at DESC,id", (user_id,))]


def get_profile(profile_id, user_id):
    with main_store.get() as db:
        return _profile(db, profile_id, user_id)


def create_session(profile_id, user_id, title=""):
    title = text(title, "讲述标题", 150, False) or "新的讲述"
    sid, now = uuid.uuid4().hex, time.time()
    with main_store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        _profile(db, profile_id, user_id)
        db.execute("INSERT INTO memoir_sessions VALUES(?,?,?,?,?,NULL)", (sid, profile_id, title, "live", now))
        db.execute("UPDATE memoir_profiles SET updated_at=? WHERE id=?", (now, profile_id))
        return _session(db, profile_id, sid, user_id)


def get_session(profile_id, sid, user_id):
    with main_store.get() as db:
        return _session(db, profile_id, sid, user_id)


def list_sessions(profile_id, user_id):
    with main_store.get() as db:
        _profile(db, profile_id, user_id)
        return [dict(row) for row in db.execute("SELECT * FROM memoir_sessions WHERE profile_id=? ORDER BY created_at DESC,id", (profile_id,))]


def _uuid(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("录音标识不合法")
    return value


def _recording_root(profile_id):
    return Path(config.DATA_DIR) / "memoir-private" / "recordings" / _uuid(profile_id)


def _safe_path(profile_id, value, *, must_exist=False):
    root = _recording_root(profile_id)
    value = Path(value)
    if not value.is_absolute() or value.parent != root or not re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]+", value.name):
        raise ValueError("录音文件位置不合法")
    if value.suffix.lstrip(".") not in EXTENSIONS:
        raise ValueError("不支持的录音格式")
    # 对每一级目录检查链接/Windows junction，避免可写目录跳出私有命名空间。
    boundary = Path(config.DATA_DIR).resolve()
    for item in (Path(config.DATA_DIR), root.parent.parent, root.parent, root, value):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("录音目录不允许符号链接")
    if not value.resolve().is_relative_to(boundary):
        raise ValueError("录音文件越出资料目录")
    if must_exist and not value.is_file():
        raise LookupError("录音文件不存在")
    return value


def recording_path(profile_id, user_id, turn_id, extension):
    get_profile(profile_id, user_id)
    extension = str(extension).lower().lstrip(".")
    if extension not in EXTENSIONS:
        raise ValueError("不支持的录音格式")
    target = _recording_root(profile_id) / f"{_uuid(turn_id)}.{extension}"
    _safe_path(profile_id, target)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for directory in (target.parent.parent.parent, target.parent.parent, target.parent):
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
    return _safe_path(profile_id, target)


def validate_recording_path(profile_id, user_id, path):
    get_profile(profile_id, user_id)
    return _safe_path(profile_id, path, must_exist=True)


def _append(db, profile_id, sid, user_id, role, content, client_turn_id, audio_path="", audio_mime="", turn_id=None):
    session = _session(db, profile_id, sid, user_id)
    if role not in ("human", "assistant"):
        raise ValueError("讲述角色不合法")
    content = text(content, "讲述文字", 30_000)
    client_turn_id = text(client_turn_id, "请求标识", 150)
    prior = db.execute("SELECT * FROM memoir_turns WHERE session_id=? AND role=? AND client_turn_id=?", (sid, role, client_turn_id)).fetchone()
    if prior is not None:
        if prior["text"] != content or prior["audio_path"] != str(audio_path or "") or prior["audio_mime"] != str(audio_mime or ""):
            raise ValueError("该请求标识已对应其他原始讲述，不能覆盖")
        return dict(prior)
    if session["status"] != "live":
        raise ValueError("这场讲述已结束，请新建一场讲述")
    tid = _uuid(turn_id) if turn_id else uuid.uuid4().hex
    if audio_path:
        path = _safe_path(profile_id, audio_path, must_exist=True)
        if role != "human" or path.stem != tid:
            raise ValueError("录音只能关联本人原始讲述")
    audio_mime = text(audio_mime or "", "录音格式", 100, False)
    seq = db.execute("SELECT COALESCE(MAX(seq),-1)+1 FROM memoir_turns WHERE session_id=?", (sid,)).fetchone()[0]
    now = time.time()
    db.execute("INSERT INTO memoir_turns VALUES(?,?,?,?,?,?,?,?,?,?)", (tid, profile_id, sid, seq, role, content, client_turn_id, str(audio_path or ""), audio_mime, now))
    db.execute("UPDATE memoir_profiles SET updated_at=? WHERE id=?", (now, profile_id))
    return dict(db.execute("SELECT * FROM memoir_turns WHERE id=?", (tid,)).fetchone())


def append_turn(profile_id, sid, user_id, role, text, client_turn_id, audio_path="", audio_mime="", *, turn_id=None):
    with main_store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        return _append(db, profile_id, sid, user_id, role, text, client_turn_id, audio_path, audio_mime, turn_id)


def list_turns(profile_id, sid, user_id):
    with main_store.get() as db:
        _session(db, profile_id, sid, user_id)
        return [dict(row) for row in db.execute("SELECT * FROM memoir_turns WHERE session_id=? ORDER BY seq", (sid,))]


def get_turn(profile_id, turn_id, user_id):
    with main_store.get() as db:
        _profile(db, profile_id, user_id)
        row = db.execute("SELECT * FROM memoir_turns WHERE id=? AND profile_id=?", (turn_id, profile_id)).fetchone()
        if row is None:
            raise LookupError("讲述片段不存在或无权访问")
        return dict(row)


def finish_session(profile_id, sid, user_id):
    with main_store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        _session(db, profile_id, sid, user_id)
        db.execute("UPDATE memoir_sessions SET status='done',finished_at=COALESCE(finished_at,?) WHERE id=?", (time.time(), sid))
        return _session(db, profile_id, sid, user_id)


def _source(db, profile_id, user_id, *, enforce_limits=True):
    profile = _profile(db, profile_id, user_id)
    rows = [dict(row) for row in db.execute("SELECT * FROM memoir_turns WHERE profile_id=? AND role='human' ORDER BY created_at,session_id,seq", (profile_id,))]
    if enforce_limits and (len(rows) > MAX_TURNS or sum(len(row["text"]) for row in rows) > MAX_SOURCE_CHARS):
        raise ValueError("讲述原稿超过首版处理上限（20万字或1万段），请拆分人物档案后重试；不会截断原稿")
    body = {"profile": {key: profile[key] for key in ("id", "owner_id", "name", "description")}, "turns": [{"id": row["id"], "text": row["text"]} for row in rows]}
    fingerprint = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return profile, rows, fingerprint


def _decode_event(row):
    result = dict(row)
    result["evidence"] = json.loads(result["evidence"])
    return result


def get_memories(profile_id, user_id):
    with main_store.get() as db:
        _profile(db, profile_id, user_id)
        return [_decode_event(row) for row in db.execute("SELECT * FROM memoir_events WHERE profile_id=? ORDER BY created_at,id", (profile_id,))]


def confirm_memory(profile_id, event_id, user_id):
    with main_store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        _profile(db, profile_id, user_id)
        row = db.execute("SELECT * FROM memoir_events WHERE id=? AND profile_id=?", (event_id, profile_id)).fetchone()
        if row is None:
            raise LookupError("人生线索不存在或无权访问")
        # 原话可能因删除一场讲述消失，确认不能脱离原始依据。
        for evidence in json.loads(row["evidence"]):
            source = db.execute("SELECT text FROM memoir_turns WHERE id=? AND profile_id=? AND role='human'", (evidence["turn_id"], profile_id)).fetchone()
            if source is None or evidence["quote"] not in source["text"]:
                raise ValueError("线索原文已删除，请重新整理后确认")
        db.execute("UPDATE memoir_events SET status='confirmed',confirmed_at=COALESCE(confirmed_at,?) WHERE id=?", (time.time(), event_id))
        return _decode_event(db.execute("SELECT * FROM memoir_events WHERE id=?", (event_id,)).fetchone())


def _decode_chapter(row, fingerprint):
    chapter = dict(row)
    chapter["paragraphs"] = json.loads(chapter["paragraphs"])
    chapter["source_stale"] = chapter["source_fingerprint"] != fingerprint
    lines = ["# " + chapter["title"], "", "资料边界：仅依据本人讲述整理，非独立事实核验；请本人或授权整理者审阅。", ""]
    for paragraph in chapter["paragraphs"]:
        lines.append(paragraph["text"])
        for item in paragraph["evidence"]:
            lines.append(f"\n> 原话依据 [{item['turn_id']}]：{item['quote']}")
        lines.append("")
    chapter["markdown"] = "\n".join(lines)
    return chapter


def list_chapters(profile_id, user_id):
    with main_store.get() as db:
        _, _, fingerprint = _source(db, profile_id, user_id, enforce_limits=False)
        return [_decode_chapter(row, fingerprint) for row in db.execute("SELECT * FROM memoir_chapters WHERE profile_id=? ORDER BY created_at DESC,id", (profile_id,))]


def get_chapter(profile_id, chapter_id, user_id):
    with main_store.get() as db:
        _, _, fingerprint = _source(db, profile_id, user_id, enforce_limits=False)
        row = db.execute("SELECT * FROM memoir_chapters WHERE id=? AND profile_id=?", (chapter_id, profile_id)).fetchone()
        if row is None:
            raise LookupError("章节不存在或无权访问")
        return _decode_chapter(row, fingerprint)


def confirm_chapter(profile_id, chapter_id, user_id):
    with main_store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        _, _, fingerprint = _source(db, profile_id, user_id, enforce_limits=False)
        row = db.execute("SELECT * FROM memoir_chapters WHERE id=? AND profile_id=?", (chapter_id, profile_id)).fetchone()
        if row is None:
            raise LookupError("章节不存在或无权访问")
        if row["source_fingerprint"] != fingerprint or row["status"] not in ("draft", "confirmed"):
            raise ValueError("章节未完成或原稿已更新，请先生成新版本再确认")
        db.execute("UPDATE memoir_chapters SET status='confirmed',confirmed_at=COALESCE(confirmed_at,?) WHERE id=?", (time.time(), chapter_id))
        return _decode_chapter(db.execute("SELECT * FROM memoir_chapters WHERE id=?", (chapter_id,)).fetchone(), fingerprint)


def _cancel_tasks(db, profile_id, user_id):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
        return
    now = time.time()
    # 只影响本领域、同所有者、精确档案资源，绝不触碰旧访谈任务。
    db.execute("""UPDATE tasks SET status=CASE WHEN status IN ('queued','running','retry') THEN 'cancelled' ELSE status END,
      stage=CASE WHEN status IN ('queued','running','retry') THEN 'cancelled' ELSE stage END,
      result=NULL,error='',updated_at=?,finished_at=COALESCE(finished_at,?)
      WHERE kind IN ('memoir_memory','memoir_chapter') AND owner_id=? AND resource_id=?""", (now, now, user_id, profile_id))


def _remove_audio(profile_id, paths):
    deleted = 0
    for value in paths:
        try:
            target = _safe_path(profile_id, value)
            target.unlink(missing_ok=True)
            deleted += 1
        except FileNotFoundError:
            pass
        except (ValueError, OSError):
            # DB 中非本领域安全路径绝不自动删除；返回未清理数量由接口提示。
            continue
    root = _recording_root(profile_id)
    try:
        root.rmdir()
    except OSError:
        pass
    return deleted


def delete_profile(profile_id, user_id):
    with main_store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        _profile(db, profile_id, user_id)
        paths = [row[0] for row in db.execute("SELECT audio_path FROM memoir_turns WHERE profile_id=? AND audio_path!=''", (profile_id,))]
        _cancel_tasks(db, profile_id, user_id)
        for table in ("memoir_events", "memoir_chapters", "memoir_turns", "memoir_sessions"):
            db.execute(f"DELETE FROM {table} WHERE profile_id=?", (profile_id,))
        db.execute("DELETE FROM memoir_profiles WHERE id=? AND owner_id=?", (profile_id, user_id))
    deleted = _remove_audio(profile_id, paths)
    return {"deleted": True, "deleted_audio_files": deleted, "audio_cleanup_pending": max(0, len(paths) - deleted)}


def delete_session(profile_id, sid, user_id):
    with main_store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        _session(db, profile_id, sid, user_id)
        paths = [row[0] for row in db.execute("SELECT audio_path FROM memoir_turns WHERE session_id=? AND audio_path!=''", (sid,))]
        _cancel_tasks(db, profile_id, user_id)
        # 派生物可能跨多场引用，不冒险部分重写：删除派生记忆及章稿，原始其他讲述保留。
        db.execute("DELETE FROM memoir_events WHERE profile_id=?", (profile_id,))
        db.execute("DELETE FROM memoir_chapters WHERE profile_id=?", (profile_id,))
        db.execute("DELETE FROM memoir_turns WHERE session_id=? AND profile_id=?", (sid, profile_id))
        db.execute("DELETE FROM memoir_sessions WHERE id=? AND profile_id=?", (sid, profile_id))
    deleted = _remove_audio(profile_id, paths)
    return {"deleted": True, "deleted_audio_files": deleted, "audio_cleanup_pending": max(0, len(paths) - deleted)}
