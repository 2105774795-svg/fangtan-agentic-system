"""HTTP→持久任务→真实 worker 上下文→轮询的离线集成验收。"""
import hashlib
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
import employee_auth
import job_queue
import store
import task_handlers
import worker

# app 的模块初始化也必须留在临时库，不能首次导入就触碰用户数据。
with tempfile.TemporaryDirectory(prefix="task-api-bootstrap-") as _bootstrap:
    with ExitStack() as _stack:
        _root = Path(_bootstrap)
        for _item in (patch.object(store, "DATA_DIR", _root),
                      patch.object(store, "DB_PATH", _root / "fangtan.db"),
                      patch.object(config, "DATA_DIR", _root),
                      patch.object(config, "UPLOAD_DIR", _root / "uploads"),
                      patch.object(config, "REPORT_DIR", _root / "reports")):
            _stack.enter_context(_item)
        import app


REPORT = "# 访谈纪要\n\n客流有所变化，后续核对采购数据。\n\n## 行动项与待办\n- 明天回访供应商。\n"


class TaskApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="task-api-test-")))
        for item in (patch.object(store, "DATA_DIR", self.root),
                     patch.object(store, "DB_PATH", self.root / "test.db"),
                     patch.object(config, "DATA_DIR", self.root),
                     patch.object(config, "UPLOAD_DIR", self.root / "uploads"),
                     patch.object(config, "REPORT_DIR", self.root / "reports"),
                     patch.object(config, "AUTH_ENABLED", False),
                     patch("app._schedule_auto_naming"), patch("app._schedule_followup"),
                     patch("task_handlers.telemetry.report_usage"),
                     patch("task_handlers.asr_dashscope.transcribe", side_effect=AssertionError("ASR must be mocked"))):
            self.enterContext(item)
        config.UPLOAD_DIR.mkdir(parents=True)
        config.REPORT_DIR.mkdir(parents=True)
        store.init()
        job_queue.init()
        self.report_model = self.enterContext(patch("task_handlers.kimi.gen_report", return_value=REPORT))
        self.client = self.enterContext(TestClient(app.app))
        self.settings = {"subject": "市场经营变化", "interviewee_type": "商户", "industry": "食品饮料",
                         "location": {"city": "重庆", "market": "测试市场"}}

    def run_worker(self):
        return worker.run_once(handlers=task_handlers.HANDLERS,
                               on_cancel=task_handlers.ON_CANCEL, on_success=task_handlers.ON_SUCCESS)

    def session(self):
        response = self.client.post("/api/sessions/", json=self.settings)
        self.assertEqual(response.status_code, 200, response.text)
        sid = response.json()["session_id"]
        store.add_utterance(sid, 0, 0, 1000, "老板：今年客流减少两成，明天回访供应商。")
        return sid

    def upload(self):
        response = self.client.post("/api/imports/analyze", data={"subject": "导入经营访谈"},
            files={"material": ("访谈.txt", "老板：客流减少两成。\n记者：之后有什么计划？".encode("utf-8"), "text/plain")})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def test_outline_http_enqueue_worker_and_poll(self):
        brief = {"questions": [f"这个市场第{i}项经营变化是什么？" for i in range(6)],
                 "background": "测试市场背景参考。", "background_note": "未经实时检索核实。"}
        first = self.client.post("/api/outlines/jobs", json=self.settings)
        second = self.client.post("/api/outlines/jobs", json=self.settings)
        self.assertEqual(first.status_code, 202)
        job_id = first.json()["job_id"]
        self.assertEqual(second.json()["job_id"], job_id)
        self.assertEqual(job_queue.get(job_id)["status"], "queued")
        research = {**brief, "status":"unavailable", "sources":[], "retrieved_at":""}
        with patch("task_handlers.kimi.gen_outline_brief", return_value=brief) as model, patch("place_research.research", return_value=research):
            self.assertTrue(self.run_worker())
        model.assert_called_once()
        response = self.client.get("/api/outlines/jobs/" + job_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        result = response.json()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["progress"], 100)
        self.assertEqual(result["questions"], brief["questions"])
        self.assertEqual(result["background"], brief["background"])
        self.assertEqual(result["settings"]["location"], self.settings["location"])

    def test_background_header_report_is_async_and_deduplicated(self):
        sid = self.session()
        first = self.client.post(f"/api/sessions/{sid}/finish", headers={"X-Background-Task": "1"})
        second = self.client.post(f"/api/sessions/{sid}/finish", headers={"X-Background-Task": "1"})
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["task_id"], second.json()["task_id"])
        self.report_model.assert_not_called()
        self.assertEqual(len(job_queue.find(kind="report", resource_id=sid)), 1)
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/report-job").json()["report_status"], "generating")
        self.assertTrue(self.run_worker())
        self.report_model.assert_called_once()
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/report-job").json()["report_status"], "ready")
        self.assertEqual(store.get_report(sid), REPORT)
        final = self.client.post(f"/api/sessions/{sid}/finish", headers={"X-Background-Task": "1"})
        self.assertEqual(final.status_code, 200)
        self.assertEqual(final.json()["report"], REPORT)
        self.assertEqual(len(job_queue.find(kind="report", resource_id=sid)), 1)

    def test_legacy_sync_report_does_not_overwrite_a_concurrent_result(self):
        sid = self.session()
        saved = "# 另一处理进程已经保存的纪要"

        def other_finishes_first(*_):
            store.save_report(sid, saved)
            store.add_todo(sid, "另一处理进程的行动项")
            return REPORT

        self.report_model.side_effect = other_finishes_first
        response = self.client.post(f"/api/sessions/{sid}/finish")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["report"], saved)
        self.assertEqual(len(store.list_todos(sid)), 1)

    def test_legacy_sync_report_rejects_changed_source_without_partial_todos(self):
        sid = self.session()

        def change_source(*_):
            store.add_utterance(sid, 1, 1000, 2000, "生成期间追加了不同的事实")
            return REPORT

        self.report_model.side_effect = change_source
        with patch.object(app.logger, "exception"):
            response = self.client.post(f"/api/sessions/{sid}/finish")
        self.assertEqual(response.status_code, 502)
        self.assertIsNone(store.get_report(sid))
        self.assertEqual(store.list_todos(sid), [])

    def test_import_retry_reuses_fixed_session_and_archived_transcript(self):
        uploaded = self.upload()
        task = job_queue.get(uploaded["job_id"])
        sid = task["payload"]["session_id"]
        source = Path(task["payload"]["path"])
        self.assertEqual(store.get_session_summary(sid)["status"], "importing")
        self.report_model.side_effect = RuntimeError("模拟模型暂时不可用")
        with patch("task_handlers.importer.extract_utterances", wraps=task_handlers.importer.extract_utterances) as parse:
            self.assertTrue(self.run_worker())
            parse.assert_called_once()
        self.assertEqual(job_queue.get(task["id"])["status"], "retry")
        archived = store.list_export_utterances(sid)
        self.assertTrue(archived)
        self.assertTrue(source.is_file())
        with store.get() as db:
            db.execute("UPDATE tasks SET available_at=0 WHERE id=?", (task["id"],))
        self.report_model.side_effect = None
        with patch("task_handlers.importer.extract_utterances", side_effect=AssertionError("不能重复归档原文")):
            self.assertTrue(self.run_worker())
        result = self.client.get("/api/imports/" + task["id"]).json()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["progress"], 100)
        self.assertEqual(result["session_id"], sid)
        self.assertEqual(store.list_export_utterances(sid), archived)
        self.assertEqual(store.get_session_summary(sid)["status"], "done")
        self.assertEqual(store.get_report(sid), REPORT)
        self.assertFalse(source.parent.exists())
        with store.get() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)

    def test_generic_task_endpoint_does_not_expose_private_fields(self):
        uploaded = self.upload()
        task_id = uploaded["job_id"]
        response = self.client.get("/api/tasks/" + task_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(set(response.json()), {"id", "kind", "status", "stage", "progress", "attempts",
                                               "max_attempts", "created_at", "updated_at"})
        self.assertNotIn(str(self.root), response.text)
        self.assertNotIn("path", uploaded)
        self.assertNotIn("owner_id", uploaded)
        self.assertNotIn("lease_owner", response.json())

    def test_finish_during_import_reuses_pipeline_without_empty_report(self):
        uploaded = self.upload()
        task = job_queue.get(uploaded["job_id"])
        sid = task["payload"]["session_id"]
        for header in ({}, {"X-Background-Task": "1"}):
            response = self.client.post(f"/api/sessions/{sid}/finish", headers=header)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["task_id"], task["id"])
        self.assertEqual(job_queue.find(kind="report", resource_id=sid), [])
        self.assertEqual(store.get_session_summary(sid)["status"], "importing")
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/report-job").json()["report_status"], "generating")
        self.report_model.assert_not_called()
        self.assertTrue(self.run_worker())
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/report-job").json()["report_status"], "ready")

    def test_failed_empty_import_cannot_generate_a_report(self):
        uploaded = self.upload()
        task = job_queue.get(uploaded["job_id"])
        sid = task["payload"]["session_id"]
        claimed = job_queue.claim("failed-parser")
        job_queue.fail(task["id"], claimed["lease_owner"], "文稿无有效内容", retryable=False)
        response = self.client.post(f"/api/sessions/{sid}/finish", headers={"X-Background-Task": "1"})
        self.assertEqual(response.status_code, 409)
        self.report_model.assert_not_called()

    def test_public_progress_percent_and_internal_fraction_are_consistent(self):
        uploaded = self.upload()
        task = job_queue.get(uploaded["job_id"])
        sid = task["payload"]["session_id"]
        claimed = job_queue.claim("progress-test")
        job_queue.progress(task["id"], claimed["lease_owner"], stage="正在解析", progress=0.28)
        self.assertEqual(self.client.get("/api/imports/" + task["id"]).json()["progress"], 28)
        self.assertEqual(self.client.get(f"/api/sessions/{sid}/report-job").json()["progress"], 28)
        self.assertEqual(self.client.get("/api/tasks/" + task["id"]).json()["progress"], 0.28)

    def test_queued_import_cancel_is_cleaned_by_worker_without_model(self):
        uploaded = self.upload()
        task = job_queue.get(uploaded["job_id"])
        sid = task["payload"]["session_id"]
        source = Path(task["payload"]["path"])
        response = self.client.post(f"/api/imports/{task['id']}/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelling")
        self.assertTrue(source.is_file())
        self.assertTrue(self.run_worker())
        self.assertEqual(self.client.get("/api/imports/" + task["id"]).json()["status"], "cancelled")
        self.assertFalse(source.parent.exists())
        self.assertIsNone(store.get_session_summary(sid))
        self.report_model.assert_not_called()
        self.assertFalse(self.run_worker())

    def test_delete_pending_import_prevents_revival_and_cleans_upload(self):
        uploaded = self.upload()
        task = job_queue.get(uploaded["job_id"])
        sid = task["payload"]["session_id"]
        source = Path(task["payload"]["path"])
        self.assertEqual(self.client.delete("/api/sessions/" + sid).status_code, 200)
        self.assertEqual(job_queue.get(task["id"])["status"], "cancelled")
        self.assertTrue(self.run_worker())
        self.assertIsNone(store.get_session_summary(sid))
        self.assertFalse(source.parent.exists())
        self.report_model.assert_not_called()

    def test_delete_pending_report_prevents_model_and_result_revival(self):
        sid = self.session()
        response = self.client.post(f"/api/sessions/{sid}/finish", headers={"X-Background-Task": "1"})
        task_id = response.json()["task_id"]
        self.assertEqual(self.client.delete("/api/sessions/" + sid).status_code, 200)
        self.assertEqual(job_queue.get(task_id)["status"], "cancelled")
        self.assertTrue(self.run_worker())
        self.assertIsNone(store.get_session_summary(sid))
        self.assertIsNone(store.get_report(sid))
        self.assertEqual(self.client.get("/api/tasks/" + task_id).status_code, 404)
        self.report_model.assert_not_called()

    def test_private_memoir_tasks_never_leak_via_shared_task_api(self):
        for kind in ("memoir_memory", "memoir_chapter"):
            task = job_queue.enqueue(kind, {"profile_id": "private-fixture", "user_id": 7},
                                     owner_id=7, resource_id="private-fixture")
            self.assertEqual(self.client.get("/api/tasks/" + task["id"]).status_code, 404)
            with patch.object(config, "AUTH_ENABLED", True):
                self.login_as(7)
                self.assertEqual(self.client.get("/api/tasks/" + task["id"]).status_code, 404)

    def login_as(self, user_id):
        token = f"offline-test-user-{user_id}"
        digest = hashlib.sha256(token.encode()).hexdigest()
        if not store.get_auth_session(digest):
            store.save_auth_session(digest, {"id": user_id, "name": f"员工{user_id}", "type": "正式员工"})
        self.client.cookies.set(employee_auth.COOKIE_NAME, token)

    def test_authenticated_task_outline_import_and_cancel_are_owner_scoped(self):
        with patch.object(config, "AUTH_ENABLED", True):
            self.login_as(1)
            outline = self.client.post("/api/outlines/jobs", json=self.settings)
            self.assertEqual(outline.status_code, 202)
            outline_id = outline.json()["job_id"]
            imported = self.upload()
            import_id = imported["job_id"]
            self.assertEqual(self.client.get("/api/tasks/" + import_id).status_code, 200)
            self.login_as(2)
            for endpoint in ("/api/tasks/" + outline_id, "/api/outlines/jobs/" + outline_id,
                             "/api/tasks/" + import_id, "/api/imports/" + import_id):
                self.assertEqual(self.client.get(endpoint).status_code, 404, endpoint)
            self.assertEqual(self.client.post(f"/api/imports/{import_id}/cancel").status_code, 404)
            self.assertEqual(job_queue.get(import_id)["status"], "queued")
            self.login_as(1)
            self.assertEqual(self.client.get("/api/tasks/" + outline_id).status_code, 200)


if __name__ == "__main__":
    unittest.main()
