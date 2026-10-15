"""私有人生口述史 API。每个入口显式校验独立登录和档案所有者。"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import tempfile
import threading
import uuid
import wave
from pathlib import Path
from urllib.parse import quote

import imageio_ffmpeg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.concurrency import run_in_threadpool

import asr_dashscope
import config
import employee_auth
import job_queue
import task_api
import word_export
from memoir import auth, services, store


router = APIRouter(prefix="/api/memoir", tags=["时叙录"])
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_AUDIO_SECONDS = 120
AUDIO_MIME = {".wav": "audio/wav", ".webm": "audio/webm", ".m4a": "audio/mp4", ".mp4": "audio/mp4",
              ".mp3": "audio/mpeg", ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac"}
_slots = threading.BoundedSemaphore(4)
_locks_guard = threading.Lock()
_turn_locks: dict[str, threading.Lock] = {}


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Body):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=512)
    privacy_consent: StrictBool = False  # 兼容前端提示；材料处理另在建档时单独同意。


class NewProfile(Body):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    cloud_consent: StrictBool = False
    recording_consent: StrictBool = False


class NewSession(Body):
    title: str = Field(default="", max_length=160)


class NewChapter(Body):
    title: str = Field(default="人生故事", min_length=1, max_length=160)


def _json(value, status=200):
    return JSONResponse(value, status_code=status, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(404, "时叙录资料不存在或无权访问") from exc
    except ValueError as exc:
        # 领域层只允许固定的中文校验错误，不把上游模型返回作为 detail。
        raise HTTPException(409, str(exc)[:180]) from exc


def _public_turn(row):
    result = {key: value for key, value in row.items() if key != "audio_path"}
    if row.get("audio_path"):
        result["audio_url"] = f"api/memoir/profiles/{row['profile_id']}/audio/{row['id']}"
    else:
        result["audio_url"] = ""
    return result


def _public_question(question):
    value = dict(question)
    if value.get("turn"):
        value["turn"] = _public_turn(value["turn"])
    return value


def _session_lock(sid):
    with _locks_guard:
        if sid not in _turn_locks:
            # 删除空闲锁防止长期进程随历史会话无限增长；已持有锁不移除。
            if len(_turn_locks) >= 512:
                for key, lock in list(_turn_locks.items()):
                    if not lock.locked():
                        _turn_locks.pop(key, None)
                    if len(_turn_locks) < 384:
                        break
            _turn_locks[sid] = threading.Lock()
        lock = _turn_locks[sid]
        return lock if lock.acquire(blocking=False) else None


def _next_question(pid, sid, uid):
    try:
        return _public_question(services.next_question(pid, sid, uid))
    except LookupError as exc:
        raise HTTPException(404, "时叙录资料已删除或无权访问") from exc
    except Exception:
        # 人类原稿在调用前已提交；云服务失败不可撤销或吞掉原稿。
        return {"text": "这一段已经保存。你可以继续讲，也可以稍后回来。", "turn": None,
                "degraded": True, "paused": False}


def _private_temp():
    root = config.DATA_DIR / "memoir-private" / "temporary"
    for item in (config.DATA_DIR, root.parent, root):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise HTTPException(503, "私有录音目录配置不安全")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.resolve().is_relative_to(config.DATA_DIR.resolve()):
        raise HTTPException(503, "私有录音目录配置不安全")
    return tempfile.TemporaryDirectory(prefix="turn-", dir=root)


def _decode_audio(source: Path, target: Path):
    """完整解码并检验时长；不用 -t 截断冒充符合上传限制。"""
    try:
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-v", "error", "-y",
                        "-protocol_whitelist", "file,pipe", "-format_whitelist", "aac,flac,matroska,webm,mov,mp3,ogg,wav",
                        "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", "-fs", str(4 * 1024 * 1024), str(target)],
                       check=True, capture_output=True, timeout=25)
        with wave.open(str(target), "rb") as audio:
            seconds = audio.getnframes() / max(1, audio.getframerate())
            if seconds <= 0 or seconds > MAX_AUDIO_SECONDS:
                raise HTTPException(413, "每段录音须在 120 秒以内，请分段讲述")
        if target.stat().st_size > 4 * 1024 * 1024:
            raise HTTPException(413, "录音解码后过大，请分段讲述")
    except HTTPException:
        raise
    except (OSError, ValueError, wave.Error, subprocess.SubprocessError) as exc:
        raise HTTPException(422, "无法识别这段音频，请使用常见音频格式重新提交") from exc


def _audio_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while block := source.read(65536):
            digest.update(block)
    return digest.digest()


@router.post("/auth/login")
async def login(body: Login, request: Request):
    auth.require_same_origin(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "请填写员工姓名")
    auth.check_login_rate(request, name)
    data = await employee_auth.upstream("/api/verify-login", {"name": name, "password": body.password})
    user = auth.normalize_user(data) if data.get("success") is True else None
    if not user:
        raise HTTPException(401, "员工姓名或密码不正确")
    response = _json({"user": user})
    # 登录轮换并撤销同浏览器旧的私人 cookie，不触碰旧访谈会话。
    auth.revoke_session(request, response)
    auth.issue_session(response, user)
    return response


@router.get("/auth/me")
async def me(user=Depends(auth.require_user)):
    return _json({"user": user})


@router.post("/auth/logout")
async def logout(request: Request):
    auth.require_same_origin(request)
    response = _json({"ok": True})
    auth.revoke_session(request, response)
    return response


@router.get("/profiles")
async def profiles(user=Depends(auth.require_user)):
    return _json({"profiles": _call(store.list_profiles, user["id"])})


@router.post("/profiles")
async def create_profile(body: NewProfile, user=Depends(auth.require_user)):
    profile = _call(store.create_profile, user["id"], body.name, body.description,
                    cloud_consent=body.cloud_consent, recording_consent=body.recording_consent)
    return _json({"profile": profile}, 201)


@router.get("/profiles/{pid}")
async def profile_detail(pid: str, user=Depends(auth.require_user)):
    uid = user["id"]
    profile = _call(store.get_profile, pid, uid)
    tasks = []
    for task in job_queue.find(owner_id=uid, resource_id=pid, limit=10):
        if task["kind"] not in {"memoir_memory", "memoir_chapter"} or task["payload"].get("profile_id") != pid:
            continue
        tasks.append({"id": task["id"], "kind": task["kind"], "status": task["status"],
                      "stage": task_api.public_stage(task), "error": "整理暂未完成，可重试" if task["status"] == "failed" else ""})
    return _json({"profile": profile,
                  "sessions": _call(store.list_sessions, pid, uid),
                  "memories": _call(store.get_memories, pid, uid),
                  "chapters": _call(store.list_chapters, pid, uid), "tasks": tasks})


@router.delete("/profiles/{pid}")
async def delete_profile(pid: str, user=Depends(auth.require_user)):
    result = _call(store.delete_profile, pid, user["id"])
    return _json({"ok": True, **result})


@router.post("/profiles/{pid}/sessions")
async def create_session(pid: str, body: NewSession, user=Depends(auth.require_user)):
    session = _call(store.create_session, pid, user["id"], body.title)
    question = await run_in_threadpool(_next_question, pid, session["id"], user["id"])
    return _json({"session": session, "question": question}, 201)


@router.get("/profiles/{pid}/sessions/{sid}")
async def session_detail(pid: str, sid: str, user=Depends(auth.require_user)):
    return _json({"session": _call(store.get_session, pid, sid, user["id"]),
                  "turns": [_public_turn(t) for t in _call(store.list_turns, pid, sid, user["id"])]})


@router.delete("/profiles/{pid}/sessions/{sid}")
async def delete_session(pid: str, sid: str, user=Depends(auth.require_user)):
    result = _call(store.delete_session, pid, sid, user["id"])
    return _json({"ok": True, **result})


def _submit_turn(pid, sid, uid, text, client_turn_id, source=None):
    lock = _session_lock(sid)
    if lock is None:
        raise HTTPException(409, "上一段还在处理，请稍后重试；不要重复录制")
    try:
        _call(store.get_session, pid, sid, uid)
        previous = _call(store.list_turns, pid, sid, uid)
        existing = next((t for t in previous if t["client_turn_id"] == client_turn_id and t["role"] == "human"), None)
        if existing:
            if text and text != existing["text"]:
                raise HTTPException(409, "这段提交标识已使用，请刷新后重试")
            if bool(source) != bool(existing.get("audio_path")):
                raise HTTPException(409, "相同提交标识不能替换原始材料类型")
            if source:
                original = _call(store.validate_recording_path, pid, uid, existing["audio_path"])
                if _audio_hash(source) != _audio_hash(original):
                    raise HTTPException(409, "相同提交标识不能替换另一段录音")
            after = next((t for t in previous if t["role"] == "assistant" and t["seq"] > existing["seq"]), None)
            question = (_public_question(services._question_result(after))
                        if after else _next_question(pid, sid, uid))
            return {"human_turn": _public_turn(existing), "question": question}
        audio_path = None
        if source:
            with _private_temp() as work:
                decoded = Path(work) / "decoded.wav"
                _decode_audio(source, decoded)
                if not text:
                    if not asr_dashscope.DASHSCOPE_API_KEY:
                        raise HTTPException(503, "语音转写服务未配置，可以先使用文字讲述")
                    try:
                        text = asr_dashscope._transcribe_inline(str(decoded))
                    except Exception as exc:
                        raise HTTPException(503, "语音转写暂未完成，请保留本地录音后重试") from exc
                if not text or len(text) > 20000:
                    raise HTTPException(422, "未识别到有效讲述，或文字过长，请分段重试")
                turn_id = uuid.uuid4().hex
                audio_path = _call(store.recording_path, pid, uid, turn_id, source.suffix)
                # 独占创建，永不覆盖已有资料；所有最终文件由领域层限定私有路径。
                created = False
                try:
                    with source.open("rb") as src, open(audio_path, "xb") as dst:
                        created = True
                        os.chmod(audio_path, 0o600)
                        while chunk := src.read(65536):
                            dst.write(chunk)
                    human = _call(store.append_turn, pid, sid, uid, "human", text, client_turn_id,
                                  audio_path=str(audio_path), audio_mime=AUDIO_MIME[source.suffix], turn_id=turn_id)
                except BaseException:
                    if created and audio_path:
                        Path(audio_path).unlink(missing_ok=True)
                    raise
        else:
            human = _call(store.append_turn, pid, sid, uid, "human", text, client_turn_id)
        return {"human_turn": _public_turn(human), "question": _next_question(pid, sid, uid)}
    finally:
        lock.release()


@router.post("/profiles/{pid}/sessions/{sid}/turn")
async def submit_turn(pid: str, sid: str, text: str = Form(default=""),
                      client_turn_id: str = Form(...), audio: UploadFile | None = File(default=None),
                      user=Depends(auth.require_user)):
    _call(store.get_session, pid, sid, user["id"])
    text = text.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", client_turn_id) or len(text) > 20000:
        raise HTTPException(422, "讲述提交标识或文字长度不合法")
    if not text and audio is None:
        raise HTTPException(422, "请录一段讲述或填写文字")
    if text and audio is not None:
        raise HTTPException(422, "请分别提交文字或录音，不能用手填文字覆盖原始录音转写")
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, "当前讲述处理较忙，请稍后重试")
    try:
        if audio is None:
            return _json(await run_in_threadpool(_submit_turn, pid, sid, user["id"], text, client_turn_id))
        extension = Path(audio.filename or "recording.webm").suffix.lower()
        if extension not in AUDIO_MIME:
            raise HTTPException(422, "请上传常见音频格式")
        with _private_temp() as work:
            source, size = Path(work) / ("source" + extension), 0
            with source.open("xb") as stream:
                os.chmod(source, 0o600)
                while chunk := await audio.read(65536):
                    size += len(chunk)
                    if size > MAX_AUDIO_BYTES:
                        raise HTTPException(413, "每段音频不能超过 10MB")
                    stream.write(chunk)
            if size == 0:
                raise HTTPException(422, "音频文件为空")
            return _json(await run_in_threadpool(_submit_turn, pid, sid, user["id"], text, client_turn_id, source))
    finally:
        if audio:
            await audio.close()
        _slots.release()


@router.post("/profiles/{pid}/sessions/{sid}/finish")
async def finish_session(pid: str, sid: str, user=Depends(auth.require_user)):
    def finish():
        lock = _session_lock(sid)
        if lock is None:
            raise HTTPException(409, "上一段讲述还在处理，请保存成功后再结束")
        try:
            turns = _call(store.list_turns, pid, sid, user["id"])
            if not any(turn["role"] == "human" for turn in turns):
                raise HTTPException(409, "请先保存一段讲述再结束；当前场次尚未结束")
            # 先校验并幂等入队，避免空稿或超上限失败后留下已结束但前端未知的状态。
            task = _call(services.enqueue_memory, pid, user["id"])
            session = _call(store.finish_session, pid, sid, user["id"])
            return {"session": session, "task_id": task["id"]}
        finally:
            lock.release()
    return _json(await run_in_threadpool(finish), 202)


@router.post("/profiles/{pid}/chapters")
async def create_chapter(pid: str, body: NewChapter, user=Depends(auth.require_user)):
    task = _call(services.enqueue_chapter, pid, user["id"], body.title)
    return _json({"task_id": task["id"]}, 202)


@router.get("/profiles/{pid}/chapters/{cid}")
async def chapter(pid: str, cid: str, user=Depends(auth.require_user)):
    return _json({"chapter": _call(store.get_chapter, pid, cid, user["id"])})


@router.post("/profiles/{pid}/chapters/{cid}/confirm")
async def confirm_chapter(pid: str, cid: str, user=Depends(auth.require_user)):
    return _json({"chapter": _call(store.confirm_chapter, pid, cid, user["id"])})


@router.post("/profiles/{pid}/events/{eid}/confirm")
async def confirm_event(pid: str, eid: str, user=Depends(auth.require_user)):
    return _json({"memory": _call(store.confirm_memory, pid, eid, user["id"])})


@router.get("/profiles/{pid}/chapters/{cid}/export")
async def export_chapter(pid: str, cid: str, format: str = "md", user=Depends(auth.require_user)):
    if format not in {"md", "docx"}:
        raise HTTPException(422, "仅支持 Markdown 或 Word")
    row = _call(store.get_chapter, pid, cid, user["id"])
    markdown = _call(services.export_markdown, pid, user["id"], confirmed_only=True, chapter_id=cid)
    filename = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", row["title"])[:80] or "人生故事"
    body = (word_export.render_minutes(row["title"], markdown) if format == "docx" else markdown.encode("utf-8"))
    media = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document" if format == "docx"
             else "text/markdown; charset=utf-8")
    return Response(body, media_type=media, headers={"Cache-Control": "no-store",
                    "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename + '.' + format)}"})


@router.get("/profiles/{pid}/audio/{turn_id}")
async def audio(pid: str, turn_id: str, user=Depends(auth.require_user)):
    row = _call(store.get_turn, pid, turn_id, user["id"])
    if row.get("role") != "human" or not row.get("audio_path"):
        raise HTTPException(404, "这一段没有录音")
    path = _call(store.validate_recording_path, pid, user["id"], row["audio_path"])
    if not path.is_file():
        raise HTTPException(404, "录音文件不存在")
    return FileResponse(path, media_type=AUDIO_MIME.get(path.suffix.lower(), "application/octet-stream"), headers={"Cache-Control": "no-store",
                        "X-Content-Type-Options": "nosniff"})


@router.get("/tasks/{task_id}")
async def task_status(task_id: str, user=Depends(auth.require_user)):
    task = job_queue.get(task_id)
    if not task or task["kind"] not in {"memoir_memory", "memoir_chapter"} or task["owner_id"] != user["id"]:
        raise HTTPException(404, "时叙录任务不存在或无权访问")
    pid = (task.get("payload") or {}).get("profile_id")
    if not pid or task["resource_id"] != pid:
        raise HTTPException(404, "时叙录任务所属档案无效")
    _call(store.get_profile, pid, user["id"])
    value = {key: task[key] for key in ("id", "kind", "status", "progress", "attempts", "created_at", "updated_at")}
    value["stage"] = task_api.public_stage(task)
    if task["status"] == "failed":
        value["error"] = "后台整理暂未完成，可以重新提交；原稿已保留"
    return _json(value)
