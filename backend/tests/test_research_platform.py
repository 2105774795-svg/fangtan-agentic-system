"""Isolated integration coverage for the phase-one platform (no cloud calls)."""
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import research_platform as platform
import store
import worker
import job_queue


class ResearchPlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patches = [patch.object(store, "DATA_DIR", root), patch.object(store, "DB_PATH", root / "test.db")]
        for value in self.patches:
            value.start()
        store.init()
        platform.init()
        app = FastAPI()
        app.include_router(platform.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        for value in reversed(self.patches):
            value.stop()
        self.temp.cleanup()

    def create(self, scenario="market", request_id="isolated-request"):
        response = self.client.post("/api/platform/projects", json={"prompt": "锦州调研", "scenario": scenario, "request_id": request_id, "mode": "demo"})
        self.assertEqual(response.status_code, 201)
        return response.json()

    def command(self, project, action, **kwargs):
        return self.client.post(f"/api/platform/projects/{project['id']}/commands", json={"revision": project["revision"], "action": action, **kwargs})

    def answers(self, scenario):
        return {field["key"]: [field["options"][0]] for field in platform.SCENARIOS[scenario]["fields"]}

    def test_creation_is_idempotent_and_persisted(self):
        first = self.create()
        second = self.create()
        self.assertEqual(first, second)
        self.assertEqual(self.client.get(f"/api/platform/projects/{first['id']}").json(), first)
        self.assertEqual(len(self.client.get("/api/platform/projects").json()), 1)

    def test_delete_hides_project_and_rejects_stale_writes_without_touching_other_projects(self):
        project = self.create(request_id="delete-target")
        other = self.create(request_id="keep-other")
        self.client.put(f"/api/platform/projects/{project['id']}/layout", json={"version": 0, "positions": {"project": {"x": 1, "y": 2}}})
        store.create_session("retained-recording", "原始访谈", "门店", "商户", [], owner_id=-1)
        with store.get() as db:
            db.execute("UPDATE sessions SET project_id=? WHERE id='retained-recording'", (project["id"],))
        url = f"/api/platform/projects/{project['id']}"
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.command(project, "message", content="过期写入").status_code, 404)
        self.assertEqual(self.client.put(url + "/layout", json={"version": 1, "positions": {}}).status_code, 404)
        self.assertEqual(self.client.get(url + "/events").status_code, 404)
        self.assertEqual([p["id"] for p in self.client.get("/api/platform/projects").json()], [other["id"]])
        self.assertIsNotNone(store.get_session_summary("retained-recording"))
        with store.get() as db:
            self.assertIsNone(db.execute("SELECT * FROM research_workspace_layouts WHERE project_id=?", (project["id"],)).fetchone())
        replay = self.client.post("/api/platform/projects", json={"prompt": "锦州调研", "request_id": "delete-target", "mode": "demo"})
        self.assertEqual(replay.status_code, 410)
        self.assertEqual(self.client.delete("/api/platform/projects/missing").status_code, 404)

    def test_delete_cancels_running_ai_job_without_resurrection(self):
        with patch.object(platform.config, "KIMI_API_KEY", "isolated-fixture"):
            response = self.client.post("/api/platform/projects", json={"prompt": "锦州调研", "request_id": "delete-running", "mode": "ai"})
        self.assertEqual(response.status_code, 201)
        project = response.json()
        claimed = job_queue.claim("delete-fixture")
        self.assertEqual(claimed["id"], project["job"]["id"])
        self.assertEqual(self.client.delete(f"/api/platform/projects/{project['id']}").status_code, 200)
        self.assertEqual(job_queue.get(claimed["id"])["status"], "cancelled")
        self.assertEqual(self.client.get(f"/api/platform/projects/{project['id']}").status_code, 404)

    def test_all_scenarios_work_without_login(self):
        for scene in platform.SCENARIOS:
            project = self.create(scene, f"isolated-{scene}")
            for step in ("brief", "advance", "advance"):
                response = self.command(project, step, answers=self.answers(scene))
                self.assertEqual(response.status_code, 200, response.text)
                project = response.json()
            self.assertEqual(project["stage"], 3)
            self.assertEqual(len(project["artifacts"]), 4)
            self.assertTrue(all(item["demo"] for item in project["artifacts"]))
            self.assertEqual(self.command(project, "advance").status_code, 409)

    def test_demo_restart_copies_only_user_inputs(self):
        source = self.create()
        source = self.command(source, "brief", answers=self.answers("market"), context="古塔区").json()
        payload = {"prompt": source["prompt"], "scenario": source["scenario"],
                   "request_id": "restart-demo-as-ai", "mode": "ai", "source_project_id": source["id"]}
        with patch.object(platform.config, "KIMI_API_KEY", "fake"):
            response = self.client.post("/api/platform/projects", json=payload)
            self.assertEqual(response.status_code, 201, response.text)
            restarted = response.json()
            self.assertEqual(self.client.post("/api/platform/projects", json=payload).json(), restarted)
        self.assertNotEqual(restarted["id"], source["id"])
        self.assertEqual(restarted["mode"], "ai")
        self.assertEqual(restarted["stage"], 0)
        self.assertEqual(restarted["answers"], source["answers"])
        self.assertEqual(restarted["context"], "古塔区")
        self.assertEqual(restarted["artifacts"], [])
        self.assertEqual(len(restarted["messages"]), 1)
        self.assertNotIn("research", restarted)
        self.assertEqual(restarted["job"]["kind"], "intake")
        self.assertEqual(self.client.get(f"/api/platform/projects/{source['id']}").json(), source)
        modes = {p["id"]: p["mode"] for p in self.client.get("/api/platform/projects").json()}
        self.assertEqual(modes, {source["id"]: "demo", restarted["id"]: "ai"})

    def test_draft_and_scene_switch(self):
        project = self.create()
        draft = self.command(project, "draft", answers={"goal": ["了解市场现状"]}, context="古塔区").json()
        self.assertEqual(draft["stage"], 0)
        self.assertEqual(draft["context"], "古塔区")
        changed = self.command(draft, "scenario", scenario="memoir").json()
        self.assertEqual(changed["answers"], {})
        self.assertEqual(changed["scenario"], "memoir")

    def test_stale_writer_cannot_overwrite(self):
        project = self.create()
        self.assertEqual(self.command(project, "draft", answers={}).status_code, 200)
        self.assertEqual(self.command(project, "message", content="过期写入").status_code, 409)

    def test_artifact_versions_and_downstream_staleness(self):
        project = self.create()
        project = self.command(project, "brief", answers=self.answers("market")).json()
        original = project["artifacts"][0]
        updated = self.command(project, "artifact", artifact_id=original["id"], content="# 人工修改后的需求").json()
        self.assertEqual(updated["artifacts"][0]["version"], 2)
        self.assertEqual(updated["artifacts"][0]["versions"][0]["content"], original["content"])
        self.assertTrue(updated["artifacts"][1]["stale"])

    def test_invalid_input_does_not_mutate(self):
        project = self.create()
        for answers in ({}, {"industry": ["不存在"]}, {"unexpected": ["注入"]}):
            self.assertEqual(self.command(project, "brief", answers=answers).status_code, 422)
        self.assertEqual(self.client.get(f"/api/platform/projects/{project['id']}").json()["revision"], 1)
        self.assertEqual(self.command(project, "scenario", scenario="unknown").status_code, 422)

    def test_new_platform_does_not_call_employee_service(self):
        with patch("employee_auth.upstream", side_effect=AssertionError("must not authenticate")):
            self.assertEqual(self.client.get("/api/platform/scenarios").status_code, 200)
            project = self.create("memoir")
            self.assertEqual(self.command(project, "brief", answers=self.answers("memoir")).status_code, 200)

    def ai_project(self):
        with patch.object(platform.config, "KIMI_API_KEY", "test-secret-not-for-browser"):
            response = self.client.post("/api/platform/projects", json={"prompt": "锦州市场调研", "request_id": "real-workflow-test", "mode": "ai"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def run_ai(self, fail=False):
        def generate(kind, state, ctx, on_text):
            if fail:
                raise ValueError("do-not-expose-test-secret")
            if kind == "intake":
                return {"intake": {"scenario": "market", "title": "锦州市场调研", "reply": "请选择关注方向。", "location": {"city": "锦州", "market": ""}}}
            content = f"# {kind}\n\n基于原始材料的草稿 [M1-P1]"
            on_text(content[:10])
            on_text(content[10:])
            return {"content": content, "research": {"status": "not_requested", "note": "测试无联网", "sources": []}, "source_ids": [a["id"] for a in state["artifacts"] if a.get("kind") == "material"]}
        with patch("platform_ai.generate", side_effect=generate):
            self.assertTrue(worker.run_once(handlers={"research_platform": platform.execute_ai_job}))

    def test_recording_checklist_and_idempotent_raw_sync(self):
        project = self.ai_project()
        self.run_ai()
        project = platform.project(project["id"])
        # Simulate a saved, reviewed AI outline without a paid call.
        with store.get() as db:
            state = platform._read(db, project["id"])
            state["stage"] = 2
            outline = platform._artifact(state, 2, "调研提纲", "# 开场\n- 您经营多久了？\n1. **最近顾客有哪些变化？**\n说明文字不是问题", origin="ai")
            platform._save(db, state)
        project = platform.project(project["id"])
        self.assertEqual(project["outline_questions"], ["您经营多久了？", "最近顾客有哪些变化？"])
        args = {"selected_questions": [project["outline_questions"][1]], "outline_version": outline["version"], "request_id": "test-capture-session"}
        invalid = {**args, "selected_questions": ["未生成的问题？"]}
        self.assertEqual(self.command(project, "start_interview", **invalid).status_code, 422)
        self.assertEqual(self.command(project, "start_interview", **{**args, "outline_version": 99}).status_code, 409)
        result = self.command(project, "start_interview", **args)
        self.assertEqual(result.status_code, 200, result.text)
        project = result.json()
        sid = project["active_session_id"]
        self.assertEqual(project["stage"], 3)
        self.assertEqual(store.get_session_summary(sid)["project_id"], project["id"])
        self.assertEqual(project["interviews"][0]["outline"], args["selected_questions"])
        project = self.command(project, "start_interview", **args).json()
        self.assertEqual(len(project["interviews"]), 1)
        self.assertEqual(self.command(project, "sync_session", session_id=sid).status_code, 409)
        store.add_utterance(sid, 0, 0, 1000, "顾客比去年少了。这是合成测试发言。")
        store.finish_session(sid)
        project = self.command(project, "sync_session", session_id=sid).json()
        raw = next(a for a in project["artifacts"] if a.get("kind") == "material")
        self.assertIn("合成测试发言", raw["content"])
        again = self.command(project, "sync_session", session_id=sid).json()
        self.assertEqual(sum(a.get("kind") == "material" for a in again["artifacts"]), 1)
        self.assertEqual(next(a for a in again["artifacts"] if a.get("kind") == "material")["version"], 1)
        self.assertEqual(self.command(again, "sync_session", session_id="unrelated").status_code, 404)
        self.assertEqual(platform.outline_questions("# 只有说明\n这里没有任何实际问题。"), [])

    def test_multiple_interviews_keep_separate_evidence_and_resume_selection(self):
        project = self.ai_project()
        self.run_ai()
        with store.get() as db:
            state = platform._read(db, project["id"])
            state["stage"] = 2
            platform._artifact(state, 2, "提纲", "- 最近经营如何？", origin="ai")
            platform._save(db, state)
        project = platform.project(project["id"])
        args = {"selected_questions": ["最近经营如何？"], "outline_version": 1}
        project = self.command(project, "start_interview", **args, title="张店主初访", request_id="first-interview").json()
        first = project["active_session_id"]
        store.add_utterance(first, 0, 0, 1000, "初访：需要遮雨棚。")
        project = self.command(project, "start_interview", **args, title="张店主补访", request_id="second-interview").json()
        second = project["active_session_id"]
        self.assertNotEqual(first, second)
        self.assertEqual([s["subject"] for s in project["interviews"]], ["张店主初访", "张店主补访"])
        self.assertEqual(store.get_session_summary(first)["status"], "live")
        project = self.command(project, "select_interview", session_id=first).json()
        self.assertEqual(platform.project(project["id"])["active_session_id"], first)
        self.assertEqual(self.command(project, "select_interview", session_id="another-project").status_code, 404)
        store.finish_session(first)
        project = self.command(project, "sync_session", session_id=first).json()
        store.add_utterance(second, 0, 0, 1000, "补访：现有冷库足够使用。")
        store.finish_session(second)
        with patch.object(platform.config, "KIMI_API_KEY", "fake"):
            self.assertEqual(self.command(project, "advance").status_code, 409, "Unsynced interviews must not be silently omitted")
        project = self.command(project, "sync_session", session_id=second).json()
        project = self.command(project, "sync_session", session_id=first).json()
        raw = [a for a in project["artifacts"] if a.get("kind") == "material"]
        self.assertEqual(len(raw), 2)
        self.assertEqual([a["source_session"] for a in raw], [first, second])
        self.assertIn("遮雨棚", raw[0]["content"])
        self.assertIn("冷库", raw[1]["content"])
        self.assertTrue(all(item["synced"] for item in project["interviews"]))
        def generate(kind, snapshot, ctx, on_text):
            self.assertEqual([item["id"] for item in snapshot["interview_inventory"]], [first, second])
            return {"content": "两次访谈反映不同需求 [M1-P1] [M2-P1]", "source_ids": [a["id"] for a in raw]}
        with patch.object(platform.config, "KIMI_API_KEY", "fake"):
            project = self.command(project, "advance").json()
        with patch("platform_ai.generate", side_effect=generate):
            self.assertTrue(worker.run_once(handlers={"research_platform": platform.execute_ai_job}))

    def test_report_word_export_is_scoped_and_rejects_stale_report(self):
        import io
        import zipfile
        import xml.etree.ElementTree as ET
        project = self.create()
        with store.get() as db:
            state = platform._read(db, project["id"])
            report = platform._artifact(state, 5, "成果归档", "# 合成调研整合报告\n报告日期：2026年10月17日\n\n## 执行摘要\n**两场**访谈，各自保留出处。\n\n| 对象 | 需求 |\n| --- | --- |\n| 初访 | 遮雨棚 |\n| 补访 | 无新增冷库需求 |\n\n## 一 研究范围与判读方法\n场次不等于人数。")
            platform._save(db, state)
        url = f"/api/platform/projects/{project['id']}/artifacts/{report['id']}/export"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.text[:100] if response.status_code != 200 else "")
        self.assertIn("wordprocessingml", response.headers["content-type"])
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            doc = ET.fromstring(z.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            self.assertEqual(len(doc.findall('.//w:tbl', ns)), 1)
            self.assertEqual(len(doc.findall('.//w:tblHeader', ns)), 1)
            self.assertNotIn('**', ''.join(doc.itertext()))
            self.assertIn('目录', ''.join(doc.itertext()))
            self.assertIn('footer1.xml', z.read('word/_rels/document.xml.rels').decode())
        self.assertEqual(self.client.get(url.replace(report['id'], 'unrelated')).status_code, 404)
        with store.get() as db:
            state = platform._read(db, project["id"])
            state["artifacts"][-1]["stale"] = True
            platform._save(db, state)
        self.assertEqual(self.client.get(url).status_code, 409)

    def test_report_regeneration_preserves_previous_report_and_requires_current_analysis(self):
        project = self.ai_project()
        self.run_ai()
        with store.get() as db:
            state = platform._read(db, project["id"])
            state.update(stage=5, completed=True)
            platform._artifact(state, 3, "原始材料", "真实讲述。", kind="material", origin="user")
            old = platform._artifact(state, 5, "旧汇总报告", "# 旧报告正文", origin="ai")
            platform._save(db, state)
        project = platform.project(project["id"])
        self.assertEqual(self.command(project, "regenerate_report").status_code, 409)
        with store.get() as db:
            state = platform._read(db, project["id"])
            platform._artifact(state, 4, "分析", "已整理 [M1-P1]", origin="ai")
            platform._save(db, state)
        project = platform.project(project["id"])
        with patch.object(platform.config, "KIMI_API_KEY", "fake"):
            project = self.command(project, "regenerate_report").json()
        self.assertEqual(project["job"]["kind"], "report")
        self.assertFalse(project["completed"])
        previous = next(a for a in project["artifacts"] if a["id"] == old["id"])
        self.assertTrue(previous["stale"])
        self.assertEqual(previous["content"], "# 旧报告正文")
        self.run_ai()
        project = platform.project(project["id"])
        self.assertEqual(len([a for a in project["artifacts"] if a["step"] == 5 and not a.get("stale")]), 1)

    def test_known_industry_and_custom_goal_audience_are_preserved(self):
        with patch.object(platform.config, "KIMI_API_KEY", "fake"):
            response = self.client.post("/api/platform/projects", json={"prompt": "我想调研上海新能源汽车的市场情况", "scenario": "market", "request_id": "known-industry-custom", "mode": "ai"})
        project = response.json()
        self.run_ai()
        project = platform.project(project["id"])
        self.assertEqual(project["known_industry"], "新能源汽车")
        self.assertEqual(project["answers"]["industry"], ["新能源汽车"])
        self.assertEqual(project["checklist"][0]["resolved_value"], "新能源汽车")
        choices = {"goal": ["其他"], "audience": ["消费者", "其他"]}
        self.assertEqual(self.command(project, "brief", answers=choices).status_code, 422)
        self.assertEqual(self.command(project, "draft", answers=choices, custom_answers={"goal": "A" * 161, "audience": "车主"}).status_code, 422)
        custom = {"goal": "评估充电服务的商业机会", "audience": "充电站运营方"}
        draft = self.command(project, "draft", answers=choices, custom_answers=custom)
        self.assertEqual(draft.status_code, 200, draft.text)
        project = draft.json()
        self.assertEqual(project["custom_answers"], custom)
        self.assertEqual(project["answers"]["goal"], [custom["goal"]])
        self.assertEqual(project["answers"]["audience"], ["消费者", custom["audience"]])
        with patch.object(platform.config, "KIMI_API_KEY", "fake"):
            result = self.command(project, "brief", answers=project["answers"])
        self.assertEqual(result.status_code, 200, result.text)
        project = result.json()
        self.assertIn(custom["goal"], project["artifacts"][0]["content"])
        self.assertIn(custom["audience"], project["artifacts"][0]["content"])
        self.assertIn("新能源汽车", project["artifacts"][0]["content"])
        self.assertEqual(platform.known_industry({"scenario": "market", "prompt": "不考虑新能源汽车，只想研究咖啡市场"}), "咖啡")

    def test_refined_industry_accepts_initial_auto_value_but_rejects_invalid_choices(self):
        with patch.object(platform.config, "KIMI_API_KEY", "fake"):
            response = self.client.post("/api/platform/projects", json={"prompt": "想调研锦州精品咖啡市场", "scenario": "market", "request_id": "refined-industry-test", "mode": "ai"})
        initial = response.json()
        self.assertEqual(initial["answers"]["industry"], ["咖啡"])
        def intake(*args):
            return {"intake": {"scenario": "market", "industry": "精品咖啡", "title": "锦州精品咖啡调研", "reply": "请选择目标与受访对象。", "location": {}}}
        with patch("platform_ai.generate", side_effect=intake):
            worker.run_once(handlers={"research_platform": platform.execute_ai_job})
        project = platform.project(initial["id"])
        self.assertEqual(project["known_industry"], "精品咖啡")
        answers = self.answers("market")
        for invalid in (["不存在"], ["咖啡", "制造业"], ["咖啡", "咖啡"]):
            answers["industry"] = invalid
            self.assertEqual(self.command(project, "draft", answers=answers).status_code, 422)
        answers["industry"] = initial["answers"]["industry"]
        with patch.object(platform.config, "KIMI_API_KEY", "fake"):
            result = self.command(project, "brief", answers=answers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["answers"]["industry"], ["精品咖啡"])
        self.assertIn("精品咖啡", result.json()["artifacts"][0]["content"])

    def test_every_visible_market_industry_radio_is_valid(self):
        project = self.ai_project()
        self.run_ai()
        project = platform.project(project["id"])
        for option in platform.SCENARIOS["market"]["fields"][0]["options"]:
            result = self.command(project, "draft", answers={"industry": [option]})
            self.assertEqual(result.status_code, 200, (option, result.text))
            project = result.json()
            self.assertEqual(project["answers"]["industry"], [option])

    def test_agent_document_is_added_without_overwriting_or_advancing(self):
        project = self.ai_project()
        self.run_ai()
        with store.get() as db:
            state = platform._read(db, project["id"])
            state["stage"] = 1
            original = platform._artifact(state, 1, "背景与计划", "# 原始准备计划", origin="ai")
            platform._save(db, state)
        project = platform.project(project["id"])
        with patch.object(platform.config, "KIMI_API_KEY", "fake"):
            response = self.command(project, "message", content="在背景与计划新加一份行程安排", selected_step=1)
        self.assertEqual(response.status_code, 200, response.text)
        project = response.json()
        result = {"content": "# 行程安排\n\n所有安排待确认。", "document": {"step": 1, "title": "访谈行程安排"}, "source_ids": [original["id"]]}
        with patch("platform_ai.generate", return_value=result):
            self.assertTrue(worker.run_once(handlers={"research_platform": platform.execute_ai_job}))
        project = platform.project(project["id"])
        self.assertEqual(project["stage"], 1)
        self.assertEqual(project["artifacts"][0], original)
        added = project["artifacts"][-1]
        self.assertEqual(added["kind"], "document")
        self.assertEqual(added["step"], 1)
        self.assertEqual(added["version"], 1)
        self.assertNotEqual(added["id"], original["id"])
        self.assertEqual(project["messages"][-1]["artifact_id"], added["id"])
        self.assertEqual(project["messages"][-1]["document_created"]["step"], 1)
        # An extra document is not a substitute for the workflow's main output.
        with store.get() as db:
            state = platform._read(db, project["id"])
            state["artifacts"] = [a for a in state["artifacts"] if a["id"] != original["id"]]
            platform._save(db, state)
        project = platform.project(project["id"])
        self.assertEqual(self.command(project, "advance").status_code, 409)

    def test_ai_closed_loop_and_reference_materials(self):
        project = self.ai_project()
        self.assertEqual(project["job"]["status"], "queued")
        self.run_ai()
        project = self.client.get(f"/api/platform/projects/{project['id']}").json()
        self.assertEqual(project["location"]["city"], "锦州")
        with patch.object(platform.config, "KIMI_API_KEY", "fake"):
            project = self.command(project, "brief", answers=self.answers("market"), location={"city": "锦州"}).json()
            self.run_ai()
            project = platform.project(project["id"])
            self.assertFalse(project["artifacts"][1]["demo"])
            project = self.command(project, "advance").json()
            self.run_ai()
            project = platform.project(project["id"])
            project = self.command(project, "advance").json()
            self.assertEqual(project["stage"], 3)
            self.assertEqual(self.command(project, "advance").status_code, 422)
            project = self.command(project, "material", title="合成访谈", content="访谈者：发生了什么？\n受访者：有一位顾客询问夜间服务。").json()
            for expected in (4, 5):
                project = self.command(project, "advance").json()
                self.assertEqual(project["stage"], expected)
                self.run_ai()
                project = platform.project(project["id"])
            project = self.command(project, "advance").json()
            self.assertTrue(project["completed"])
            report = next(a for a in project["artifacts"] if a["step"] == 5)
            self.assertEqual(report["origin"], "ai")
            self.assertEqual(len(report["source_ids"]), 1)
            material = next(a for a in project["artifacts"] if a.get("kind") == "material")
            changed = self.command(project, "artifact", artifact_id=material["id"], content="修订后的原始讲述。\n原始资料已更新。").json()
            self.assertFalse(changed["completed"])
            self.assertTrue(all(a["stale"] for a in changed["artifacts"] if a["step"] >= 4))

    def test_layout_persists_without_mutating_business_revision(self):
        project = self.ai_project()
        response = self.client.put(f"/api/platform/projects/{project['id']}/layout", json={"version": 0, "positions": {"0": {"x": 43, "y": 90}}})
        self.assertEqual(response.status_code, 200)
        self.run_ai()
        result = platform.project(project["id"])
        self.assertEqual(result["layout"]["positions"]["0"], {"x": 43, "y": 90})
        self.assertEqual(result["revision"], project["revision"] + 1)
        self.assertEqual(self.client.put(f"/api/platform/projects/{project['id']}/layout", json={"version": 0, "positions": {}}).status_code, 409)
        self.assertEqual(self.client.put(f"/api/platform/projects/{project['id']}/layout", json={"version": 1, "positions": {"5": {"x": 0, "y": 0}}}).status_code, 422)

    def test_ai_failure_is_explicit_and_retryable(self):
        project = self.ai_project()
        self.run_ai(fail=True)
        project = platform.project(project["id"])
        self.assertEqual(project["job"]["status"], "failed")
        self.assertNotIn("test-secret", str(project))
        self.assertEqual(project["artifacts"], [])
        with patch.object(platform.config, "KIMI_API_KEY", "fake"):
            project = self.command(project, "retry").json()
        self.run_ai()
        self.assertEqual(platform.project(project["id"])["job"]["status"], "ready")

    def test_pending_work_rejects_input_mutation_and_can_cancel(self):
        project = self.ai_project()
        self.assertEqual(self.command(project, "message", content="并发补充").status_code, 409)
        project = self.command(project, "cancel").json()
        self.assertEqual(project["job"]["status"], "cancelled")
        self.assertEqual(project["artifacts"], [])

    def test_missing_credentials_never_falls_back_to_fake_ai(self):
        with patch.object(platform.config, "KIMI_API_KEY", ""):
            response = self.client.post("/api/platform/projects", json={"prompt": "调研", "request_id": "missing-configuration"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.client.get("/api/platform/projects").json(), [])


if __name__ == "__main__":
    unittest.main()
