import sys
import tempfile
import threading
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app
import project_records
import job_queue
import store
import worker


class ProjectManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="interview-project-tests-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for item in (
            patch.object(app.config, "AUTH_ENABLED", True),
            patch.object(store, "DATA_DIR", root),
            patch.object(store, "DB_PATH", root / "test.db"),
            patch("app.employee_auth.current_user", side_effect=self.user),
        ):
            item.start()
            self.addCleanup(item.stop)
        store.init()
        self.client = self.enterContext(TestClient(app.app, headers={"X-Test-User": "1"}))
        self.other = self.enterContext(TestClient(app.app, headers={"X-Test-User": "2"}))
        self.project = self.client.post("/api/projects", json={"name": "重庆调研"}).json()

    async def user(self, request):
        user_id = int(request.headers.get("X-Test-User", "0"))
        if not user_id:
            raise HTTPException(401, "请先登录")
        return {"id": user_id, "name": f"员工{user_id}", "type": "员工"}

    def session(self, sid, owner=1, finished=True, text="原文中的订单增长两成", report="# 单场纪要"):
        store.create_session(sid, sid, "导入音频", "企业家", [], owner_id=owner)
        if text:
            store.add_utterance(sid, 0, 62000, 65000, text, speaker="受访者")
        if report is not None:
            store.save_report(sid, report)
        if finished:
            store.finish_session(sid)
        store.assign_session_project(sid, self.project["id"])
        return sid

    def generate(self, ids):
        with patch("app.threading.Thread"):
            response = self.client.post(f"/api/projects/{self.project['id']}/records",
                                         json={"session_ids": ids, "title": "总体调研记录"})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["record_id"]

    def ready(self):
        self.session("one")
        self.session("two", report=None)
        record_id = self.generate(["two", "one"])
        with patch("project_records.kimi._chat", return_value="# 总访谈记录\n订单变化 [访谈01·段落01·00:01:02]"):
            self.assertTrue(worker.run_once())
        self.assertEqual(store.get_project_record(record_id)["status"], "ready")
        return record_id

    def test_empty_folder_naming_assignment_and_rename(self):
        overview = self.client.get("/api/projects/overview").json()["projects"]
        self.assertEqual(overview[0]["interviews"], 0)
        same = self.client.post("/api/projects", json={"name": "重庆调研"}).json()
        self.assertEqual(same["id"], self.project["id"])
        self.session("one")
        response = self.client.patch(f"/api/projects/{self.project['id']}", json={"name": "重庆制造业"})
        self.assertEqual(response.status_code, 200)
        summary = store.get_session_summary("one")
        self.assertEqual(summary["project_name"], "重庆制造业")
        self.assertEqual(summary["project_id"], self.project["id"])
        assigned = self.client.patch("/api/sessions/one/project", json={"project_id": ""})
        self.assertEqual(assigned.status_code, 200)
        self.assertIsNone(store.get_session_summary("one")["project_id"])
        self.assertEqual(self.client.get(f"/api/projects/{self.project['id']}").json()["total"], 0)
        self.client.post("/api/projects", json={"name": "已有名称"})
        self.assertEqual(self.client.patch(f"/api/projects/{self.project['id']}", json={"name": "已有名称"}).status_code, 409)

    def test_same_name_other_owner_does_not_expose_private_sessions(self):
        other = self.other.post("/api/projects", json={"name": "重庆调研"}).json()
        self.assertNotEqual(other["id"], self.project["id"])
        self.session("private")
        self.assertEqual(self.other.get(f"/api/projects/{self.project['id']}").status_code, 404)
        self.assertEqual(len(self.other.get("/api/projects").json()["items"]), 1)
        self.assertEqual(self.client.patch("/api/sessions/private/project", json={"project_id": other["id"]}).status_code, 404)
        store.add_share("private", 2, "员工2")
        detail = self.other.get(f"/api/projects/{self.project['id']}").json()
        self.assertEqual(detail["project"]["access"], "shared")
        self.assertEqual(detail["total"], 1)
        self.assertEqual(detail["records"], [])
        self.assertEqual(self.other.post(f"/api/projects/{self.project['id']}/records",
                                         json={"session_ids": ["private", "two"]}).status_code, 403)
        self.assertEqual(self.other.patch(f"/api/projects/{self.project['id']}", json={"name": "改名"}).status_code, 403)

    def test_merge_rejects_bad_selection_missing_transcript_live_and_other_project(self):
        self.session("good")
        self.session("no_text", text="")
        self.session("live", finished=False)
        self.session("outside")
        store.assign_session_project("outside", None)
        route = f"/api/projects/{self.project['id']}/records"
        for ids, expected in ((["good"], 400), (["good", "good"], 400),
                              (["good", "no_text"], 409), (["good", "live"], 409),
                              (["good", "outside"], 409), (["good", "missing"], 409)):
            with self.subTest(ids=ids):
                self.assertEqual(self.client.post(route, json={"session_ids": ids}).status_code, expected)

    def test_async_generation_consumes_full_transcript_and_minutes_and_exports(self):
        self.session("one", text="纪要没有提到的限定条件", report="纪要中订单增长一成")
        self.session("two", text="实际订单增长两成", report="第二场完整纪要")
        # More than the former 10,000-segment query window must still be read.
        with store.get() as db:
            db.executemany("""INSERT INTO utterances(session_id,seq,start_ms,end_ms,text,created_at)
                VALUES('one',?,0,1,?,1)""", [(i, "完整内容") for i in range(1, 10001)])
        record_id = self.generate(["two", "one"])
        calls = []
        def fake_generate(title, name, materials, progress):
            calls.extend(materials)
            progress("正在核对原文")
            return "# 综合记录\n根据原文补充了限定条件 [访谈01·段落01·00:01:02]"
        with patch("app.project_records.generate", side_effect=fake_generate):
            self.assertTrue(worker.run_once())
        by_id = {item["session"]["id"]: item for item in calls}
        self.assertEqual(len(by_id["one"]["transcript"]), 10001)
        self.assertEqual(by_id["one"]["report"], "纪要中订单增长一成")
        self.assertEqual(by_id["two"]["report"], "第二场完整纪要")
        record = self.client.get(f"/api/project-records/{record_id}").json()
        self.assertEqual(record["status"], "ready")
        self.assertEqual(sum(source["utterance_count"] for source in record["sources"]), 10002)
        self.assertNotIn("fingerprint", str(record))
        md = self.client.get(f"/api/project-records/{record_id}/export?format=md")
        self.assertEqual(md.status_code, 200)
        self.assertIn("限定条件", md.text)
        word = self.client.get(f"/api/project-records/{record_id}/export?format=docx")
        self.assertEqual(word.status_code, 200)
        with ZipFile(BytesIO(word.content)) as archive:
            self.assertIn("限定条件", archive.read("word/document.xml").decode())
        self.assertEqual(self.other.get(f"/api/project-records/{record_id}").status_code, 404)

    def test_modified_source_blocks_stale_export_and_deleted_source_is_not_exposed(self):
        record_id = self.ready()
        store.save_report("one", "# 已更正的纪要")
        record = self.client.get(f"/api/project-records/{record_id}").json()
        self.assertEqual(record["status"], "stale")
        self.assertEqual(record["markdown"], "")
        self.assertEqual(self.client.get(f"/api/project-records/{record_id}/export").status_code, 409)
        store.delete_session_records("one")
        self.assertEqual(store.get_project_record(record_id)["markdown"], "")
        self.assertEqual(self.client.get(f"/api/project-records/{record_id}/export").status_code, 409)

    def test_generation_failure_persisted_and_restart_recoverable(self):
        self.session("one")
        self.session("two")
        record_id = self.generate(["one", "two"])
        with store.get() as db:
            db.execute("UPDATE tasks SET max_attempts=1 WHERE id=?", (record_id,))
        with patch("app.project_records.generate", side_effect=RuntimeError("模型暂时不可用")):
            self.assertTrue(worker.run_once())
        result = self.client.get(f"/api/project-records/{record_id}").json()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["error"])
        self.assertEqual(job_queue.get(record_id)["status"], "failed")
        store.update_project_record(record_id, "generating", "分析中")
        store.recover_project_records()
        self.assertEqual(store.get_project_record(record_id)["status"], "failed")

    def test_source_change_during_generation_does_not_publish_record(self):
        self.session("one")
        self.session("two")
        record_id = self.generate(["one", "two"])
        def change(*args):
            store.assign_session_project("two", None)
            return "旧材料生成的结果"
        with store.get() as db:
            db.execute("UPDATE tasks SET max_attempts=1 WHERE id=?", (record_id,))
        with patch("app.project_records.generate", side_effect=change):
            self.assertTrue(worker.run_once())
        self.assertEqual(self.client.get(f"/api/project-records/{record_id}").json()["status"], "failed")
        self.assertEqual(store.get_project_record(record_id)["markdown"], "")

    def test_duplicate_active_request_reuses_the_same_job(self):
        self.session("one")
        self.session("two")
        route = f"/api/projects/{self.project['id']}/records"
        with patch("app.threading.Thread") as thread:
            first = self.client.post(route, json={"session_ids": ["one", "two"]})
            second = self.client.post(route, json={"session_ids": ["two", "one"]})
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json()["record_id"], second.json()["record_id"])
        self.assertEqual(thread.call_count, 0)
        tasks = job_queue.find(kind="project_record", resource_id=first.json()["record_id"])
        self.assertEqual(len(tasks), 1)

    def test_concurrent_source_deletion_clears_published_derived_text(self):
        self.session("one")
        self.session("two")
        record_id = self.generate(["one", "two"])
        reserved, release, deleted = threading.Event(), threading.Event(), threading.Event()
        failures = []
        def verify():
            reserved.set()
            if not release.wait(5):
                raise RuntimeError("测试同步超时")
        def publish():
            try:
                store.publish_project_record(record_id, "应随着来源删除的正文", verify)
            except Exception as exc:
                failures.append(exc)
        def delete():
            try:
                store.delete_session_records("one")
                deleted.set()
            except Exception as exc:
                failures.append(exc)
        publisher = threading.Thread(target=publish)
        deleter = threading.Thread(target=delete)
        publisher.start()
        try:
            self.assertTrue(reserved.wait(5))
            deleter.start()
            self.assertFalse(deleted.wait(0.1))
        finally:
            release.set()
            publisher.join(5)
            if deleter.ident:
                deleter.join(5)
        self.assertEqual(failures, [])
        self.assertTrue(deleted.is_set())
        self.assertEqual(store.get_project_record(record_id)["markdown"], "")
        self.assertEqual(store.get_project_record(record_id)["status"], "failed")

    def test_local_mode_accepts_legacy_folders_and_projects_stay_owner_isolated(self):
        self.session("one")
        self.session("two")
        with patch.object(app.config, "AUTH_ENABLED", False):
            record_id = self.generate(["one", "two"])
            with patch("project_records.kimi._chat", return_value="# 总记录"):
                app._run_project_record(record_id)
            self.assertEqual(self.client.get(f"/api/project-records/{record_id}/export?format=md").status_code, 200)


