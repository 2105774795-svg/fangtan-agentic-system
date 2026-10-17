"""独立后台任务的业务入口；不导入 app，也不依赖进程内线程锁。

处理函数为 ``handler(payload, ctx) -> dict``。ctx.progress(stage, progress)
持久化进度；ctx.check_cancelled(db=None) 在给定写事务中核验取消和租约，
失去执行权时抛异常。外部模型异常直接上抛，由任务中心统一重试。
"""
import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import wave
from pathlib import Path

import imageio_ffmpeg

import asr_dashscope
import config
import dimensions
import importer
import kimi
import project_records
import store
import telemetry
import coverage_ai
from services import outline_generation
from services.materials import (load_project_materials, location_text, read_session,
                                report_source_fingerprint)
from memoir.handlers import HANDLERS as MEMOIR_HANDLERS


logger = logging.getLogger("fangtan.tasks.handlers")


def extract_report_todos(markdown):
    match = re.search(r"(?m)^##\s*行动项与待办\s*\n(.*?)(?=^##\s|\Z)", markdown, re.S | re.M)
    if not match:
        return []
    lines, headers = [], []
    for line in match.group(1).splitlines():
        raw = line.strip()
        if raw.startswith("|") and raw.endswith("|"):
            cells = [cell.strip() for cell in raw.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells):
                continue
            if not headers:
                headers = cells
                continue
            item = "；".join(f"{headers[i]}：{cell}" for i, cell in enumerate(cells)
                            if cell and i < len(headers))
        else:
            item = re.sub(r"^\s*(?:[-*]\s*\[[ xX]\]\s*|[-*+]\s*|\d+[.、]\s*)", "", raw).strip()
        if (item and not re.fullmatch(r"(?:未提及|无|暂无|无明确行动项)[。.]?", item)
                and not item.startswith("###")):
            lines.append(item[:500])
    return lines[:100]


