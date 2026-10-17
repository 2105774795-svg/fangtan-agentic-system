"""SQLite 存储：sessions / utterances / suggestions / reports。"""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from config import DB_PATH, DATA_DIR


def init():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions(
            id TEXT PRIMARY KEY,
            subject TEXT, scene TEXT, interviewee_type TEXT,
            industry TEXT DEFAULT '', focus TEXT DEFAULT '',
            outline TEXT,          -- JSON array
            status TEXT DEFAULT 'live',   -- live | done
            created_at REAL, finished_at REAL,
            subject_source TEXT DEFAULT 'default'
        );
        CREATE TABLE IF NOT EXISTS utterances(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT, seq INTEGER,
            start_ms INTEGER, end_ms INTEGER,
            text TEXT, created_at REAL
        );
        CREATE TABLE IF NOT EXISTS suggestions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT, kind TEXT,      -- follow_up | outline
            payload TEXT,                    -- JSON
            status TEXT DEFAULT 'new',       -- new | asked | dismissed
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS reports(
            session_id TEXT PRIMARY KEY,
            markdown TEXT, created_at REAL
        );
        """)
        # 兼容升级：utterances 加 dims 列（维度归档结果）
        cols = [r["name"] for r in db.execute("PRAGMA table_info(utterances)").fetchall()]
        if "dims" not in cols:
            db.execute("ALTER TABLE utterances ADD COLUMN dims TEXT DEFAULT ''")
        # Compatibility migration: add only the title provenance field. Never rebuild
        # the sessions table, so existing sessions and foreign references are kept.
        session_cols = [r["name"] for r in db.execute("PRAGMA table_info(sessions)").fetchall()]
        if "purpose" not in session_cols:
            db.execute("ALTER TABLE sessions ADD COLUMN purpose TEXT NOT NULL DEFAULT 'research_capture'")
        if "subject_source" not in session_cols:
            db.execute("ALTER TABLE sessions ADD COLUMN subject_source TEXT DEFAULT 'default'")
        if "industry" not in session_cols:
            db.execute("ALTER TABLE sessions ADD COLUMN industry TEXT DEFAULT ''")
        if "focus" not in session_cols:
            db.execute("ALTER TABLE sessions ADD COLUMN focus TEXT DEFAULT ''")
        if "location" not in session_cols:
            db.execute("ALTER TABLE sessions ADD COLUMN location TEXT DEFAULT '{}'")
        if "user_name" not in session_cols:
            db.execute("ALTER TABLE sessions ADD COLUMN user_name TEXT DEFAULT ''")
        if "owner_id" not in session_cols:
            db.execute("ALTER TABLE sessions ADD COLUMN owner_id INTEGER")
        if "project_name" not in session_cols:
            db.execute("ALTER TABLE sessions ADD COLUMN project_name TEXT DEFAULT ''")
        if "project_id" not in session_cols:
            db.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT")
        if "audio_path" not in session_cols:
            db.execute("ALTER TABLE sessions ADD COLUMN audio_path TEXT DEFAULT ''")
        if "speaker_consented_at" not in session_cols:
            db.execute("ALTER TABLE sessions ADD COLUMN speaker_consented_at REAL")
        if "speaker_job_id" not in session_cols:
            db.execute("ALTER TABLE sessions ADD COLUMN speaker_job_id TEXT DEFAULT ''")
        upgrading_speaker_results = "speaker_result_job_id" not in session_cols
        if upgrading_speaker_results:
            db.execute("ALTER TABLE sessions ADD COLUMN speaker_result_job_id TEXT DEFAULT ''")
        if "speaker" not in cols:
            db.execute("ALTER TABLE utterances ADD COLUMN speaker TEXT DEFAULT ''")
        db.execute("CREATE INDEX IF NOT EXISTS idx_utterances_session_seq ON utterances(session_id, seq)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status_created ON sessions(status, created_at DESC)")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS auth_sessions(
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, user_name TEXT NOT NULL,
            user_type TEXT NOT NULL, expires_at REAL NOT NULL, checked_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS session_shares(
            session_id TEXT NOT NULL, user_id INTEGER NOT NULL, user_name TEXT NOT NULL,
            created_at REAL NOT NULL, PRIMARY KEY(session_id,user_id)
        );
        CREATE TABLE IF NOT EXISTS session_todos(
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS session_questions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            user_id INTEGER NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS session_share_links(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL, kind TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE, created_by INTEGER NOT NULL,
            created_at REAL NOT NULL, expires_at REAL NOT NULL,
            revoked_at REAL
        );
        CREATE TABLE IF NOT EXISTS speaker_segments(
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            speaker_id INTEGER NOT NULL, start_ms INTEGER NOT NULL,
            end_ms INTEGER NOT NULL, text TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_owner_created ON sessions(owner_id,created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_shares_user ON session_shares(user_id,session_id);
        CREATE INDEX IF NOT EXISTS idx_share_links_session ON session_share_links(session_id,created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_speaker_segments_session ON speaker_segments(session_id,start_ms);
        CREATE TABLE IF NOT EXISTS projects(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_id INTEGER NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL,
            UNIQUE(owner_id,name)
        );
        CREATE TABLE IF NOT EXISTS project_records(
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, owner_id INTEGER NOT NULL,
            title TEXT NOT NULL, sources TEXT NOT NULL, status TEXT NOT NULL,
            stage TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
            markdown TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, finished_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_project_records_project ON project_records(project_id,created_at DESC);
        CREATE TABLE IF NOT EXISTS outline_jobs(
            id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, fingerprint TEXT NOT NULL,
            params TEXT NOT NULL, status TEXT NOT NULL, questions TEXT NOT NULL DEFAULT '[]',
            error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL,
            background TEXT NOT NULL DEFAULT '', background_note TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_outline_jobs_owner ON outline_jobs(owner_id,fingerprint,created_at DESC);
        CREATE TABLE IF NOT EXISTS audio_receipts(
            session_id TEXT NOT NULL, chunk_id TEXT NOT NULL, digest TEXT NOT NULL, seq INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            PRIMARY KEY(session_id,chunk_id)
        );
        """)
        outline_columns = {row["name"] for row in db.execute("PRAGMA table_info(outline_jobs)")}
        for column in ("background", "background_note"):
            if column not in outline_columns:
                db.execute(f"ALTER TABLE outline_jobs ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        for column, definition in (("sources", "TEXT NOT NULL DEFAULT '[]'"), ("claims", "TEXT NOT NULL DEFAULT '[]'"),
                                   ("research_status", "TEXT NOT NULL DEFAULT 'legacy'"),
                                   ("retrieved_at", "TEXT NOT NULL DEFAULT ''")):
            if column not in outline_columns:
                db.execute(f"ALTER TABLE outline_jobs ADD COLUMN {column} {definition}")
        if "status" not in [row["name"] for row in db.execute("PRAGMA table_info(audio_receipts)")]:
            db.execute("ALTER TABLE audio_receipts ADD COLUMN status TEXT DEFAULT 'ready'")
        # Materialize legacy name-only groups without combining different employees.
        legacy = db.execute("""SELECT DISTINCT project_name,COALESCE(owner_id,0) AS owner
            FROM sessions WHERE project_name!='' AND project_id IS NULL""").fetchall()
        for row in legacy:
            project = _ensure_project(db, row["project_name"], row["owner"])
            db.execute("""UPDATE sessions SET project_id=?
                WHERE project_name=? AND COALESCE(owner_id,0)=? AND project_id IS NULL""",
                (project["id"], row["project_name"], row["owner"]))
        if upgrading_speaker_results:
            # Existing completed analyses predate the result-version column. Preserve
            # their association, but do not mark jobs without segments as complete.
            db.execute("""UPDATE sessions SET speaker_result_job_id=speaker_job_id
                WHERE speaker_job_id!='' AND EXISTS (
                    SELECT 1 FROM speaker_segments
                    WHERE speaker_segments.session_id=sessions.id
                )""")


