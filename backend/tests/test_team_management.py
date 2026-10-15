import sys
import tempfile
import time
import unittest
import wave
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app
import store


class TeamManagementTests(unittest.TestCase):
    def setUp(self):
        app.SPEAKER_JOBS.clear()
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [
            patch.object(app.config, "AUTH_ENABLED", True),
            patch.object(store, "DATA_DIR", root),
            patch.object(store, "DB_PATH", root / "test.db"),
            patch.object(app.config, "DATA_DIR", root),
            patch.object(app.config, "UPLOAD_DIR", root / "uploads"),
            patch.object(app.config, "REPORT_DIR", root / "reports"),
            patch("app.employee_auth.upstream", side_effect=self.fake_upstream),
            patch.object(app.asr_dashscope, "PUBLIC_BASE", "https://interview.example.com"),
            patch.object(app.asr_dashscope, "DASHSCOPE_API_KEY", "test-only"),
        ]
        for item in self.patches:
            item.start()
        store.init()
        (root / "reports").mkdir()
        self.owner = TestClient(app.app)
        self.colleague = TestClient(app.app)

    def tearDown(self):
        app.SPEAKER_JOBS.clear()
        app.IMPORT_JOBS.clear()
        self.owner.close()
        self.colleague.close()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    async def fake_upstream(self, path, body=None, method="POST"):
        if path == "/api/verify-login":
            if body.get("password") != "correct":
                return {"success": False, "message": "密码错误"}
            return {"success": True, "data": {"id": 1 if body["name"] == "甲" else 2,
                                               "name": body["name"], "type": "正式员工"}}
        if path.startswith("/api/employees"):
            return {"success": True, "data": [{"id": 2, "name": "乙", "type": "正式员工"}]}
        if path == "/api/check-session":
            return {"success": True, "data": {"id": 1 if body["name"] == "甲" else 2,
                                               "name": body["name"]}}
        raise AssertionError(path)

    def login(self, client, name):
        response = client.post("/api/auth/login", json={"name": name, "password": "correct"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_login_owner_and_explicit_share(self):
        self.assertEqual(self.owner.get("/api/sessions/").status_code, 401)
        self.login(self.owner, "甲")
        self.login(self.colleague, "乙")
        response = self.owner.post("/api/sessions/", json={"subject": "成本变化"})
        self.assertEqual(response.status_code, 200, response.text)
        sid = response.json()["session_id"]
        self.assertEqual(self.colleague.get(f"/api/sessions/{sid}").status_code, 404)
        self.assertEqual(self.colleague.get("/api/sessions/").json()["total"], 0)

        response = self.owner.post(f"/api/sessions/{sid}/shares",
                                   json={"user_id": 2, "user_name": "乙"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.colleague.get(f"/api/sessions/{sid}").status_code, 200)
        self.assertEqual(self.colleague.get("/api/sessions/?scope=shared").json()["total"], 1)
        self.assertEqual(self.colleague.patch(f"/api/sessions/{sid}", json={"subject": "改名"}).status_code, 403)
        self.assertEqual(self.colleague.delete(f"/api/sessions/{sid}").status_code, 403)
        self.owner.delete(f"/api/sessions/{sid}/shares/2")
        self.assertEqual(self.colleague.get(f"/api/sessions/{sid}").status_code, 404)

    def test_project_search_and_todo(self):
        self.login(self.owner, "甲")
        sid = self.owner.post("/api/sessions/", json={"subject": "渠道变化"}).json()["session_id"]
        store.add_utterance(sid, 0, 0, 10000, "今年订单增长两成")
        self.assertEqual(self.owner.get("/api/sessions/?search=订单").json()["total"], 1)
        self.owner.patch(f"/api/sessions/{sid}/project", json={"project_name": "制造业"})
        self.assertEqual(self.owner.get("/api/sessions/?project=制造业").json()["total"], 1)
        self.assertEqual(self.owner.get("/api/projects").json()["projects"], ["制造业"])
        response = self.owner.post(f"/api/sessions/{sid}/todos", json={"text": "核实订单"})
        todo = response.json()["todos"][0]
        self.owner.patch(f"/api/sessions/{sid}/todos/{todo['id']}", json={"done": True})
        self.assertEqual(self.owner.get(f"/api/sessions/{sid}/todos").json()["todos"][0]["done"], 1)

    def test_project_overview_only_counts_visible_sessions(self):
        self.login(self.owner, "甲")
        self.login(self.colleague, "乙")
        first = self.owner.post("/api/sessions/", json={"subject": "项目甲"}).json()["session_id"]
        second = self.owner.post("/api/sessions/", json={"subject": "项目乙"}).json()["session_id"]
        self.owner.patch(f"/api/sessions/{first}/project", json={"project_name": "制造业"})
        self.owner.patch(f"/api/sessions/{second}/project", json={"project_name": "制造业"})
        self.owner.post(f"/api/sessions/{first}/shares", json={"user_id": 2, "user_name": "乙"})
        self.owner.post(f"/api/sessions/{first}/todos", json={"text": "核实采购"})
        own = self.owner.get("/api/projects/overview").json()["projects"]
        shared = self.colleague.get("/api/projects/overview").json()["projects"]
        self.assertEqual(own[0]["interviews"], 2)
        self.assertEqual(own[0]["open_todos"], 1)
        self.assertEqual(shared[0]["interviews"], 1)
        self.assertEqual(shared[0]["shared"], 1)

    def test_report_todo_extraction(self):
        md = "# 纪要\n## 行动项与待办\n- [ ] 张三核实合同 [00:20]\n- 未提及\n## 主题章节\n内容"
        self.assertEqual(app._extract_report_todos(md), ["张三核实合同 [00:20]"])
        table = "## 行动项与待办\n| 事项 | 负责人 | 来源 |\n|---|---|---|\n| 核实合同 | 未提及 | [00:20] |\n## 主题章节"
        self.assertEqual(app._extract_report_todos(table), ["事项：核实合同；负责人：未提及；来源：[00:20]"])

    def test_verified_name_claims_only_matching_legacy_rows(self):
        store.create_session("old_a", "旧访谈", "导入文字稿", "商户", [], user_name="甲")
        store.create_session("old_b", "匿名旧访谈", "导入文字稿", "商户", [])
        self.login(self.owner, "甲")
        self.assertEqual(self.owner.get("/api/sessions/?scope=mine").json()["total"], 1)
        self.assertEqual(self.owner.get("/api/sessions/old_b").status_code, 404)

    def test_live_chunks_are_assembled_for_private_playback(self):
        self.login(self.owner, "甲")
        sid = self.owner.post("/api/sessions/", json={"subject": "录音测试"}).json()["session_id"]
        folder = app.config.DATA_DIR / "recordings" / sid
        folder.mkdir(parents=True)
        for index in range(2):
            with wave.open(str(folder / f"{index:05d}.wav"), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b"\0\0" * 160)
        app._assemble_live_recording(sid)
        self.assertTrue((folder / "recording.wav").exists())
        self.assertFalse((folder / "00000.wav").exists())
        self.assertEqual(self.owner.get(f"/api/sessions/{sid}/recording").status_code, 200)
        self.assertEqual(self.colleague.get(f"/api/sessions/{sid}/recording").status_code, 401)

    def test_shared_user_cannot_change_report_or_persist_questions(self):
        self.login(self.owner, "甲")
        self.login(self.colleague, "乙")
        sid = self.owner.post("/api/sessions/", json={"subject": "成本"}).json()["session_id"]
        store.add_utterance(sid, 0, 0, 10000, "原料涨价一成")
        store.save_report(sid, "# 原始纪要")
        self.owner.post(f"/api/sessions/{sid}/shares", json={"user_id": 2, "user_name": "乙"})
        self.assertEqual(self.colleague.put(f"/api/sessions/{sid}/report",
                                            json={"markdown": "# 篡改"}).status_code, 403)
        response = self.owner.put(f"/api/sessions/{sid}/report", json={"markdown": "# 人工修订"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.colleague.get(f"/api/sessions/{sid}/report.md").text, "# 人工修订")
        with patch("app.kimi.answer_from_transcript", return_value="涨价一成 [00:00]"):
            reply = self.colleague.post(f"/api/sessions/{sid}/ask", json={"question": "涨了多少？"})
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(store.list_questions(sid), [])

    def test_word_export_and_expiring_revocable_share_link(self):
        self.login(self.owner, "甲")
        self.login(self.colleague, "乙")
        sid = self.owner.post("/api/sessions/", json={"subject": "经营访谈"}).json()["session_id"]
        store.add_utterance(sid, 0, 1200, 3000, "今年订单增长两成", speaker="受访者")
        store.save_report(sid, "# 摘要\n订单增长两成")
        export = self.owner.get(f"/api/sessions/{sid}/minutes.docx")
        self.assertEqual(export.status_code, 200)
        with ZipFile(BytesIO(export.content)) as archive:
            document = archive.read("word/document.xml").decode("utf-8")
            self.assertIn("经营访谈", document)
            self.assertIn("受访者", document)
        self.assertEqual(self.colleague.get(f"/api/sessions/{sid}/minutes.docx").status_code, 404)
        self.assertEqual(self.colleague.post(f"/api/sessions/{sid}/share-links",
                                             json={"kind": "word"}).status_code, 404)
        created = self.owner.post(f"/api/sessions/{sid}/share-links", json={"kind": "word"})
        self.assertEqual(created.status_code, 200, created.text)
        link = created.json()
        self.assertEqual(self.colleague.get("/" + link["path"]).status_code, 200)
        self.assertNotIn("token_hash", str(self.owner.get(f"/api/sessions/{sid}/share-links").json()))
        self.owner.delete(f"/api/sessions/{sid}/share-links/{link['id']}")
        self.assertEqual(self.colleague.get("/" + link["path"]).status_code, 404)

        expired = self.owner.post(f"/api/sessions/{sid}/share-links", json={"kind": "report"}).json()
        with store.get() as db:
            db.execute("UPDATE session_share_links SET expires_at=0 WHERE id=?", (expired["id"],))
        self.assertEqual(self.colleague.get("/" + expired["path"]).status_code, 404)

    def test_speaker_analysis_with_local_callback_does_not_create_job(self):
        self.login(self.owner, "甲")
        sid = self.owner.post("/api/sessions/", json={"subject": "本地发言人分析"}).json()["session_id"]
        store.finish_session(sid)
        folder = app.config.DATA_DIR / "recordings" / sid
        folder.mkdir(parents=True)
        original = folder / "recording.wav"
        original.write_bytes(b"RIFFtest")
        store.set_audio_path(sid, str(original))
        with patch.object(app.asr_dashscope, "PUBLIC_BASE", "http://127.0.0.1:18995"), \
                patch("app.threading.Thread"):
            response = self.owner.post(f"/api/sessions/{sid}/speakers/analyze", json={"consent": True})
            self.assertEqual(response.status_code, 503)
            self.assertIn("HTTPS", response.json()["detail"])
            status = self.owner.get(f"/api/sessions/{sid}/speakers").json()
        self.assertFalse(status["available"])
        self.assertTrue(status["unavailable_reason"])
        self.assertEqual(status["status"], "none")
        self.assertIsNone(status["consented_at"])
        self.assertNotIn(sid, app.SPEAKER_JOBS)

    def test_speaker_analysis_without_api_key_does_not_record_consent(self):
        self.login(self.owner, "甲")
        sid = self.owner.post("/api/sessions/", json={"subject": "未配置转写服务"}).json()["session_id"]
        store.finish_session(sid)
        folder = app.config.DATA_DIR / "recordings" / sid
        folder.mkdir(parents=True)
        original = folder / "recording.wav"
        original.write_bytes(b"RIFFtest")
        store.set_audio_path(sid, str(original))
        with patch.object(app.asr_dashscope, "DASHSCOPE_API_KEY", ""), patch("app.threading.Thread"):
            response = self.owner.post(f"/api/sessions/{sid}/speakers/analyze", json={"consent": True})
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(store.get_session_summary(sid)["speaker_consented_at"])
        self.assertNotIn(sid, app.SPEAKER_JOBS)

    def test_speaker_analysis_requires_consent_and_can_be_deleted(self):
        self.login(self.owner, "甲")
        sid = self.owner.post("/api/sessions/", json={"subject": "双人访谈"}).json()["session_id"]
        store.finish_session(sid)
        folder = app.config.DATA_DIR / "recordings" / sid
        folder.mkdir(parents=True)
        original = folder / "recording.wav"
        original.write_bytes(b"RIFFtest")
        store.set_audio_path(sid, str(original))
        self.assertEqual(self.owner.post(f"/api/sessions/{sid}/speakers/analyze",
                                         json={"consent": False}).status_code, 400)
        with patch("app.threading.Thread"):
            response = self.owner.post(f"/api/sessions/{sid}/speakers/analyze", json={"consent": True})
        self.assertEqual(response.status_code, 202, response.text)
        job_id = app.SPEAKER_JOBS[sid]["job_id"]
        segments = [{"speaker_id": 0, "start_ms": 0, "end_ms": 1200, "text": "你好"},
                    {"speaker_id": 1, "start_ms": 1200, "end_ms": 2300, "text": "谢谢"}]
        with patch("app._ffmpeg_executable", return_value="test-ffmpeg"), \
                patch("app.subprocess.run"), patch("app.asr_dashscope.transcribe_speakers", return_value=segments):
            app._run_speaker_analysis(sid, job_id)
        result = self.owner.get(f"/api/sessions/{sid}/speakers").json()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["segments"]), 2)
        self.assertIn("发言人 B", self.owner.get(f"/api/sessions/{sid}/speakers.txt").text)
        self.assertEqual(self.owner.delete(f"/api/sessions/{sid}/speakers").status_code, 200)
        self.assertEqual(self.owner.get(f"/api/sessions/{sid}/speakers").json()["segments"], [])
        self.assertFalse(store.save_speaker_segments(sid, job_id, segments))

    def test_exports_include_earliest_utterance_beyond_ten_thousand(self):
        self.login(self.owner, "甲")
        sid = self.owner.post("/api/sessions/", json={"subject": "长访谈"}).json()["session_id"]
        with store.get() as db:
            db.executemany("""INSERT INTO utterances(session_id,seq,start_ms,end_ms,text,created_at,dims,speaker)
                VALUES(?,?,?,?,?,?,?,?)""",
                ((sid, seq, seq * 1000, (seq + 1) * 1000,
                  "最初发言" if seq == 0 else f"第{seq}段", 0, "", "") for seq in range(10001)))
        store.save_report(sid, "# 纪要\n测试")
        transcript = self.owner.get(f"/api/sessions/{sid}/transcript.txt")
        self.assertEqual(transcript.status_code, 200)
        self.assertTrue("最初发言" in transcript.text, "TXT 导出遗漏最早一段")
        word = self.owner.get(f"/api/sessions/{sid}/minutes.docx")
        with ZipFile(BytesIO(word.content)) as archive:
            self.assertTrue("最初发言" in archive.read("word/document.xml").decode("utf-8"),
                            "Word 导出遗漏最早一段")

    def test_cancel_during_report_failure_cleans_imported_session(self):
        folder = app.config.UPLOAD_DIR / "_imports" / "cancelrace"
        folder.mkdir(parents=True)
        material = folder / "material.txt"
        material.write_text("受访者：成本上涨。", encoding="utf-8")
        now = time.time()
        app.IMPORT_JOBS["cancelrace"] = {
            "job_id": "cancelrace", "status": "queued", "stage": "待处理", "progress": 0,
            "filename": "material.txt", "kind": "transcript", "session_id": None,
            "created_at": now, "updated_at": now, "path": str(material), "subject": "成本",
            "user_name": "甲", "interviewee_type": "商户", "industry": "制造业",
            "focus": "成本", "owner_id": 1,
        }

        def fail_after_cancel(_sid):
            app.IMPORT_JOBS["cancelrace"]["cancel_requested"] = True
            raise RuntimeError("模型暂时不可用")

        with patch("app._make_report", side_effect=fail_after_cancel):
            app._run_import_job("cancelrace")
        self.assertEqual(app.IMPORT_JOBS["cancelrace"]["status"], "cancelled")
        self.assertEqual(store.list_sessions(user_id=1)["total"], 0)
        self.assertFalse(folder.exists())

    def test_failed_speaker_reanalysis_marks_previous_result_as_stale(self):
        self.login(self.owner, "甲")
        sid = self.owner.post("/api/sessions/", json={"subject": "复核发言人"}).json()["session_id"]
        store.finish_session(sid)
        folder = app.config.DATA_DIR / "recordings" / sid
        folder.mkdir(parents=True)
        original = folder / "recording.wav"
        original.write_bytes(b"RIFFtest")
        store.set_audio_path(sid, str(original))
        old = [{"speaker_id": 0, "start_ms": 0, "end_ms": 1000, "text": "旧结果"}]
        store.start_speaker_analysis(sid, "oldjob")
        store.save_speaker_segments(sid, "oldjob", old)
        with patch("app.threading.Thread"):
            response = self.owner.post(f"/api/sessions/{sid}/speakers/analyze", json={"consent": True})
        self.assertEqual(response.status_code, 202)
        new_job = app.SPEAKER_JOBS[sid]["job_id"]
        with patch("app._ffmpeg_executable", return_value="test-ffmpeg"), \
                patch("app.subprocess.run"), patch("app.asr_dashscope.transcribe_speakers",
                                               side_effect=RuntimeError("云端失败")):
            app._run_speaker_analysis(sid, new_job)
        result = self.owner.get(f"/api/sessions/{sid}/speakers").json()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result.get("stale", False), "旧发言人结果未标记为上轮结果")
        self.assertEqual(result["segments"][0]["text"], "旧结果")

    def test_cancel_arriving_during_failure_commit_is_not_acknowledged(self):
        self.login(self.owner, "甲")
        folder = app.config.UPLOAD_DIR / "_imports" / "failurewindow"
        folder.mkdir(parents=True)
        material = folder / "material.txt"
        material.write_text("受访者：利润下降。", encoding="utf-8")
        app.IMPORT_JOBS["failurewindow"] = {
            "job_id": "failurewindow", "status": "queued", "stage": "待处理", "progress": 0,
            "kind": "transcript", "session_id": None, "path": str(material),
            "subject": "利润", "user_name": "甲", "interviewee_type": "商户",
            "industry": "制造业", "focus": "利润", "owner_id": 1,
        }
        original_finish = store.finish_session
        cancel_status = []

        def finish_after_cancel(sid):
            cancel_status.append(self.owner.post("/api/imports/failurewindow/cancel").status_code)
            original_finish(sid)

        with patch("app._make_report", side_effect=RuntimeError("模型失败")), \
                patch("app.store.finish_session", side_effect=finish_after_cancel), \
                patch.object(app.logger, "exception"):
            app._run_import_job("failurewindow")
        self.assertEqual(cancel_status, [409], "失败提交后不应再答复取消成功")
        self.assertEqual(app.IMPORT_JOBS["failurewindow"]["status"], "failed")

    def test_partial_import_failure_does_not_offer_report_retry(self):
        folder = app.config.UPLOAD_DIR / "_imports" / "partial"
        folder.mkdir(parents=True)
        material = folder / "material.txt"
        material.write_text("受访者：第一段。\n受访者：第二段。", encoding="utf-8")
        app.IMPORT_JOBS["partial"] = {
            "job_id": "partial", "status": "queued", "stage": "待处理", "progress": 0,
            "kind": "transcript", "session_id": None, "path": str(material),
            "subject": "两段", "user_name": "甲", "interviewee_type": "商户",
            "industry": "制造业", "focus": "", "owner_id": 1,
        }
        actual_add = store.add_utterance
        attempts = 0

        def fail_second(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise RuntimeError("写入第二段失败")
            return actual_add(*args, **kwargs)

        with patch("app.store.add_utterance", side_effect=fail_second), patch.object(app.logger, "exception"):
            app._run_import_job("partial")
        self.assertEqual(app.IMPORT_JOBS["partial"]["status"], "failed")
        self.assertIsNone(app.IMPORT_JOBS["partial"]["session_id"])
        self.assertEqual(store.list_sessions(user_id=1)["total"], 0)


if __name__ == "__main__":
    unittest.main()
