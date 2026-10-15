"""已有录音/文字稿导入：格式校验、文本提取与逐段标准化。"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree


AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac", ".wma", ".webm", ".mp4"}
TEXT_EXTENSIONS = {".txt", ".md", ".docx", ".srt", ".vtt", ".csv", ".json", ".html", ".htm"}
SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS | TEXT_EXTENSIONS
MAX_EXTRACTED_TEXT_BYTES = 20 * 1024 * 1024
MAX_TRANSCRIPT_CHARS = 500_000
MAX_UTTERANCES = 10_000


class TranscriptFormatError(ValueError):
    pass


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise TranscriptFormatError("无法识别文字稿编码，请转换为 UTF-8 后重试")


def _timestamp_ms(value: str) -> int:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
        elif len(parts) == 2:
            hours, minutes, seconds = 0, int(parts[0]), float(parts[1])
        else:
            return 0
    except ValueError:
        return 0
    return max(0, int((hours * 3600 + minutes * 60 + seconds) * 1000))


def _clean_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _plain_utterances(text: str, max_chars: int = 800) -> list[dict]:
    paragraphs = [p.strip() for p in re.split(r"\n+", _clean_text(text)) if p.strip()]
    if not paragraphs:
        paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    utterances: list[dict] = []
    for paragraph in paragraphs:
        pieces = [paragraph[i:i + max_chars].strip() for i in range(0, len(paragraph), max_chars)]
        for piece in pieces:
            if not piece:
                continue
            seq = len(utterances)
            utterances.append({"start_ms": seq * 30000, "end_ms": (seq + 1) * 30000,
                               "text": piece})
    return utterances


def _subtitle_utterances(text: str) -> list[dict]:
    blocks = re.split(r"\n\s*\n", _clean_text(text))
    utterances = []
    pattern = re.compile(
        r"(?P<start>\d{1,2}:\d{2}(?::\d{2})?[,.]\d{1,3})\s*-->\s*"
        r"(?P<end>\d{1,2}:\d{2}(?::\d{2})?[,.]\d{1,3})"
    )
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        time_index = next((i for i, line in enumerate(lines) if pattern.search(line)), None)
        if time_index is None:
            continue
        match = pattern.search(lines[time_index])
        content = " ".join(lines[time_index + 1:]).strip()
        content = re.sub(r"<[^>]+>", "", content)
        if content:
            utterances.append({"start_ms": _timestamp_ms(match.group("start")),
                               "end_ms": _timestamp_ms(match.group("end")),
                               "text": content})
    return utterances or _plain_utterances(text)


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_EXTRACTED_TEXT_BYTES:
                raise TranscriptFormatError("DOCX 解压后的正文过大")
            root = ElementTree.fromstring(archive.read(info))
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise TranscriptFormatError("DOCX 文件损坏或格式不受支持") from exc
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(namespace + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(namespace + "t")).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


class _HTMLTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str):
        if data.strip():
            self.parts.append(data.strip())


def _csv_utterances(text: str) -> list[dict]:
    stream = io.StringIO(text)
    try:
        sample = text[:4096]
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(stream, dialect))
    if not rows:
        return []
    header = [cell.strip().lower() for cell in rows[0]]
    aliases = {
        "text": {"text", "content", "transcript", "sentence", "正文", "内容", "逐字稿", "发言"},
        "speaker": {"speaker", "name", "发言人", "说话人", "姓名"},
        "start": {"start", "start_time", "time", "timestamp", "开始时间", "时间"},
    }

    def column(kind: str):
        return next((i for i, name in enumerate(header) if name in aliases[kind]), None)

    text_col, speaker_col, start_col = column("text"), column("speaker"), column("start")
    data_rows = rows[1:] if text_col is not None else rows
    if text_col is None:
        text_col = len(rows[0]) - 1
    utterances = []
    for row in data_rows:
        if text_col >= len(row) or not row[text_col].strip():
            continue
        content = row[text_col].strip()
        if speaker_col is not None and speaker_col < len(row) and row[speaker_col].strip():
            content = f"{row[speaker_col].strip()}：{content}"
        start_ms = _timestamp_ms(row[start_col]) if start_col is not None and start_col < len(row) else len(utterances) * 30000
        utterances.append({"start_ms": start_ms, "end_ms": start_ms + 30000, "text": content})
    return utterances


def _json_utterances(text: str) -> list[dict]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TranscriptFormatError("JSON 格式错误") from exc
    candidates = payload if isinstance(payload, list) else next(
        (payload[key] for key in ("utterances", "transcript", "segments", "items", "data")
         if isinstance(payload.get(key), list)), []) if isinstance(payload, dict) else []
    utterances = []
    for item in candidates:
        if isinstance(item, str):
            content, start_ms, end_ms = item.strip(), len(utterances) * 30000, (len(utterances) + 1) * 30000
        elif isinstance(item, dict):
            content = next((str(item[key]).strip() for key in ("text", "content", "sentence", "transcript")
                            if item.get(key) is not None), "")
            speaker = next((str(item[key]).strip() for key in ("speaker", "name", "speaker_name")
                            if item.get(key) is not None), "")
            if speaker and content:
                content = f"{speaker}：{content}"
            start = item.get("start_ms", item.get("start", len(utterances) * 30))
            end = item.get("end_ms", item.get("end", None))
            try:
                start_ms = int(float(start) * (1 if "start_ms" in item else 1000))
                end_ms = int(float(end) * (1 if "end_ms" in item else 1000)) if end is not None else start_ms + 30000
            except (TypeError, ValueError):
                start_ms, end_ms = len(utterances) * 30000, (len(utterances) + 1) * 30000
        else:
            continue
        if content:
            utterances.append({"start_ms": max(0, start_ms), "end_ms": max(start_ms, end_ms), "text": content})
    return utterances or _plain_utterances(text)


def extract_utterances(path: str | Path) -> list[dict]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in TEXT_EXTENSIONS:
        raise TranscriptFormatError(f"不支持的文字稿格式：{suffix or '无扩展名'}")
    if suffix == ".docx":
        text = _docx_text(path)
    else:
        raw = path.read_bytes()
        if len(raw) > MAX_EXTRACTED_TEXT_BYTES:
            raise TranscriptFormatError("文字稿过大")
        text = _decode(raw)
    if suffix in {".srt", ".vtt"}:
        utterances = _subtitle_utterances(text)
    elif suffix == ".csv":
        utterances = _csv_utterances(text)
    elif suffix == ".json":
        utterances = _json_utterances(text)
    elif suffix in {".html", ".htm"}:
        parser = _HTMLTextParser()
        parser.feed(text)
        utterances = _plain_utterances("\n\n".join(parser.parts))
    else:
        utterances = _plain_utterances(text)
    if not utterances:
        raise TranscriptFormatError("文字稿中没有可分析的正文")
    if sum(len(row["text"]) for row in utterances) > MAX_TRANSCRIPT_CHARS:
        raise TranscriptFormatError("文字稿正文超过 50 万字，请拆分后再上传")
    if len(utterances) > MAX_UTTERANCES:
        raise TranscriptFormatError("文字稿段落超过 1 万段，请合并过短段落后再上传")
    return utterances