def handle_outline(payload, ctx):
    job_id, params = payload["job_id"], payload["params"]
    ctx.check_cancelled()
    with store.get() as db:
        row = db.execute("SELECT * FROM outline_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("提纲任务已删除")
        if row["status"] == "ready":
            return {"job_id": job_id, "questions": json.loads(row["questions"]),
                    "background": row["background"], "background_note": row["background_note"],
                    "sources": json.loads(row["sources"]), "research_status": row["research_status"],
                    "claims": json.loads(row["claims"]),
                    "retrieved_at": row["retrieved_at"]}
        if json.loads(row["params"]) != params:
            raise ValueError("提纲任务设置已经改变")
    brief = outline_generation.generate(params, ctx)
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        ctx.check_cancelled(db)
        row = db.execute("SELECT params FROM outline_jobs WHERE id=?", (job_id,)).fetchone()
        if not row or json.loads(row["params"]) != params:
            raise ValueError("提纲任务已删除或设置已经改变")
        db.execute("""UPDATE outline_jobs SET status='ready',questions=?,background=?,background_note=?,sources=?,claims=?,research_status=?,retrieved_at=?,
            error='',updated_at=? WHERE id=?""", (json.dumps(brief["questions"], ensure_ascii=False),
                brief.get("background", ""), brief.get("background_note", ""), json.dumps(brief["sources"], ensure_ascii=False),
                json.dumps(brief["claims"], ensure_ascii=False), brief["research_status"], brief["retrieved_at"], time.time(), job_id))
    return {"job_id": job_id, **brief}


def handle_report(payload, ctx):
    sid = payload["sid"]
    ctx.check_cancelled()
    with store.get() as db:
        db.execute("BEGIN")
        session, transcript, cached = read_session(db, sid)
    if cached is None:
        fingerprint = report_source_fingerprint(session, transcript)
        focus = session.get("focus") or ""
        place = location_text(session.get("location"))
        if place:
            focus += f"\n采访地点：{place}（用户给定背景，具体事实仍以逐字稿为准）"
        ctx.progress("正在提炼摘要、结论、章节与待办", 0.82)
        markdown = kimi.gen_report(session["subject"], session["interviewee_type"], transcript,
                                   session.get("industry") or "", focus, session.get("scene") or "")
        if not isinstance(markdown, str) or not markdown.strip():
            raise RuntimeError("模型未返回有效纪要，请重试")
    else:
        markdown, fingerprint = cached, None
    # 一次事务保存纪要、待办与结束状态；重试不能重复追加待办或复活已删除访谈。
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        ctx.check_cancelled(db)
        current, current_transcript, existing = read_session(db, sid)
        if existing is not None:
            markdown = existing
        else:
            if fingerprint is None or report_source_fingerprint(current, current_transcript) != fingerprint:
                raise ValueError("生成期间逐字稿或访谈设置已改变，请重新生成")
            db.execute("INSERT INTO reports(session_id,markdown,created_at) VALUES(?,?,?)",
                       (sid, markdown, time.time()))
            db.executemany("INSERT INTO session_todos(session_id,text,created_at) VALUES(?,?,?)",
                           [(sid, item, time.time()) for item in extract_report_todos(markdown)])
        db.execute("UPDATE sessions SET status='done',finished_at=COALESCE(finished_at,?) WHERE id=?",
                   (time.time(), sid))
    # 数据库是报告的唯一依据，不再在提交事务后写可复活的明文副本。
    return {"session_id": sid, "cached": cached is not None or existing is not None}


def handle_project_record(payload, ctx):
    record_id = payload["record_id"]
    ctx.check_cancelled()
    record = store.get_project_record(record_id)
    if not record:
        raise ValueError("总访谈记录已删除")
    if record["status"] == "ready":
        # 即使命中缓存，也要核验来源仍然有效。
        _, sources = load_project_materials(record["project_id"], [item["id"] for item in record["sources"]],
                                           record["owner_id"])
        if sources != record["sources"]:
            raise ValueError("访谈材料已经改变，请重新勾选后生成")
        return {"record_id": record_id, "cached": True}
    project = store.get_project(record["project_id"])
    if not project:
        raise ValueError("项目已删除")
    materials, sources = load_project_materials(record["project_id"],
                                                [item["id"] for item in record["sources"]], record["owner_id"])
    if sources != record["sources"]:
        raise ValueError("访谈材料已经改变，请重新勾选后生成")

    def progress(stage):
        ctx.check_cancelled()
        ctx.progress(stage, 0.60)
        with store.get() as db:
            db.execute("BEGIN IMMEDIATE")
            ctx.check_cancelled(db)
            row = db.execute("SELECT status,sources FROM project_records WHERE id=?", (record_id,)).fetchone()
            if not row or row["status"] not in {"generating", "failed"} or json.loads(row["sources"]) != sources:
                raise ValueError("来源访谈已失效，请重新生成")
            db.execute("UPDATE project_records SET status='generating',stage=?,error='' WHERE id=?",
                       (stage, record_id))

    progress("正在读取全部逐字稿与已有纪要")
    markdown = project_records.generate(record["title"], project["name"], materials, progress)
    if not isinstance(markdown, str) or not markdown.strip():
        raise RuntimeError("模型未返回有效总访谈记录，请重试")
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        ctx.check_cancelled(db)
        current = db.execute("SELECT * FROM project_records WHERE id=?", (record_id,)).fetchone()
        if not current or json.loads(current["sources"]) != sources or current["status"] != "generating":
            raise ValueError("来源访谈已失效，请重新生成")
        if (current["title"] != record["title"] or not db.execute(
                "SELECT 1 FROM projects WHERE id=? AND name=?", (project["id"], project["name"])).fetchone()):
            raise ValueError("生成期间项目或记录标题已改变，请重新生成")
        _, current_sources = load_project_materials(record["project_id"], [item["id"] for item in sources],
                                                    record["owner_id"], db=db)
        if current_sources != sources:
            raise ValueError("生成期间访谈材料已经改变，请重新生成")
        db.execute("""UPDATE project_records SET status='ready',stage='总访谈记录已生成',markdown=?,
            error='',finished_at=? WHERE id=?""", (markdown, time.time(), record_id))
    return {"record_id": record_id, "cached": False}


def _ffmpeg_executable():
    return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


def _recording_path(sid):
    session = store.get_session_summary(sid)
    if not session or session["status"] == "deleting":
        raise ValueError("访谈已删除")
    path = Path(session.get("audio_path") or "")
    root = (config.DATA_DIR / "recordings" / sid).resolve()
    if not session.get("audio_path") or not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError("录音文件不存在")
    return path


def handle_speaker(payload, ctx):
    sid, job_id = payload["sid"], payload["job_id"]

    def check_consent(db):
        ctx.check_cancelled(db)
        row = db.execute("""SELECT speaker_consented_at,speaker_job_id,audio_path,speaker_result_job_id
            FROM sessions WHERE id=? AND status='done'""", (sid,)).fetchone()
        if not row or not row["speaker_consented_at"] or row["speaker_job_id"] != job_id:
            raise ValueError("发言人分析已取消、已删除或同意已经撤回")
        return row

    with store.get() as db:
        row = check_consent(db)
        if row["speaker_result_job_id"] == job_id:
            count = db.execute("SELECT COUNT(*) FROM speaker_segments WHERE session_id=?", (sid,)).fetchone()[0]
            return {"session_id": sid, "segment_count": count, "cached": True}
    source = _recording_path(sid)
    source_stat = source.stat()
    ctx.progress("正在标准化录音并区分发言人", 0.20)
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="speaker-analysis-", dir=config.UPLOAD_DIR) as folder:
        wav = Path(folder) / "mono.wav"
        subprocess.run([_ffmpeg_executable(), "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", str(wav)], check=True, capture_output=True, timeout=600)
        ctx.check_cancelled()
        segments = asr_dashscope.transcribe_speakers(str(wav))
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        current = check_consent(db)
        if (current["audio_path"] != str(source) or not source.is_file()
                or (source.stat().st_size, source.stat().st_mtime_ns) !=
                (source_stat.st_size, source_stat.st_mtime_ns)):
            raise ValueError("分析期间录音已经改变，请重新取得同意后分析")
        db.execute("DELETE FROM speaker_segments WHERE session_id=?", (sid,))
        db.executemany("""INSERT INTO speaker_segments(session_id,speaker_id,start_ms,end_ms,text)
            VALUES(?,?,?,?,?)""", [(sid, item["speaker_id"], item["start_ms"], item["end_ms"], item["text"])
                                    for item in segments])
        db.execute("UPDATE sessions SET speaker_result_job_id=? WHERE id=?", (job_id, sid))
    return {"session_id": sid, "segment_count": len(segments), "cached": False}


