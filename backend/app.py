"""访谈-体察 FastAPI 后端：音频分片上传 → 转写 → 信号检测 → Kimi 追问建议（SSE 推送）。"""
import asyncio
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from pathlib import Path

import imageio_ffmpeg
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from starlette.concurrency import run_in_threadpool

import asr_dashscope
import config
import dimensions
import employee_auth
import importer
import kimi
import playbook
import project_records
import signals
import store
import telemetry
import word_export
import job_queue
import task_api
import coverage_ai
import project_bundle
from services import outline_generation
from services.inline_context import SessionContext
from services.downloads import TemporaryDownloadResponse
from services.materials import load_project_materials, location_text
from memoir import api as memoir_api, auth as memoir_auth, store as memoir_store
import research_platform

app = FastAPI(title="访谈-体察", redirect_slashes=False)
app.mount(
    "/assets",
    StaticFiles(directory=Path(__file__).parent / "static" / "assets"),
    name="assets",
)
logger = logging.getLogger("fangtan.sessions")
INTERVIEWEE_TYPES = {
    "商户", "企业家", "企业高管", "一线员工", "行业专家", "投资人", "政府/协会人士", "消费者"
}
INDUSTRIES = {
    "综合/不限", "金融业", "汽车业", "制造业", "食品饮料", "消费零售", "互联网科技",
    "房地产建筑", "能源化工", "医药健康", "农业", "交通物流", "文化传媒", "教育服务"
}
store.init()
job_queue.init()
coverage_ai.init()
memoir_store.init()
memoir_auth.init()
app.include_router(task_api.router)
app.include_router(memoir_api.router)
research_platform.init()
app.include_router(research_platform.router)
app.mount("/platform-assets", StaticFiles(directory=Path(__file__).parent / "static" / "platform" / "platform-assets", check_dir=False), name="platform-assets")
app.mount("/platform-media", StaticFiles(directory=Path(__file__).parent / "static" / "platform-media", check_dir=False), name="platform-media")
config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
config.REPORT_DIR.mkdir(parents=True, exist_ok=True)


@app.exception_handler(RequestValidationError)
async def private_validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/memoir/"):
        # Pydantic 默认 input 字段可能回显密码或口述原话，私密域统一脱敏。
        return JSONResponse({"detail": "提交内容格式不正确，请检查必填项与长度"}, status_code=422,
                            headers={"Cache-Control": "private, no-store"})
    return await request_validation_exception_handler(request, exc)

# ---------- SSE 事件总线 ----------
LOOP: asyncio.AbstractEventLoop | None = None
BUS: dict[str, list[asyncio.Queue]] = {}
CHUNK_LOCKS: dict[str, threading.Lock] = {}
CHUNK_SEQ: dict[str, int] = {}
REPORT_LOCKS: dict[str, threading.Lock] = {}
REPORT_JOBS: dict[str, str] = {}
NAMING_LOCKS: dict[str, threading.Lock] = {}
IMPORT_JOBS: dict[str, dict] = {}
IMPORT_JOBS_LOCK = threading.Lock()
SPEAKER_JOBS: dict[str, dict] = {}
SPEAKER_JOBS_LOCK = threading.Lock()
PROJECT_RECORD_LOCK = threading.Lock()
OUTLINE_JOB_LOCK = threading.Lock()
FOLLOWUP_LOCKS: dict[str, threading.Lock] = {}
FOLLOWUP_STARTED: dict[str, float] = {}
PROJECT_BUNDLE_SLOTS = threading.BoundedSemaphore(2)


class ImportCancelled(Exception):
    """由创建人请求取消导入；在安全检查点结束当前任务。"""


