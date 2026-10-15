import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app


class ImportedAudioTimingTests(unittest.TestCase):
    def transcribe_fixture(self, seconds, texts=None):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(bytes(seconds * 16000 * 2))
            app.IMPORT_JOBS["timing-test"] = {"status": "processing"}
            try:
                # 真实 FFmpeg 切片；只有外部语音识别使用固定结果。
                with patch("app.asr_dashscope.transcribe", side_effect=texts, return_value="测试音频内容"):
                    return app._transcribe_import_audio("timing-test", path)
            finally:
                app.IMPORT_JOBS.pop("timing-test", None)

    def test_short_recording_ends_at_actual_duration(self):
        utterances = self.transcribe_fixture(2)
        self.assertEqual(len(utterances), 1)
        self.assertEqual(utterances[0]["start_ms"], 0)
        self.assertEqual(utterances[0]["end_ms"], 2000)

    def test_short_final_segment_does_not_extend_recording(self):
        utterances = self.transcribe_fixture(241)
        self.assertEqual(len(utterances), 2)
        self.assertEqual(utterances[1]["start_ms"], utterances[0]["end_ms"])
        self.assertEqual(utterances[-1]["end_ms"], 241000)

    def test_empty_first_segment_keeps_later_audio_timestamp(self):
        utterances = self.transcribe_fixture(241, texts=["", "尾段内容"])
        self.assertEqual(len(utterances), 1)
        self.assertGreaterEqual(utterances[0]["start_ms"], 240000)
        self.assertLess(utterances[0]["start_ms"], 241000)
        self.assertEqual(utterances[0]["end_ms"], 241000)


if __name__ == "__main__":
    unittest.main()
