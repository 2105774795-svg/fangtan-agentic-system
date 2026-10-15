"""与会议龙虾共用员工中心，本站使用独立的 HttpOnly 登录会话。"""
import hashlib
import os
import secrets
import time

import httpx
from fastapi import HTTPException, Request

import config  # 加载 backend/.env 后再读取 AUTH_API_URL
import store


AUTH_API_URL = os.getenv("AUTH_API_URL", "http://127.0.0.1:3003").rstrip("/")
COOKIE_NAME = "fangtan_employee_session"
LOCAL_USER = {"id": -1, "name": "本地使用者", "type": "本地用户"}


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def upstream(path, body=None, method="POST"):
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            if method == "GET":
                response = await client.get(f"{AUTH_API_URL}{path}")
            else:
                response = await client.post(f"{AUTH_API_URL}{path}", json=body)
            data = response.json()
    except httpx.TransportError as exc:
        raise HTTPException(503, "暂时无法连接员工中心服务，请检查员工中心服务是否运行，以及 backend/.env 中的 AUTH_API_URL 是否正确") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "员工登录服务暂时不可用") from exc
    if not isinstance(data, dict):
        raise HTTPException(502, "员工登录服务返回异常")
    return data


def normalize_user(data):
    raw = data.get("data") or data.get("user") or data
    try:
        user_id = int(raw.get("id"))
        name = str(raw.get("name") or "").strip()
    except (TypeError, ValueError, AttributeError):
        return None
    if user_id <= 0 or not name:
        return None
    return {"id": user_id, "name": name, "type": str(raw.get("type") or "正式员工")}


def issue_session(response, user, secure=False):
    token = secrets.token_urlsafe(36)
    store.save_auth_session(_token_hash(token), user)
    response.set_cookie(COOKIE_NAME, token, max_age=7*86400, httponly=True,
                        secure=secure, samesite="lax", path="/")


def revoke_session(request, response):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        store.delete_auth_session(_token_hash(token))
    response.delete_cookie(COOKIE_NAME, path="/")


async def current_user(request: Request):
    if not config.AUTH_ENABLED:
        return dict(LOCAL_USER)
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "请先登录员工账号")
    token_hash = _token_hash(token)
    row = store.get_auth_session(token_hash)
    if not row:
        raise HTTPException(401, "登录已过期，请重新登录")
    if time.time() - row["checked_at"] > 300:
        data = await upstream("/api/check-session", {"name": row["user_name"]})
        user = normalize_user(data) if data.get("success") else None
        if not user or user["id"] != row["user_id"]:
            store.delete_auth_session(token_hash)
            raise HTTPException(401, "员工账号已失效，请重新登录")
        store.touch_auth_session(token_hash)
    return {"id": row["user_id"], "name": row["user_name"], "type": row["user_type"]}
