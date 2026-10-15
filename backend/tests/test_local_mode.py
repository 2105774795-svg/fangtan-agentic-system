import sys
import tempfile
import unittest
import wave
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app
import store
import worker


class LocalModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fangtan-local-mode-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for item in (
            patch.object(app.config, "AUTH_ENABLED", False, create=True),
            patch.object(store, "DATA_DIR", root),
            patch.object(store, "DB_PATH", root / "test.db"),
            patch.object(app.config, "DATA_DIR", root),
            patch.object(app.config, "UPLOAD_DIR", root / "uploads"),
            patch.object(app.config, "REPORT_DIR", root / "reports"),
            patch("app.employee_auth.upstream", side_effect=AssertionError("本地模式不能访问员工中心")),
        ):
            item.start()
            self.addCleanup(item.stop)
        store.init()
        app.config.UPLOAD_DIR.mkdir()
        app.config.REPORT_DIR.mkdir()
        self.client = self.enterContext(TestClient(app.app))
        self.addCleanup(app.IMPORT_JOBS.clear)

    def create(self):
        response = self.client.post("/api/sessions/", json={"subject": "本地访谈"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["session_id"]

    def test_open_without_login_can_create_and_manage_session(self):
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["auth_enabled"])
        self.assertEqual(response.json()["user"]["id"], -1)
        self.assertNotIn("set-cookie", response.headers)
        sid = self.create()
        self.assertEqual(store.get_session_summary(sid)["owner_id"], -1)
        self.assertEqual(self.client.get(f"/api/sessions/{sid}").json()["access"], "owner")
        self.assertEqual(self.client.patch(f"/api/sessions/{sid}", json={"subject": "改好的标题"}).status_code, 200)
        todo = self.client.post(f"/api/sessions/{sid}/todos", json={"text": "核实订单"})
        self.assertEqual(todo.status_code, 200)
        self.assertEqual(self.client.delete(f"/api/sessions/{sid}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/sessions/{sid}").status_code, 404)

    def test_existing_employee_and_legacy_records_are_visible_without_reassigning(self):
        for sid, owner in (("employee-one", 1), ("employee-two", 2), ("legacy", None)):
            store.create_session(sid, sid, "商户门店", "商户", [], user_name="原员工", owner_id=owner)
            store.update_session_project(sid, "原项目")
        store.add_todo("employee-two", "旧待办")
        response = self.client.get("/api/sessions/")
        self.assertEqual(response.status_code, 200, response.text)
        page = response.json()
        self.assertEqual(page["total"], 3)
        self.assertTrue(all(item["access"] == "owner" for item in page["items"]))
        self.assertEqual(self.client.get("/api/projects").json()["projects"], ["原项目"])
        overview = self.client.get("/api/projects/overview").json()["projects"]
        # Same-name projects retain their separate owners after the folder upgrade.
        self.assertEqual(len({item["id"] for item in overview}), 3)
        self.assertEqual(sum(item["interviews"] for item in overview), 3)
        self.assertEqual(sum(item["shared"] for item in overview), 0)
        management = self.client.get("/api/management/overview").json()
        self.assertEqual(management["stats"]["interviews"], 3)
        self.assertEqual(management["todos"][0]["access"], "owner")
        self.assertEqual(self.client.patch("/api/sessions/employee-two", json={"subject": "本地修订"}).status_code, 200)
        self.assertEqual(store.get_session_summary("employee-two")["owner_id"], 2)
        self.assertIsNone(store.get_session_summary("legacy")["owner_id"])

    def test_account_and_share_endpoints_are_paused_without_upstream_calls(self):
        sid = self.create()
        for method, path, body in (
            ("POST", "/api/auth/login", {"name": "甲", "password": "test"}),
            ("POST", "/api/auth/register", {}),
            ("POST", "/api/auth/reset-password", {}),
            ("GET", "/api/auth/people", None),
            ("GET", f"/api/sessions/{sid}/shares", None),
            ("POST", f"/api/sessions/{sid}/shares", {"user_id": 1, "user_name": "甲"}),
            ("POST", f"/api/sessions/{sid}/share-links", {"kind": "transcript"}),
            ("GET", "/api/share/" + "a" * 43, None),
        ):
            with self.subTest(path=path):
                response = self.client.request(method, path, json=body)
                self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)
        with store.get() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0], 0)

    def test_switching_back_restores_auth_without_publicizing_local_records(self):
        sid = self.create()
        store.create_session("employee-one", "员工资料", "商户门店", "商户", [], owner_id=1)
        with patch.object(app.config, "AUTH_ENABLED", True):
            self.assertEqual(self.client.get("/api/sessions/").status_code, 401)
            self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
            async def employee_login(path, body=None, method="POST"):
                return {"success": True, "data": {"id": 1, "name": "甲"}}
            with patch("app.employee_auth.upstream", side_effect=employee_login):
                response = self.client.post("/api/auth/login", json={"name": "甲", "password": "test"})
                self.assertEqual(response.status_code, 200)
            self.assertTrue(self.client.get("/api/auth/me").json()["auth_enabled"])
            self.assertEqual(self.client.get(f"/api/sessions/{sid}").status_code, 404)
            self.assertEqual(self.client.get("/api/sessions/employee-one").status_code, 200)
        self.assertEqual(store.get_session_summary(sid)["owner_id"], -1)

    def test_local_text_import_exports_without_employee_login(self):
        with patch("app.threading.Thread"):
            response = self.client.post("/api/imports/analyze", files={
                "material": ("访谈.txt", "受访者：订单增长两成。".encode(), "text/plain")})
            self.assertEqual(response.status_code, 202, response.text)
            job = response.json()
            with patch("app.kimi.gen_report", return_value="# 本地纪要\n订单增长两成"):
                self.assertTrue(worker.run_once())
        result = self.client.get("/api/imports/" + job["job_id"]).json()
        self.assertEqual(result["status"], "ready")
        sid = result["session_id"]
        for suffix in ("report.md", "transcript.txt", "minutes.docx"):
            response = self.client.get(f"/api/sessions/{sid}/{suffix}")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.content)

    def test_local_recording_upload_finish_and_playback(self):
        sid = self.create()
        raw = BytesIO()
        with wave.open(raw, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(bytes(32000))
        with patch("app.asr_dashscope.transcribe", return_value="测试录音内容"):
            response = self.client.post(f"/api/sessions/{sid}/audio", files={"audio": ("chunk.wav", raw.getvalue())})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.client.get(f"/api/sessions/{sid}/state").json()["utterances"]), 1)
        with patch("app.kimi.gen_report", return_value="# 纪要\n测试内容"):
            response = self.client.post(f"/api/sessions/{sid}/finish")
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.get(f"/api/sessions/{sid}/recording", headers={"Range": "bytes=0-99"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(response.content), 100)


if __name__ == "__main__":
    unittest.main()
