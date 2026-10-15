"""人生之书的所有者隔离、原话保全、全量成书和删除fence，全部离线临时数据库。"""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import job_queue
import store as shared_store
import worker
from memoir import handlers, services, store


class Context:
    def check_cancelled(self, db=None):
        return None

    def progress(self, *args):
        pass


class MemoirDomainTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="memoir-domain-")))
        self.enterContext(patch.object(config, "DATA_DIR", self.root))
        self.enterContext(patch.object(shared_store, "DATA_DIR", self.root))
        self.enterContext(patch.object(shared_store, "DB_PATH", self.root / "test.db"))
        shared_store.init()
        job_queue.init()
        store.init()
        self.profile = store.create_profile(7, "测试长者", "只记录本人愿意讲的故事", cloud_consent=True, recording_consent=True)
        self.pid = self.profile["id"]
        self.session = store.create_session(self.pid, 7)
        self.sid = self.session["id"]
        self.enterContext(patch("kimi._chat", side_effect=AssertionError("不得未经mock发送真人材料")))

    def human(self, content="我好像是七十年代初去过河边的工厂。", client="human-1", sid=None):
        return store.append_turn(self.pid, sid or self.sid, 7, "human", content, client)

    def chapter_response(self, block):
        return {"paragraphs": [{"text": item["text"][:1000], "evidence": [{"turn_id": item["turn_id"], "quote": item["text"][:min(1000, len(item["text"]))]}]} for item in block]}

    def generate_chapter(self):
        task = services.enqueue_chapter(self.pid, 7)
        with patch("memoir.handlers._chat", side_effect=lambda system, block: self.chapter_response(block)):
            handlers.chapter(task["payload"], Context())
        return store.get_chapter(self.pid, task["payload"]["chapter_id"], 7)

    def test_requires_personal_owner_and_two_separate_consents(self):
        for user in (0, -1, None, True, "7"):
            with self.assertRaises(ValueError):
                store.create_profile(user, "档案", cloud_consent=True, recording_consent=True)
        for kwargs in ({}, {"cloud_consent": True}, {"recording_consent": True}):
            with self.assertRaises(ValueError):
                store.create_profile(9, "档案", **kwargs)
        self.assertGreater(self.profile["cloud_consented_at"], 0)

    def test_all_profile_resources_are_owner_only_and_never_old_shared_rows(self):
        self.human()
        for func, args in ((store.get_profile, (self.pid, 8)), (store.list_sessions, (self.pid, 8)),
                           (store.get_session, (self.pid, self.sid, 8)), (store.list_turns, (self.pid, self.sid, 8)),
                           (store.get_memories, (self.pid, 8)), (store.list_chapters, (self.pid, 8)),
                           (store.delete_profile, (self.pid, 8)), (services.enqueue_memory, (self.pid, 8))):
            with self.assertRaises(LookupError):
                func(*args)
        self.assertEqual(store.list_profiles(8), [])
        with shared_store.get() as db:
            for table in ("sessions", "utterances", "reports", "projects"):
                self.assertEqual(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_raw_turn_idempotency_and_no_overwrite(self):
        first = self.human()
        again = self.human()
        self.assertEqual(first, again)
        with self.assertRaises(ValueError):
            self.human("另一段原话")
        self.assertEqual(len(store.list_turns(self.pid, self.sid, 7)), 1)
        store.finish_session(self.pid, self.sid, 7)
        self.assertEqual(self.human()["id"], first["id"])
        with self.assertRaises(ValueError):
            self.human("新内容", "new")

    def test_private_audio_paths_and_delete_original_and_derived(self):
        tid = uuid.uuid4().hex
        path = store.recording_path(self.pid, 7, tid, "wav")
        path.write_bytes(b"synthetic-not-real-audio")
        store.append_turn(self.pid, self.sid, 7, "human", "这是测试原话。", "audio-1", str(path), "audio/wav", turn_id=tid)
        self.assertIn("memoir-private", str(path))
        with self.assertRaises(ValueError):
            store.validate_recording_path(self.pid, 7, self.root / "other.wav")
        task = services.enqueue_memory(self.pid, 7)
        result = store.delete_profile(self.pid, 7)
        self.assertFalse(path.exists())
        self.assertEqual(result["audio_cleanup_pending"], 0)
        self.assertEqual(job_queue.get(task["id"])["status"], "cancelled")
        with shared_store.get() as db:
            for table in ("memoir_turns", "memoir_chapters", "memoir_events", "memoir_sessions"):
                self.assertEqual(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_next_question_is_one_question_and_idempotent(self):
        with patch("kimi._chat", return_value=json.dumps({"text": "您愿意先讲哪一段经历？", "mode": "question"})) as model:
            opening = services.next_question(self.pid, self.sid, 7)
            self.assertEqual(services.next_question(self.pid, self.sid, 7)["turn"]["id"], opening["turn"]["id"])
            self.assertEqual(model.call_count, 1)
        self.human()
        with patch("kimi._chat", return_value=json.dumps({"text": "您还记得工厂门口是什么样子吗？", "mode": "question"})):
            result = services.next_question(self.pid, self.sid, 7)
        self.assertFalse(result["degraded"])
        self.assertEqual(result["turn"]["role"], "assistant")

    def test_pause_refusal_and_model_failure_keep_original(self):
        human = self.human("我想先休息，今天不聊了。")
        result = services.next_question(self.pid, self.sid, 7)
        self.assertTrue(result["paused"])
        self.assertEqual(store.get_turn(self.pid, human["id"], 7)["text"], human["text"])
        other = store.create_session(self.pid, 7)
        self.human("这事不方便说。", "refusal", other["id"])
        self.assertIn("先不谈", services.next_question(self.pid, other["id"], 7)["text"])
        third = store.create_session(self.pid, 7)
        self.human("第一次工作是在工厂。", "test", third["id"])
        with patch("kimi._chat", side_effect=RuntimeError("secret response must not leak")):
            result = services.next_question(self.pid, third["id"], 7)
        self.assertTrue(result["degraded"])
        self.assertNotIn("secret", str(result))
        self.assertTrue(services.next_question(self.pid, third["id"], 7)["degraded"])

    def test_candidates_are_only_verification_hints_across_sessions(self):
        turn = self.human()
        payload = services.enqueue_memory(self.pid, 7)["payload"]
        event = {"title": "工厂往事", "date_text": "七十年代初", "description": "自述记忆中曾去工厂。", "status": "candidate", "evidence": [{"turn_id": turn["id"], "quote": turn["text"]}]}
        with patch("memoir.handlers._chat", return_value={"events": [event]}):
            handlers.memory(payload, Context())
        memory = store.get_memories(self.pid, 7)[0]
        self.assertEqual(memory["status"], "uncertain")
        new = store.create_session(self.pid, 7)
        seen = []
        def model(messages, **kwargs):
            seen.append(messages)
            return json.dumps({"text": "您上次好像提到工厂，方便确认是哪段经历吗？", "mode": "question"})
        with patch("kimi._chat", side_effect=model):
            services.next_question(self.pid, new["id"], 7)
        self.assertIn("uncertain", seen[0][1]["content"])
        self.assertIn("只能用", seen[0][0]["content"])
        confirmed = store.confirm_memory(self.pid, memory["id"], 7)
        self.assertEqual(confirmed["status"], "confirmed")

    def test_memory_rejects_fabricated_dates_and_quotes(self):
        turn = self.human()
        task = services.enqueue_memory(self.pid, 7)
        for date, quote in (("1972年", turn["text"]), ("七十年代初", "不存在的经历")):
            with patch("memoir.handlers._chat", return_value={"events": [{"title": "工作", "date_text": date, "description": "工作", "evidence": [{"turn_id": turn["id"], "quote": quote}]}]}):
                with self.assertRaises(ValueError):
                    handlers.memory(task["payload"], Context())
        self.assertEqual(store.get_memories(self.pid, 7), [])

    def test_chapter_uses_all_human_raw_text_not_events_or_assistant(self):
        first = self.human("童年片段。" * 2500)
        store.append_turn(self.pid, self.sid, 7, "assistant", "助手编造的1948年。", "assistant-1")
        second = self.human("尾部特别经历，不能丢失。", "human-2")
        seen = []
        task = services.enqueue_chapter(self.pid, 7)
        def model(system, block):
            seen.extend(block)
            return self.chapter_response(block)
        with patch("memoir.handlers._chat", side_effect=model):
            handlers.chapter(task["payload"], Context())
        self.assertEqual("".join(item["text"] for item in seen), first["text"] + second["text"])
        self.assertNotIn("1948", str(seen))
        chapter = store.get_chapter(self.pid, task["payload"]["chapter_id"], 7)
        self.assertGreaterEqual(len(chapter["paragraphs"]), 3)
        self.assertFalse(chapter["source_stale"])

    def test_missing_or_fake_chapter_evidence_never_saved(self):
        self.human()
        task = services.enqueue_chapter(self.pid, 7)
        for evidence in ([], [{"turn_id": "unknown", "quote": "伪造原话"}]):
            with patch("memoir.handlers._chat", return_value={"paragraphs": [{"text": "一段故事", "evidence": evidence}]}):
                with self.assertRaises(ValueError):
                    handlers.chapter(task["payload"], Context())
        self.assertEqual(store.get_chapter(self.pid, task["payload"]["chapter_id"], 7)["paragraphs"], [])

    def test_confirmed_version_kept_but_stale_not_exported(self):
        self.human()
        chapter = self.generate_chapter()
        confirmed = store.confirm_chapter(self.pid, chapter["id"], 7)
        exported = services.export_markdown(self.pid, 7, confirmed_only=True, chapter_id=chapter["id"])
        self.assertIn("原话依据", exported)
        self.human("后来我又搬去了另一个地方。", "later")
        stale = store.get_chapter(self.pid, chapter["id"], 7)
        self.assertEqual(stale["status"], "confirmed")
        self.assertEqual(stale["paragraphs"], confirmed["paragraphs"])
        self.assertTrue(stale["source_stale"])
        for operation in (lambda: store.confirm_chapter(self.pid, chapter["id"], 7), lambda: services.export_markdown(self.pid, 7, confirmed_only=True, chapter_id=chapter["id"])):
            with self.assertRaises(ValueError):
                operation()

    def test_generation_fenced_on_source_append_or_profile_delete(self):
        self.human()
        task = services.enqueue_chapter(self.pid, 7)
        def change_source(system, block):
            self.human("追加的新原稿。", "late")
            return self.chapter_response(block)
        with patch("memoir.handlers._chat", side_effect=change_source):
            with self.assertRaises(ValueError):
                handlers.chapter(task["payload"], Context())
        self.assertEqual(store.get_chapter(self.pid, task["payload"]["chapter_id"], 7)["paragraphs"], [])
        def delete_source(system, block):
            store.delete_profile(self.pid, 7)
            return self.chapter_response(block)
        with patch("memoir.handlers._chat", side_effect=delete_source):
            with self.assertRaises(LookupError):
                handlers.chapter(services.enqueue_chapter(self.pid, 7)["payload"], Context())
        self.assertEqual(store.list_profiles(7), [])

    def test_task_payload_result_private_and_real_worker_lease(self):
        self.human("测试原话不会进任务payload。")
        task = services.enqueue_chapter(self.pid, 7)
        self.assertEqual(set(task["payload"]), {"profile_id", "user_id", "chapter_id"})
        with patch("memoir.handlers._chat", side_effect=lambda system, block: self.chapter_response(block)):
            self.assertTrue(worker.run_once(handlers={"memoir_chapter": handlers.chapter}))
        ready = job_queue.get(task["id"])
        self.assertEqual(ready["status"], "ready")
        self.assertNotIn("测试原话", str(ready))

    def test_delete_session_clears_cross_session_derived_but_preserves_other_raw(self):
        self.human()
        chapter = self.generate_chapter()
        other = store.create_session(self.pid, 7)
        turn = self.human("另一场保留的原始讲述。", "other", other["id"])
        store.delete_session(self.pid, self.sid, 7)
        self.assertEqual(store.list_chapters(self.pid, 7), [])
        self.assertEqual(store.get_turn(self.pid, turn["id"], 7)["text"], turn["text"])
        self.assertEqual(len(store.list_sessions(self.pid, 7)), 1)

    def test_source_limit_explicitly_rejects_no_truncation_and_views_work(self):
        self.human("一段原稿。")
        with patch.object(store, "MAX_SOURCE_CHARS", 2):
            with self.assertRaisesRegex(ValueError, "不会截断"):
                services.enqueue_chapter(self.pid, 7)
            self.assertEqual(store.list_chapters(self.pid, 7), [])
        self.assertEqual(store.list_turns(self.pid, self.sid, 7)[0]["text"], "一段原稿。")

    def test_init_does_not_commit_callers_transaction(self):
        with shared_store.get() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO memoir_profiles VALUES('rollback',1,'测试','',1,1,1,1)")
            store.init(db)
            db.rollback()
        with self.assertRaises(LookupError):
            store.get_profile("rollback", 1)


if __name__ == "__main__":
    unittest.main()
