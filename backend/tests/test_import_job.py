import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app


class ImportJobTests(unittest.TestCase):
    def setUp(self):
        app.IMPORT_JOBS.clear()

    def tearDown(self):
        app.IMPORT_JOBS.clear()

    def test_text_job_creates_session_and_report_then_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as root:
            job_dir = Path(root) / "job"
            job_dir.mkdir()
            material = job_dir / "material.txt"
            material.write_text("主持人：今年最大的变化是什么？\n受访者：订单增长了两成。", encoding="utf-8")
            now = time.time()
            app.IMPORT_JOBS["job1"] = {
                "job_id": "job1", "status": "queued", "stage": "已接收文件", "progress": 5,
                "filename": "访谈.txt", "kind": "transcript", "session_id": None, "error": None,
                "created_at": now, "updated_at": now, "path": str(material), "subject": "订单变化",
                "user_name": "测试人员", "interviewee_type": "企业高管", "industry": "制造业", "focus": "订单",
            }
            with patch("app.uuid.uuid4") as make_uuid, \
                    patch("app.store.create_session") as create_session, \
                    patch("app.store.add_utterance") as add_utterance, \
                    patch("app._make_report", return_value="# 纪要") as make_report, \
                    patch("app.threading.Thread"):
                make_uuid.return_value.hex = "abcdef1234567890"
                app._run_import_job("job1")

            result = app.IMPORT_JOBS["job1"]
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["session_id"], "abcdef123456")
            self.assertEqual(result["progress"], 100)
            self.assertFalse(job_dir.exists())
            create_session.assert_called_once()
            self.assertEqual(create_session.call_args.args[2], "导入文字稿")
            self.assertEqual(add_utterance.call_count, 2)
            make_report.assert_called_once_with("abcdef123456")

    def test_cancelled_job_discards_upload_without_creating_session(self):
        with tempfile.TemporaryDirectory() as root:
            job_dir = Path(root) / "cancel"
            job_dir.mkdir()
            material = job_dir / "material.txt"
            material.write_text("待取消", encoding="utf-8")
            app.IMPORT_JOBS["cancel1"] = {
                "job_id": "cancel1", "status": "cancelling", "cancel_requested": True,
                "path": str(material), "kind": "transcript", "session_id": None,
            }
            with patch("app.store.create_session") as create_session:
                app._run_import_job("cancel1")
            self.assertEqual(app.IMPORT_JOBS["cancel1"]["status"], "cancelled")
            self.assertFalse(job_dir.exists())
            create_session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
