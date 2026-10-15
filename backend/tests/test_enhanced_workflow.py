"""三项增强的 HTTP→持久任务→独立 Worker 业务链路，模型与检索均离线模拟。"""
from contextlib import ExitStack
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import coverage_ai
import job_queue
import store
import task_handlers
import worker


# 即使单独运行本文件，首次导入 app 的建表操作也不能接触用户数据库。
with tempfile.TemporaryDirectory(prefix="enhanced-api-bootstrap-") as _bootstrap:
    with ExitStack() as _stack:
        _root = Path(_bootstrap)
        for _item in (patch.object(store, "DATA_DIR", _root),
                      patch.object(store, "DB_PATH", _root / "test.db"),
                      patch.object(config, "DATA_DIR", _root),
                      patch.object(config, "UPLOAD_DIR", _root / "uploads"),
                      patch.object(config, "REPORT_DIR", _root / "reports")):
            _stack.enter_context(_item)
        import app


class EnhancedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="enhanced-api-tests-")))
        for item in (patch.object(store, "DATA_DIR", self.root),
                     patch.object(store, "DB_PATH", self.root / "test.db"),
                     patch.object(config, "DATA_DIR", self.root),
                     patch.object(config, "UPLOAD_DIR", self.root / "uploads"),
                     patch.object(config, "REPORT_DIR", self.root / "reports"),
                     patch.object(config, "AUTH_ENABLED", False),
                     patch.dict("os.environ", {"FANGTAN_ALLOW_SIM": "1"}),
                     patch("app._schedule_auto_naming"), patch("app._schedule_followup"),
                     patch("task_handlers.telemetry.report_usage"),
                     patch("place_research._request_search", side_effect=AssertionError("不得执行真实联网检索"))):
            self.enterContext(item)
        self.unexpected_model = self.enterContext(patch("kimi._chat", side_effect=AssertionError("必须显式模拟模型调用")))
        config.UPLOAD_DIR.mkdir(parents=True)
        config.REPORT_DIR.mkdir(parents=True)
        store.init()
        job_queue.init()
        coverage_ai.init()
        self.client = self.enterContext(TestClient(app.app))
        self.settings = {"subject": "市场经营调研", "interviewee_type": "商户", "industry": "食品饮料",
                         "location": {"city": "重庆", "market": "测试市场"}}

    def session(self, **extra):
        response = self.client.post("/api/sessions/", json={**self.settings, **extra})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["session_id"]

    def run_worker(self, kind):
        return worker.run_once(handlers={kind: task_handlers.HANDLERS[kind]},
                               on_cancel=task_handlers.ON_CANCEL, on_success=task_handlers.ON_SUCCESS)

    def test_online_place_research_then_outline_persists_real_sources(self):
        retrieved = "2026-10-15T09:30:00+00:00"
        excerpt = "重庆测试市场是服务本地批发商户的综合市场，主要经营食品和农副产品。"
        source = {"id": 1, "title": "重庆测试市场公开介绍", "url": "https://www.cq.gov.cn/test-market/",
                  "snippet": excerpt, "retrieved_at": retrieved}
        research = {"status": "ready", "background": f"公开资料摘录：\n“{excerpt}”[1]",
                    "background_note": "已联网检索并附公开来源，不代表实地或独立核验。",
                    "sources": [source], "claims": [{"source_id": 1, "excerpt": excerpt}], "retrieved_at": retrieved}
        questions = [f"您在测试市场经营的第{i}项变化是什么？" for i in range(1, 7)]
        order = []

        def fake_research(location, ctx=None):
            order.append("research")
            self.assertEqual(location, self.settings["location"])
            self.assertIsNotNone(ctx)
            return research

        def fake_outline(*args, **kwargs):
            order.append("outline")
            self.assertEqual(kwargs["research"]["sources"][0]["id"], source["id"])
            self.assertEqual(kwargs["research"]["background"], research["background"])
            return {"questions": questions, "background": "模型自行补充的未核实背景必须被丢弃", "background_note": "模型备注"}

        response = self.client.post("/api/outlines/jobs", json=self.settings)
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["job_id"]
        self.assertEqual(job_queue.get(job_id)["status"], "queued")
        with patch("services.outline_generation.place_research.research", side_effect=fake_research), \
                patch("services.outline_generation.kimi.gen_outline_brief", side_effect=fake_outline):
            self.assertTrue(self.run_worker("outline"))
        self.assertEqual(order, ["research", "outline"])
        result = self.client.get(f"/api/outlines/jobs/{job_id}")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.headers["cache-control"], "no-store")
        body = result.json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["questions"], questions)
        self.assertEqual(body["background"], research["background"])
        self.assertNotIn("自行补充", body["background"])
        self.assertEqual(body["sources"], [source])
        self.assertEqual(body["claims"], research["claims"])
        self.assertEqual(body["research_status"], "ready")
        self.assertEqual(body["retrieved_at"], retrieved)
        persisted = store.get_outline_job(job_id)
        self.assertEqual(json.loads(persisted["sources"]), [source])
        self.assertEqual(json.loads(persisted["claims"]), research["claims"])
        self.assertEqual(persisted["retrieved_at"], retrieved)
        cached = self.client.post("/api/outlines/jobs", json=self.settings)
        self.assertEqual(cached.json()["job_id"], job_id)
        self.assertEqual(cached.json()["sources"], [source])
        self.unexpected_model.assert_not_called()

    def test_transcript_evidence_not_manual_marks_drives_coverage(self):
        questions = ["今年客流变化是什么？", "今年房租成本变化多少？"]
        sid = self.session(outline=questions)
        marked = self.client.post(f"/api/sessions/{sid}/outline_asked", json={"item": questions[0]})
        self.assertEqual(marked.status_code, 200, marked.text)
        initial = self.client.get(f"/api/sessions/{sid}/coverage")
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertEqual(initial.json()["ratio"], 0)
        self.assertEqual(initial.json()["engine"], "ai")
        self.assertTrue(initial.json()["dims"][0]["manual_asked"])
        self.assertEqual(initial.json()["analysis_status"], "empty")

        def fake_assessment(messages, **kwargs):
            payload = json.loads(messages[1]["content"])
            rows = payload["逐字稿"]
            answer = next((row for row in rows if "降至800人" in row["text"]), None)
            evidence = answer or rows[0]
            return json.dumps({"assessments": [
                {"id": "outline:0", "status": "sufficient", "answered": True,
                 "answer_quality": "specific", "reason": "有具体客流对比与原因。", "follow_up": "",
                 "evidence": [{"seq": evidence["seq"], "quote": evidence["text"]}]},
                {"id": "outline:1", "status": "not_discussed", "answered": False,
                 "answer_quality": "none", "evidence": [], "reason": "还没有回答房租问题。", "follow_up": questions[1]}
            ]}, ensure_ascii=False)

        injected = self.client.post(f"/api/sessions/{sid}/simulate", json={"text": "记者：今年客流变化是什么？"})
        self.assertEqual(injected.status_code, 200, injected.text)
        self.assertTrue(job_queue.find(kind="coverage", resource_id=sid, statuses=coverage_ai.ACTIVE))
        with patch("kimi._chat", side_effect=fake_assessment):
            self.assertTrue(self.run_worker("coverage"))
        asked_only = self.client.get(f"/api/sessions/{sid}/coverage").json()
        self.assertEqual(asked_only["analysis_status"], "ready")
        self.assertEqual(asked_only["ratio"], 0)
        self.assertEqual(asked_only["dims"][0]["status"], "mentioned")

        answer = "今年日均客流从去年的1000人降至800人，主要原因是周边新市场分流。"
        self.assertEqual(self.client.post(f"/api/sessions/{sid}/simulate", json={"text": answer}).status_code, 200)
        analyze = self.client.post(f"/api/sessions/{sid}/coverage/analyze")
        self.assertIn(analyze.status_code, (200, 202), analyze.text)
        with patch("kimi._chat", side_effect=fake_assessment):
            for _ in range(5):
                if not self.run_worker("coverage"):
                    break
        final = self.client.get(f"/api/sessions/{sid}/coverage").json()
        self.assertEqual(final["analysis_status"], "ready")
        self.assertEqual(final["ratio"], 0.5)
        self.assertEqual(final["dims"][0]["status"], "sufficient")
        self.assertTrue(final["dims"][0]["covered"])
        self.assertEqual(final["dims"][0]["evidence"][0]["quote"], answer)
        self.assertEqual(final["dims"][0]["evidence"][0]["seq"], 1)
        self.assertFalse(final["dims"][1]["covered"])
        self.assertEqual(self.client.get(f"/api/sessions/{sid}").json()["coverage"]["ratio"], 0.5)
        # 修改已引用原文不能继续展示先前“问透”的旧结果；只读刷新不自行上传旧资料。
        with store.get() as db:
            db.execute("UPDATE utterances SET text='我不知道' WHERE session_id=? AND seq=1", (sid,))
        changed = self.client.get(f"/api/sessions/{sid}/coverage").json()
        self.assertEqual(changed["ratio"], 0)
        self.assertNotEqual(changed["analysis_status"], "ready")
        self.unexpected_model.assert_not_called()

    def test_bundle_route_failure_cleans_prepared_artifact(self):
        project_id = self.client.post("/api/projects", json={"name": "异常清理验收"}).json()["id"]
        with patch("app.project_bundle.build_bundle") as build:
            artifact = build.return_value
            artifact.verify.side_effect = RuntimeError("模拟响应前校验中断")
            with self.assertRaises(RuntimeError):
                self.client.get(f"/api/projects/{project_id}/bundle.zip")
            artifact.cleanup.assert_called_once_with()

    def test_project_zip_http_download_full_transcript_missing_manifest_and_cleanup(self):
        project_response = self.client.post("/api/projects", json={"name": "../重庆😀项目\r\n"})
        self.assertEqual(project_response.status_code, 200, project_response.text)
        project_id = project_response.json()["id"]
        for index in range(2):
            sid = self.session(subject="同名访谈")
            assigned = self.client.patch(f"/api/sessions/{sid}/project", json={"project_id": project_id})
            self.assertEqual(assigned.status_code, 200, assigned.text)
            with store.get() as db:
                db.executemany("""INSERT INTO utterances(session_id,seq,start_ms,end_ms,text,created_at,speaker)
                    VALUES(?,?,?,?,?,1,?)""", [(sid, i, i * 1000 + 123, i * 1000 + 999,
                                               f"第{i}段完整逐字稿", "手工发言人") for i in range(605)])
            store.finish_session(sid)
            if index == 0:
                audio = self.root / "recordings" / sid / "original.m4a"
                audio.parent.mkdir(parents=True)
                audio.write_bytes(b"test-original-audio")
                store.set_audio_path(sid, str(audio))
                store.save_report(sid, "# 会议纪要\n纪要内容。")
                shared_sid = sid
        route = f"/api/projects/{project_id}/bundle.zip"
        response = self.client.get(route)
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertNotIn("\r", response.headers["content-disposition"])
        with ZipFile(BytesIO(response.content)) as archive:
            names = archive.namelist()
            manifest = json.loads(archive.read(next(name for name in names if name.endswith("/manifest.json"))))
            self.assertEqual(len(manifest["sessions"]), 2)
            self.assertEqual(len({row["folder"] for row in manifest["sessions"]}), 2)
            self.assertEqual(len(manifest["warnings"]), 1)
            self.assertEqual(archive.read(next(name for name in names if name.endswith(".m4a"))), b"test-original-audio")
            self.assertTrue(any(name.endswith(".docx") for name in names))
            for name in [name for name in names if name.endswith(".jsonl")]:
                rows = [json.loads(line) for line in archive.read(name).decode().splitlines()]
                self.assertEqual(len(rows), 605)
                self.assertEqual(rows[-1]["text"], "第604段完整逐字稿")
                self.assertEqual(rows[0]["speaker"], "手工发言人")
                self.assertEqual(rows[0]["start_ms"], 123)
        self.assertEqual(list((self.root / "exports").iterdir()), [])
        self.assertEqual(self.client.get("/api/projects/no_such_project/bundle.zip").status_code, 404)
        with patch.object(config, "AUTH_ENABLED", True):
            self.assertEqual(self.client.get(route).status_code, 401)
            with store.get() as db:
                db.execute("UPDATE projects SET owner_id=1 WHERE id=?", (project_id,))
                db.execute("UPDATE sessions SET owner_id=1 WHERE project_id=?", (project_id,))
            store.add_share(shared_sid, 2, "员工2")

            async def current_user(request):
                if request.headers.get("X-Test-User") == "2":
                    return {"id": 2, "name": "员工2", "type": "员工"}
                raise HTTPException(401, "请先登录")

            with patch("app.employee_auth.current_user", side_effect=current_user):
                shared = self.client.get(route, headers={"X-Test-User": "2"})
            self.assertEqual(shared.status_code, 200, shared.text if shared.status_code != 200 else "")
            with ZipFile(BytesIO(shared.content)) as archive:
                manifest = json.loads(archive.read(next(name for name in archive.namelist() if name.endswith("/manifest.json"))))
                self.assertEqual([row["id"] for row in manifest["sessions"]], [shared_sid])
        self.assertEqual(list((self.root / "exports").iterdir()), [])
        self.unexpected_model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
