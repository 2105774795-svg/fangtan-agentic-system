"""基于 SQLite 的持久任务队列，不在网页进程内执行耗时工作。

每次领取都生成独立租约令牌。更新必须同时匹配令牌和有效期，防止
失联的旧 worker 在任务被重新领取后覆盖新结果。取消是合作式的，不能
撤回已经发送给云端的请求；业务处理器也应在写入业务结果前检查取消。
"""
import json
import math
import time
import uuid
from contextlib import contextmanager

import store


STATUSES = frozenset({"queued", "running", "retry", "ready", "failed", "cancelled"})
TERMINAL = frozenset({"ready", "failed", "cancelled"})


class TaskUnavailable(RuntimeError):
    """取消或租约失效，当前处理器必须丢弃迟到结果。"""


class TaskCancelled(TaskUnavailable):
    pass


class LeaseLost(TaskUnavailable):
    pass


def init(db=None):
    """仅增加任务表及索引，不修改已有访谈数据。"""
    if db is None:
        with store.get() as connection:
            init(connection)
        return
    # 不使用 executescript：它会隐式提交调用者已有的业务事务。
    statements = """
        CREATE TABLE IF NOT EXISTS tasks(
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_id INTEGER NOT NULL,
            resource_id TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL,
            dedupe_key TEXT, status TEXT NOT NULL DEFAULT 'queued',
            stage TEXT NOT NULL DEFAULT 'queued', progress REAL NOT NULL DEFAULT 0,
            result TEXT, error TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
            available_at REAL NOT NULL, lease_owner TEXT, lease_until REAL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL, finished_at REAL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_dedupe
            ON tasks(kind,owner_id,dedupe_key) WHERE dedupe_key IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_tasks_claim ON tasks(status,available_at,created_at);
        CREATE INDEX IF NOT EXISTS idx_tasks_resource ON tasks(resource_id,created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_id,created_at DESC);
        """
    for statement in statements.split(";"):
        if statement.strip():
            db.execute(statement)


@contextmanager
def _connection():
    # 兼容迁移、测试临时 DB 和从未启动过 API 的独立 worker。
    with store.get() as db:
        init(db)
        yield db


def _decode(row):
    if row is None:
        return None
    value = dict(row)
    value["payload"] = json.loads(value["payload"])
    value["result"] = json.loads(value["result"]) if value["result"] is not None else None
    return value


def _json(value):
    # bytes（例如原始录音）不能进入队列；这里只保存 JSON 参数、文件引用及结果。
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _text(value, name, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"{name} 不合法")
    return value


def _finite(value, name):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} 必须是有限数值")
    return result


