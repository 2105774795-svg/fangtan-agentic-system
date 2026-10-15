"""独立任务处理器的原子发布、缓存和删除保护，不访问真实模型或录音。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import store
import task_handlers as handlers
from services.materials import load_project_materials


class Context:
    def __init__(self):
        self.events = []
        self.cancelled = False
        self.transactions = 0

    def check_cancelled(self, db=None):
        if self.cancelled:
            raise RuntimeError("已取消或失去租约")
        if db is not None:
            self.transactions += 1

    def progress(self, stage, progress):
        self.check_cancelled()
        if not 0 <= progress <= 1:
            raise ValueError("任务进度必须为0到1")
        self.events.append((stage, progress))


class TaskHandlerTests(unittest.TestCase):
    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory(prefix="task-handler-tests-"))
        self.root = Path(self.folder)
        for item in (patch.object(store, "DATA_DIR", self.root),
                     patch.object(store, "DB_PATH", self.root / "test.db"),
                     patch.object(config, "DATA_DIR", self.root),
                     patch.object(config, "UPLOAD_DIR", self.root / "uploads"),
                     patch.object(config, "REPORT_DIR", self.root / "reports"),
                     patch.object(config, "AUTH_ENABLED", True)):
            self.enterContext(item)
        store.init()
        self.ctx = Context()

    def session(self, sid="one", text="订单增长两成，明天回访供应商。", done=False):
        store.create_session(sid, sid, "导入文字稿", "企业家", [], owner_id=1,
                             location={"city": "重庆", "market": "测试园区"})
        if text:
            store.add_utterance(sid, 0, 0, 1000, text)
        if done:
            store.finish_session(sid)
        return sid

    def test_report_atomically_saves_todos_and_retry_uses_cache(self):
        self.session()
        markdown = "# 纪要\n## 行动项与待办\n- 明天回访供应商\n## 风险\n未提及"
        with patch("task_handlers.kimi.gen_report", return_value=markdown) as generate:
            first = handlers.handle_report({"sid": "one"}, self.ctx)
            again = handlers.handle_report({"sid": "one"}, self.ctx)
        self.assertEqual(generate.call_count, 1)
        self.assertFalse(first["cached"])
        self.assertTrue(again["cached"])
        self.assertEqual(len(store.list_todos("one")), 1)
        self.assertEqual(store.get_session_summary("one")["status"], "done")
        self.assertEqual(self.ctx.transactions, 2)
        self.assertFalse((config.REPORT_DIR / "one.md").exists())

    def test_report_deleted_during_model_call_is_not_recreated(self):
        self.session()

        def generate(*_):
            store.delete_session_records("one")
            return "# 不可回写\n## 行动项与待办\n- 不可回写"

        with patch("task_handlers.kimi.gen_report", side_effect=generate), self.assertRaises(ValueError):
            handlers.handle_report({"sid": "one"}, self.ctx)
        self.assertIsNone(store.get_report("one"))
        self.assertEqual(store.list_todos("one"), [])

    def test_report_changed_source_and_lease_loss_reject_publish(self):
        for action in (lambda: store.add_utterance("one", 1, 1000, 2000, "新内容"),
                       lambda: setattr(self.ctx, "cancelled", True)):
            with self.subTest(action=action):
                if store.get_session_summary("one"):
                    store.delete_session_records("one")
                self.ctx.cancelled = False
                self.session()

                def generate(*_):
                    action()
                    return "# 纪要"

                with patch("task_handlers.kimi.gen_report", side_effect=generate), self.assertRaises((ValueError, RuntimeError)):
                    handlers.handle_report({"sid": "one"}, self.ctx)
                self.assertIsNone(store.get_report("one"))
                self.assertNotEqual(store.get_session_summary("one")["status"], "done")

    def test_outline_persists_background_cache_and_rejects_deleted_job(self):
        params = {"subject": "订单", "interviewee_type": "企业家", "industry": "制造业", "focus": "",
                  "location": {"city": "重庆", "market": "测试园区"}}
        store.create_outline_job("job", 1, "fingerprint", params)
        brief = {"background": "园区参考介绍", "background_note": "未经实时核实", "questions": ["订单变化？"]}
        research = {**brief, "status":"unavailable", "sources":[], "retrieved_at":""}
        with patch("task_handlers.kimi.gen_outline_brief", return_value=brief) as generate, patch("place_research.research", return_value=research):
            result = handlers.handle_outline({"job_id": "job", "params": params}, self.ctx)
            handlers.handle_outline({"job_id": "job", "params": params}, self.ctx)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(result["background"], brief["background"])
        self.assertEqual(store.get_outline_job("job")["status"], "ready")
        store.create_outline_job("deleted", 1, "other", params)

        def delete(*_, **_kwargs):
            with store.get() as db:
                db.execute("DELETE FROM outline_jobs WHERE id='deleted'")
            return brief

        with patch("task_handlers.kimi.gen_outline_brief", side_effect=delete), patch("place_research.research", return_value=research), self.assertRaises(ValueError):
            handlers.handle_outline({"job_id": "deleted", "params": params}, self.ctx)
        self.assertIsNone(store.get_outline_job("deleted"))

    def project_record(self):
        project = store.create_project("项目", 1)
        for sid in ("one", "two"):
            self.session(sid, text=f"{sid}纪要遗漏的限定条件", done=True)
            store.assign_session_project(sid, project["id"])
            store.save_report(sid, f"# {sid}已有纪要")
        _, sources = load_project_materials(project["id"], ["two", "one"], 1)
        store.create_project_record("record", project["id"], 1, "综合记录", sources)
        return project

    def test_project_record_uses_original_and_report_and_checks_late_changes(self):
        self.project_record()

        def generate(title, name, materials, progress):
            self.assertEqual(len(materials), 2)
            self.assertIn("纪要遗漏", materials[0]["transcript"][0]["text"])
            self.assertIn("已有纪要", materials[0]["report"])
            progress("核对全部原文")
            return "# 综合记录"

        with patch("task_handlers.project_records.generate", side_effect=generate):
            handlers.handle_project_record({"record_id": "record"}, self.ctx)
        self.assertEqual(store.get_project_record("record")["status"], "ready")
        # 新任务基于当前来源，模型返回前出现的新原文必须阻止提交。
        store.update_project_record("record", "generating", "重新生成")

        def changed(*_):
            store.add_utterance("one", 1, 1, 2, "模型执行中新增原文")
            return "# 过期结果"

        with patch("task_handlers.project_records.generate", side_effect=changed), self.assertRaises(ValueError):
            handlers.handle_project_record({"record_id": "record"}, self.ctx)
        self.assertNotEqual(store.get_project_record("record")["status"], "ready")

    def test_import_retry_reuses_archived_transcript_and_missing_session_stays_deleted(self):
        self.session(text="")
        folder = config.UPLOAD_DIR / "_imports" / "import-job"
        folder.mkdir(parents=True)
        path = folder / "material.txt"
        path.write_text("主持人：订单怎么样？\n受访者：增长两成。", encoding="utf-8")
        payload = {"job_id": "import-job", "session_id": "one", "owner_id": 1,
                   "path": str(path), "kind": "transcript"}
        with patch("task_handlers.kimi.gen_report", side_effect=RuntimeError("临时限流")):
            with self.assertRaises(RuntimeError):
                handlers.handle_import(payload, self.ctx)
        self.assertEqual(len(store.list_export_utterances("one")), 2)
        self.assertTrue(path.exists())
        with patch("task_handlers.importer.extract_utterances", side_effect=AssertionError("不应重复解析")), \
                patch("task_handlers.kimi.gen_report", return_value="# 纪要"):
            result = handlers.handle_import(payload, self.ctx)
        self.assertEqual(result["session_id"], "one")
        self.assertEqual(len(store.list_export_utterances("one")), 2)
        handlers.cleanup_import_upload(payload)
        self.assertFalse(folder.exists())
        store.delete_session_records("one")
        with self.assertRaises(ValueError):
            handlers.handle_import(payload, self.ctx)
        self.assertIsNone(store.get_session_summary("one"))

    def test_cancel_cleanup_constrained_to_task_directory(self):
        self.session()
        outside = self.root / "do-not-delete.txt"
        outside.write_text("保留", encoding="utf-8")
        payload = {"job_id": "job", "session_id": "one", "path": str(outside)}
        with self.assertRaises(ValueError):
            handlers.cleanup_import_upload(payload)
        self.assertTrue(outside.exists())
        with self.assertRaises(ValueError):
            handlers.cleanup_cancelled_import({**payload, "session_id": "../one"})
        self.assertIsNotNone(store.get_session_summary("one"))

    def test_speaker_revoked_consent_never_publishes_result(self):
        self.session(done=True)
        recording = config.DATA_DIR / "recordings" / "one" / "original.wav"
        recording.parent.mkdir(parents=True)
        recording.write_bytes(b"fake recording")
        store.set_audio_path("one", str(recording))
        store.start_speaker_analysis("one", "consent-job")

        def revoke(*_):
            store.clear_speaker_analysis("one")
            return [{"speaker_id": 1, "start_ms": 0, "end_ms": 100, "text": "不得保存"}]

        with patch("task_handlers.subprocess.run"), patch("task_handlers._ffmpeg_executable", return_value="ffmpeg"), \
                patch("task_handlers.asr_dashscope.transcribe_speakers", side_effect=revoke), \
                self.assertRaises(ValueError):
            handlers.handle_speaker({"sid": "one", "job_id": "consent-job"}, self.ctx)
        self.assertEqual(store.list_speaker_segments("one"), [])

    def test_success_usage_uses_existing_stable_event_reporting(self):
        self.session(done=True)
        with store.get() as db:
            db.execute("UPDATE sessions SET user_name='测试员工' WHERE id='one'")
        store.save_report("one", "# 纪要")
        with patch("task_handlers.telemetry.report_usage") as usage:
            handlers.ON_SUCCESS["report"]({"sid": "one"})
        usage.assert_called_once_with("one", "测试员工")
        store.delete_session_records("one")
        with patch("task_handlers.telemetry.report_usage") as usage:
            handlers.ON_SUCCESS["report"]({"sid": "one"})
        usage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