@contextmanager
def get():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    finally:
        db.close()


def create_session(sid, subject, scene, itype, outline, industry="", focus="", user_name="", owner_id=None, location=None):
    with get() as db:
        db.execute(
            """INSERT INTO sessions(
                id,subject,scene,interviewee_type,industry,focus,user_name,outline,
                status,created_at,finished_at,subject_source,owner_id,location
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, subject, scene, itype, industry, focus, user_name,
             json.dumps(outline, ensure_ascii=False), "live", time.time(), None,
             "default" if subject == "未命名访谈" else "user", owner_id,
             json.dumps(location or {}, ensure_ascii=False)))


def list_sessions(limit=100, offset=0, user_id=None, search="", project="", scope="all", project_id=""):
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    with get() as db:
        filters = ["s.status != 'deleting'", "s.purpose != 'interview_answer'"]
        params = []
        if user_id is not None:
            if scope == "mine":
                filters.append("s.owner_id=?")
            elif scope == "shared":
                filters.append("s.owner_id!=? AND EXISTS(SELECT 1 FROM session_shares sh WHERE sh.session_id=s.id AND sh.user_id=?)")
                params.append(user_id)
            else:
                filters.append("(s.owner_id=? OR EXISTS(SELECT 1 FROM session_shares sh WHERE sh.session_id=s.id AND sh.user_id=?))")
                params.append(user_id)
            params.insert(0, user_id)
        if project:
            filters.append("s.project_name=?")
            params.append(project)
        if project_id:
            filters.append("s.project_id=?")
            params.append(project_id)
        if search:
            filters.append("(s.subject LIKE ? OR s.project_name LIKE ? OR EXISTS(SELECT 1 FROM utterances u WHERE u.session_id=s.id AND u.text LIKE ?))")
            params.extend([f"%{search}%"] * 3)
        where = " AND ".join(filters)
        total = db.execute(f"SELECT COUNT(*) FROM sessions s WHERE {where}", params).fetchone()[0]
        rows = db.execute(f"""
            SELECT s.id,s.subject,s.subject_source,s.interviewee_type,s.industry,s.status,s.created_at,
                   s.project_name,s.project_id,s.owner_id,s.user_name,
                   (SELECT COUNT(*) FROM utterances u WHERE u.session_id=s.id) AS utterance_count,
                   CASE WHEN r.session_id IS NULL THEN 0 ELSE 1 END AS has_report
            FROM sessions s LEFT JOIN reports r ON r.session_id=s.id
            WHERE {where}
            ORDER BY CASE WHEN s.status='live' THEN 0 ELSE 1 END, s.created_at DESC
            LIMIT ? OFFSET ?
        """, (*params, limit, offset)).fetchall()
    return {"items": [dict(r) for r in rows], "total": total}


def get_session_summary(sid):
    with get() as db:
        r = db.execute("""
            SELECT s.*, (SELECT COUNT(*) FROM utterances u WHERE u.session_id=s.id) AS utterance_count,
                   CASE WHEN r.session_id IS NULL THEN 0 ELSE 1 END AS has_report,
                   r.markdown AS report_markdown
            FROM sessions s LEFT JOIN reports r ON r.session_id=s.id WHERE s.id=?
        """, (sid,)).fetchone()
    return dict(r) if r else None


def get_next_utterance_seq(sid):
    with get() as db:
        row = db.execute("SELECT COALESCE(MAX(seq), -1) + 1 AS next_seq FROM utterances WHERE session_id=?", (sid,)).fetchone()
    return row["next_seq"]


def rename_session(sid, subject, source="user"):
    with get() as db:
        cur = db.execute("UPDATE sessions SET subject=?, subject_source=? WHERE id=?",
                         (subject, source, sid))
    return cur.rowcount > 0


def try_set_auto_subject(sid, subject):
    with get() as db:
        cur = db.execute("""UPDATE sessions SET subject=?, subject_source='auto'
            WHERE id=? AND subject_source='default' AND subject='未命名访谈'""", (subject, sid))
    return cur.rowcount > 0


def delete_session_records(sid):
    with get() as db:
        import coverage_ai
        coverage_ai.clear(db, sid)
        db.execute("DELETE FROM audio_receipts WHERE session_id=?", (sid,))
        _invalidate_project_records(db, sid, "来源访谈已删除，请重新选择访谈生成")
        db.execute("DELETE FROM speaker_segments WHERE session_id=?", (sid,))
        db.execute("DELETE FROM session_share_links WHERE session_id=?", (sid,))
        db.execute("DELETE FROM session_shares WHERE session_id=?", (sid,))
        db.execute("DELETE FROM session_todos WHERE session_id=?", (sid,))
        db.execute("DELETE FROM session_questions WHERE session_id=?", (sid,))
        db.execute("DELETE FROM suggestions WHERE session_id=?", (sid,))
        db.execute("DELETE FROM utterances WHERE session_id=?", (sid,))
        db.execute("DELETE FROM reports WHERE session_id=?", (sid,))
        cur = db.execute("DELETE FROM sessions WHERE id=?", (sid,))
    return cur.rowcount > 0


def mark_session_deleting(sid):
    with get() as db:
        cur = db.execute("UPDATE sessions SET status='deleting' WHERE id=?", (sid,))
    return cur.rowcount > 0


def get_report_status(sid, transient_status=None):
    if transient_status in ("generating", "failed"):
        return transient_status
    with get() as db:
        row = db.execute("SELECT 1 FROM reports WHERE session_id=?", (sid,)).fetchone()
    return "ready" if row else "none"


def auto_naming_attempts(sid):
    """Persist attempt markers in the existing suggestions table (no schema expansion)."""
    with get() as db:
        return db.execute("SELECT COUNT(*) FROM suggestions WHERE session_id=? AND kind='auto_naming'", (sid,)).fetchone()[0]


def add_auto_naming_attempt(sid, phase):
    with get() as db:
        cur = db.execute("INSERT INTO suggestions(session_id,kind,payload,status,created_at) VALUES(?,?,?,?,?)",
                         (sid, "auto_naming", json.dumps({"phase": phase}), "new", time.time()))
        return cur.lastrowid


def set_auto_naming_attempt_status(attempt_id, status):
    with get() as db:
        db.execute("UPDATE suggestions SET status=? WHERE id=? AND kind='auto_naming'", (status, attempt_id))


def add_utterance(sid, seq, start_ms, end_ms, text, dims="", speaker=""):
    with get() as db:
        db.execute(
            "INSERT INTO utterances(session_id,seq,start_ms,end_ms,text,created_at,dims,speaker) VALUES(?,?,?,?,?,?,?,?)",
            (sid, seq, start_ms, end_ms, text, time.time(), dims, speaker))


def list_utterances(sid, last_n=50):
    with get() as db:
        rows = db.execute(
            "SELECT * FROM utterances WHERE session_id=? ORDER BY seq DESC LIMIT ?",
            (sid, last_n)).fetchall()
    return [dict(r) for r in reversed(rows)]


def list_export_utterances(sid):
    """导出必须覆盖全部内容；实时页面仍使用受限的近期窗口。"""
    with get() as db:
        rows = db.execute("SELECT * FROM utterances WHERE session_id=? ORDER BY seq,id", (sid,)).fetchall()
    return [dict(row) for row in rows]


def add_suggestion(sid, kind, payload):
    with get() as db:
        cur = db.execute(
            "INSERT INTO suggestions(session_id,kind,payload,status,created_at) VALUES(?,?,?,?,?)",
            (sid, kind, json.dumps(payload, ensure_ascii=False), "new", time.time()))
        return cur.lastrowid


def set_suggestion_status(sug_id, status):
    with get() as db:
        cur = db.execute("UPDATE suggestions SET status=? WHERE id=?", (status, sug_id))
    return cur.rowcount > 0


def list_suggestions(sid):
    with get() as db:
        rows = db.execute(
            "SELECT * FROM suggestions WHERE session_id=? AND kind!='import_archive' ORDER BY id", (sid,)).fetchall()
    return [dict(r) for r in rows]


def recent_suggestion_time(sid):
    with get() as db:
        r = db.execute(
            "SELECT MAX(created_at) t FROM suggestions WHERE session_id=? AND kind='follow_up'",
            (sid,)).fetchone()
    return r["t"] or 0


def finish_session(sid):
    with get() as db:
        db.execute("UPDATE sessions SET status='done', finished_at=COALESCE(finished_at, ?) WHERE id=?",
                   (time.time(), sid))


def save_report(sid, md):
    with get() as db:
        db.execute("INSERT OR REPLACE INTO reports VALUES(?,?,?)",
                   (sid, md, time.time()))


def get_report(sid):
    with get() as db:
        r = db.execute("SELECT markdown FROM reports WHERE session_id=?", (sid,)).fetchone()
    return r["markdown"] if r else None


def claim_legacy_sessions(user_id, user_name):
    """Only match a verified employee's exact name; anonymous old records stay private."""
    with get() as db:
        legacy = db.execute("""SELECT id,project_name FROM sessions
            WHERE owner_id IS NULL AND user_name=? AND user_name!=''""", (user_name,)).fetchall()
        for row in legacy:
            if row["project_name"]:
                project = _ensure_project(db, row["project_name"], user_id)
                db.execute("UPDATE sessions SET project_id=? WHERE id=?", (project["id"], row["id"]))
        db.execute("UPDATE sessions SET owner_id=? WHERE owner_id IS NULL AND user_name=? AND user_name!=''",
                   (user_id, user_name))


def session_access(sid, user_id):
    with get() as db:
        row = db.execute("""SELECT s.owner_id,
            EXISTS(SELECT 1 FROM session_shares sh WHERE sh.session_id=s.id AND sh.user_id=?) AS shared
            FROM sessions s WHERE s.id=? AND s.status!='deleting'""", (user_id, sid)).fetchone()
    if not row:
        return None
    return "owner" if row["owner_id"] == user_id else "shared" if row["shared"] else None


def save_auth_session(token_hash, user):
    now = time.time()
    with get() as db:
        db.execute("DELETE FROM auth_sessions WHERE expires_at<?", (now,))
        db.execute("INSERT INTO auth_sessions VALUES(?,?,?,?,?,?)",
                   (token_hash, int(user["id"]), user["name"], user.get("type", "正式员工"), now + 7*86400, now))


def get_auth_session(token_hash):
    with get() as db:
        row = db.execute("SELECT * FROM auth_sessions WHERE token_hash=? AND expires_at>?",
                         (token_hash, time.time())).fetchone()
    return dict(row) if row else None


def touch_auth_session(token_hash):
    with get() as db:
        db.execute("UPDATE auth_sessions SET checked_at=? WHERE token_hash=?", (time.time(), token_hash))


def delete_auth_session(token_hash):
    with get() as db:
        db.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))