def enqueue(kind, payload, *, owner_id=0, resource_id="", dedupe_key=None,
            max_attempts=3, available_at=None, task_id=None):
    """幂等提交，返回任务及 created 标志。

    非空 dedupe_key 在同一种类、同一所有者范围内唯一，终态也复用。
    用户明确再次执行时，应使用新的业务版本号或新的幂等键。
    """
    kind = _text(kind, "任务类型", 80, True)
    resource_id = _text(resource_id, "资源标识", 200)
    if dedupe_key is not None:
        dedupe_key = _text(dedupe_key, "幂等键", 256, True)
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 100:
        raise ValueError("最大尝试次数应为 1 至 100")
    owner_id = int(owner_id or 0)
    encoded = _json(payload)
    now = time.time()
    available = now if available_at is None else _finite(available_at, "可执行时间")
    task_id = _text(task_id, "任务标识", 200, True) if task_id else uuid.uuid4().hex
    with _connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = None
        if dedupe_key is not None:
            row = db.execute("SELECT * FROM tasks WHERE kind=? AND owner_id=? AND dedupe_key=?",
                             (kind, owner_id, dedupe_key)).fetchone()
        if row is None:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row and (row["kind"] != kind or row["owner_id"] != owner_id
                        or row["resource_id"] != resource_id or row["payload"] != encoded):
                raise ValueError("任务标识已被其他请求使用")
        created = row is None
        if created:
            db.execute("""INSERT INTO tasks(
                id,kind,owner_id,resource_id,payload,dedupe_key,max_attempts,
                available_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (task_id, kind, owner_id, resource_id, encoded, dedupe_key, max_attempts,
                 available, now, now))
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        result = _decode(row)
        result["created"] = created
        return result


def get(task_id):
    with _connection() as db:
        return _decode(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())


def find(*, kind=None, owner_id=None, resource_id=None, statuses=None, limit=100):
    conditions, args = [], []
    for name, value in (("kind", kind), ("owner_id", owner_id), ("resource_id", resource_id)):
        if value is not None:
            conditions.append(name + "=?")
            args.append(value)
    if statuses is not None:
        statuses = list(statuses)
        if not statuses:
            return []
        if not set(statuses) <= STATUSES:
            raise ValueError("任务状态不合法")
        conditions.append("status IN (" + ",".join("?" for _ in statuses) + ")")
        args.extend(statuses)
    limit = max(1, min(int(limit), 1000))
    sql = "SELECT * FROM tasks"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    with _connection() as db:
        return [_decode(row) for row in db.execute(sql + " ORDER BY created_at DESC,id DESC LIMIT ?",
                                                   (*args, limit)).fetchall()]


def _recover_expired(db, now):
    failed = db.execute("""UPDATE tasks SET status='failed',stage='failed',
        error='处理进程中断或租约过期，尝试次数已耗尽',lease_owner=NULL,lease_until=NULL,
        updated_at=?,finished_at=? WHERE status='running' AND lease_until<=?
        AND attempts>=max_attempts""", (now, now, now)).rowcount
    retried = db.execute("""UPDATE tasks SET status='retry',stage='retry',
        error='处理进程中断或租约过期，等待重新执行',lease_owner=NULL,lease_until=NULL,
        available_at=?,updated_at=? WHERE status='running' AND lease_until<=?
        AND attempts<max_attempts""", (now, now, now)).rowcount
    return failed + retried


def recover_expired():
    """回收崩溃 worker 的过期租约；有效租约不会被 API 重启影响。"""
    with _connection() as db:
        db.execute("BEGIN IMMEDIATE")
        return _recover_expired(db, time.time())


def claim(worker_id, *, lease_seconds=60, kinds=None):
    """原子领取一个任务，返回独立 fencing token（lease_owner）。"""
    worker_id = _text(worker_id, "处理进程标识", 120, True)
    seconds = _finite(lease_seconds, "租约时长")
    if seconds <= 0:
        raise ValueError("租约时长必须大于 0")
    selected_kinds = list(kinds) if kinds is not None else None
    if selected_kinds == []:
        return None
    now = time.time()
    with _connection() as db:
        db.execute("BEGIN IMMEDIATE")
        _recover_expired(db, now)
        sql = "SELECT id FROM tasks WHERE status IN ('queued','retry') AND available_at<=? AND attempts<max_attempts"
        args = [now]
        if selected_kinds is not None:
            sql += " AND kind IN (" + ",".join("?" for _ in selected_kinds) + ")"
            args.extend(selected_kinds)
        row = db.execute(sql + " ORDER BY available_at,created_at,id LIMIT 1", args).fetchone()
        if row is None:
            return None
        token = worker_id + ":" + uuid.uuid4().hex
        db.execute("""UPDATE tasks SET status='running',stage='running',progress=0,attempts=attempts+1,
            lease_owner=?,lease_until=?,updated_at=?,finished_at=NULL WHERE id=?""",
            (token, now + seconds, now, row["id"]))
        return _decode(db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())


def heartbeat(task_id, lease_owner, *, lease_seconds=60):
    seconds = _finite(lease_seconds, "租约时长")
    if seconds <= 0:
        raise ValueError("租约时长必须大于 0")
    now = time.time()
    with _connection() as db:
        return bool(db.execute("""UPDATE tasks SET lease_until=?,updated_at=? WHERE id=?
            AND status='running' AND lease_owner=? AND lease_until>?""",
            (now + seconds, now, task_id, lease_owner, now)).rowcount)


def progress(task_id, lease_owner, *, stage="", progress=None):
    stage = _text(stage, "处理阶段", 120)
    value = None if progress is None else _finite(progress, "任务进度")
    if value is not None and not 0 <= value <= 1:
        raise ValueError("任务进度应在 0 至 1 之间")
    now = time.time()
    with _connection() as db:
        return bool(db.execute("""UPDATE tasks SET stage=?,progress=COALESCE(?,progress),
            updated_at=? WHERE id=? AND status='running' AND lease_owner=? AND lease_until>?""",
            (stage, value, now, task_id, lease_owner, now)).rowcount)


def complete(task_id, lease_owner, result=None):
    encoded = _json(result)
    now = time.time()
    with _connection() as db:
        return bool(db.execute("""UPDATE tasks SET status='ready',stage='ready',progress=1,
            result=?,error='',lease_owner=NULL,lease_until=NULL,updated_at=?,finished_at=?
            WHERE id=? AND status='running' AND lease_owner=? AND lease_until>?""",
            (encoded, now, now, task_id, lease_owner, now)).rowcount)


def fail(task_id, lease_owner, error, *, retryable=True, retry_delay=None):
    """指数退避（5 秒起、最多 5 分钟），耗尽尝试次数后终止。"""
    message = str(error)[:4000]
    now = time.time()
    with _connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""SELECT * FROM tasks WHERE id=? AND status='running'
            AND lease_owner=? AND lease_until>?""", (task_id, lease_owner, now)).fetchone()
        if row is None:
            return False
        retry = bool(retryable and row["attempts"] < row["max_attempts"])
        delay = min(300, 5 * (2 ** min(row["attempts"] - 1, 8))) if retry_delay is None else _finite(retry_delay, "重试间隔")
        if delay < 0:
            raise ValueError("重试间隔不得小于 0")
        status = "retry" if retry else "failed"
        db.execute("""UPDATE tasks SET status=?,stage=?,error=?,available_at=?,
            lease_owner=NULL,lease_until=NULL,updated_at=?,finished_at=? WHERE id=?""",
            (status, status, message, now + delay if retry else now, now,
             None if retry else now, task_id))
        return True


