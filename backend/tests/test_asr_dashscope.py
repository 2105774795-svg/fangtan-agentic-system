import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asr_dashscope


class FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, task_body, transcript_body=None):
        self.task_body = task_body
        self.transcript_body = transcript_body or {"transcripts": []}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def post(self, *_args, **_kwargs):
        return FakeResponse({"output": {"task_id": "task-1"}})

    def get(self, url, **_kwargs):
        if url.endswith("task-1"):
            return FakeResponse(self.task_body)
        return FakeResponse(self.transcript_body)


class DashScopeNoWordsTests(unittest.TestCase):
    def _run(self, task_body, transcript_body=None, diarization=False):
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio:
            fake = FakeClient(task_body, transcript_body)
            with patch("asr_dashscope.httpx.Client", return_value=fake), \
                    patch("asr_dashscope.time.sleep"), \
                    patch("asr_dashscope.time.time", return_value=0):
                return asr_dashscope._transcribe_filetrans(audio.name, 10, diarization=diarization)

    def test_failed_task_with_no_words_is_an_empty_segment(self):
        result = self._run({"output": {"task_status": "FAILED",
                                       "code": "ASR_RESPONSE_HAVE_NO_WORDS",
                                       "message": "ASR_RESPONSE_HAVE_NO_WORDS"}})
        self.assertEqual(result, "")

    def test_failed_subtask_is_skipped_when_another_subtask_succeeds(self):
        result = self._run({"output": {"task_status": "SUCCEEDED", "results": [
            {"subtask_status": "FAILED", "code": "ASR_RESPONSE_HAVE_NO_WORDS"},
            {"subtask_status": "SUCCEEDED", "transcription_url": "https://example.test/transcript"},
        ]}}, {"transcripts": [{"text": "有效内容"}]})
        self.assertEqual(result, "有效内容")

    def test_other_failed_subtask_keeps_actionable_error(self):
        with self.assertRaisesRegex(RuntimeError, "ASR_RATE_LIMIT"):
            self._run({"output": {"task_status": "FAILED", "code": "ASR_RATE_LIMIT",
                                   "message": "稍后重试"}})


if __name__ == "__main__":
    unittest.main()
