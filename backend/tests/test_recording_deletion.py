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


class RecordingDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fangtan-recording-delete-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        patches = (
            patch.object(app.config, "AUTH_ENABLED", False, create=True),
            patch.object(store, "DATA_DIR", root),
            patch.object(store, "DB_PATH", root / "test.db"),
            patch.object(app.config, "DATA_DIR", root),
            patch.object(app.config, "UPLOAD_DIR", root / "uploads"),
            patch.object(app.config, "REPORT_DIR", root / "reports"),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        store.init()
        app.config.UPLOAD_DIR.mkdir()
        app.config.REPORT_DIR.mkdir()
        self.client = self.enterContext(TestClient(app.app))

    def _wav(self, sid, seconds=4):
        recording_dir = Path(app.config.DATA_DIR) / "recordings" / sid
        recording_dir.mkdir(parents=True, exist_ok=True)
        path = recording_dir / "recording.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(bytes(seconds * 16000 * 2))
        store.set_audio_path(sid, str(path))
        return path

    def _session(self, sid="delete-me"):
        store.create_session(sid, "访谈", "门店", "商户", [], user_name="本地使用者", owner_id=-1)
        return sid

    def test_delete_finished_recording_keeps_transcript_and_report(self):
        sid = self._session()
        path = self._wav(sid)
        store.add_utterance(sid, 0, 0, 4000, "保留的逐字稿")
        store.save_report(sid, "# 保留的纪要")
        store.finish_session(sid)

        response = self.client.delete(f"/api/sessions/{sid}/recording")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(path.exists())
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/recording").status_code, 404)
        detail = self.client.get(f"/api/sessions/{sid}").json()
        self.assertFalse(detail["has_audio"])
        self.assertEqual(detail["utterances"][0]["text"], "保留的逐字稿")
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/report.md").text, "# 保留的纪要")

    def test_delete_one_finished_segment_cuts_audio_and_removes_transcript(self):
        sid = self._session("delete-segment")
        path = self._wav(sid)
        store.add_utterance(sid, 0, 0, 2000, "应删除")
        store.add_utterance(sid, 1, 2000, 4000, "应保留")
        store.save_report(sid, "# 需要重新生成")
        store.finish_session(sid)

        response = self.client.delete(f"/api/sessions/{sid}/utterances/0")

        self.assertEqual(response.status_code, 200, response.text)
        with wave.open(str(path), "rb") as audio:
            self.assertEqual(audio.getnframes(), 2 * 16000)
        detail = self.client.get(f"/api/sessions/{sid}").json()
        self.assertEqual([(u["seq"], u["start_ms"], u["end_ms"], u["text"])
                          for u in detail["utterances"]], [(1, 0, 2000, "应保留")])
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/report.md").status_code, 404)

    def test_recording_deletion_requires_finished_audio(self):
        sid = self._session("live-delete")
        self._wav(sid)
        self.assertEqual(self.client.delete(f"/api/sessions/{sid}/recording").status_code, 409)
        self.assertEqual(self.client.delete(f"/api/sessions/{sid}/utterances/0").status_code, 409)


if __name__ == "__main__":
    unittest.main()
