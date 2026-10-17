"""Recording failures retain audio; silence never schedules an empty report."""
import io
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import store

class CaptureRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(temp)
        for target, key, value in ((store, "DATA_DIR", root), (store, "DB_PATH", root / "test.db"),
                                   (app.config, "DATA_DIR", root), (app.config, "UPLOAD_DIR", root / "uploads"),
                                   (app.config, "REPORT_DIR", root / "reports"), (app.config, "AUTH_ENABLED", False)):
            self.enterContext(patch.object(target, key, value))
        store.init()
        self.client = self.enterContext(TestClient(app.app))
        self.sid = "capture-recovery"
        store.create_session(self.sid, "测试", "门店", "商户", [], owner_id=-1)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(bytes(16000 * 2))
        self.audio = buffer.getvalue()

    def upload(self):
        return self.client.post(f"/api/sessions/{self.sid}/audio", data={"chunk_id": "retry-chunk-001"},
                                files={"audio": ("silence.wav", self.audio, "audio/wav")})

    def test_failed_transcription_can_retry_same_saved_recording_then_finish_silence(self):
        with patch("app.asr_dashscope.transcribe", side_effect=RuntimeError("provider unavailable")):
            failed = self.upload()
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(store.get_audio_receipt(self.sid, "retry-chunk-001")["status"], "pending")
        unfinished = self.client.post(f"/api/sessions/{self.sid}/finish?background=true")
        self.assertEqual(unfinished.status_code, 409)
        self.assertEqual(store.get_session_summary(self.sid)["status"], "live")
        folder = Path(app.config.DATA_DIR) / "recordings" / self.sid
        self.assertEqual(len(list(folder.glob("[0-9]*.wav"))), 1)
        with patch("app.asr_dashscope.transcribe", return_value=""):
            retried = self.upload()
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["transcription_status"], "no_speech")
        self.assertTrue(retried.json()["audio_saved"])
        self.assertEqual(len(list(folder.glob("[0-9]*.wav"))), 1)
        with patch("app.asr_dashscope.transcribe", side_effect=AssertionError("must not retranscribe completed chunk")):
            self.assertEqual(self.upload().status_code, 200)
        with patch("app.job_queue.enqueue") as enqueue:
            result = self.client.post(f"/api/sessions/{self.sid}/finish?background=true")
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["report_status"], "empty")
            enqueue.assert_not_called()
        session = self.client.get(f"/api/sessions/{self.sid}").json()
        self.assertEqual(session["status"], "done")
        self.assertEqual(session["report_status"], "empty")
        self.assertTrue(session["has_audio"])
        self.assertEqual(session["utterances"], [])
        self.assertEqual(self.client.get(f"/api/sessions/{self.sid}/recording").status_code, 200)