def _import_path(payload):
    job_id = payload["job_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id):
        raise ValueError("无效导入任务编号")
    path = Path(payload["path"]).resolve()
    imports_root = (config.UPLOAD_DIR / "_imports").resolve()
    root = (imports_root / job_id).resolve()
    if (imports_root.parent != config.UPLOAD_DIR.resolve() or root.parent != imports_root
            or not path.is_relative_to(root) or path.parent != root):
        raise ValueError("导入文件不在当前任务目录内")
    return path, root


def _transcribe_import(path, ctx):
    segments_dir = path.parent / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    # 每次重试只使用本次转换结果，不能混入上次尾部切片。
    with tempfile.TemporaryDirectory(prefix="attempt-", dir=segments_dir) as folder:
        ctx.progress("正在切分并标准化音频", 0.12)
        try:
            subprocess.run([_ffmpeg_executable(), "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                            "-f", "segment", "-segment_time", "240", "-reset_timestamps", "1",
                            str(Path(folder) / "part_%04d.wav")],
                           check=True, capture_output=True, timeout=600)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("无法读取该音频，请确认文件未损坏且格式正确") from exc
        segments = sorted(Path(folder).glob("part_*.wav"))
        utterances, elapsed_ms = [], 0.0
        for index, segment in enumerate(segments):
            ctx.check_cancelled()
            with wave.open(str(segment), "rb") as audio:
                duration_ms = audio.getnframes() * 1000 / audio.getframerate()
            start_ms = round(elapsed_ms)
            elapsed_ms += duration_ms
            ctx.progress(f"正在转写音频（{index + 1}/{len(segments)}）", 0.18 + index / len(segments) * 0.52)
            text = asr_dashscope.transcribe(str(segment), timeout=300).strip()
            if text:
                utterances.append({"start_ms": start_ms, "end_ms": round(elapsed_ms), "text": text})
    if not utterances:
        raise RuntimeError("音频转写结果为空")
    return utterances


def handle_import(payload, ctx):
    """API 先创建固定 session_id 的空会话；原文归档后重试直接生成报告。"""
    sid, job_id = payload["session_id"], payload["job_id"]
    ctx.check_cancelled()
    with store.get() as db:
        session, transcript, _ = read_session(db, sid)
        archived = db.execute("SELECT 1 FROM suggestions WHERE session_id=? AND kind='import_archive' AND payload=?",
                              (sid, json.dumps({"job_id": job_id}))).fetchone()
        if session.get("owner_id") != payload.get("owner_id"):
            raise ValueError("导入会话所有者已改变")
        if transcript and not archived:
            raise ValueError("导入会话已有其他内容，不能覆盖")
    if not archived:
        path, _ = _import_path(payload)
        if not path.is_file():
            raise ValueError("导入原文件不存在")
        if payload["kind"] == "audio":
            utterances = _transcribe_import(path, ctx)
        else:
            ctx.progress("正在解析文字稿", 0.28)
            utterances = importer.extract_utterances(path)
        ctx.check_cancelled()
        ctx.progress(f"已提取 {len(utterances)} 段内容，正在归档", 0.72)
        retained = None
        if payload["kind"] == "audio":
            recording_dir = config.DATA_DIR / "recordings" / sid
            recording_dir.mkdir(parents=True, exist_ok=True)
            retained = recording_dir / f"original{path.suffix.lower()}"
            # 每次尝试拥有自己的临时副本，不破坏其他重试进程正在读取的文件。
            staged = recording_dir / f".{uuid.uuid4().hex}.upload"
            shutil.copy2(path, staged)
        try:
            with store.get() as db:
                db.execute("BEGIN IMMEDIATE")
                ctx.check_cancelled(db)
                current, current_transcript, _ = read_session(db, sid)
                if current.get("owner_id") != payload.get("owner_id") or current_transcript:
                    raise ValueError("导入会话内容已改变，请重新导入")
                if retained:
                    staged.replace(retained)
                    db.execute("UPDATE sessions SET audio_path=? WHERE id=?", (str(retained), sid))
                rows = []
                for seq, utterance in enumerate(utterances):
                    text = utterance["text"].strip()
                    match = re.match(r"^([^：:]{1,20})[：:]\s*", text)
                    rows.append((sid, seq, int(utterance["start_ms"]), int(utterance["end_ms"]), text,
                                 time.time(), ",".join(dimensions.classify(text)), match.group(1) if match else ""))
                db.executemany("""INSERT INTO utterances(session_id,seq,start_ms,end_ms,text,created_at,dims,speaker)
                    VALUES(?,?,?,?,?,?,?,?)""", rows)
                db.execute("INSERT INTO suggestions(session_id,kind,payload,status,created_at) VALUES(?,?,?,?,?)",
                           (sid, "import_archive", json.dumps({"job_id": job_id}), "asked", time.time()))
        finally:
            if retained:
                staged.unlink(missing_ok=True)
    result = handle_report({"sid": sid}, ctx)
    ctx.check_cancelled()
    # 成功后的上传副本清理由 worker 完成确认后执行 cleanup_import_upload，
    # 不能在此处先删原文件，避免结果确认前租约丢失导致无法恢复。
    return {"session_id": sid, "report_ready": True, "cached": result["cached"]}


def cleanup_import_upload(payload):
    """仅供已确认终态的任务调用，删除本任务上传副本，不触碰录音归档。"""
    _, root = _import_path(payload)
    if root.is_dir():
        shutil.rmtree(root)


def cleanup_cancelled_import(payload):
    """调用方须确认任务已取消且无运行租约；清理只能针对固定任务与会话。"""
    sid = payload["session_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", sid):
        raise ValueError("无效导入会话编号")
    _, upload_root = _import_path(payload)
    recording_root = (config.DATA_DIR / "recordings").resolve()
    recording_dir = (recording_root / sid).resolve()
    if recording_dir.parent != recording_root:
        raise ValueError("无效录音目录")
    report_path = (config.REPORT_DIR / f"{sid}.md").resolve()
    if report_path.parent != config.REPORT_DIR.resolve():
        raise ValueError("无效纪要文件")
    # 验证全部绝对路径后再开始任何删除，且不触及其他任务的文件。
    store.mark_session_deleting(sid)
    store.delete_session_records(sid)
    if recording_dir.is_dir():
        shutil.rmtree(recording_dir)
    report_path.unlink(missing_ok=True)
    if upload_root.is_dir():
        shutil.rmtree(upload_root)


def report_usage_after_success(payload):
    """沿用稳定 fangtan-report-{sid} 事件 ID，统计失败不改变业务成功状态。"""
    sid = payload.get("sid") or payload.get("session_id")
    summary = store.get_session_summary(sid) if sid else None
    if summary and summary.get("has_report"):
        try:
            telemetry.report_usage(sid, summary.get("user_name") or "")
        except Exception as exc:
            logger.warning("成功任务用量上报异常 error_type=%s", type(exc).__name__)


def import_after_success(payload):
    try:
        cleanup_import_upload(payload)
    except Exception as exc:
        logger.warning("成功导入的临时材料清理失败 error_type=%s", type(exc).__name__)
    report_usage_after_success(payload)


def coverage_after_success(payload):
    """完成确认后再核对最新原文，补上提交结果与队列 ready 之间到达的末帧。"""
    sid = payload["sid"]
    try:
        # 当前源已有结果时 schedule 返回 None，force 任务也不会不断重评。
        coverage_ai.schedule(sid)
    except ValueError:
        # 删除与完成并发是正常边界，不能复活访谈或令已完成任务重试。
        return


HANDLERS = {"outline": handle_outline, "report": handle_report, "project_record": handle_project_record,
            "import": handle_import, "speaker": handle_speaker, "coverage": coverage_ai.handler}
HANDLERS.update(MEMOIR_HANDLERS)
from interview_prep.ai import HANDLERS as INTERVIEW_HANDLERS
HANDLERS.update(INTERVIEW_HANDLERS)
from research_platform import execute_ai_job
HANDLERS["research_platform"] = execute_ai_job
from conversation_engine import HANDLERS as CONVERSATION_HANDLERS
HANDLERS.update(CONVERSATION_HANDLERS)
ON_CANCEL = {"import": cleanup_cancelled_import}
ON_SUCCESS = {"import": import_after_success, "report": report_usage_after_success,
              "coverage": coverage_after_success}