class EvidencePipelineTests(unittest.TestCase):
    def material(self, sid, text, report, scene="导入音频"):
        return {"session": {"id": sid, "subject": sid, "scene": scene, "created_at": 1},
                "transcript": [{"seq": 0, "start_ms": 62000, "text": text, "speaker": "受访者"}], "report": report}

    def test_small_merge_uses_both_raw_transcripts_and_complete_minutes_with_distinct_sources(self):
        with patch("project_records.kimi._chat", return_value="# 综合记录") as chat:
            output = project_records.generate("总记录", "项目", [
                self.material("first", "原文甲关键细节", "第一场纪要"),
                self.material("second", "原文乙不同观点", "第二场纪要", "导入文字稿")])
        prompt = chat.call_args.args[0][1]["content"]
        for text in ("原文甲关键细节", "原文乙不同观点", "第一场纪要", "第二场纪要",
                     "[访谈01·段落01·00:01:02]", "[访谈02·段落01]"):
            self.assertIn(text, prompt)
        self.assertIn("原文为准", chat.call_args.args[0][0]["content"])
        self.assertIn("来源访谈与核对说明", output)

    def test_long_paragraph_and_minutes_tail_enter_evidence_pipeline_without_truncation(self):
        materials = [self.material("first", "原文开头甲" + "中间内容" * 10000 + "原文末尾甲", "纪要开头甲" + "纪要内容" * 8000 + "纪要末尾甲"),
                     self.material("second", "原文开头乙" + "中间内容" * 10000 + "原文末尾乙", "纪要末尾乙")]
        calls = []
        def chat(messages, **kwargs):
            calls.append(messages)
            return "[访谈01·段落01·00:01:02] 证据甲；[访谈02·段落01·00:01:02] 证据乙" if messages[0]["content"] == project_records.EVIDENCE_SYSTEM else "# 综合记录"
        with patch("project_records.kimi._chat", side_effect=chat):
            project_records.generate("总记录", "项目", materials)
        extracted = "\n".join(messages[1]["content"] for messages in calls[:-1])
        for text in ("原文开头甲", "原文末尾甲", "原文末尾乙", "纪要开头甲", "纪要末尾甲", "纪要末尾乙"):
            self.assertIn(text, extracted)
        self.assertGreater(len(calls), 10)
        self.assertTrue(all(len(messages[1]["content"]) < 25000 for messages in calls))

    def test_minutes_alone_and_oversize_inputs_fail_without_model_call(self):
        with patch("project_records.kimi._chat") as chat:
            with self.assertRaisesRegex(ValueError, "缺少逐字稿"):
                project_records.generate("记录", "项目", [self.material("one", "", "仅有纪要")])
            with patch.object(project_records, "MAX_SOURCE_CHARS", 100):
                with self.assertRaisesRegex(ValueError, "未截断原文"):
                    project_records.generate("记录", "项目", [self.material("one", "原文" * 100, "完整纪要")])
        chat.assert_not_called()

    def test_incomplete_model_output_is_not_treated_as_success(self):
        with patch.object(project_records.kimi, "KIMI_API_KEY", "test-only"), patch("kimi.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.return_value.json.return_value = {
                "choices": [{"finish_reason": "length", "message": {"content": "不完整的记录"}}]}
            with self.assertRaisesRegex(RuntimeError, "结果不完整"):
                project_records.generate("总记录", "项目", [self.material("one", "原文", "纪要")])


if __name__ == "__main__":
    unittest.main()