@app.middleware("http")
async def employee_gate(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/memoir/"):
        # 私密口述史不继承共享工作区 LOCAL_USER，也不依赖旧员工 Cookie。
        # 该路由组的独立依赖始终验证具体员工身份和档案所有权。
        response = await call_next(request)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    if not config.AUTH_ENABLED:
        account_action = path.startswith("/api/auth/") and path not in {"/api/auth/me", "/api/auth/logout"}
        sharing_action = (path.startswith("/api/share/") or
                          re.fullmatch(r"/api/sessions/[^/]+/(?:shares|share-links)(?:/[^/]+)?", path))
        if account_action or sharing_action:
            return JSONResponse({"detail": "当前为免登录模式，员工账号和共享权限功能暂未启用"}, status_code=409)
    if (path.startswith("/api/") and not path.startswith(("/api/auth/", "/api/share/"))
            and path != "/api/dev-progress"):
        try:
            request.state.user = await employee_auth.current_user(request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return await call_next(request)


def _user(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "请先登录员工账号")
    return user


def _require_session(sid: str, request: Request, write=False):
    if not config.AUTH_ENABLED:
        _user(request)
        session = store.get_session_summary(sid)
        if not session or session["status"] == "deleting":
            raise HTTPException(404, "session not found")
        return "owner"
    access = store.session_access(sid, _user(request)["id"])
    if access is None:
        raise HTTPException(404, "session not found")
    if write and access != "owner":
        raise HTTPException(403, "只有创建人可以修改这场访谈")
    return access


def _recording_file(sid: str) -> Path:
    summary = store.get_session_summary(sid)
    audio_path = (summary or {}).get("audio_path") or ""
    path = Path(audio_path)
    root = (config.DATA_DIR / "recordings" / sid).resolve()
    if not audio_path or not path.is_file() or not path.resolve().is_relative_to(root):
        raise HTTPException(404, "录音文件不存在")
    return path


def _remove_wav_range(path: Path, start_ms: int, end_ms: int) -> int:
    """从 WAV 中移除指定时间段，返回实际移除的毫秒数。"""
    if path.suffix.lower() != ".wav":
        raise HTTPException(409, "当前录音格式不支持删除单个片段，请删除整场原始录音")
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with wave.open(str(path), "rb") as source:
            frame_rate = source.getframerate()
            total_frames = source.getnframes()
            start_frame = max(0, min(total_frames, round(start_ms * frame_rate / 1000)))
            end_frame = max(start_frame, min(total_frames, round(end_ms * frame_rate / 1000)))
            if end_frame <= start_frame:
                raise HTTPException(409, "该录音片段没有可删除的音频")
            target = wave.open(str(temp_path), "wb")
            try:
                target.setnchannels(source.getnchannels())
                target.setsampwidth(source.getsampwidth())
                target.setframerate(frame_rate)
                target.setcomptype(source.getcomptype(), source.getcompname())
                source.rewind()
                remaining = start_frame
                while remaining:
                    frames = source.readframes(min(65536, remaining))
                    if not frames:
                        break
                    target.writeframes(frames)
                    remaining -= len(frames) // source.getnchannels() // source.getsampwidth()
                source.setpos(end_frame)
                remaining = total_frames - end_frame
                while remaining:
                    frames = source.readframes(min(65536, remaining))
                    if not frames:
                        break
                    target.writeframes(frames)
                    remaining -= len(frames) // source.getnchannels() // source.getsampwidth()
            finally:
                target.close()
        os.replace(temp_path, path)
        return round((end_frame - start_frame) * 1000 / frame_rate)
    except HTTPException:
        temp_path.unlink(missing_ok=True)
        raise
    except (OSError, wave.Error, ValueError) as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(422, "原始录音无法修改，请先下载备份后重试") from exc


def _clear_speaker_job(sid: str):
    job_queue.cancel_resource(sid, kind="speaker")
    with SPEAKER_JOBS_LOCK:
        SPEAKER_JOBS.pop(sid, None)


def _transcript_body(sid: str) -> str:
    lines = []
    for row in store.list_export_utterances(sid):
        seconds = int(row["start_ms"] or 0) // 1000
        stamp = f"{seconds // 60:02d}:{seconds % 60:02d}"
        speaker = f"{row['speaker']}：" if row.get("speaker") else ""
        lines.append(f"[{stamp}] {speaker}{row['text']}")
    return "\n".join(lines)


def _word_body(sid: str) -> bytes:
    summary = store.get_session_summary(sid)
    if not summary or not summary.get("has_report"):
        raise HTTPException(404, "纪要尚未生成")
    return word_export.render_minutes(summary["subject"], store.get_report(sid),
                                      store.list_export_utterances(sid))


def _speaker_status(sid: str) -> dict:
    summary = store.get_session_summary(sid)
    segments = store.list_speaker_segments(sid)
    with SPEAKER_JOBS_LOCK:
        job = dict(SPEAKER_JOBS.get(sid) or {})
    active = bool(summary and summary.get("speaker_job_id") and
                  job.get("job_id") == summary["speaker_job_id"])
    stale = bool(segments and summary and summary.get("speaker_job_id") != summary.get("speaker_result_job_id"))
    status = (job.get("status") if active else
              "failed" if summary and summary.get("speaker_job_id") and stale else
              "ready" if segments else
              "failed" if summary and summary.get("speaker_job_id") else "none")
    task = task_api.latest("speaker", sid)
    if task and summary and task["payload"].get("job_id") == summary.get("speaker_job_id"):
        if segments and not stale:
            status = "ready"
        elif not (active and job.get("status") in {"failed", "ready"}):
            status = "processing" if task["status"] in task_api.ACTIVE else "failed"
    unavailable_reason = asr_dashscope.speaker_unavailable_reason()
    return {"status": status, "stale": stale,
            "available": not bool(unavailable_reason), "unavailable_reason": unavailable_reason or None,
            "error": job.get("error") if active else ("上次分析未完成" if status == "failed" else None),
            "consented_at": (summary or {}).get("speaker_consented_at"),
            "segments": segments}


def _speaker_text(sid: str) -> str:
    lines = []
    for row in store.list_speaker_segments(sid):
        seconds = row["start_ms"] // 1000
        label = chr(65 + row["speaker_id"]) if row["speaker_id"] < 26 else str(row["speaker_id"] + 1)
        lines.append(f"[{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}] "
                     f"发言人 {label}：{row['text']}")
    return "\n".join(lines)


def _run_speaker_analysis(sid: str, job_id: str):
    try:
        source = _recording_file(sid)
        with tempfile.TemporaryDirectory(prefix="speaker-analysis-", dir=config.UPLOAD_DIR) as folder:
            wav = Path(folder) / "mono.wav"
            subprocess.run([_ffmpeg_executable(), "-y", "-i", str(source), "-vn",
                            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                           check=True, capture_output=True, timeout=600)
            segments = asr_dashscope.transcribe_speakers(str(wav))
        if not store.save_speaker_segments(sid, job_id, segments):
            return
        with SPEAKER_JOBS_LOCK:
            if SPEAKER_JOBS.get(sid, {}).get("job_id") == job_id:
                SPEAKER_JOBS[sid] = {"job_id": job_id, "status": "ready"}
    except Exception as exc:
        logger.exception("speaker analysis failed sid=%s", sid)
        with SPEAKER_JOBS_LOCK:
            if SPEAKER_JOBS.get(sid, {}).get("job_id") == job_id:
                SPEAKER_JOBS[sid] = {"job_id": job_id, "status": "failed", "error": str(exc)[:300]}


@app.post("/api/auth/login")
async def auth_login(request: Request, body: dict):
    name = _body_text(body, "name", "", 50)
    password = _body_text(body, "password", "", 200)
    if not name or not password:
        raise HTTPException(400, "请输入姓名和密码")
    data = await employee_auth.upstream("/api/verify-login", {"name": name, "password": password})
    user = employee_auth.normalize_user(data) if data.get("success") else None
    if not user:
        raise HTTPException(401, data.get("error") or data.get("message") or "登录失败")
    store.claim_legacy_sessions(user["id"], user["name"])
    response = JSONResponse({"user": user})
    employee_auth.issue_session(response, user, secure=request.url.scheme == "https" or
                                request.headers.get("x-forwarded-proto") == "https")
    return response


@app.get("/api/auth/me")
async def auth_me(request: Request):
    return {"user": await employee_auth.current_user(request), "auth_enabled": config.AUTH_ENABLED}


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    response = JSONResponse({"ok": True})
    if config.AUTH_ENABLED:
        employee_auth.revoke_session(request, response)
    return response


@app.post("/api/auth/register")
async def auth_register(body: dict):
    payload = {key: _body_text(body, key, "", 200) for key in
               ("name", "id_card", "phone", "password", "confirmPassword")}
    data = await employee_auth.upstream("/api/register", payload)
    if not data.get("success"):
        raise HTTPException(400, data.get("error") or data.get("message") or "注册失败")
    return {"ok": True, "message": data.get("message") or "注册成功，请登录"}


@app.post("/api/auth/reset-password")
async def auth_reset_password(body: dict):
    payload = {key: _body_text(body, key, "", 200) for key in
               ("name", "id_card", "password", "confirmPassword")}
    data = await employee_auth.upstream("/api/reset-password", payload)
    if not data.get("success"):
        raise HTTPException(400, data.get("error") or data.get("message") or "修改失败")
    return {"ok": True, "message": data.get("message") or "密码已修改"}


@app.get("/api/auth/people")
async def auth_people(request: Request, search: str = ""):
    await employee_auth.current_user(request)
    from urllib.parse import quote
    data = await employee_auth.upstream("/api/employees?search=" + quote(search[:50]), method="GET")
    if data.get("success") is False:
        raise HTTPException(502, data.get("error") or "人员名单暂时读不到")
    people = []
    for raw in data.get("data") or []:
        user = employee_auth.normalize_user(raw)
        if user:
            people.append({**user, "expired": bool(raw.get("expired") or raw.get("is_expired"))})
    return {"people": people}


@app.on_event("startup")
async def _startup():
    global LOOP
    LOOP = asyncio.get_running_loop()


def publish(sid: str, event: dict):
    if LOOP is None:
        return
    event.setdefault("event_id", uuid.uuid4().hex)
    for q in BUS.get(sid, []):
        LOOP.call_soon_threadsafe(_enqueue_event, q, event)


def _enqueue_event(q: asyncio.Queue, event: dict):
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        logger.warning("SSE queue full; dropping event type=%s", event.get("type"))


def _schedule_auto_naming(sid: str, phase: str):
    lock = NAMING_LOCKS.setdefault(sid, threading.Lock())
    with lock:
        summary = store.get_session_summary(sid)
        if not summary or summary.get("subject_source") != "default" or summary.get("subject") != "未命名访谈":
            return
        attempts = store.auto_naming_attempts(sid)
        if attempts >= 2 or (phase == "early" and attempts > 0):
            return
        attempt_id = store.add_auto_naming_attempt(sid, phase)
    publish(sid, {"type": "naming_started", "phase": phase})
    threading.Thread(target=_run_auto_naming, args=(sid, attempt_id), daemon=True,
                     name=f"session-naming-{sid}").start()


def _run_auto_naming(sid: str, attempt_id: int):
    try:
        transcript = store.list_utterances(sid, last_n=2000)
        title = kimi.gen_subject(_session_field(sid, "interviewee_type"), transcript)
        if title and store.try_set_auto_subject(sid, title):
            store.set_auto_naming_attempt_status(attempt_id, "asked")
            publish(sid, {"type": "renamed", "subject": title, "subject_source": "auto"})
            logger.info("session auto-named sid=%s", sid)
        else:
            store.set_auto_naming_attempt_status(attempt_id, "dismissed")
            logger.info("session auto-naming discarded sid=%s", sid)
    except Exception:
        store.set_auto_naming_attempt_status(attempt_id, "dismissed")
        logger.exception("session auto-naming failed sid=%s", sid)
        publish(sid, {"type": "naming_failed"})


# ---------- 静态前端 ----------
@app.get("/", response_class=HTMLResponse)
async def index():
    platform_index = Path(__file__).parent / "static" / "platform" / "index.html"
    if platform_index.exists():
        return HTMLResponse(platform_index.read_text(encoding="utf-8"))
    html = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/legacy", response_class=HTMLResponse)
async def legacy_index():
    return HTMLResponse((Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8"))


# ---------- API ----------
def _body_text(body: dict, key: str, default: str = "", max_length: int = 200) -> str:
    value = body.get(key, default)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise HTTPException(400, f"{key} must be a string")
    value = value.strip()
    if len(value) > max_length:
        raise HTTPException(400, f"{key} is too long")
    return value


def _location(body, required=False):
    value = body.get("location") or {}
    if not isinstance(value, dict):
        raise HTTPException(400, "地区/市场必须是地址对象")
    location = {key: _body_text(value, key, "", limit) for key, limit in
                (("city", 80), ("market", 120))}
    location = {key: text for key, text in location.items() if text}
    if required and any(not location.get(key) for key in ("city", "market")):
        raise HTTPException(400, "请填写城市和市场/园区")
    return location


def _location_text(location):
    return location_text(location)


def _outline_params(body, required_location=False):
    subject = _body_text(body, "subject", "", 80)
    itype = _body_text(body, "interviewee_type", "", 20)
    industry = _body_text(body, "industry", "综合/不限", 30) or "综合/不限"
    if not subject or itype not in INTERVIEWEE_TYPES or industry not in INDUSTRIES:
        raise HTTPException(400, "请填写访谈主题，并选择有效的访谈对象与行业")
    return {"subject": subject, "interviewee_type": itype, "industry": industry,
            "focus": _body_text(body, "focus", "", 500), "location": _location(body, required_location)}


def _public_outline_job(job):
    return task_api.outline_view(job)


def _run_outline_job(job_id, params):
    try:
        brief = outline_generation.generate(params)
        store.finish_outline_job(job_id, brief["questions"], background=brief["background"],
                                 background_note=brief["background_note"], sources=brief["sources"],
                                 research_status=brief["research_status"], retrieved_at=brief["retrieved_at"],
                                 claims=brief.get("claims"))
    except Exception:
        logger.exception("outline job failed id=%s", job_id)
        store.finish_outline_job(job_id, error="生成暂时失败或超时，可重试；已填写的设置不会丢失")


@app.post("/api/outlines/jobs", status_code=202)
async def create_outline_job(body: dict, request: Request):
    params = _outline_params(body, required_location=True)
    fingerprint = hashlib.sha256(json.dumps({"settings": params, "model": config.KIMI_OUTLINE_MODEL,
        "result_version": "place-web-sources-v2", "search_day": int(time.time() // 86400),
        "prompt": kimi.OUTLINE_SYSTEM}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    owner = _user(request)["id"]
    with OUTLINE_JOB_LOCK:
        existing = store.find_outline_job(owner, fingerprint)
        if existing and (_public_outline_job(existing)["status"] == "generating" or
                         (_public_outline_job(existing)["status"] == "ready" and not body.get("regenerate"))):
            return _public_outline_job(existing)
        job_id = uuid.uuid4().hex[:16]
        store.create_outline_job(job_id, owner, fingerprint, params)
        job_queue.enqueue("outline", {"job_id": job_id, "params": params}, owner_id=owner,
                          resource_id=job_id, dedupe_key=job_id, task_id=job_id)
    return _public_outline_job(store.get_outline_job(job_id))


@app.get("/api/outlines/jobs/{job_id}")
async def get_outline_job(job_id: str, request: Request):
    job = store.get_outline_job(job_id)
    if not job or job["owner_id"] != _user(request)["id"]:
        raise HTTPException(404, "提纲任务不存在")
    return JSONResponse(_public_outline_job(job), headers={"Cache-Control": "no-store"})


@app.post("/api/outlines/generate")
async def generate_outline(body: dict):
    params = _outline_params(body, required_location=True)
    try:
        brief = await run_in_threadpool(outline_generation.generate, params)
    except Exception as exc:
        logger.exception("outline generation failed")
        raise HTTPException(502, f"outline generation failed: {exc}") from exc
    return {**brief, "count": len(brief["questions"])}


@app.get("/api/sessions/")
async def list_sessions(request: Request, limit: int = 100, offset: int = 0,
                        search: str = "", project: str = "", scope: str = "all", project_id: str = ""):
    if scope not in {"all", "mine", "shared"}:
        raise HTTPException(400, "invalid scope")
    user_id = _user(request)["id"] if config.AUTH_ENABLED else None
    if not config.AUTH_ENABLED and scope == "shared":
        return {"items": [], "total": 0}
    page = store.list_sessions(limit, offset, user_id, search[:100], project[:80], scope, project_id[:32])
    for item in page["items"]:
        item["has_report"] = bool(item["has_report"])
        item["report_status"] = task_api.report_status(item["id"], REPORT_JOBS.get(item["id"]))
        item["display_subject"] = _display_subject(item["subject"], item["created_at"])
        item["access"] = "owner" if not config.AUTH_ENABLED or item["owner_id"] == user_id else "shared"
    return page


@app.get("/api/sessions/{sid}")
async def get_session(sid: str, request: Request):
    access = _require_session(sid, request)
    summary = store.get_session_summary(sid)
    if not summary:
        raise HTTPException(404, "session not found")
    summary["report_status"] = task_api.report_status(sid, REPORT_JOBS.get(sid))
    summary["has_report"] = bool(summary["has_report"])
    summary["display_subject"] = _display_subject(summary["subject"], summary["created_at"])
    summary["outline"] = json.loads(summary.get("outline") or "[]")
    summary.pop("report_markdown", None)
    summary["utterances"] = store.list_utterances(sid, last_n=10000)
    if summary["status"] == "done" and not summary["utterances"] and not summary["has_report"]:
        summary["report_status"] = "empty"
    summary["suggestions"] = [s for s in store.list_suggestions(sid) if s["kind"] != "auto_naming"]
    summary["coverage"] = _coverage(sid)
    summary["access"] = access
    audio_path = summary.get("audio_path") or ""
    summary["has_audio"] = bool(audio_path)
    summary["can_delete_segments"] = bool(audio_path and Path(audio_path).suffix.lower() == ".wav"
                                          and summary["status"] == "done")
    summary["todos"] = store.list_todos(sid)
    summary["questions"] = store.list_questions(sid)
    summary.pop("audio_path", None)
    return summary


def _display_subject(subject, created_at):
    if subject and subject != "未命名访谈":
        return subject
    from datetime import datetime
    return "未命名 · " + datetime.fromtimestamp(created_at).strftime("%m-%d %H:%M")


@app.patch("/api/sessions/{sid}")
async def rename_session(sid: str, body: dict, request: Request):
    _require_session(sid, request, write=True)
    subject = body.get("subject")
    if not isinstance(subject, str):
        raise HTTPException(400, "subject is required")
    subject = subject.strip()
    if not subject or len(subject) > 80:
        raise HTTPException(400, "subject must contain 1-80 characters")
    if not store.rename_session(sid, subject, "user"):
        raise HTTPException(404, "session not found")
    publish(sid, {"type": "renamed", "subject": subject, "subject_source": "user"})
    return {"id": sid, "subject": subject, "subject_source": "user"}


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str, request: Request):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid):
        raise HTTPException(400, "invalid session id")
    _require_session(sid, request, write=True)
    lock = CHUNK_LOCKS.setdefault(sid, threading.Lock())
    with lock:
        summary = store.get_session_summary(sid)
        already_deleted = summary is None
        if summary:
            job_queue.cancel_resource(sid)
            store.mark_session_deleting(sid)
            store.delete_session_records(sid)
            CHUNK_SEQ.pop(sid, None)
            REPORT_JOBS.pop(sid, None)
        errors = []
        upload_path = config.UPLOAD_DIR / sid
        report_path = config.REPORT_DIR / f"{sid}.md"
        recording_path = config.DATA_DIR / "recordings" / sid
        for path in (upload_path, report_path, recording_path):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except OSError as exc:
                logger.exception("session file cleanup failed sid=%s path=%s", sid, path)
                errors.append(str(path))
        for q in BUS.pop(sid, []):
            try:
                q.put_nowait({"type": "error", "message": "会话已删除",
                              "event_id": uuid.uuid4().hex})
            except asyncio.QueueFull:
                pass
    return {"ok": True, "already_deleted": already_deleted, "files_deleted": not errors,
            "cleanup_errors": errors}


@app.post("/api/sessions/")
async def create_session(body: dict, request: Request):
    sid = uuid.uuid4().hex[:12]
    subject = _body_text(body, "subject", "未命名访谈", 80) or "未命名访谈"
    scene = _body_text(body, "scene", "商户门店", 40) or "商户门店"
    interviewee_type = _body_text(body, "interviewee_type", "商户", 20) or "商户"
    industry = _body_text(body, "industry", "综合/不限", 30) or "综合/不限"
    focus = _body_text(body, "focus", "", 500)
    user = _user(request)
    user_name = user["name"]
    if interviewee_type not in INTERVIEWEE_TYPES:
        raise HTTPException(400, "invalid interviewee type")
    if industry not in INDUSTRIES:
        raise HTTPException(400, "invalid industry")
    raw_outline = body.get("outline", "")
    if isinstance(raw_outline, str):
        outline = [x.strip() for x in raw_outline.splitlines() if x.strip()]
    elif isinstance(raw_outline, list) and all(isinstance(x, str) for x in raw_outline):
        outline = [x.strip() for x in raw_outline if x.strip()]
    else:
        raise HTTPException(400, "outline must be a string or string array")
    if len(outline) > 100 or any(len(item) > 200 for item in outline):
        raise HTTPException(400, "outline is too long")
    store.create_session(
        sid,
        subject,
        scene,
        interviewee_type,
        outline,
        industry,
        focus,
        user_name,
        user["id"],
        _location(body),
    )
    (config.UPLOAD_DIR / sid).mkdir(parents=True, exist_ok=True)
    # 推送"作战开场卡"（话术库）：SSE 连接后回放可见
    opening = playbook.opening_payload(interviewee_type)
    sid_sug = store.add_suggestion(sid, "playbook", opening)
    publish(sid, {"type": "suggestion", "kind": "playbook", "id": sid_sug,
                  "event_id": f"suggestion:{sid_sug}", **opening})
    return {"session_id": sid}


def _update_import_job(job_id: str, **changes):
    with IMPORT_JOBS_LOCK:
        job = IMPORT_JOBS.get(job_id)
        if job:
            if job.get("cancel_requested") and changes.get("status") not in {"cancelled", "failed"}:
                return
            job.update(changes)
            job["updated_at"] = time.time()


def _mark_import_ready(job_id: str):
    with IMPORT_JOBS_LOCK:
        job = IMPORT_JOBS.get(job_id)
        if not job or job.get("cancel_requested"):
            raise ImportCancelled()
        job.update(status="ready", stage="纪要已生成", progress=100, updated_at=time.time())


def _public_import_job(job: dict) -> dict:
    return {key: value for key, value in job.items()
            if key not in {"path", "subject", "user_name", "interviewee_type", "industry", "focus", "location", "owner_id",
                           "cancel_requested"}}


def _check_import_cancelled(job_id: str):
    with IMPORT_JOBS_LOCK:
        job = IMPORT_JOBS.get(job_id)
        if job and job.get("cancel_requested"):
            raise ImportCancelled()


def _prune_import_jobs():
    deadline = time.time() - 24 * 3600
    with IMPORT_JOBS_LOCK:
        expired = [job_id for job_id, job in IMPORT_JOBS.items()
                   if job.get("updated_at", job.get("created_at", 0)) < deadline]
        for job_id in expired:
            IMPORT_JOBS.pop(job_id, None)


async def _save_upload_limited(upload: UploadFile, destination: Path, max_bytes: int):
    total = 0
    try:
        with destination.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(413, f"文件不能超过 {max_bytes // (1024 * 1024)}MB")
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    if not total:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, "文件内容为空")


@app.post("/api/imports/analyze", status_code=202)
async def analyze_import(
    request: Request,
    material: UploadFile = File(...),
    subject: str = Form(""),
    user_name: str = Form(""),
    interviewee_type: str = Form("商户"),
    industry: str = Form("综合/不限"),
    focus: str = Form(""),
    location: str = Form("{}"),
):
    """导入已有音频或文字稿，后台完成转写、分析与纪要生成。"""
    _prune_import_jobs()
    filename = (material.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    suffix = Path(filename).suffix.lower()
    if suffix not in importer.SUPPORTED_EXTENSIONS:
        supported = "、".join(sorted(ext.lstrip(".").upper() for ext in importer.SUPPORTED_EXTENSIONS))
        raise HTTPException(415, f"不支持该文件格式。支持：{supported}")
    subject = subject.strip()[:40] or Path(filename).stem.strip()[:40] or "导入材料"
    user = _user(request)
    user_name = user["name"]
    if interviewee_type not in INTERVIEWEE_TYPES:
        raise HTTPException(400, "invalid interviewee type")
    if industry not in INDUSTRIES:
        raise HTTPException(400, "invalid industry")
    focus = focus.strip()[:500]
    try:
        import_location = _location({"location": json.loads(location[:3000])})
    except ValueError as exc:
        raise HTTPException(400, "地区/市场格式无效") from exc
    job_id = uuid.uuid4().hex[:16]
    job_dir = config.UPLOAD_DIR / "_imports" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(filename).stem)[:80] or "material"
    safe_name = f"{safe_stem}{suffix}"
    destination = job_dir / safe_name
    max_bytes = config.MAX_IMPORT_AUDIO_BYTES if suffix in importer.AUDIO_EXTENSIONS else config.MAX_IMPORT_TEXT_BYTES
    try:
        await _save_upload_limited(material, destination, max_bytes)
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    now = time.time()
    job = {
        "job_id": job_id, "status": "queued", "stage": "已接收文件", "progress": 5,
        "filename": filename, "kind": "audio" if suffix in importer.AUDIO_EXTENSIONS else "transcript",
        "session_id": None, "error": None, "created_at": now, "updated_at": now,
        "path": str(destination), "subject": subject, "user_name": user_name,
        "interviewee_type": interviewee_type,
        "industry": industry, "focus": focus, "owner_id": user["id"], "location": import_location,
    }
    with IMPORT_JOBS_LOCK:
        sid = uuid.uuid4().hex[:12]
        scene = "导入音频" if job["kind"] == "audio" else "导入字幕稿" if suffix in {".srt", ".vtt"} else "导入文字稿"
        store.create_session(sid, subject, scene, interviewee_type, [], industry, focus, user_name, user["id"], import_location)
        with store.get() as db:
            db.execute("UPDATE sessions SET status='importing' WHERE id=?", (sid,))
        job["session_id"] = sid
        try:
            task = job_queue.enqueue("import", job, owner_id=user["id"], resource_id=sid,
                                      dedupe_key=job_id, task_id=job_id)
        except Exception:
            store.delete_session_records(sid)
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
    return task_api.import_view(task)


@app.get("/api/imports/{job_id}")
async def get_import_job(job_id: str, request: Request):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id):
        raise HTTPException(400, "invalid job id")
    task = job_queue.get(job_id)
    if task:
        if task["kind"] != "import" or (config.AUTH_ENABLED and task["owner_id"] != _user(request)["id"]):
            raise HTTPException(404, "import job not found")
        return task_api.import_view(task)
    with IMPORT_JOBS_LOCK:
        job = dict(IMPORT_JOBS.get(job_id) or {})
    if not job:
        raise HTTPException(404, "import job not found")
    if config.AUTH_ENABLED and job.get("owner_id") != _user(request)["id"]:
        raise HTTPException(404, "import job not found")
    return _public_import_job(job)


@app.post("/api/imports/{job_id}/cancel")
async def cancel_import_job(job_id: str, request: Request):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id):
        raise HTTPException(400, "invalid job id")
    task = job_queue.get(job_id)
    if task:
        if task["kind"] != "import" or (config.AUTH_ENABLED and task["owner_id"] != _user(request)["id"]):
            raise HTTPException(404, "import job not found")
        if task["status"] in {"ready", "failed"}:
            raise HTTPException(409, "这项任务已经结束")
        job_queue.cancel(job_id)
        # 包括尚未领取的任务也由 Worker 清理，HTTP 中断不会跳过材料删除。
        return task_api.import_view(job_queue.get(job_id))
    with IMPORT_JOBS_LOCK:
        job = IMPORT_JOBS.get(job_id)
        if not job or (config.AUTH_ENABLED and job.get("owner_id") != _user(request)["id"]):
            raise HTTPException(404, "import job not found")
        if job["status"] in {"ready", "failed", "cancelled"}:
            raise HTTPException(409, "这项任务已经结束")
        job["cancel_requested"] = True
        job["status"] = "cancelling"
        job["stage"] = "正在取消，当前处理步骤结束后清理临时文件"
        job["updated_at"] = time.time()
        return _public_import_job(job)


def _transcribe_import_audio(job_id: str, path: Path) -> list[dict]:
    segment_dir = path.parent / "segments"
    segment_dir.mkdir(parents=True, exist_ok=True)
    pattern = segment_dir / "part_%04d.wav"
    _update_import_job(job_id, status="processing", stage="正在切分并标准化音频", progress=12)
    try:
        subprocess.run(
            [_ffmpeg_executable(), "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
             "-f", "segment", "-segment_time", "240", "-reset_timestamps", "1", str(pattern)],
            check=True, capture_output=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace")[-500:]
        logger.warning("import audio decode failed job_id=%s detail=%s", job_id, detail)
        raise RuntimeError("无法读取该音频，请确认文件未损坏且格式正确") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("音频处理超时") from exc
    segments = sorted(segment_dir.glob("part_*.wav"))
    if not segments:
        raise RuntimeError("音频中没有可识别的内容")
    utterances = []
    elapsed_ms = 0.0
    for index, segment in enumerate(segments):
        _check_import_cancelled(job_id)
        # 分段长度可能略偏离切分阈值，尾段通常更短；以标准化 WAV 的采样数为准。
        with wave.open(str(segment), "rb") as audio:
            duration_ms = audio.getnframes() * 1000 / audio.getframerate()
        start_ms = round(elapsed_ms)
        elapsed_ms += duration_ms
        end_ms = round(elapsed_ms)
        progress = 18 + int(index / max(1, len(segments)) * 52)
        _update_import_job(job_id, stage=f"正在转写音频（{index + 1}/{len(segments)}）", progress=progress)
        text = asr_dashscope.transcribe(str(segment), timeout=300).strip()
        if text:
            utterances.append({"start_ms": start_ms, "end_ms": end_ms,
                               "text": text})
    if not utterances:
        raise RuntimeError("音频转写结果为空")
    return utterances


def _remove_import_session(sid: str):
    store.mark_session_deleting(sid)
    store.delete_session_records(sid)
    shutil.rmtree(config.DATA_DIR / "recordings" / sid, ignore_errors=True)
    (config.REPORT_DIR / f"{sid}.md").unlink(missing_ok=True)


def _discard_cancelled_import(job_id: str, sid: str | None):
    if sid:
        _remove_import_session(sid)
    _update_import_job(job_id, status="cancelled", stage="已取消并清理临时文件",
                       session_id=None, progress=0)


def _run_import_job(job_id: str):
    with IMPORT_JOBS_LOCK:
        job = dict(IMPORT_JOBS.get(job_id) or {})
    if not job:
        return
    path = Path(job["path"])
    sid = None
    archive_complete = False
    try:
        _check_import_cancelled(job_id)
        if job["kind"] == "audio":
            utterances = _transcribe_import_audio(job_id, path)
            scene = "导入音频"
        else:
            _update_import_job(job_id, status="processing", stage="正在解析文字稿", progress=28)
            utterances = importer.extract_utterances(path)
            scene = "导入字幕稿" if path.suffix.lower() in {".srt", ".vtt"} else "导入文字稿"
        _check_import_cancelled(job_id)
        _update_import_job(job_id, stage=f"已提取 {len(utterances)} 段内容，正在归档", progress=72)
        sid = uuid.uuid4().hex[:12]
        store.create_session(sid, job["subject"], scene, job["interviewee_type"], [],
                             job["industry"], job["focus"], job["user_name"], job.get("owner_id"), job.get("location"))
        if job["kind"] == "audio":
            recording_dir = config.DATA_DIR / "recordings" / sid
            recording_dir.mkdir(parents=True, exist_ok=True)
            retained = recording_dir / f"original{path.suffix.lower()}"
            shutil.copy2(path, retained)
            store.set_audio_path(sid, str(retained))
        for seq, utterance in enumerate(utterances):
            if seq % 100 == 0:
                _check_import_cancelled(job_id)
            text = utterance["text"].strip()
            dims = dimensions.classify(text)
            match = re.match(r"^([^：:]{1,20})[：:]\s*", text)
            speaker = match.group(1) if match else ""
            store.add_utterance(sid, seq, int(utterance["start_ms"]), int(utterance["end_ms"]),
                                text, ",".join(dims), speaker)
        archive_complete = True
        _update_import_job(job_id, session_id=sid, stage="正在提炼摘要、结论、章节与待办", progress=84)
        _check_import_cancelled(job_id)
        _make_report(sid)
        _mark_import_ready(job_id)
        threading.Thread(target=telemetry.report_usage, args=(sid, job["user_name"]), daemon=True,
                         name=f"import-usage-{sid}").start()
    except ImportCancelled:
        _discard_cancelled_import(job_id, sid)
    except Exception as exc:
        if sid and not archive_complete:
            try:
                _remove_import_session(sid)
            except Exception:
                logger.exception("partial import cleanup failed job_id=%s sid=%s", job_id, sid)
            sid = None
        with IMPORT_JOBS_LOCK:
            current = IMPORT_JOBS.get(job_id)
            cancelled = bool(current and current.get("cancel_requested"))
            if current and not cancelled:
                current.update(status="failed", stage="处理失败", error=str(exc)[:500],
                               session_id=sid, updated_at=time.time())
        if cancelled:
            _discard_cancelled_import(job_id, sid)
            return
        if sid:
            store.finish_session(sid)
        logger.exception("material import failed job_id=%s sid=%s", job_id, sid)
    finally:
        try:
            shutil.rmtree(path.parent)
        except OSError:
            logger.warning("import temporary file cleanup failed job_id=%s path=%s", job_id, path.parent)


@app.post("/api/sessions/{sid}/audio")
async def upload_chunk(sid: str, request: Request, audio: UploadFile = File(...), chunk_id: str = Form("")):
    _require_session(sid, request, write=True)
    if not _session_exists(sid):
        raise HTTPException(404, "session not found")
    raw = await audio.read(config.MAX_AUDIO_BYTES + 1)
    if not raw:
        raise HTTPException(400, "empty audio")
    if len(raw) > config.MAX_AUDIO_BYTES:
        raise HTTPException(413, "audio chunk too large")
    if chunk_id and not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", chunk_id):
        raise HTTPException(400, "无效录音分片编号")
    seq = await run_in_threadpool(_process_chunk, sid, raw,
                                  audio.filename or "chunk.webm", chunk_id)
    return {"seq": seq, "audio_saved": True,
            "transcription_status": "ready" if store.get_utterance(sid, seq) else "no_speech"}


def _session_exists(sid: str) -> bool:
    with store.get() as db:
        return db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone() is not None


def _ffmpeg_executable() -> str:
    """优先使用系统 FFmpeg，否则使用 Python 依赖自带的可执行文件。"""
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise HTTPException(503, "FFmpeg 不可用，请重新安装项目依赖") from exc


def _process_chunk(sid: str, raw: bytes, filename: str, chunk_id="") -> int:
    """同步处理（在线程池跑）：存文件 → ffmpeg 转 16k wav → ASR → 入库 → 信号检测 → 追问建议。"""
    lock = CHUNK_LOCKS.setdefault(sid, threading.Lock())
    with lock:
        digest = hashlib.sha256(raw).hexdigest()
        receipt = store.get_audio_receipt(sid, chunk_id) if chunk_id else None
        if receipt:
            if receipt["digest"] != digest:
                raise HTTPException(409, "相同分片编号对应不同录音，拒绝覆盖")
            if receipt["status"] == "ready":
                return receipt["seq"]
        session = store.get_session_summary(sid)
        if not session or session["status"] != "live":
            raise HTTPException(409, "session is not live")
        # 逐字稿可能为空，但失败录音仍要占用编号，避免下一片覆盖原音。
        recording_dir = config.DATA_DIR / "recordings" / sid
        recording_dir.mkdir(parents=True, exist_ok=True)
        retained = [int(p.stem) for p in recording_dir.glob("[0-9][0-9][0-9][0-9][0-9].wav")]
        seq = receipt["seq"] if receipt else max(store.get_next_utterance_seq(sid), max(retained, default=-1) + 1)
        CHUNK_SEQ[sid] = seq + 1
        start_ms = 0
        for index in sorted(i for i in retained if i < seq):
            with wave.open(str(recording_dir / f"{index:05d}.wav"), "rb") as audio_part:
                start_ms += round(audio_part.getnframes() * 1000 / audio_part.getframerate())

        safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1]
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", safe_name)[:80]
        if safe_name in {"", ".", ".."}:
            safe_name = "chunk.webm"
        session_dir = config.UPLOAD_DIR / sid
        session_dir.mkdir(parents=True, exist_ok=True)
        raw_path = session_dir / f"{seq:05d}_{safe_name}"
        wav_path = raw_path.with_name(f"{raw_path.stem}_16k.wav")
        raw_path.write_bytes(raw)
        try:
            try:
                subprocess.run(
                    [_ffmpeg_executable(), "-y", "-i", str(raw_path), "-ac", "1", "-ar", "16000",
                     "-f", "wav", str(wav_path)],
                    check=True, capture_output=True, timeout=120)
            except subprocess.CalledProcessError as e:
                message = e.stderr.decode(errors="replace")[-500:]
                raise HTTPException(422, f"ffmpeg failed: {message}") from e
            except subprocess.TimeoutExpired as e:
                raise HTTPException(504, "ffmpeg timeout") from e
            except OSError as e:
                raise HTTPException(503, "FFmpeg 启动失败，请重新安装项目依赖") from e

            shutil.copy2(wav_path, recording_dir / f"{seq:05d}.wav")
            with wave.open(str(wav_path), "rb") as audio_part:
                end_ms = start_ms + round(audio_part.getnframes() * 1000 / audio_part.getframerate())
            if chunk_id:
                store.save_audio_receipt(sid, chunk_id, digest, seq)
            if receipt and store.get_utterance(sid, seq):
                store.complete_audio_receipt(sid, chunk_id)
                return seq
            try:
                text = asr_dashscope.transcribe(str(wav_path))
            except Exception as e:
                logger.exception("ASR failed sid=%s seq=%s", sid, seq)
                publish(sid, {"type": "error", "message": f"转写失败：{e}"})
                raise HTTPException(502, "录音已保存，转写失败；重试不会重复保存录音") from e

            text = text.strip()
            if text:
                _handle_transcript(sid, seq, start_ms, end_ms, text)
            if chunk_id:
                store.complete_audio_receipt(sid, chunk_id)
            return seq
        finally:
            for path in (raw_path, wav_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("temporary audio cleanup failed path=%s", path)


def _handle_transcript(sid: str, seq: int, start_ms: int, end_ms: int, text: str):
    """一条转写文本的完整处理：入库 → 维度归档 → 信号 → 追问建议 → 覆盖度提醒。"""
    dims = dimensions.classify(text)
    store.add_utterance(sid, seq, start_ms, end_ms, text, ",".join(dims))
    try:
        coverage_ai.schedule(sid)
    except Exception:
        logger.warning("问透评估排队暂时失败 sid=%s", sid)
    publish(sid, {"type": "transcript", "seq": seq, "event_id": f"transcript:{seq}",
                  "start_ms": start_ms, "end_ms": end_ms, "text": text,
                  "dims": dims})
    publish(sid, {"type": "coverage", "coverage": _coverage(sid)})
    summary = store.get_session_summary(sid)
    if summary and summary.get("subject_source") == "default" and summary.get("utterance_count", 0) >= 5:
        _schedule_auto_naming(sid, "early")

    hits = signals.detect(text)
    if hits:
        publish(sid, {"type": "signal", "seq": seq, "signals": hits})

        _schedule_followup(sid, hits)

    # 覆盖度提醒：≥3 句转写后，每 5 分钟最多推一次
    cov = _coverage(sid)
    n_utt = len(store.list_utterances(sid, last_n=1000))
    if n_utt >= 3 and cov["missing"] and cov.get("analysis_status") == "ready":
        if time.time() - _last_gap_push.get(sid, 0) >= 300:
            gap = dimensions.gap_suggestion_payload(cov)
            if gap:
                _last_gap_push[sid] = time.time()
                sid_sug = store.add_suggestion(sid, "coverage", gap)
                publish(sid, {"type": "suggestion", "kind": "coverage",
                              "id": sid_sug, "event_id": f"suggestion:{sid_sug}", **gap})


_last_gap_push: dict[str, float] = {}


def _schedule_followup(sid, hits):
    lock = FOLLOWUP_LOCKS.setdefault(sid, threading.Lock())
    if not lock.acquire(blocking=False):
        return
    if time.time() - max(FOLLOWUP_STARTED.get(sid, 0), store.recent_suggestion_time(sid)) < config.SUGGEST_COOLDOWN_SEC:
        lock.release()
        return
    FOLLOWUP_STARTED[sid] = time.time()
    def generate():
        try:
            us = store.list_utterances(sid, last_n=20)
            sug = kimi.gen_followup(_session_field(sid, "interviewee_type"), _session_field(sid, "subject"),
                "\n".join(f"[{u['seq']}] {u['text']}" for u in us), hits, _outline_remaining(sid),
                _session_field(sid, "industry"), _session_field(sid, "focus"),
                _location_text(_session_field(sid, "location")))
            with CHUNK_LOCKS.setdefault(sid, threading.Lock()):
                summary = store.get_session_summary(sid)
                if sug and summary and summary["status"] == "live":
                    sid_sug = store.add_suggestion(sid, "follow_up", sug)
                    publish(sid, {"type": "suggestion", "kind": "follow_up", "id": sid_sug,
                                  "event_id": f"suggestion:{sid_sug}", **sug})
        except Exception:
            logger.exception("follow-up unavailable sid=%s", sid)
        finally:
            lock.release()
    threading.Thread(target=generate, daemon=True, name=f"followup-{sid}").start()


def _coverage(sid: str) -> dict:
    return coverage_ai.view(sid)


@app.get("/api/sessions/{sid}/coverage")
async def get_coverage(sid: str, request: Request):
    _require_session(sid, request)
    return JSONResponse(await run_in_threadpool(_coverage, sid), headers={"Cache-Control": "no-store"})


@app.post("/api/sessions/{sid}/coverage/analyze")
async def analyze_coverage(sid: str, request: Request):
    _require_session(sid, request, write=True)
    task = await run_in_threadpool(coverage_ai.schedule, sid, True)
    result = await run_in_threadpool(_coverage, sid)
    return JSONResponse({**result, "task_id": task["id"] if task else result.get("task_id")},
                        status_code=202 if task else 200, headers={"Cache-Control": "no-store"})


def _session_field(sid: str, field: str) -> str:
    with store.get() as db:
        r = db.execute(f"SELECT {field} FROM sessions WHERE id=?", (sid,)).fetchone()
    return r[0] if r else ""


def _outline_remaining(sid: str) -> list[str]:
    return [item["item"] for item in _coverage(sid)["dims"] if item.get("item") and not item["covered"]]


@app.get("/api/sessions/{sid}/events")
async def events(sid: str, request: Request):
    """SSE：转写、信号、建议实时推送。"""
    _require_session(sid, request)
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    BUS.setdefault(sid, []).append(q)

    async def stream():
        try:
            # 回放历史
            for u in store.list_utterances(sid, last_n=500):
                ev = {"type": "transcript", **u, "event_id": f"transcript:{u['seq']}"}
                yield f"id: {ev['event_id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
            for s in store.list_suggestions(sid):
                if s["status"] == "new" and s["kind"] != "auto_naming":
                    ev = {"type": "suggestion", "kind": s["kind"], **json.loads(s["payload"]), "id": s["id"], "event_id": f"suggestion:{s['id']}"}
                    yield f"id: {ev['event_id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
            yield "data: {\"type\": \"ready\"}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                except asyncio.TimeoutError:
                    event = None
                try:
                    user = await employee_auth.current_user(request)
                except HTTPException:
                    break
                if store.session_access(sid, user["id"]) is None:
                    break
                if event is None:
                    yield ": keepalive\n\n"
                    continue
                yield f"id: {event['event_id']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            if q in BUS.get(sid, []):
                BUS[sid].remove(q)

    from fastapi.responses import StreamingResponse
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/sessions/{sid}/state")
async def state(sid: str, request: Request):
    _require_session(sid, request)
    with store.get() as db:
        s = db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        raise HTTPException(404)
    d = dict(s)
    d["outline"] = json.loads(d["outline"] or "[]")
    d["report_status"] = task_api.report_status(sid, REPORT_JOBS.get(sid))
    d["has_report"] = d["report_status"] == "ready"
    d["display_subject"] = _display_subject(d["subject"], d["created_at"])
    d["utterances"] = store.list_utterances(sid, last_n=500)
    d["suggestions"] = [s for s in store.list_suggestions(sid) if s["kind"] != "auto_naming"]
    d["coverage"] = _coverage(sid)
    d.pop("audio_path", None)
    return d


@app.post("/api/suggestions/{sug_id}/{action}")
async def act_on_suggestion(sug_id: int, action: str, request: Request):
    if action not in ("asked", "dismissed"):
        raise HTTPException(400)
    with store.get() as db:
        row = db.execute("SELECT session_id FROM suggestions WHERE id=?", (sug_id,)).fetchone()
    if not row:
        raise HTTPException(404, "suggestion not found")
    _require_session(row["session_id"], request, write=True)
    if not store.set_suggestion_status(sug_id, action):
        raise HTTPException(404, "suggestion not found")
    return {"ok": True}


@app.post("/api/sessions/{sid}/outline_asked")
async def outline_asked(sid: str, body: dict, request: Request):
    _require_session(sid, request, write=True)
    item = _body_text(body, "item", "", 200)
    if item:
        if item not in json.loads(_session_field(sid, "outline") or "[]"):
            raise HTTPException(400, "此问题不属于本次筛选的访谈提纲")
        sug_id = store.add_suggestion(sid, "outline", {"item": item, "asked": True})
        store.set_suggestion_status(sug_id, "asked")
    publish(sid, {"type": "coverage", "coverage": _coverage(sid)})
    return {"ok": True}


@app.post("/api/sessions/{sid}/dim_asked")
async def dim_asked(sid: str, body: dict, request: Request):
    """手动把某维度标记为已摸排（实习生点仪表盘上的维度）。"""
    _require_session(sid, request, write=True)
    dim = _body_text(body, "dim", "", 30)
    if dim not in dimensions.DIM_MAP:
        raise HTTPException(400, "invalid dimension")
    sug_id = store.add_suggestion(sid, "dim", {"dim": dim})
    store.set_suggestion_status(sug_id, "asked")
    publish(sid, {"type": "coverage", "coverage": _coverage(sid)})
    return {"ok": True}


@app.post("/api/sessions/{sid}/simulate")
async def simulate(sid: str, body: dict, request: Request):
    """测试入口：直接注入一段'受访者说的话'，走完整处理管线。
    仅当服务器设置 FANGTAN_ALLOW_SIM=1 时开放（默认关闭，防滥用）。"""
    if os.getenv("FANGTAN_ALLOW_SIM") != "1":
        raise HTTPException(403, "simulate disabled")
    _require_session(sid, request, write=True)
    text = _body_text(body, "text", "", 5000)
    if not text:
        raise HTTPException(400, "empty text")
    seq = await run_in_threadpool(_process_simulated_transcript, sid, text)
    return {"seq": seq}


def _process_simulated_transcript(sid, text):
    lock = CHUNK_LOCKS.setdefault(sid, threading.Lock())
    with lock:
        summary = store.get_session_summary(sid)
        if not summary or summary["status"] != "live":
            raise HTTPException(409, "session is not live")
        seq = store.get_next_utterance_seq(sid)
        CHUNK_SEQ[sid] = seq + 1
        _handle_transcript(sid, seq, seq * config.CHUNK_MS, (seq + 1) * config.CHUNK_MS, text)
        return seq


@app.post("/api/sessions/{sid}/finish")
async def finish(sid: str, request: Request, background: bool = False):
    background = background or request.headers.get("X-Background-Task") == "1"
    _require_session(sid, request, write=True)
    summary = store.get_session_summary(sid)
    if not summary:
        raise HTTPException(404)
    importing = task_api.latest("import", sid)
    if importing and importing["status"] in task_api.ACTIVE:
        return JSONResponse({"session_id": sid, "task_id": importing["id"],
                             "report_status": "generating"}, status_code=202)
    if importing and not store.list_utterances(sid, last_n=1):
        raise HTTPException(409, "材料尚未成功归档，请重新导入后生成纪要")
    with store.get() as db:
        pending_audio = db.execute("SELECT 1 FROM audio_receipts WHERE session_id=? AND status='pending' LIMIT 1", (sid,)).fetchone()
    if pending_audio:
        raise HTTPException(409, "录音已保存，仍有分片待转写；可以离开页面稍后重试，完成后再结束访谈")
    await run_in_threadpool(_close_session, sid)
    if not store.list_utterances(sid, last_n=1):
        # Silence is a completed recording, not a failed or endlessly queued report.
        REPORT_JOBS.pop(sid, None)
        return {"session_id": sid, "report_status": "empty", "report": None}
    if summary.get("subject_source") == "default":
        _schedule_auto_naming(sid, "finish")
    if store.get_report(sid) is not None:
        REPORT_JOBS.pop(sid, None)
        asyncio.create_task(run_in_threadpool(
            telemetry.report_usage, sid, summary.get("user_name", "")
        ))
        return {"report": store.get_report(sid), "report_status": "ready"}
    if background:
        with REPORT_LOCKS.setdefault(sid, threading.Lock()):
            existing = task_api.latest("report", sid)
            if not existing or existing["status"] not in task_api.ACTIVE:
                existing = job_queue.enqueue("report", {"sid": sid}, owner_id=_user(request)["id"],
                    resource_id=sid, dedupe_key=uuid.uuid4().hex)
        return JSONResponse({"session_id": sid, "task_id": existing["id"], "report_status": "generating"}, status_code=202)
    REPORT_JOBS[sid] = "generating"
    try:
        md = await run_in_threadpool(_make_report, sid)
    except Exception as e:
        REPORT_JOBS[sid] = "failed"
        logger.exception("report generation failed sid=%s", sid)
        raise HTTPException(502, f"report generation failed: {e}")
    REPORT_JOBS[sid] = "ready"
    REPORT_JOBS.pop(sid, None)
    publish(sid, {"type": "report_ready"})
    asyncio.create_task(run_in_threadpool(
        telemetry.report_usage, sid, summary.get("user_name", "")
    ))
    return {"report": md, "report_status": "ready"}


@app.get("/api/sessions/{sid}/report-job")
async def get_report_job(sid: str, request: Request):
    _require_session(sid, request)
    task = task_api.report_task(sid)
    status = task_api.report_status(sid)
    return JSONResponse({"report_status": status, "task_id": task["id"] if task else None,
        "stage": task_api.public_stage(task), "progress": round(task["progress"] * 100) if task else 0,
        "error": "纪要生成未完成，请重试" if status == "failed" else None}, headers={"Cache-Control": "no-store"})


def _iso_timestamp(timestamp: float) -> str:
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _development_commits(limit: int) -> list[dict]:
    project_root = Path(__file__).resolve().parent.parent
    try:
        output = subprocess.check_output(
            ["git", "-C", str(project_root), "log", f"-{limit}",
             "--date=iso-strict", "--pretty=format:%H%x1f%aI%x1f%an%x1f%s"],
            text=True,
            encoding="utf-8",
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        commits = []
        for line in output.splitlines():
            commit_hash, created_at, author, message = line.split("\x1f", 3)
            commits.append({"hash": commit_hash, "time": created_at,
                            "author": author, "message": message})
        if commits:
            return commits
    except (OSError, subprocess.SubprocessError, ValueError):
        pass

    candidates = [
        path for path in project_root.rglob("*")
        if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts
    ]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return [{
        "hash": f"file-{int(path.stat().st_mtime)}-{index}",
        "time": _iso_timestamp(path.stat().st_mtime),
        "author": "部署系统",
        "message": f"更新 {path.relative_to(project_root).as_posix()}",
        "files": [path.relative_to(project_root).as_posix()],
    } for index, path in enumerate(candidates[:limit])]


@app.get("/api/dev-progress")
async def development_progress(request: Request, limit: int = 20):
    expected = config.DEV_PROGRESS_TOKEN
    supplied = request.headers.get("authorization", "")
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    else:
        supplied = ""
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "invalid progress token")
    limit = max(1, min(limit, 50))
    commits = _development_commits(limit)
    project_root = Path(__file__).resolve().parent.parent
    marker = project_root / ".deployed"
    deployed_at = marker.stat().st_mtime if marker.exists() else max(
        (path.stat().st_mtime for path in project_root.rglob("*") if path.is_file()),
        default=time.time(),
    )
    version = commits[0]["hash"][:12] if commits else "unknown"
    return {
        "app": "访谈-体察Agentic系统",
        "version": version,
        "lastDeployedAt": _iso_timestamp(deployed_at),
        "generatedAt": _iso_timestamp(time.time()),
        "commits": commits,
    }


def _close_session(sid: str):
    lock = CHUNK_LOCKS.setdefault(sid, threading.Lock())
    with lock:
        if not _session_exists(sid):
            raise HTTPException(404, "session not found")
        store.finish_session(sid)
        _assemble_live_recording(sid)


def _assemble_live_recording(sid: str):
    parts_dir = config.DATA_DIR / "recordings" / sid
    parts = sorted(parts_dir.glob("[0-9][0-9][0-9][0-9][0-9].wav")) if parts_dir.exists() else []
    if not parts:
        return
    output = parts_dir / "recording.wav"
    if output.exists():
        store.set_audio_path(sid, str(output))
        return
    try:
        with wave.open(str(output), "wb") as target:
            expected = None
            for part in parts:
                with wave.open(str(part), "rb") as source:
                    signature = (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype())
                    if expected is None:
                        expected = signature
                        target.setparams(source.getparams())
                    elif signature != expected:
                        raise ValueError("audio chunk parameters differ")
                    target.writeframes(source.readframes(source.getnframes()))
        store.set_audio_path(sid, str(output))
        for part in parts:
            part.unlink(missing_ok=True)
    except (OSError, ValueError, wave.Error):
        output.unlink(missing_ok=True)
        logger.exception("recording assembly failed sid=%s", sid)


def _extract_report_todos(markdown: str) -> list[str]:
    from task_handlers import extract_report_todos
    return extract_report_todos(markdown)


def _make_report(sid: str) -> str:
    """保留同步客户端，但与 Worker 共享原子发布及来源校验。"""
    from task_handlers import handle_report
    chunk_lock = CHUNK_LOCKS.setdefault(sid, threading.Lock())
    lock = REPORT_LOCKS.setdefault(sid, threading.Lock())
    with chunk_lock, lock:
        handle_report({"sid": sid}, SessionContext(sid))
        markdown = store.get_report(sid)
        if markdown is None:
            raise ValueError("访谈已删除或纪要已失效")
        return markdown


@app.get("/asr-audio/{token}")
async def asr_audio(token: str):
    """给百炼拉取音频的一次性端点（仿 minutes-web）。"""
    path = asr_dashscope.pop_audio(token)
    if not path or not Path(path).exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")


@app.get("/api/sessions/{sid}/report.md")
async def report_md(sid: str, request: Request):
    _require_session(sid, request)
    md = store.get_report(sid)
    if md is None:
        raise HTTPException(404)
    return Response(md, media_type="text/markdown; charset=utf-8")


@app.get("/api/sessions/{sid}/transcript.txt")
async def transcript_text(sid: str, request: Request):
    _require_session(sid, request)
    return Response(_transcript_body(sid), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{sid}-transcript.txt"'})


@app.get("/api/sessions/{sid}/minutes.docx")
async def minutes_word(sid: str, request: Request):
    _require_session(sid, request)
    return Response(_word_body(sid),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{sid}-minutes.docx"'})


@app.get("/api/sessions/{sid}/speakers")
async def get_session_speakers(sid: str, request: Request):
    _require_session(sid, request)
    return _speaker_status(sid)


@app.get("/api/sessions/{sid}/speakers.txt")
async def download_session_speakers(sid: str, request: Request):
    _require_session(sid, request)
    content = _speaker_text(sid)
    if not content:
        raise HTTPException(404, "尚无发言人分离结果")
    return Response(content, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{sid}-speakers.txt"'})


@app.post("/api/sessions/{sid}/speakers/analyze", status_code=202)
async def analyze_session_speakers(sid: str, body: dict, request: Request):
    _require_session(sid, request, write=True)
    if body.get("consent") is not True:
        raise HTTPException(400, "须先确认已取得所有发言人的单独同意")
    summary = store.get_session_summary(sid)
    if not summary or summary["status"] != "done":
        raise HTTPException(409, "请先结束访谈并保存完整录音")
    _recording_file(sid)
    unavailable_reason = asr_dashscope.speaker_unavailable_reason()
    if unavailable_reason:
        raise HTTPException(503, unavailable_reason)
    with SPEAKER_JOBS_LOCK:
        existing = task_api.latest("speaker", sid)
        if existing and existing["status"] in task_api.ACTIVE:
            raise HTTPException(409, "发言人分析正在进行")
        job_id = uuid.uuid4().hex
        if not store.start_speaker_analysis(sid, job_id):
            raise HTTPException(409, "访谈状态已改变，请刷新后重试")
        SPEAKER_JOBS[sid] = {"job_id": job_id, "status": "processing"}
    job_queue.enqueue("speaker", {"sid": sid, "job_id": job_id}, owner_id=_user(request)["id"],
                      resource_id=sid, dedupe_key=job_id, task_id=job_id)
    return _speaker_status(sid)


@app.delete("/api/sessions/{sid}/speakers")
async def delete_session_speakers(sid: str, request: Request):
    _require_session(sid, request, write=True)
    job_queue.cancel_resource(sid, kind="speaker")
    store.clear_speaker_analysis(sid)
    with SPEAKER_JOBS_LOCK:
        SPEAKER_JOBS.pop(sid, None)
    return {"ok": True, "status": "none"}


SHARE_LINK_KINDS = {"report", "transcript", "word", "audio"}
SHARE_LINK_TTL = 7 * 86400


@app.get("/api/sessions/{sid}/share-links")
async def list_session_share_links(sid: str, request: Request):
    _require_session(sid, request, write=True)
    return {"links": store.list_share_links(sid)}


@app.post("/api/sessions/{sid}/share-links")
async def create_session_share_link(sid: str, request: Request, body: dict):
    _require_session(sid, request, write=True)
    kind = _body_text(body, "kind", "", 20)
    if kind not in SHARE_LINK_KINDS:
        raise HTTPException(400, "不支持的分享类型")
    if kind == "audio":
        _recording_file(sid)
    elif kind in {"report", "word"} and store.get_report(sid) is None:
        raise HTTPException(409, "请先生成纪要")
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    expires_at = time.time() + SHARE_LINK_TTL
    link_id = store.create_share_link(sid, kind, token_hash, _user(request)["id"], expires_at)
    return {"id": link_id, "kind": kind, "expires_at": expires_at,
            "path": "api/share/" + token,
            "notice": "持有链接的人可在7天内查看该项内容；可随时撤销。"}


@app.delete("/api/sessions/{sid}/share-links/{link_id}")
async def revoke_session_share_link(sid: str, link_id: int, request: Request):
    _require_session(sid, request, write=True)
    if not store.revoke_share_link(sid, link_id):
        raise HTTPException(404, "分享链接不存在或已撤销")
    return {"ok": True}


@app.get("/api/share/{token}")
async def open_session_share_link(token: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise HTTPException(404, "分享链接不存在或已失效")
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    link = store.get_share_link(token_hash)
    if not link or not store.get_session_summary(link["session_id"]):
        raise HTTPException(404, "分享链接不存在或已失效")
    sid = link["session_id"]
    headers = {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer",
               "X-Content-Type-Options": "nosniff"}
    if link["kind"] == "report":
        content = store.get_report(sid)
        if content is None:
            raise HTTPException(404, "纪要不存在")
        return Response(content, media_type="text/markdown; charset=utf-8", headers=headers)
    if link["kind"] == "transcript":
        return Response(_transcript_body(sid), media_type="text/plain; charset=utf-8", headers=headers)
    if link["kind"] == "word":
        headers["Content-Disposition"] = f'attachment; filename="{sid}-minutes.docx"'
        return Response(_word_body(sid),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers=headers)
    path = _recording_file(sid)
    return FileResponse(path, media_type="audio/wav" if path.suffix == ".wav" else "application/octet-stream",
                        headers=headers)


@app.get("/api/projects")
async def list_projects(request: Request):
    items = store.list_projects(_user(request)["id"] if config.AUTH_ENABLED else None)
    return {"projects": sorted({row["name"] for row in items}), "items": items}


@app.get("/api/projects/overview")
async def project_overview(request: Request):
    return {"projects": store.list_projects(_user(request)["id"] if config.AUTH_ENABLED else None)}


def _require_project(project_id: str, request: Request, write=False):
    user_id = _user(request)["id"]
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    owner = not config.AUTH_ENABLED or project["owner_id"] == user_id
    if not owner:
        visible = store.list_sessions(1, 0, user_id, project_id=project_id)
        if not visible["total"]:
            raise HTTPException(404, "项目不存在")
        if write:
            raise HTTPException(403, "只有项目创建人可以改名或合并访谈")
    return {**project, "access": "owner" if owner else "shared"}


@app.post("/api/projects")
async def create_project(body: dict, request: Request):
    name = _body_text(body, "name", "", 80)
    if not name:
        raise HTTPException(400, "请填写项目名称")
    project = store.create_project(name, _user(request)["id"])
    return {**project, "access": "owner"}


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str, request: Request, limit: int = 100, offset: int = 0):
    project = _require_project(project_id, request)
    page = store.list_sessions(limit, offset, _user(request)["id"] if config.AUTH_ENABLED else None,
                               project_id=project_id)
    for item in page["items"]:
        item["display_subject"] = _display_subject(item["subject"], item["created_at"])
        item["report_status"] = task_api.report_status(item["id"], REPORT_JOBS.get(item["id"]))
        item["merge_eligible"] = item["status"] == "done" and item["utterance_count"] > 0
    records = store.list_project_records(project_id) if project["access"] == "owner" else []
    for record in records:
        record["sources"] = [{key: value for key, value in item.items() if key != "fingerprint"}
                             for item in record["sources"]]
    return {"project": project, **page, "records": records}


@app.patch("/api/projects/{project_id}")
async def rename_project(project_id: str, body: dict, request: Request):
    _require_project(project_id, request, write=True)
    name = _body_text(body, "name", "", 80)
    if not name:
        raise HTTPException(400, "请填写项目名称")
    try:
        store.rename_project(project_id, name)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "已有同名项目，请换一个名称") from exc
    return {"ok": True, "name": name}


@app.get("/api/projects/{project_id}/bundle.zip")
async def download_project_bundle(project_id: str, request: Request):
    _require_project(project_id, request)
    if not PROJECT_BUNDLE_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "当前正在生成其他资料包，请稍后重试")
    artifact = None
    try:
        artifact = await run_in_threadpool(project_bundle.build_bundle, project_id, _user(request)["id"])
        await run_in_threadpool(artifact.verify)
        return TemporaryDownloadResponse(artifact.path, cleanup=artifact.cleanup, media_type="application/zip", filename=artifact.filename,
                                         headers={"Cache-Control": "private, no-store"})
    except project_bundle.BundleError as exc:
        if artifact:
            artifact.cleanup()
        raise HTTPException(exc.status_code, exc.detail) from exc
    except BaseException:
        # 响应交给 TemporaryDownloadResponse 前被取消或失败时，也不遗留资料包。
        if artifact:
            artifact.cleanup()
        raise
    finally:
        PROJECT_BUNDLE_SLOTS.release()


def _load_project_materials(project_id, session_ids, user_id):
    return load_project_materials(project_id, session_ids, user_id)


def _run_project_record(record_id):
    try:
        record = store.get_project_record(record_id)
        project = store.get_project(record["project_id"])
        materials, sources = _load_project_materials(record["project_id"],
                                                     [item["id"] for item in record["sources"]], record["owner_id"])
        if sources != record["sources"]:
            raise ValueError("访谈材料已经改变，请重新勾选后生成")
        markdown = project_records.generate(record["title"], project["name"], materials,
                    lambda stage: store.update_project_record(record_id, "generating", stage))
        def verify_sources():
            _, current_sources = _load_project_materials(record["project_id"],
                                                         [item["id"] for item in sources], record["owner_id"])
            if current_sources != sources:
                raise ValueError("生成期间访谈材料已经改变，请重新生成")
        store.publish_project_record(record_id, markdown, verify_sources)
    except Exception as exc:
        logger.exception("project record generation failed record=%s", record_id)
        store.update_project_record(record_id, "failed", "生成失败", error=str(exc)[:300])


@app.post("/api/projects/{project_id}/records")
async def generate_project_record(project_id: str, body: dict, request: Request):
    project = _require_project(project_id, request, write=True)
    title = _body_text(body, "title", "", 120) or f"{project['name']}·总访谈记录"
    session_ids = body.get("session_ids")
    if (not isinstance(session_ids, list) or not 2 <= len(session_ids) <= 50
            or any(not isinstance(sid, str) or not sid or len(sid) > 32 for sid in session_ids)
            or len(set(session_ids)) != len(session_ids)):
        raise HTTPException(400, "请选择同一项目下 2–50 条不同的已结束访谈")
    try:
        _, sources = await run_in_threadpool(_load_project_materials, project_id, session_ids, _user(request)["id"])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    with PROJECT_RECORD_LOCK:
        for existing in store.list_project_records(project_id):
            if task_api.project_view(existing)["status"] == "generating" and existing["sources"] == sources and existing["title"] == title:
                return JSONResponse({"record_id": existing["id"], "status": "generating"}, status_code=202)
        record_id = uuid.uuid4().hex[:16]
        store.create_project_record(record_id, project_id, _user(request)["id"], title, sources)
        job_queue.enqueue("project_record", {"record_id": record_id}, owner_id=_user(request)["id"],
                          resource_id=record_id, dedupe_key=record_id, task_id=record_id)
    return JSONResponse({"record_id": record_id, "status": "generating"}, status_code=202)


def _read_project_record(record_id, request):
    record = store.get_project_record(record_id)
    if not record:
        raise HTTPException(404, "总访谈记录不存在")
    _require_project(record["project_id"], request, write=True)
    task_api.project_view(record)
    if record["status"] == "ready":
        try:
            _, sources = _load_project_materials(record["project_id"], [item["id"] for item in record["sources"]],
                                                 _user(request)["id"])
            if sources != record["sources"]:
                raise ValueError("来源访谈的逐字稿或纪要已经修改，请重新勾选生成")
        except ValueError as exc:
            record.update(status="stale", error=str(exc), markdown="")
    return record


@app.get("/api/project-records/{record_id}")
async def get_project_record(record_id: str, request: Request):
    record = await run_in_threadpool(_read_project_record, record_id, request)
    record["sources"] = [{key: value for key, value in item.items() if key != "fingerprint"} for item in record["sources"]]
    return JSONResponse(record, headers={"Cache-Control": "no-store"})


@app.get("/api/project-records/{record_id}/export")
async def export_project_record(record_id: str, request: Request, format: str = "docx"):
    record = await run_in_threadpool(_read_project_record, record_id, request)
    if record["status"] != "ready":
        raise HTTPException(409, record["error"] or "总访谈记录尚未生成")
    if format == "md":
        content, media, suffix = record["markdown"], "text/markdown; charset=utf-8", "md"
    elif format == "docx":
        content = await run_in_threadpool(word_export.render_minutes, record["title"], record["markdown"])
        media, suffix = "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
    else:
        raise HTTPException(400, "仅支持 Word 或 Markdown 导出")
    from urllib.parse import quote
    filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", record["title"])[:100] or "总访谈记录"
    return Response(content, media_type=media, headers={"Cache-Control": "no-store",
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename + '.' + suffix)}"})


@app.get("/api/management/overview")
async def management_overview(request: Request):
    user_id = _user(request)["id"]
    with store.get() as db:
        stats = db.execute("""SELECT COUNT(*) AS interviews,
            SUM(CASE WHEN r.session_id IS NOT NULL THEN 1 ELSE 0 END) AS reports,
            SUM(CASE WHEN s.audio_path!='' THEN 1 ELSE 0 END) AS recordings
            FROM sessions s LEFT JOIN reports r ON r.session_id=s.id
            WHERE (?=0 OR s.owner_id=?) AND s.status!='deleting'""", (config.AUTH_ENABLED, user_id)).fetchone()
        duration = db.execute("""SELECT COALESCE(SUM(last_ms),0) AS duration_ms FROM (
            SELECT MAX(u.end_ms) AS last_ms FROM utterances u
            JOIN sessions s ON s.id=u.session_id WHERE (?=0 OR s.owner_id=?) AND s.status!='deleting'
                AND s.scene!='导入文字稿'
            GROUP BY s.id)""", (config.AUTH_ENABLED, user_id)).fetchone()["duration_ms"]
        rows = db.execute("""SELECT t.*,s.subject,s.project_name,s.user_name,s.owner_id FROM session_todos t
            JOIN sessions s ON s.id=t.session_id WHERE s.status!='deleting' AND
            (?=0 OR s.owner_id=? OR EXISTS(SELECT 1 FROM session_shares sh WHERE sh.session_id=s.id AND sh.user_id=?))
            ORDER BY t.done,t.created_at DESC LIMIT 500""", (config.AUTH_ENABLED, user_id, user_id)).fetchall()
    return {"stats": {"interviews": stats["interviews"], "reports": stats["reports"] or 0,
                      "recordings": stats["recordings"] or 0,
                      "duration_minutes": round((duration or 0)/60000)},
            "todos": [{**dict(row), "access": "owner" if not config.AUTH_ENABLED or row["owner_id"] == user_id else "shared"}
                      for row in rows]}


@app.put("/api/sessions/{sid}/report")
async def replace_report(sid: str, body: dict, request: Request):
    _require_session(sid, request, write=True)
    markdown = _body_text(body, "markdown", "", 120000)
    if not markdown:
        raise HTTPException(400, "纪要不能为空")
    if store.get_report(sid) is None:
        raise HTTPException(404, "report not found")
    store.save_report(sid, markdown)
    try:
        (config.REPORT_DIR / f"{sid}.md").write_text(markdown, encoding="utf-8")
    except OSError:
        logger.exception("report file copy failed sid=%s", sid)
    return {"ok": True}


@app.get("/api/sessions/{sid}/recording")
async def get_recording(sid: str, request: Request, download: bool = False):
    _require_session(sid, request)
    path = _recording_file(sid)
    media_type = {
        ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
        ".mp4": "audio/mp4", ".webm": "audio/webm", ".ogg": "audio/ogg",
        ".flac": "audio/flac", ".aac": "audio/aac", ".wma": "audio/x-ms-wma",
    }.get(path.suffix, "application/octet-stream")
    return FileResponse(path, media_type=media_type,
                        filename=f"{sid}{path.suffix}" if download else None)


@app.delete("/api/sessions/{sid}/recording")
async def delete_recording(sid: str, request: Request):
    _require_session(sid, request, write=True)
    lock = CHUNK_LOCKS.setdefault(sid, threading.Lock())
    with lock:
        summary = store.get_session_summary(sid)
        if not summary:
            raise HTTPException(404, "session not found")
        if summary["status"] == "live":
            raise HTTPException(409, "请先结束访谈，再删除原始录音")
        _recording_file(sid)
        recording_dir = (config.DATA_DIR / "recordings" / sid).resolve()
        try:
            shutil.rmtree(recording_dir)
        except OSError as exc:
            raise HTTPException(422, "原始录音删除失败，请稍后重试") from exc
        store.delete_recording_metadata(sid)
        _clear_speaker_job(sid)
    return {"ok": True, "has_audio": False}


@app.delete("/api/sessions/{sid}/utterances/{seq}")
async def delete_recording_segment(sid: str, seq: int, request: Request):
    _require_session(sid, request, write=True)
    lock = CHUNK_LOCKS.setdefault(sid, threading.Lock())
    with lock:
        summary = store.get_session_summary(sid)
        if not summary:
            raise HTTPException(404, "session not found")
        if summary["status"] == "live":
            raise HTTPException(409, "请先结束访谈，再删除录音片段")
        utterance = store.get_utterance(sid, seq)
        if not utterance:
            raise HTTPException(404, "utterance not found")
        path = _recording_file(sid)
        removed_ms = _remove_wav_range(path, int(utterance["start_ms"] or 0),
                                       int(utterance["end_ms"] or 0))
        if not store.delete_utterance_and_shift(sid, seq, utterance["start_ms"],
                                                utterance["end_ms"], removed_ms):
            raise HTTPException(404, "utterance not found")
    report_path = config.REPORT_DIR / f"{sid}.md"
    report_path.unlink(missing_ok=True)
    _clear_speaker_job(sid)
    detail = store.get_session_summary(sid)
    return {"ok": True, "report_cleared": True,
            "utterances": store.list_utterances(sid, last_n=10000),
            "has_audio": bool(detail and detail.get("audio_path"))}


@app.patch("/api/sessions/{sid}/project")
async def update_project(sid: str, body: dict, request: Request):
    _require_session(sid, request, write=True)
    if "project_id" in body:
        project_id = _body_text(body, "project_id", "", 32) or None
        project = _require_project(project_id, request, write=True) if project_id else None
        store.assign_session_project(sid, project_id)
        return {"project_id": project_id, "project_name": project["name"] if project else ""}
    name = _body_text(body, "project_name", "", 80)
    project = store.update_session_project(sid, name)
    return {"project_name": name, "project_id": project["id"] if project else None}


@app.get("/api/sessions/{sid}/shares")
async def get_shares(sid: str, request: Request):
    _require_session(sid, request, write=True)
    return {"shares": store.list_shares(sid)}


@app.post("/api/sessions/{sid}/shares")
async def create_share(sid: str, body: dict, request: Request):
    _require_session(sid, request, write=True)
    try:
        target_id = int(body.get("user_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "invalid user_id")
    name = _body_text(body, "user_name", "", 50)
    if target_id == _user(request)["id"] or not name:
        raise HTTPException(400, "请选择其他在职员工")
    from urllib.parse import quote
    data = await employee_auth.upstream("/api/employees?search=" + quote(name), method="GET")
    valid = any(int(row.get("id") or 0) == target_id and row.get("name") == name and
                not (row.get("expired") or row.get("is_expired")) for row in data.get("data") or [])
    if not valid:
        raise HTTPException(400, "员工不存在或账号已过期")
    store.add_share(sid, target_id, name)
    return {"shares": store.list_shares(sid)}


@app.delete("/api/sessions/{sid}/shares/{user_id}")
async def delete_share(sid: str, user_id: int, request: Request):
    _require_session(sid, request, write=True)
    store.remove_share(sid, user_id)
    return {"shares": store.list_shares(sid)}


@app.get("/api/sessions/{sid}/todos")
async def get_todos(sid: str, request: Request):
    _require_session(sid, request)
    return {"todos": store.list_todos(sid)}


@app.post("/api/sessions/{sid}/todos")
async def create_todo(sid: str, body: dict, request: Request):
    _require_session(sid, request, write=True)
    text = _body_text(body, "text", "", 500)
    if not text:
        raise HTTPException(400, "待办内容不能为空")
    store.add_todo(sid, text)
    return {"todos": store.list_todos(sid)}


@app.patch("/api/sessions/{sid}/todos/{todo_id}")
async def update_todo(sid: str, todo_id: int, body: dict, request: Request):
    _require_session(sid, request, write=True)
    if not isinstance(body.get("done"), bool):
        raise HTTPException(400, "done must be boolean")
    if not store.set_todo_done(sid, todo_id, body["done"]):
        raise HTTPException(404, "todo not found")
    return {"todos": store.list_todos(sid)}


@app.delete("/api/sessions/{sid}/todos/{todo_id}")
async def remove_todo(sid: str, todo_id: int, request: Request):
    _require_session(sid, request, write=True)
    if not store.delete_todo(sid, todo_id):
        raise HTTPException(404, "todo not found")
    return {"todos": store.list_todos(sid)}


@app.patch("/api/sessions/{sid}/utterances/{seq}/speaker")
async def update_speaker(sid: str, seq: int, body: dict, request: Request):
    _require_session(sid, request, write=True)
    speaker = _body_text(body, "speaker", "", 40)
    if not store.set_speaker(sid, seq, speaker):
        raise HTTPException(404, "utterance not found")
    return {"speaker": speaker}


@app.post("/api/sessions/{sid}/ask")
async def ask_session(sid: str, body: dict, request: Request):
    access = _require_session(sid, request)
    question = _body_text(body, "question", "", 500)
    if not question:
        raise HTTPException(400, "请输入问题")
    transcript = store.list_utterances(sid, last_n=10000)
    try:
        answer = await run_in_threadpool(kimi.answer_from_transcript, question, transcript,
                                         store.get_report(sid) or "", _session_field(sid, "scene"))
    except Exception as exc:
        logger.exception("session question failed sid=%s", sid)
        raise HTTPException(502, "逐字稿问答暂时失败") from exc
    if access == "owner":
        store.add_question(sid, _user(request)["id"], question, answer)
    return {"answer": answer, "questions": store.list_questions(sid)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=config.PORT)