def cancel(task_id, *, owner_id=None):
    now = time.time()
    sql = """UPDATE tasks SET status='cancelled',stage='cancelled',
        updated_at=?,finished_at=? WHERE id=?
        AND status IN ('queued','running','retry')"""
    args = [now, now, task_id]
    if owner_id is not None:
        sql += " AND owner_id=?"
        args.append(owner_id)
    with _connection() as db:
        return bool(db.execute(sql, args).rowcount)


def release(task_id, lease_owner, *, error="处理进程安全退出，等待继续执行"):
    """正常退出时归还执行权，不因维护性退出消耗业务重试次数。"""
    now = time.time()
    with _connection() as db:
        return bool(db.execute("""UPDATE tasks SET status='retry',stage='retry',
            attempts=MAX(0,attempts-1),error=?,available_at=?,lease_owner=NULL,
            lease_until=NULL,updated_at=? WHERE id=? AND status='running'
            AND lease_owner=? AND lease_until>?""",
            (str(error)[:4000], now, now, task_id, lease_owner, now)).rowcount)


def cancel_resource(resource_id, *, kind=None, owner_id=None):
    now = time.time()
    sql = """UPDATE tasks SET status='cancelled',stage='cancelled',
        updated_at=?,finished_at=? WHERE resource_id=?
        AND status IN ('queued','running','retry')"""
    args = [now, now, resource_id]
    for name, value in (("kind", kind), ("owner_id", owner_id)):
        if value is not None:
            sql += " AND " + name + "=?"
            args.append(value)
    with _connection() as db:
        return db.execute(sql, args).rowcount


def claim_cancelled(worker_id, *, lease_seconds=60, kinds=None):
    """领取尚未清理或崩溃后留待清理的取消任务，不重新执行业务。"""
    worker_id = _text(worker_id, "处理进程标识", 120, True)
    seconds = _finite(lease_seconds, "租约时长")
    if seconds <= 0:
        raise ValueError("租约时长必须大于 0")
    selected_kinds = list(kinds) if kinds is not None else None
    if selected_kinds == []:
        return None
    now = time.time()
    with _connection() as db:
        db.execute("BEGIN IMMEDIATE")
        sql = """SELECT id FROM tasks WHERE status='cancelled' AND
            ((lease_owner IS NULL AND stage='cancelled') OR
             (lease_owner IS NOT NULL AND lease_until<=?))"""
        args = [now]
        if selected_kinds is not None:
            sql += " AND kind IN (" + ",".join("?" for _ in selected_kinds) + ")"
            args.extend(selected_kinds)
        row = db.execute(sql + " ORDER BY updated_at,id LIMIT 1", args).fetchone()
        if row is None:
            return None
        token = worker_id + ":" + uuid.uuid4().hex
        db.execute("UPDATE tasks SET lease_owner=?,lease_until=?,updated_at=? WHERE id=?",
                   (token, now + seconds, now, row["id"]))
        return _decode(db.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())


def finish_cancel(task_id, lease_owner, *, stage="已取消并清理临时文件"):
    """仅在材料清理完成后清除取消任务的执行权；排队任务可传 None。"""
    stage = _text(stage, "取消阶段", 120)
    with _connection() as db:
        return bool(db.execute("""UPDATE tasks SET stage=?,lease_owner=NULL,lease_until=NULL,
            updated_at=? WHERE id=? AND status='cancelled' AND lease_owner IS ?""",
            (stage, time.time(), task_id, lease_owner)).rowcount)


def assert_running(task_id, lease_owner, db=None):
    """检查执行权；业务写入应在 BEGIN IMMEDIATE 后传同一 db 检查。

    不传 db 时只做瞬时检查，不构成后续业务写入的事务保护。
    """
    if db is None:
        with _connection() as connection:
            return assert_running(task_id, lease_owner, connection)
    row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is not None and row["status"] == "cancelled" and row["lease_owner"] == lease_owner:
        raise TaskCancelled("任务已取消，请丢弃迟到结果")
    if (row is None or row["status"] != "running" or row["lease_owner"] != lease_owner
            or row["lease_until"] is None or row["lease_until"] <= time.time()):
        raise LeaseLost("任务执行租约已失效，请丢弃迟到结果")
    return _decode(row)
