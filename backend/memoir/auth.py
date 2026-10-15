"""时叙录专用认证：不继承免登录访谈工作区或旧员工 cookie。"""
from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

import config
import employee_auth
import store as shared_store


COOKIE_NAME = "fangtan_memoir_session"
SESSION_SECONDS = 7 * 86400
CHECK_SECONDS = 300
_attempts: OrderedDict[str, deque] = OrderedDict()
_attempt_lock = threading.Lock()
_MAX_RATE_KEYS = 4096


def init(db=None):
    if db is None:
        with shared_store.get() as connection:
            init(connection)
        return
    db.execute("""CREATE TABLE IF NOT EXISTS memoir_auth_sessions(
        token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL CHECK(user_id>0),
        user_name TEXT NOT NULL, user_type TEXT NOT NULL,
        expires_at REAL NOT NULL, checked_at REAL NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_memoir_auth_expiry ON memoir_auth_sessions(expires_at)")


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_user(data):
    raw = data.get("data") or data.get("user") or data
    if not isinstance(raw, dict):
        return None
    value = raw.get("id")
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]{0,17}", str(value)):
        return None
    return employee_auth.normalize_user(data)


def _origin(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return parsed.scheme.lower(), parsed.hostname.lower(), port
    except (TypeError, ValueError):
        return None


def require_same_origin(request: Request):
    """配置的站点为唯一公开来源；只为本机诊断允许本机请求来源。"""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise HTTPException(403, "请从本站页面提交操作")
    supplied = request.headers.get("origin")
    if supplied is None:
        return
    allowed = {_origin(config.PUBLIC_BASE)}
    current = _origin(str(request.url))
    if current and current[1] in {"127.0.0.1", "localhost", "::1", "testserver"}:
        allowed.add(current)
    if _origin(supplied) is None or _origin(supplied) not in allowed:
        raise HTTPException(403, "请求来源不受信任")


def check_login_rate(request: Request, name: str):
    now = time.monotonic()
    address = request.client.host if request.client else "unknown"
    # 限流器只留摘要和时间，不留姓名、密码或请求体。
    keys = (_hash("ip:" + address), _hash("account:" + name.strip().casefold()))
    with _attempt_lock:
        for key in keys:
            values = _attempts.get(key)
            if values is not None:
                while values and values[0] <= now - 60:
                    values.popleft()
                if len(values) >= 8:
                    raise HTTPException(429, "登录尝试过于频繁，请稍后再试", headers={"Retry-After": "60"})
        for key in keys:
            values = _attempts.setdefault(key, deque())
            values.append(now)
            _attempts.move_to_end(key)
        while len(_attempts) > _MAX_RATE_KEYS:
            _attempts.popitem(last=False)


def issue_session(response, user):
    if not isinstance(user.get("id"), int) or isinstance(user["id"], bool) or user["id"] <= 0:
        raise HTTPException(401, "员工身份无效")
    token, now = secrets.token_urlsafe(36), time.time()
    with shared_store.get() as db:
        init(db)
        db.execute("DELETE FROM memoir_auth_sessions WHERE expires_at<=?", (now,))
        db.execute("INSERT INTO memoir_auth_sessions VALUES(?,?,?,?,?,?)",
                   (_hash(token), user["id"], user["name"], user.get("type", "正式员工"),
                    now + SESSION_SECONDS, now))
    secure = (_origin(config.PUBLIC_BASE) or ("http",))[0] == "https"
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=secure, samesite="strict", path="/")


def revoke_session(request, response):
    token = request.cookies.get(COOKIE_NAME)
    if token and len(token) <= 200:
        with shared_store.get() as db:
            init(db)
            db.execute("DELETE FROM memoir_auth_sessions WHERE token_hash=?", (_hash(token),))
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True,
                           secure=(_origin(config.PUBLIC_BASE) or ("http",))[0] == "https",
                           samesite="strict")


async def require_user(request: Request):
    require_same_origin(request)
    token = request.cookies.get(COOKIE_NAME)
    if not token or len(token) > 200:
        raise HTTPException(401, "请单独登录时叙录，访谈共享工作区的身份不能用于私人资料")
    token_hash, now = _hash(token), time.time()
    with shared_store.get() as db:
        init(db)
        raw = db.execute("SELECT * FROM memoir_auth_sessions WHERE token_hash=?", (token_hash,)).fetchone()
        row = dict(raw) if raw else None
        if row and (row["expires_at"] <= now or row["user_id"] <= 0):
            db.execute("DELETE FROM memoir_auth_sessions WHERE token_hash=?", (token_hash,))
            row = None
    if not row:
        raise HTTPException(401, "时叙录登录已过期，请重新登录")
    if now - row["checked_at"] >= CHECK_SECONDS:
        # 上游不可达时闭锁，不降级到 LOCAL_USER 或沿用未确认的身份。
        data = await employee_auth.upstream("/api/check-session", {"name": row["user_name"]})
        user = normalize_user(data) if data.get("success") is True else None
        if not user or user["id"] != row["user_id"]:
            with shared_store.get() as db:
                db.execute("DELETE FROM memoir_auth_sessions WHERE token_hash=?", (token_hash,))
            raise HTTPException(401, "员工账号已失效，请重新登录时叙录")
        with shared_store.get() as db:
            changed = db.execute("""UPDATE memoir_auth_sessions SET checked_at=?
                WHERE token_hash=? AND expires_at>?""", (now, token_hash, now)).rowcount
        if not changed:
            raise HTTPException(401, "时叙录登录已失效")
    return {"id": row["user_id"], "name": row["user_name"], "type": row["user_type"]}
