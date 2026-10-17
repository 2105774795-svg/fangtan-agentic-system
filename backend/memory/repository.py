"""记忆、访谈状态与运行检查点，均与 conversation_* 共用 SQLite。"""
import hashlib
import json
import re
import time

from fastapi import HTTPException


def init(db):
    statements = (
        """CREATE TABLE IF NOT EXISTS memoir_agent_memory(
            id TEXT PRIMARY KEY,project_id TEXT NOT NULL,category TEXT NOT NULL,title TEXT NOT NULL,
            description TEXT NOT NULL,date_text TEXT NOT NULL,status TEXT NOT NULL,
            evidence TEXT NOT NULL,related_ids TEXT NOT NULL DEFAULT '[]',revision INTEGER NOT NULL DEFAULT 1,
            note TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL,updated_at REAL NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS memoir_memory_project ON memoir_agent_memory(project_id,status,updated_at)",
        """CREATE TABLE IF NOT EXISTS memoir_agent_plans(
            session_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,revision INTEGER NOT NULL,
            plan TEXT NOT NULL,source_seq INTEGER NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS memoir_agent_runs(
            task_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,session_id TEXT NOT NULL,turn_id TEXT NOT NULL,
            checkpoint TEXT NOT NULL DEFAULT '[]',decision TEXT NOT NULL DEFAULT '',
            elapsed_ms INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS memoir_agent_diagnostics(
            id INTEGER PRIMARY KEY,task_id TEXT NOT NULL,project_id TEXT NOT NULL,
            code TEXT NOT NULL,stage TEXT NOT NULL,created_at REAL NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS memoir_diagnostics_task ON memoir_agent_diagnostics(task_id,id)",
        """CREATE TABLE IF NOT EXISTS memoir_memory_versions(
            memory_id TEXT NOT NULL,revision INTEGER NOT NULL,document TEXT NOT NULL,created_at REAL NOT NULL,
            PRIMARY KEY(memory_id,revision))""",
        """CREATE TABLE IF NOT EXISTS memoir_books(
            project_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,document TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL,updated_at REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS memoir_book_versions(
            project_id TEXT NOT NULL,revision INTEGER NOT NULL,document TEXT NOT NULL,created_at REAL NOT NULL,
            PRIMARY KEY(project_id,revision))""",
        """CREATE TABLE IF NOT EXISTS memoir_book_steps(
            project_id TEXT NOT NULL,input_fingerprint TEXT NOT NULL,step TEXT NOT NULL,output TEXT NOT NULL,
            PRIMARY KEY(project_id,input_fingerprint,step))""",
        """CREATE TABLE IF NOT EXISTS memoir_book_settings(
            project_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,document TEXT NOT NULL,updated_at REAL NOT NULL)""",
    )
    for sql in statements:
        db.execute(sql)


def require_project(db, pid):
    row = db.execute("SELECT snapshot FROM research_workspace_runs WHERE project_id=? AND deleted_at=0", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "项目不存在")
    return json.loads(row["snapshot"])


def sources(db, pid, ids):
    require_project(db, pid)
    if not isinstance(ids, list) or not 1 <= len(ids) <= 20 or any(not isinstance(i, str) for i in ids):
        raise ValueError("原话编号无效")
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(f"""SELECT t.id,t.text,t.session_id,t.seq FROM conversation_turns t
        JOIN conversation_sessions s ON s.id=t.session_id WHERE s.project_id=? AND s.kind='memoir'
        AND t.role='human' AND t.control='' AND t.id IN ({placeholders})""", (pid, *ids)).fetchall()
    if len(rows) != len(set(ids)):
        raise ValueError("原话不存在或不属于当前项目")
    return [dict(r) for r in rows]


def validate_evidence(db, pid, evidence):
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 4:
        raise ValueError("记忆必须提供原话依据")
    rows = {r["id"]: r["text"] for r in sources(db, pid, [e.get("turn_id") for e in evidence if isinstance(e, dict)])}
    result = []
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("原话格式无效")
        quote = item.get("quote")
        if not isinstance(quote, str) or not 2 <= len(quote) <= 1600 or quote not in rows.get(item.get("turn_id"), ""):
            raise ValueError("引用必须是本项目本人连续原话")
        result.append({"turn_id": item["turn_id"], "quote": quote})
    return result


def public(row):
    item = dict(row)
    item["evidence"] = json.loads(item["evidence"])
    item["related_ids"] = json.loads(item["related_ids"])
    return item


def memories(db, pid, limit=100):
    require_project(db, pid)
    return [public(r) for r in db.execute("SELECT * FROM memoir_agent_memory WHERE project_id=? ORDER BY updated_at DESC,id LIMIT ?", (pid, limit))]


def save_candidate(db, pid, value):
    require_project(db, pid)
    category = value.get("category", "event")
    title, description = value.get("title"), value.get("description")
    date = value.get("date_text", "未提及")
    if category not in {"event", "person", "place", "thread", "preference"}:
        raise ValueError("记忆类型无效")
    if any(not isinstance(v, str) or not v.strip() or len(v) > size for v, size in ((title, 3000), (description, 3000), (date, 100))):
        raise ValueError("记忆内容无效")
    evidence = validate_evidence(db, pid, value.get("evidence"))
    if date != "未提及" and not any(date in e["quote"] for e in evidence):
        raise ValueError("时间没有原话依据")
    digest = json.dumps([pid, category, title.strip(), evidence], ensure_ascii=False, sort_keys=True)
    mid = hashlib.sha256(digest.encode()).hexdigest()[:32]
    old = db.execute("SELECT * FROM memoir_agent_memory WHERE id=? AND project_id=?", (mid, pid)).fetchone()
    if old:
        return public(old)
    # 相似标题只建立关联，绝不静默覆盖、合并或自动确认旧记忆。
    related = db.execute("SELECT id,date_text FROM memoir_agent_memory WHERE project_id=? AND category=? AND title=? AND status!='dismissed'", (pid, category, title.strip())).fetchall()
    conflict = any(r["date_text"] not in {date, "未提及"} and date != "未提及" for r in related)
    uncertain = conflict or any(re.search("好像|大约|可能|左右|记不清", e["quote"]) for e in evidence)
    now = time.time()
    db.execute("""INSERT INTO memoir_agent_memory(id,project_id,category,title,description,date_text,status,evidence,related_ids,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (mid, pid, category, title.strip(), description.strip(), date,
        "uncertain" if uncertain or value.get("status") == "uncertain" else "candidate",
        json.dumps(evidence, ensure_ascii=False), json.dumps([r["id"] for r in related]), now, now))
    return public(db.execute("SELECT * FROM memoir_agent_memory WHERE id=?", (mid,)).fetchone())


def review(db, pid, mid, revision, action, note="", replaces=None):
    require_project(db, pid)
    row = db.execute("SELECT * FROM memoir_agent_memory WHERE project_id=? AND id=?", (pid, mid)).fetchone()
    if not row:
        raise HTTPException(404, "人生线索不存在")
    if row["revision"] != revision:
        raise HTTPException(409, "线索已更新，请刷新后重试")
    statuses = {"confirm": "confirmed", "dispute": "disputed", "dismiss": "dismissed"}
    if action not in statuses:
        raise HTTPException(422, "不支持的线索操作")
    links = json.loads(row["related_ids"])
    if replaces:
        previous = db.execute("SELECT * FROM memoir_agent_memory WHERE id=? AND project_id=?", (replaces, pid)).fetchone()
        if action != "confirm" or not previous or replaces == mid:
            raise HTTPException(422, "被更正线索无效")
        _version(db, previous)
        db.execute("UPDATE memoir_agent_memory SET status='superseded',revision=revision+1,updated_at=? WHERE id=?", (time.time(), replaces))
        links = list(dict.fromkeys([*links, replaces]))
    _version(db, row)
    db.execute("UPDATE memoir_agent_memory SET status=?,note=?,related_ids=?,revision=revision+1,updated_at=? WHERE id=?", (statuses[action], note, json.dumps(links), time.time(), mid))
    return public(db.execute("SELECT * FROM memoir_agent_memory WHERE id=?", (mid,)).fetchone())


def _version(db, row):
    db.execute("INSERT INTO memoir_memory_versions VALUES(?,?,?,?)", (row["id"], row["revision"], json.dumps(public(row), ensure_ascii=False), time.time()))
