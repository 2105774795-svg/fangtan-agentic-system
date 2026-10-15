"""跨 API/Worker 进程共享的一次性 ASR 文件读取凭据。"""
import hashlib
import time
import uuid

import store


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS audio_access_tokens(
        token_hash TEXT PRIMARY KEY, path TEXT NOT NULL, expires_at REAL NOT NULL
    )""")


def register(path, ttl=1800):
    token = uuid.uuid4().hex
    with store.get() as db:
        _schema(db)
        db.execute("DELETE FROM audio_access_tokens WHERE expires_at<?", (time.time(),))
        db.execute("INSERT INTO audio_access_tokens VALUES(?,?,?)",
                   (hashlib.sha256(token.encode()).hexdigest(), str(path), time.time() + ttl))
    return token


def consume(token):
    with store.get() as db:
        _schema(db)
        db.execute("BEGIN IMMEDIATE")
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = db.execute("SELECT path,expires_at FROM audio_access_tokens WHERE token_hash=?", (digest,)).fetchone()
        db.execute("DELETE FROM audio_access_tokens WHERE token_hash=?", (digest,))
        return row["path"] if row and row["expires_at"] > time.time() else None


def revoke(token):
    with store.get() as db:
        _schema(db)
        db.execute("DELETE FROM audio_access_tokens WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
