"""项目资料快照导出：只打包当前可见访谈，临时 ZIP 不进入静态目录。"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import unicodedata
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import config
import store
import word_export


MAX_SESSIONS = 200
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
MAX_TEXT_CHARS = 10_000_000
MAX_UTTERANCES = 200_000
_COPY_CHUNK = 1024 * 1024
_RESERVE_BYTES = 32 * 1024 * 1024


class BundleError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def safe_name(value, limit=64):
    """仅用于下载名与 ZIP 条目，绝不用于服务器资料目录。"""
    text = unicodedata.normalize("NFC", str(value or ""))
    text = "".join("_" if unicodedata.category(char) in {"Cc", "Cs"}
                   or char in '\\/:*?"<>|' or char in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
                   else char for char in text)
    text = re.sub(r"\s+", " ", text).strip(" .")[:limit].rstrip(" .") or "未命名"
    if text.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
                                      *[f"LPT{i}" for i in range(1, 10)]}:
        text = "_" + text
    return text


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise BundleError(404, "项目或访谈不存在")
    return value


def _reject_links(path, root):
    """拒绝软链接、Windows junction 及其他 reparse point，避免校验后跟随跳转。"""
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise BundleError(409, "录音路径不在允许的资料目录内，已停止打包") from exc
    current = root
    for component in relative.parts:
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
            raise BundleError(409, "资料目录含链接或重定向路径，已停止打包")


def _file_version(info):
    # Windows 的 stat 与 fstat 在部分 Python 版本中分别以出生时间、变更时间
    # 填充 ctime；不能用这个不一致字段误报原文件发生修改。
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns if os.name != "nt" else None)


def _audio_info(sid, audio_path):
    base = Path(config.DATA_DIR).resolve()
    recordings = base / "recordings"
    folder = recordings / _identifier(sid)
    _reject_links(folder, base)
    if not audio_path:
        if folder.exists() and any(item.is_file() for item in folder.iterdir()):
            raise BundleError(409, "项目有尚未合成完整录音的访谈，请先结束访谈并完成录音保存后重试")
        return None
    path = Path(audio_path)
    if not path.is_absolute() or ".." in path.parts:
        raise BundleError(409, "录音路径不合法，已停止打包")
    _reject_links(path, base)
    if not path.resolve().is_relative_to(folder):
        raise BundleError(409, "录音路径越出所属访谈目录，已停止打包")
    try:
        info = path.stat()
    except FileNotFoundError:
        return {"path": str(path), "missing": True}
    if not stat.S_ISREG(info.st_mode):
        raise BundleError(409, "录音不是普通文件，已停止打包")
    suffix = path.suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
        raise BundleError(409, "录音扩展名异常，已停止打包")
    return {"path": str(path), "missing": False, "suffix": suffix,
            "version": _file_version(info), "size": info.st_size}


def _snapshot(project_id, user_id, max_sessions, max_source_bytes, max_text_chars):
    _identifier(project_id)
    with store.get() as db:
        db.execute("BEGIN")
        found = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not found:
            raise BundleError(404, "项目不存在或无权访问")
        project = dict(found)
        visible = ""
        params = [project_id]
        if config.AUTH_ENABLED:
            visible = " AND (s.owner_id=? OR EXISTS(SELECT 1 FROM session_shares sh WHERE sh.session_id=s.id AND sh.user_id=?))"
            params += [user_id, user_id]
        rows = db.execute("SELECT s.* FROM sessions s WHERE s.project_id=? AND s.status!='deleting'" + visible +
                          " ORDER BY s.created_at,s.id LIMIT ?", (*params, max_sessions + 1)).fetchall()
        if config.AUTH_ENABLED and project["owner_id"] != user_id and not rows:
            raise BundleError(404, "项目不存在或无权访问")
        if not rows:
            raise BundleError(400, "项目中没有可下载的访谈，请先将访谈保存到项目")
        if len(rows) > max_sessions:
            raise BundleError(413, f"项目可见访谈超过 {max_sessions} 场，请拆分项目后再打包；不会截断导出")
        items, characters, source_bytes, utterance_count = [], 0, 0, 0
        for raw in rows:
            session = dict(raw)
            _identifier(session["id"])
            if session["status"] != "done":
                raise BundleError(409, "项目含未结束或正在导入的访谈，请先结束访谈并完成保存后再打包")
            sid = session["id"]
            amount, row_count = db.execute("SELECT COALESCE(SUM(LENGTH(COALESCE(text,''))+LENGTH(COALESCE(speaker,''))),0),COUNT(*) FROM utterances WHERE session_id=?", (sid,)).fetchone()
            utterance_count += row_count
            if utterance_count > MAX_UTTERANCES:
                raise BundleError(413, f"项目逐字稿超过 {MAX_UTTERANCES:,} 段，请拆分项目；不会截断导出")
            report = db.execute("SELECT markdown,created_at FROM reports WHERE session_id=?", (sid,)).fetchone()
            report = dict(report) if report else None
            characters += amount + len((report or {}).get("markdown") or "")
            if characters > max_text_chars:
                raise BundleError(413, f"项目文字材料超过 {max_text_chars:,} 字符，请拆分项目；不会截断逐字稿")
            transcript = [dict(row) for row in db.execute(
                "SELECT * FROM utterances WHERE session_id=? ORDER BY seq,id", (sid,))]
            audio = _audio_info(sid, session.get("audio_path") or "")
            source_bytes += (audio or {}).get("size", 0)
            # UTF-8 文本、JSONL、Word 的写入还会再次按实际字节数检查。
            if source_bytes > max_source_bytes:
                raise BundleError(413, f"项目录音超过 {max_source_bytes // (1024 * 1024)} MB，请拆分项目后下载")
            items.append({"session": session, "transcript": transcript, "report": report, "audio": audio})
    snapshot = {"project": project, "items": items, "auth_enabled": config.AUTH_ENABLED}
    digest = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return snapshot, digest, source_bytes, characters


@dataclass
class BundleArtifact:
    path: Path
    filename: str
    session_count: int
    source_bytes: int
    _project_id: str = field(repr=False)
    _user_id: int = field(repr=False)
    _fingerprint: str = field(repr=False)
    _limits: tuple = field(repr=False)

    def cleanup(self):
        """供 FileResponse 的 BackgroundTask 调用；仅删除本次独占临时文件。"""
        self.path.unlink(missing_ok=True)
        try:
            self.path.parent.rmdir()
        except FileNotFoundError:
            pass

    def verify(self):
        """发送首字节前复核快照。已传出的下载字节无法因后续撤权而撤回。"""
        try:
            _, fingerprint, _, _ = _snapshot(self._project_id, self._user_id, *self._limits)
            if fingerprint == self._fingerprint:
                return
        except (BundleError, OSError):
            pass
        self.cleanup()
        raise BundleError(409, "打包期间项目权限、归属或资料已改变，请刷新后重新下载")


def _stamp(value):
    milliseconds = max(0, int(value or 0))
    return f"{milliseconds // 3600000:02d}:{milliseconds // 60000 % 60:02d}:{milliseconds // 1000 % 60:02d}.{milliseconds % 1000:03d}"


def _transcript_text(rows):
    for row in rows:
        speaker = (row.get("speaker") or "") + "：" if row.get("speaker") else ""
        yield (f"[段 {row['seq']} | {_stamp(row.get('start_ms'))}–{_stamp(row.get('end_ms'))} | "
               f"start_ms={row.get('start_ms')} end_ms={row.get('end_ms')}] {speaker}{row.get('text') or ''}\n").encode("utf-8")


def _docx_safe(value):
    return "".join(char for char in (value or "") if char in "\t\n\r"
                   or 0x20 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD
                   or 0x10000 <= ord(char) <= 0x10FFFF)


def _safe_open_audio(item):
    expected = item["audio"]
    actual = _audio_info(item["session"]["id"], expected["path"])
    if actual != expected:
        raise BundleError(409, "打包期间录音文件已改变，请重新下载")
    try:
        descriptor = os.open(expected["path"], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise BundleError(409, "打包期间录音文件已删除、替换或无法读取，请重新下载") from exc
    source = os.fdopen(descriptor, "rb")
    try:
        if _file_version(os.fstat(source.fileno())) != expected["version"] or _audio_info(item["session"]["id"], expected["path"]) != expected:
            raise BundleError(409, "打包期间录音路径或文件已改变，请重新下载")
        return source
    except BaseException:
        source.close()
        raise


def build_bundle(project_id, user_id, *, max_sessions=MAX_SESSIONS,
                 max_source_bytes=MAX_SOURCE_BYTES, max_text_chars=MAX_TEXT_CHARS):
    """用磁盘流构建当前用户可见、已结束访谈的完整资料 ZIP，不移动原始资料。"""
    limits = (int(max_sessions), int(max_source_bytes), int(max_text_chars))
    if min(limits) < 1:
        raise BundleError(400, "导出限额必须大于零")
    snapshot, fingerprint, audio_bytes, characters = _snapshot(project_id, int(user_id), *limits)
    data_root = Path(config.DATA_DIR).resolve()
    export_root = data_root / "exports"
    _reject_links(export_root, data_root)
    export_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    estimate = (audio_bytes + characters * 12 + sum(len(item["transcript"]) for item in snapshot["items"]) * 256
                + len(snapshot["items"]) * 32768 + _RESERVE_BYTES)
    if shutil.disk_usage(export_root).free < estimate:
        raise BundleError(507, "服务器剩余空间不足以安全打包，请稍后或拆分项目下载")
    folder = Path(tempfile.mkdtemp(prefix="project-bundle-", dir=export_root))
    os.chmod(folder, 0o700)
    artifact = BundleArtifact(folder / "materials.zip", safe_name(snapshot["project"]["name"]) + "-项目资料.zip",
                              len(snapshot["items"]), audio_bytes, project_id, int(user_id), fingerprint, limits)
    count = 0
    try:
        descriptor = os.open(artifact.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        project = snapshot["project"]
        prefix = safe_name(project["name"]) + "_" + project_id
        manifest = {"format_version": 1, "project": {"id": project_id, "name": project["name"]},
                    "exported_at": datetime.now(timezone.utc).isoformat(),
                    "scope": "仅导出下载时当前账号可见的访谈，不包含项目内其他私有访谈。",
                    "snapshot_boundary": "下载开始前复核权限、项目归属和资料版本；下载开始后的撤权不能撤回已传出的字节。",
                    "sessions": [], "warnings": []}
        with ZipFile(artifact.path, "w", compression=ZIP_DEFLATED, allowZip64=True) as archive:
            def write_chunks(name, chunks, *, compression=ZIP_DEFLATED):
                nonlocal count
                # 名称均由净化组件构建，仍在最后一道关卡拒绝穿越。
                if name.startswith(("/", "\\")) or any(part in {"", ".", ".."} for part in name.split("/")):
                    raise BundleError(409, "导出文件名不安全")
                from zipfile import ZipInfo
                info = ZipInfo(name)
                info.compress_type = compression
                info.external_attr = 0o600 << 16
                with archive.open(info, "w", force_zip64=True) as target:
                    for content in chunks:
                        count += len(content)
                        if count > max_source_bytes:
                            raise BundleError(413, f"项目资料总量超过 {max_source_bytes // (1024 * 1024)} MB，请拆分项目；不会截断导出")
                        target.write(content)

            for item in snapshot["items"]:
                session, rows, audio = item["session"], item["transcript"], item["audio"]
                sid = session["id"]
                label = safe_name(session.get("subject")) + "_" + sid
                target = prefix + "/" + label
                entry = {"id": sid, "subject": session.get("subject") or "", "folder": target,
                         "utterance_count": len(rows), "files": [], "missing": []}
                manifest["sessions"].append(entry)
                if audio and not audio.get("missing"):
                    name = target + "/录音" + audio["suffix"]
                    with _safe_open_audio(item) as source:
                        write_chunks(name, iter(lambda: source.read(_COPY_CHUNK), b""), compression=ZIP_STORED)
                        if _file_version(os.fstat(source.fileno())) != audio["version"]:
                            raise BundleError(409, "打包期间录音内容已改变，请重新下载")
                    entry["files"].append(name)
                else:
                    entry["missing"].append("录音文件缺失" if audio else "未保存音频（可能仅导入了文字稿）")
                if not rows:
                    entry["missing"].append("逐字稿为空")
                name = target + "/完整逐字稿.txt"
                write_chunks(name, _transcript_text(rows))
                entry["files"].append(name)
                name = target + "/完整逐字稿.jsonl"
                write_chunks(name, ((json.dumps({key: row.get(key) for key in
                             ("seq", "start_ms", "end_ms", "speaker", "text")}, ensure_ascii=False) + "\n").encode("utf-8") for row in rows))
                entry["files"].append(name)
                report = (item["report"] or {}).get("markdown") or ""
                if report:
                    for filename, content in (("会议纪要.md", report.encode("utf-8")),
                        ("会议纪要.docx", word_export.render_minutes(_docx_safe(session.get("subject")), _docx_safe(report)))):
                        name = target + "/" + filename
                        write_chunks(name, (content,))
                        entry["files"].append(name)
                else:
                    entry["missing"].append("会议纪要尚未生成或为空")
                if entry["missing"]:
                    manifest["warnings"].append({"session_id": sid, "subject": entry["subject"], "missing": entry["missing"]})
            notes = ["项目访谈资料包", "", manifest["scope"], manifest["snapshot_boundary"],
                     "本 ZIP 未加密，包含原始访谈资料，请妥善保存并遵守受访者同意范围。",
                     "TXT 与 JSONL 包含全部逐字稿，按 seq、id 排序，保留毫秒时间点与手工发言人标记。",
                     "Word 仅导出纪要，逐字稿单独完整提供；Word 不显示的控制字符仅在 Word 中移除，Markdown 保留原文。",
                     "未合成录音或未结束访谈不允许打包；缺失材料见 manifest.json，不会伪造或自动补写。", "", "缺失材料清单："]
            notes += [f"- {entry['subject']}（{entry['session_id']}）：{'；'.join(entry['missing'])}" for entry in manifest["warnings"]] or ["无。"]
            write_chunks(prefix + "/说明.txt", (("\n".join(notes) + "\n").encode("utf-8"),))
            write_chunks(prefix + "/manifest.json", (json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),))
        artifact.source_bytes = count
        artifact.verify()
        return artifact
    except OSError as exc:
        artifact.cleanup()
        if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}:
            raise BundleError(507, "打包时服务器剩余空间不足，请稍后或拆分项目下载") from exc
        raise BundleError(409, "打包期间资料文件已改变或无法读写，请刷新后重新下载") from exc
    except BaseException:
        artifact.cleanup()
        raise
