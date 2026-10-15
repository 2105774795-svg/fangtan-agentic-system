import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app


class ImportApiTests(unittest.TestCase):
    def setUp(self):
        app.IMPORT_JOBS.clear()
        self.mode_patch = patch.object(app.config, "AUTH_ENABLED", True)
        self.mode_patch.start()
        self.auth_patch = patch("app.employee_auth.current_user", new=AsyncMock(
            return_value={"id": 1, "name": "测试人员", "type": "正式员工"}))
        self.auth_patch.start()
        self.client = TestClient(app.app)

    def tearDown(self):
        app.IMPORT_JOBS.clear()
        self.client.close()
        self.auth_patch.stop()
        self.mode_patch.stop()

    def test_upload_is_accepted_without_exposing_internal_path(self):
        with tempfile.TemporaryDirectory() as root, \
                patch.object(app.config, "UPLOAD_DIR", Path(root)), \
                patch("app._run_import_job", return_value=None):
            response = self.client.post(
                "/api/imports/analyze",
                data={"subject": "渠道变化", "user_name": "测试人员", "interviewee_type": "企业高管", "industry": "制造业"},
                files={"material": ("访谈.txt", "受访者：订单增长两成。".encode("utf-8"), "text/plain")},
            )
        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["kind"], "transcript")
        self.assertNotIn("path", payload)
        self.assertNotIn("focus", payload)

    def test_unsupported_extension_is_rejected(self):
        response = self.client.post(
            "/api/imports/analyze",
            files={"material": ("访谈.exe", b"content", "application/octet-stream")},
        )
        self.assertEqual(response.status_code, 415)

    def test_only_owner_can_cancel_pending_job(self):
        app.IMPORT_JOBS["ownedjob"] = {"job_id": "ownedjob", "owner_id": 1,
                                       "status": "queued", "stage": "待处理", "created_at": 1}
        response = self.client.post("/api/imports/ownedjob/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelling")
        self.assertTrue(app.IMPORT_JOBS["ownedjob"]["cancel_requested"])
        self.assertEqual(self.client.post("/api/imports/ownedjob/cancel").status_code, 200)
        with patch("app.employee_auth.current_user", new=AsyncMock(return_value={"id": 2, "name": "乙"})):
            self.assertEqual(self.client.post("/api/imports/ownedjob/cancel").status_code, 404)


if __name__ == "__main__":
    unittest.main()
