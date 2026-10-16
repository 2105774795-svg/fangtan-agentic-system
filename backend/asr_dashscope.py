"""阿里云百炼 DashScope filetrans 离线转写（minutes-web 同款模型，已生产验证）。

流程：上传音频文件 → 注册一次性 token URL（本服务自己提供公网下载端点，仿 minutes-web/koupen-ppt）
→ submit 任务 → 轮询 → 取回逐句文本合并。
"""
import base64
import ipaddress
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import audio_access

from config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_FILETRANS_MODEL,
    DASHSCOPE_FLASH_MODEL,
    DASHSCOPE_FLASH_URL,
    PUBLIC_BASE,
)

SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
QUERY_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{}"

# 一次性音频凭据写入共享数据库，独立 Worker 注册后 API 进程可消费。


def register_audio(local_path: str) -> tuple[str, str]:
    token = audio_access.register(local_path)
    return f"{PUBLIC_BASE.rstrip('/')}/asr-audio/{token}", token


def pop_audio(token: str):
    return audio_access.consume(token)


def discard_audio(token: str):
    audio_access.revoke(token)


def _has_public_callback_base() -> bool:
    public_url = urlparse(PUBLIC_BASE)
    hostname = public_url.hostname
    private_address = False
    try:
        private_address = ipaddress.ip_address(hostname).is_private
    except (TypeError, ValueError):
        pass
    return (public_url.scheme == "https" and hostname not in {None, "localhost"}
            and not private_address)


def _transcribe_inline(wav_path: str) -> str:
    """短音频使用 Base64 同步识别，本地开发无需公网回调地址。"""
    audio_bytes = Path(wav_path).read_bytes()
    if len(audio_bytes) > 10 * 1024 * 1024:
        raise RuntimeError("同步转写音频不能超过 10MB")
    data_uri = "data:audio/wav;base64," + base64.b64encode(audio_bytes).decode("ascii")
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
        "X-DashScope-SSE": "disable",
    }
    payload = {
        "model": DASHSCOPE_FLASH_MODEL,
        "input": {
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "input_audio",
                    "input_audio": {"data": data_uri},
                }],
            }],
        },
        "parameters": {
            "format": "wav",
            "sample_rate": "16000",
            "language_hints": ["zh", "en"],
        },
    }
    with httpx.Client(timeout=180) as cli:
        response = cli.post(DASHSCOPE_FLASH_URL, json=payload, headers=headers)
        body = response.json()
        if "ASR_RESPONSE_HAVE_NO_WORDS" in {body.get("code"), body.get("message"),
                                               (body.get("output") or {}).get("code"),
                                               (body.get("output") or {}).get("message")}:
            return ""
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:500]
            raise RuntimeError(f"ASR 请求失败（HTTP {response.status_code}）：{detail}") from exc
        body = response.json()
    text = body.get("output", {}).get("text")
    if not isinstance(text, str):
        raise RuntimeError(f"ASR 返回缺少转写文本: {body}")
    return text.strip()


def _transcribe_filetrans(wav_path: str, timeout: float, diarization: bool = False):
    """公网部署使用异步 filetrans 模型，适合较长音频。"""
    file_url, audio_token = register_audio(wav_path)
    headers = {"Authorization": f"Bearer {DASHSCOPE_API_KEY}",
               "X-DashScope-Async": "enable"}
    payload = {
        "model": DASHSCOPE_FILETRANS_MODEL,
        "input": {"file_urls": [file_url]},
        "parameters": {
            "language_hints": ["zh", "en"],
            "diarization_enabled": diarization,
        },
    }
    try:
        with httpx.Client(timeout=30) as cli:
            r = cli.post(SUBMIT_URL, json=payload, headers=headers)
            r.raise_for_status()
            task_id = r.json()["output"]["task_id"]

            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(1.2)
                q = cli.get(QUERY_URL.format(task_id), headers=headers)
                q.raise_for_status()
                body = q.json()
                status = body["output"]["task_status"]
                output = body.get("output") or {}
                task_code = output.get("code") or body.get("code")
                if status in ("FAILED", "CANCELED") and task_code == "ASR_RESPONSE_HAVE_NO_WORDS":
                    if diarization:
                        raise RuntimeError("录音中没有识别到有效语音")
                    return ""
                if status == "SUCCEEDED":
                    texts = []
                    speakers = []
                    for res in output.get("results", []):
                        subtask_status = str(res.get("subtask_status") or "").upper()
                        subtask_code = res.get("code") or res.get("message")
                        if subtask_status and subtask_status not in {"SUCCEEDED", "SUCCESS"}:
                            if subtask_code == "ASR_RESPONSE_HAVE_NO_WORDS":
                                continue
                            raise RuntimeError(f"ASR 子任务失败：{subtask_code or subtask_status}")
                        transcription_url = res.get("transcription_url")
                        if not transcription_url:
                            continue
                        tr = cli.get(transcription_url)
                        tr.raise_for_status()
                        for u in tr.json().get("transcripts", []):
                            if diarization:
                                for sentence in u.get("sentences") or []:
                                    label = sentence.get("speaker_id")
                                    content = (sentence.get("text") or "").strip()
                                    if not content or label is None:
                                        continue
                                    try:
                                        speaker_id = int(label)
                                        start = max(0, int(sentence.get("begin_time") or 0))
                                        end = max(start, int(sentence.get("end_time") or start))
                                    except (TypeError, ValueError):
                                        continue
                                    if speaker_id >= 0:
                                        speakers.append({"speaker_id": speaker_id, "start_ms": start,
                                                         "end_ms": end, "text": content})
                            text = u.get("text", "").strip()
                            if text:
                                texts.append(text)
                    if diarization:
                        if not speakers:
                            raise RuntimeError("云端未返回发言人分离结果，请稍后重试")
                        return sorted(speakers, key=lambda item: (item["start_ms"], item["end_ms"]))
                    return " ".join(texts)
                if status in ("FAILED", "CANCELED"):
                    raise RuntimeError(f"ASR task {status}: {body}")
            raise TimeoutError("ASR polling timeout")
    finally:
        discard_audio(audio_token)


def transcribe(wav_path: str, timeout: float = 180.0) -> str:
    """转写音频；公网 HTTPS 部署走 filetrans，本地环境自动走 Base64 同步接口。"""
    if not DASHSCOPE_API_KEY:
        raise RuntimeError("DASHSCOPE_API_KEY 未配置")
    if _has_public_callback_base():
        return _transcribe_filetrans(wav_path, timeout)
    return _transcribe_inline(wav_path)


def speaker_unavailable_reason() -> str:
    """检查发言人分离的必要配置；不请求云端，也不修改访谈状态。"""
    if not DASHSCOPE_API_KEY:
        return "语音服务尚未配置，请联系管理员配置 DASHSCOPE_API_KEY"
    if not _has_public_callback_base():
        return "自动区分发言人需要可供云端读取的 HTTPS 回调地址，请联系管理员配置 FANGTAN_PUBLIC_BASE"
    return ""


def transcribe_speakers(wav_path: str, timeout: float = 600.0) -> list[dict]:
    """经单独同意后区分本次录音中的发言人，不生成或持久化声纹模板。"""
    unavailable_reason = speaker_unavailable_reason()
    if unavailable_reason:
        raise RuntimeError(unavailable_reason)
    return _transcribe_filetrans(wav_path, timeout, diarization=True)