def update_session_project(sid, name):
    with get() as db:
        row = db.execute("SELECT owner_id FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row:
            return None
        project = _ensure_project(db, name, row["owner_id"] or 0) if name else None
        db.execute("UPDATE sessions SET project_name=?,project_id=? WHERE id=?",
                   (name, project["id"] if project else None, sid))
    return project


def _ensure_project(db, name, owner_id):
    now = time.time()
    db.execute("INSERT OR IGNORE INTO projects VALUES(?,?,?,?,?)",
               (uuid.uuid4().hex[:16], name, owner_id, now, now))
    return dict(db.execute("SELECT * FROM projects WHERE owner_id=? AND name=?", (owner_id, name)).fetchone())


def create_project(name, owner_id):
    with get() as db:
        return _ensure_project(db, name, owner_id)


def get_project(project_id):
    with get() as db:
        row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return dict(row) if row else None


def list_projects(user_id=None):
    """Include empty owned folders, and only visible sessions in shared folders."""
    with get() as db:
        visible = "s.status!='deleting'"
        params = []
        if user_id is not None:
            visible += " AND (s.owner_id=? OR EXISTS(SELECT 1 FROM session_shares sh WHERE sh.session_id=s.id AND sh.user_id=?))"
            params.extend([user_id, user_id])
        owned = "1" if user_id is None else "p.owner_id=?"
        mine = "1" if user_id is None else "v.owner_id=?"
        if user_id is not None:
            params.extend([user_id, user_id, user_id])  # two mine counts and owned folders
        rows = db.execute(f"""WITH visible AS (SELECT s.* FROM sessions s WHERE {visible})
            SELECT p.*,p.name AS project_name,COUNT(v.id) AS interviews,
                COALESCE(SUM(CASE WHEN v.id IS NOT NULL AND ({mine}) THEN 1 ELSE 0 END),0) AS mine,
                COUNT(v.id)-COALESCE(SUM(CASE WHEN v.id IS NOT NULL AND ({mine}) THEN 1 ELSE 0 END),0) AS shared,
                SUM(CASE WHEN r.session_id IS NOT NULL THEN 1 ELSE 0 END) AS reports,
                SUM(CASE WHEN v.audio_path!='' THEN 1 ELSE 0 END) AS recordings,
                COALESCE(SUM((SELECT COUNT(*) FROM session_todos t WHERE t.session_id=v.id AND t.done=0)),0) AS open_todos
            FROM projects p LEFT JOIN visible v ON v.project_id=p.id
            LEFT JOIN reports r ON r.session_id=v.id
            GROUP BY p.id HAVING ({owned}) OR COUNT(v.id)>0
            ORDER BY p.updated_at DESC,p.name""",
            params).fetchall()
    return [{**dict(row), "access": "owner" if user_id is None or row["owner_id"] == user_id else "shared"}
            for row in rows]


def rename_project(project_id, name):
    with get() as db:
        db.execute("UPDATE projects SET name=?,updated_at=? WHERE id=?", (name, time.time(), project_id))
        db.execute("UPDATE sessions SET project_name=? WHERE project_id=?", (name, project_id))


def assign_session_project(sid, project_id):
    with get() as db:
        project = db.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone() if project_id else None
        db.execute("UPDATE sessions SET project_id=?,project_name=? WHERE id=?",
                   (project_id, project["name"] if project else "", sid))


def create_project_record(record_id, project_id, owner_id, title, sources):
    with get() as db:
        db.execute("""INSERT INTO project_records(id,project_id,owner_id,title,sources,status,stage,created_at)
            VALUES(?,?,?,?,?,'generating','正在读取访谈材料',?)""",
            (record_id, project_id, owner_id, title, json.dumps(sources, ensure_ascii=False), time.time()))


def get_project_record(record_id):
    with get() as db:
        row = db.execute("SELECT * FROM project_records WHERE id=?", (record_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["sources"] = json.loads(result["sources"])
    return result


def list_project_records(project_id):
    with get() as db:
        rows = db.execute("""SELECT id,title,status,stage,error,created_at,finished_at,sources
            FROM project_records WHERE project_id=? ORDER BY created_at DESC LIMIT 100""", (project_id,)).fetchall()
    return [{**dict(row), "sources": json.loads(row["sources"])} for row in rows]


def update_project_record(record_id, status, stage, markdown="", error=""):
    with get() as db:
        db.execute("""UPDATE project_records SET status=?,stage=?,markdown=?,error=?,finished_at=?
            WHERE id=?""", (status, stage, markdown, error, None if status == "generating" else time.time(), record_id))


def publish_project_record(record_id, markdown, verify_sources):
    # Hold the SQLite write reservation while rechecking and publishing, so a
    # simultaneous source deletion cannot resurrect already-cleared derived text.
    with get() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status FROM project_records WHERE id=?", (record_id,)).fetchone()
        if not row or row["status"] != "generating":
            raise ValueError("来源访谈已失效，请重新生成")
        verify_sources()
        db.execute("""UPDATE project_records SET status='ready',stage='总访谈记录已生成',
            markdown=?,error='',finished_at=? WHERE id=?""", (markdown, time.time(), record_id))


def recover_project_records():
    with get() as db:
        db.execute("""UPDATE project_records SET status='failed',stage='生成中断',
            error='服务重启导致生成中断，请重新勾选访谈生成',finished_at=? WHERE status='generating'""", (time.time(),))


def recover_outline_jobs():
    with get() as db:
        db.execute("UPDATE outline_jobs SET status='failed',error='服务重启，点击重试即可继续',updated_at=? WHERE status='generating'", (time.time(),))


def find_outline_job(owner_id, fingerprint):
    with get() as db:
        row = db.execute("""SELECT * FROM outline_jobs WHERE owner_id=? AND fingerprint=?
            AND status IN ('generating','ready') AND created_at>? ORDER BY created_at DESC LIMIT 1""",
            (owner_id, fingerprint, time.time() - 86400)).fetchone()
    return dict(row) if row else None


def create_outline_job(job_id, owner_id, fingerprint, params):
    with get() as db:
        db.execute("DELETE FROM outline_jobs WHERE updated_at<? AND status!='generating'", (time.time() - 7 * 86400,))
        db.execute("""INSERT INTO outline_jobs(id,owner_id,fingerprint,params,status,questions,error,created_at,updated_at)
            VALUES(?,?,?,?,?,'[]','',?,?)""", (job_id, owner_id, fingerprint,
                   json.dumps(params, ensure_ascii=False), "generating", time.time(), time.time()))


def get_outline_job(job_id):
    with get() as db:
        row = db.execute("SELECT * FROM outline_jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def finish_outline_job(job_id, questions=None, error="", *, background="", background_note="", sources=None,
                       research_status="legacy", retrieved_at="", claims=None):
    with get() as db:
        db.execute("""UPDATE outline_jobs SET status=?,questions=?,background=?,background_note=?,sources=?,claims=?,research_status=?,retrieved_at=?,error=?,updated_at=?
            WHERE id=?""", ("failed" if error else "ready", json.dumps(questions or [], ensure_ascii=False),
                             background, background_note, json.dumps(sources or [], ensure_ascii=False),
                             json.dumps(claims or [], ensure_ascii=False), research_status, retrieved_at, error, time.time(), job_id))


def get_audio_receipt(sid, chunk_id):
    with get() as db:
        row = db.execute("SELECT seq,digest,status FROM audio_receipts WHERE session_id=? AND chunk_id=?", (sid, chunk_id)).fetchone()
    return dict(row) if row else None


def save_audio_receipt(sid, chunk_id, digest, seq):
    with get() as db:
        db.execute("INSERT OR IGNORE INTO audio_receipts(session_id,chunk_id,digest,seq,status) VALUES(?,?,?,?,'pending')", (sid, chunk_id, digest, seq))


def complete_audio_receipt(sid, chunk_id):
    with get() as db:
        db.execute("UPDATE audio_receipts SET status='ready' WHERE session_id=? AND chunk_id=?", (sid, chunk_id))


def _invalidate_project_records(db, sid, reason):
    """Deletion also clears derived text, not only the original transcript."""
    rows = db.execute("SELECT id,sources FROM project_records").fetchall()
    for row in rows:
        if any(source["id"] == sid for source in json.loads(row["sources"])):
            db.execute("""UPDATE project_records SET status='failed',stage='来源失效',
                markdown='',error=?,finished_at=? WHERE id=?""", (reason, time.time(), row["id"]))


def set_audio_path(sid, path):
    with get() as db:
        db.execute("UPDATE sessions SET audio_path=? WHERE id=?", (path, sid))


def get_utterance(sid, seq):
    with get() as db:
        row = db.execute("SELECT * FROM utterances WHERE session_id=? AND seq=?", (sid, seq)).fetchone()
    return dict(row) if row else None


def delete_recording_metadata(sid):
    """清除原始录音引用及依赖录音的分享/发言人分析结果。"""
    with get() as db:
        db.execute("DELETE FROM session_share_links WHERE session_id=? AND kind='audio'", (sid,))
        db.execute("DELETE FROM speaker_segments WHERE session_id=?", (sid,))
        cur = db.execute("""UPDATE sessions SET audio_path='',speaker_consented_at=NULL,
            speaker_job_id='',speaker_result_job_id='' WHERE id=?""", (sid,))
    return cur.rowcount > 0


def delete_utterance_and_shift(sid, seq, start_ms, end_ms, removed_ms):
    """删除一段转写并同步缩短后续时间轴；报告需重新生成。"""
    with get() as db:
        row = db.execute("SELECT 1 FROM utterances WHERE session_id=? AND seq=?", (sid, seq)).fetchone()
        if not row:
            return False
        import coverage_ai
        coverage_ai.clear(db, sid)
        _invalidate_project_records(db, sid, "来源逐字稿片段已删除，请重新生成总访谈记录")
        db.execute("DELETE FROM utterances WHERE session_id=? AND seq=?", (sid, seq))
        db.execute("""UPDATE utterances SET start_ms=MAX(0,start_ms-?), end_ms=MAX(0,end_ms-?)
            WHERE session_id=? AND start_ms>=?""", (removed_ms, removed_ms, sid, end_ms))
        db.execute("DELETE FROM speaker_segments WHERE session_id=?", (sid,))
        db.execute("DELETE FROM session_share_links WHERE session_id=? AND kind='audio'", (sid,))
        db.execute("DELETE FROM reports WHERE session_id=?", (sid,))
    return True


def list_shares(sid):
    with get() as db:
        rows = db.execute("SELECT user_id,user_name,created_at FROM session_shares WHERE session_id=? ORDER BY created_at",
                          (sid,)).fetchall()
    return [dict(row) for row in rows]


def add_share(sid, user_id, user_name):
    with get() as db:
        db.execute("INSERT OR REPLACE INTO session_shares VALUES(?,?,?,?)",
                   (sid, user_id, user_name, time.time()))


def remove_share(sid, user_id):
    with get() as db:
        db.execute("DELETE FROM session_shares WHERE session_id=? AND user_id=?", (sid, user_id))


def create_share_link(sid, kind, token_hash, user_id, expires_at):
    with get() as db:
        cur = db.execute("""INSERT INTO session_share_links
            (session_id,kind,token_hash,created_by,created_at,expires_at)
            VALUES(?,?,?,?,?,?)""", (sid, kind, token_hash, user_id, time.time(), expires_at))
    return cur.lastrowid


def list_share_links(sid):
    with get() as db:
        rows = db.execute("""SELECT id,kind,created_at,expires_at,revoked_at
            FROM session_share_links WHERE session_id=? ORDER BY created_at DESC""", (sid,)).fetchall()
    return [dict(row) for row in rows]


def get_share_link(token_hash):
    with get() as db:
        row = db.execute("""SELECT id,session_id,kind,expires_at FROM session_share_links
            WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?""",
            (token_hash, time.time())).fetchone()
    return dict(row) if row else None


def revoke_share_link(sid, link_id):
    with get() as db:
        cur = db.execute("""UPDATE session_share_links SET revoked_at=?
            WHERE id=? AND session_id=? AND revoked_at IS NULL""", (time.time(), link_id, sid))
    return cur.rowcount > 0


def start_speaker_analysis(sid, job_id):
    with get() as db:
        cur = db.execute("""UPDATE sessions SET speaker_consented_at=?,speaker_job_id=?
            WHERE id=? AND status='done'""", (time.time(), job_id, sid))
    return cur.rowcount > 0


def save_speaker_segments(sid, job_id, segments):
    with get() as db:
        row = db.execute("""SELECT speaker_consented_at,speaker_job_id FROM sessions
            WHERE id=? AND status='done'""", (sid,)).fetchone()
        if not row or not row["speaker_consented_at"] or row["speaker_job_id"] != job_id:
            return False
        db.execute("DELETE FROM speaker_segments WHERE session_id=?", (sid,))
        db.executemany("""INSERT INTO speaker_segments(session_id,speaker_id,start_ms,end_ms,text)
            VALUES(?,?,?,?,?)""", [(sid, item["speaker_id"], item["start_ms"], item["end_ms"], item["text"])
                            for item in segments])
        db.execute("UPDATE sessions SET speaker_result_job_id=? WHERE id=?", (job_id, sid))
    return True


def list_speaker_segments(sid):
    with get() as db:
        rows = db.execute("""SELECT speaker_id,start_ms,end_ms,text FROM speaker_segments
            WHERE session_id=? ORDER BY start_ms,id""", (sid,)).fetchall()
    return [dict(row) for row in rows]


def clear_speaker_analysis(sid):
    with get() as db:
        db.execute("DELETE FROM speaker_segments WHERE session_id=?", (sid,))
        cur = db.execute("""UPDATE sessions SET speaker_consented_at=NULL,speaker_job_id='',speaker_result_job_id=''
            WHERE id=?""", (sid,))
    return cur.rowcount > 0


def list_todos(sid):
    with get() as db:
        rows = db.execute("SELECT * FROM session_todos WHERE session_id=? ORDER BY id", (sid,)).fetchall()
    return [dict(row) for row in rows]


def add_todo(sid, text):
    with get() as db:
        cur = db.execute("INSERT INTO session_todos(session_id,text,created_at) VALUES(?,?,?)",
                         (sid, text, time.time()))
    return cur.lastrowid


def set_todo_done(sid, todo_id, done):
    with get() as db:
        cur = db.execute("UPDATE session_todos SET done=? WHERE id=? AND session_id=?",
                         (int(done), todo_id, sid))
    return cur.rowcount > 0


def delete_todo(sid, todo_id):
    with get() as db:
        cur = db.execute("DELETE FROM session_todos WHERE id=? AND session_id=?", (todo_id, sid))
    return cur.rowcount > 0


def set_speaker(sid, seq, speaker):
    with get() as db:
        cur = db.execute("UPDATE utterances SET speaker=? WHERE session_id=? AND seq=?",
                         (speaker, sid, seq))
    return cur.rowcount > 0


def add_question(sid, user_id, question, answer):
    with get() as db:
        db.execute("INSERT INTO session_questions(session_id,user_id,question,answer,created_at) VALUES(?,?,?,?,?)",
                   (sid, user_id, question, answer, time.time()))


def list_questions(sid):
    with get() as db:
        rows = db.execute("SELECT * FROM session_questions WHERE session_id=? ORDER BY id", (sid,)).fetchall()
    return [dict(row) for row in rows]
