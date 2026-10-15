"""问透评估的证据校验、全量分块、持久任务及并发修改保护。"""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import coverage_ai
import job_queue
import store
import task_handlers
import worker


class CoverageTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="coverage-tests-")))
        self.enterContext(patch.object(store, "DB_PATH", root / "test.db"))
        self.enterContext(patch.object(store, "DATA_DIR", root))
        store.init()
        coverage_ai.init()
        self.sid = "test-session"
        store.create_session(self.sid, "市场访谈", "现场录音", "商户", ["一个月房租多少？"], owner_id=3)

    def row(self, text="一个月房租一万元。", seq=0):
        store.add_utterance(self.sid, seq, seq * 1000, (seq + 1) * 1000, text)

    def response(self, quote="一个月房租一万元。", seq=0, **changes):
        item = {"id": "outline:0", "status": "sufficient", "answered": True,
                "answer_quality": "specific", "reason": "正面给出了月租金额。", "follow_up": "",
                "evidence": [{"seq": seq, "quote": quote, "start_ms": 999999}]}
        item.update(changes)
        return json.dumps({"assessments": [item]}, ensure_ascii=False)

    def run_job(self, response=None, **kwargs):
        task = coverage_ai.schedule(self.sid, **kwargs)
        with patch("coverage_ai.kimi._chat", return_value=response or self.response()) as model:
            worked = worker.run_once(handlers={"coverage": coverage_ai.handler})
        self.assertTrue(worked)
        self.assertEqual(job_queue.get(task["id"])["status"], "ready")
        return model

    def test_real_worker_persists_evidence_without_transcript_in_task_result(self):
        self.row()
        model = self.run_job()
        result = coverage_ai.view(self.sid)
        self.assertEqual(result["ratio"], 1)
        self.assertEqual(result["analysis_status"], "ready")
        self.assertEqual(result["dims"][0]["evidence"],
                         [{"seq": 0, "quote": "一个月房租一万元。", "start_ms": 0}])
        self.assertEqual(result["analyzed_through_seq"], 0)
        task = job_queue.find(kind="coverage", resource_id=self.sid)[0]
        self.assertNotIn("一万元", json.dumps(task["result"], ensure_ascii=False))
        self.assertEqual(model.call_args.kwargs["timeout"], 90)
        self.assertTrue(model.call_args.kwargs["json_mode"])
        self.assertTrue(model.call_args.kwargs["fast"])

    def test_manual_mark_and_keyword_classification_do_not_count_as_sufficient(self):
        self.row("成本，房租，人工。")
        store.add_suggestion(self.sid, "outline", {"item": "一个月房租多少？", "asked": True})
        with patch("coverage_ai.kimi._chat") as model:
            before = coverage_ai.view(self.sid)
        model.assert_not_called()
        self.assertEqual(before["ratio"], 0)
        self.assertTrue(before["dims"][0]["manual_asked"])
        self.run_job(self.response("成本，房租，人工。"))
        self.assertEqual(coverage_ai.view(self.sid)["ratio"], 0)

    def test_manual_marks_cannot_override_partial_model_evaluation(self):
        self.row("最近房租涨了一些。")
        self.run_job(self.response("最近房租涨了一些。", status="partial", answer_quality="general"))
        result = coverage_ai.view(self.sid, {"outline:0", "一个月房租多少？"})
        self.assertEqual(result["ratio"], 0)
        self.assertEqual(result["partial_ratio"], 0.5)
        self.assertTrue(result["dims"][0]["manual_asked"])
        self.assertTrue(result["dims"][0]["subs"])

    def test_forged_quote_wrong_seq_and_spliced_quote_are_rejected(self):
        for quote, seq in (("一个月房租两万元。", 0), ("一个月房租一万元。", 99), ("房租……一万元", 0)):
            with self.subTest(quote=quote, seq=seq):
                if not store.list_utterances(self.sid):
                    self.row()
                self.run_job(self.response(quote, seq), force=True)
                dim = coverage_ai.view(self.sid)["dims"][0]
                self.assertEqual(dim["status"], "not_discussed")
                self.assertEqual(dim["evidence"], [])

    def test_asked_unanswered_and_evasive_answers_never_sufficient(self):
        for quote, answered, quality in (("您一个月房租多少？", True, "specific"),
                                         ("这个不方便说。", True, "specific"),
                                         ("一个月房租一万元。", False, "specific"),
                                         ("一个月房租一万元。", True, "evasive")):
            with self.subTest(quote=quote, answered=answered, quality=quality):
                with store.get() as db:
                    db.execute("DELETE FROM utterances WHERE session_id=?", (self.sid,))
                self.row(quote)
                self.run_job(self.response(quote, answered=answered, answer_quality=quality), force=True)
                self.assertEqual(coverage_ai.view(self.sid)["dims"][0]["status"], "mentioned")

    def test_general_answer_is_downgraded_to_partial(self):
        self.row("租金涨了，生意不太好。")
        self.run_job(self.response("租金涨了，生意不太好。", answer_quality="general"))
        result = coverage_ai.view(self.sid)
        self.assertEqual(result["ratio"], 0)
        self.assertEqual(result["dims"][0]["status"], "partial")

    def test_questioner_role_without_question_mark_cannot_count_as_answer(self):
        text = "您一个月房租大概一万元吧"
        self.row(text)
        for role in ("记者", "采访者", "主持人", "提问者"):
            with self.subTest(role=role):
                with store.get() as db:
                    db.execute("UPDATE utterances SET speaker=? WHERE session_id=?", (role, self.sid))

                def model(messages, **_):
                    chunk = json.loads(messages[1]["content"])["逐字稿"]
                    self.assertEqual(chunk[0]["speaker"], role)
                    return self.response(text)

                coverage_ai.schedule(self.sid, force=True)
                with patch("coverage_ai.kimi._chat", side_effect=model):
                    worker.run_once(handlers={"coverage": coverage_ai.handler})
                dim = coverage_ai.view(self.sid)["dims"][0]
                self.assertEqual(dim["status"], "mentioned")
                self.assertIn("记者或提问者", dim["reason"])
                self.assertEqual(dim["evidence"][0]["quote"], text)

    def test_manual_speaker_role_change_invalidates_previous_result_and_chunk_cache(self):
        self.row()
        self.run_job()
        before = coverage_ai._snapshot(self.sid)["fingerprint"]
        self.assertEqual(coverage_ai.view(self.sid)["ratio"], 1)
        with store.get() as db:
            db.execute("UPDATE utterances SET speaker='记者' WHERE session_id=?", (self.sid,))
        after = coverage_ai._snapshot(self.sid)["fingerprint"]
        self.assertNotEqual(before, after)
        stale = coverage_ai.view(self.sid)
        self.assertEqual(stale["ratio"], 0)
        self.assertEqual(stale["dims"][0]["evidence"], [])
        self.assertEqual(stale["analyzed_utterances"], 0)
        model = self.run_job()
        self.assertEqual(model.call_count, 1, "角色改变后不能复用旧片段判断")
        self.assertEqual(coverage_ai.view(self.sid)["dims"][0]["status"], "mentioned")

    def test_questioner_specific_statement_plus_evasive_answer_is_not_substantive_answer(self):
        self.row("我听说这里一个月房租一万元。", 0)
        self.row("这个我不知道。", 1)
        with store.get() as db:
            db.execute("UPDATE utterances SET speaker='记者' WHERE session_id=? AND seq=0", (self.sid,))
            db.execute("UPDATE utterances SET speaker='受访者' WHERE session_id=? AND seq=1", (self.sid,))
        for status in ("partial", "sufficient"):
            with self.subTest(status=status):
                response = json.loads(self.response("我听说这里一个月房租一万元。", status=status))
                response["assessments"][0]["evidence"].append({"seq": 1, "quote": "这个我不知道。"})
                self.run_job(json.dumps(response, ensure_ascii=False), force=True)
                result = coverage_ai.view(self.sid)
                self.assertEqual(result["ratio"], 0)
                self.assertEqual(result["dims"][0]["status"], "mentioned")
                self.assertEqual(len(result["dims"][0]["evidence"]), 2)

    def test_same_source_dedupes_active_and_completed_results(self):
        self.row()
        first = coverage_ai.schedule(self.sid)
        second = coverage_ai.schedule(self.sid)
        self.assertEqual(first["id"], second["id"])
        with patch("coverage_ai.kimi._chat", return_value=self.response()):
            worker.run_once(handlers={"coverage": coverage_ai.handler})
        self.assertIsNone(coverage_ai.schedule(self.sid))
        self.assertNotEqual(coverage_ai.schedule(self.sid, force=True)["id"], first["id"])

    def test_repeated_force_requests_reuse_active_task(self):
        self.row()
        first = coverage_ai.schedule(self.sid, force=True)
        second = coverage_ai.schedule(self.sid, force=True)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(job_queue.find(kind="coverage")), 1)

    def test_concurrent_force_requests_share_same_generation_idempotency_key(self):
        self.row()
        snapshot = coverage_ai._snapshot(self.sid)
        barrier = threading.Barrier(2)
        original_find = job_queue.find
        results, errors = [], []

        def find(**kwargs):
            rows = original_find(**kwargs)
            if kwargs.get("limit") == 1 and kwargs.get("statuses") is None:
                barrier.wait(timeout=5)
            return rows

        def enqueue():
            try:
                results.append(coverage_ai._enqueue(self.sid, snapshot, force=True))
            except Exception as exc:
                errors.append(exc)

        with patch("coverage_ai.job_queue.find", side_effect=find):
            threads = [threading.Thread(target=enqueue) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
                self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["id"], results[1]["id"])
        self.assertEqual(len(job_queue.find(kind="coverage")), 1)

    def test_view_does_not_schedule_or_upload_old_records(self):
        self.row()
        with patch("coverage_ai.kimi._chat") as model:
            for _ in range(3):
                self.assertEqual(coverage_ai.view(self.sid)["analysis_status"], "not_analyzed")
        model.assert_not_called()
        self.assertEqual(job_queue.find(kind="coverage"), [])

    def test_empty_transcript_never_calls_model(self):
        self.row("   ")
        self.assertIsNone(coverage_ai.schedule(self.sid))
        self.assertEqual(coverage_ai.view(self.sid)["analysis_status"], "empty")

    def test_complete_transcript_over_2000_rows_and_prompt_budget(self):
        with store.get() as db:
            db.executemany("INSERT INTO utterances(session_id,seq,start_ms,end_ms,text,created_at) VALUES(?,?,?,?,?,0)",
                [(self.sid, index, index * 1000, index * 1000 + 999,
                  "一个月房租一万元。" if index == 2104 else f"这是第{index}句无关内容。") for index in range(2105)])
        seen, lengths = set(), []

        def model(messages, **_):
            lengths.append(len(messages[1]["content"]))
            data = json.loads(messages[1]["content"])
            rows = data["逐字稿"]
            seen.update(row["seq"] for row in rows)
            if any(row["seq"] == 2104 for row in rows):
                return self.response(seq=2104)
            return json.dumps({"assessments": []})

        coverage_ai.schedule(self.sid)
        with patch("coverage_ai.kimi._chat", side_effect=model):
            worker.run_once(handlers={"coverage": coverage_ai.handler})
        self.assertEqual(seen, set(range(2105)))
        self.assertLess(max(lengths), 11000)
        self.assertEqual(coverage_ai.view(self.sid)["ratio"], 1)
        self.assertEqual(coverage_ai.view(self.sid)["analyzed_utterances"], 2105)

    def test_very_long_single_utterance_is_not_truncated(self):
        self.row("无关。" * 4000 + "一个月房租一万元。")
        received = []

        def model(messages, **_):
            rows = json.loads(messages[1]["content"])["逐字稿"]
            received.extend(row["text"] for row in rows)
            return self.response() if "一个月房租一万元。" in "".join(received[-len(rows):]) else json.dumps({"assessments": []})

        coverage_ai.schedule(self.sid)
        with patch("coverage_ai.kimi._chat", side_effect=model):
            worker.run_once(handlers={"coverage": coverage_ai.handler})
        self.assertEqual("".join(received), "无关。" * 4000 + "一个月房租一万元。")
        self.assertEqual(coverage_ai.view(self.sid)["ratio"], 1)

    def test_append_reuses_stable_chunks_and_merges_new_question_evidence(self):
        for index in range(3):
            self.row("无关" * 1700 + "。", index)
        with patch("coverage_ai.kimi._chat", return_value=json.dumps({"assessments": []})) as model:
            coverage_ai.schedule(self.sid)
            worker.run_once(handlers={"coverage": coverage_ai.handler})
        self.assertEqual(model.call_count, 2)
        self.row("一个月房租一万元。", 3)
        with patch("coverage_ai.kimi._chat", return_value=self.response(seq=3)) as model:
            coverage_ai.schedule(self.sid)
            worker.run_once(handlers={"coverage": coverage_ai.handler})
        self.assertEqual(model.call_count, 1)
        self.assertEqual(coverage_ai.view(self.sid)["ratio"], 1)

    def test_append_during_model_call_publishes_verified_prefix_and_schedules_latest(self):
        self.row()
        coverage_ai.schedule(self.sid)

        def model(*_, **__):
            self.row("新追加的内容。", 1)
            return self.response()

        with patch("coverage_ai.kimi._chat", side_effect=model):
            worker.run_once(handlers={"coverage": coverage_ai.handler})
        result = coverage_ai.view(self.sid)
        self.assertEqual(result["ratio"], 1)
        self.assertEqual(result["analysis_status"], "generating")
        self.assertTrue(result["source_stale"])
        self.assertEqual(result["analyzed_utterances"], 1)
        self.assertEqual(result["transcript_utterances"], 2)
        self.assertEqual(len(job_queue.find(kind="coverage", statuses=("queued",))), 1)
        self.assertEqual(len(job_queue.find(kind="coverage")), 2)

    def test_edit_during_model_call_does_not_publish_old_evidence(self):
        self.row()
        coverage_ai.schedule(self.sid)

        def model(*_, **__):
            with store.get() as db:
                db.execute("UPDATE utterances SET text='一个月房租两万元。' WHERE session_id=?", (self.sid,))
            return self.response()

        with patch("coverage_ai.kimi._chat", side_effect=model):
            worker.run_once(handlers={"coverage": coverage_ai.handler})
        self.assertEqual(coverage_ai.view(self.sid)["ratio"], 0)
        with store.get() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM coverage_results").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM coverage_chunks").fetchone()[0], 0)

    def test_changing_outline_invalidates_results(self):
        self.row()
        self.run_job()
        with store.get() as db:
            db.execute("UPDATE sessions SET outline=? WHERE id=?", (json.dumps(["每天流水多少？"]), self.sid))
        result = coverage_ai.view(self.sid)
        self.assertEqual(result["ratio"], 0)
        self.assertEqual(result["dims"][0]["evidence"], [])
        self.assertEqual(result["analyzed_utterances"], 0)
        self.assertIsNotNone(coverage_ai.schedule(self.sid))

    def test_delete_during_model_call_never_resurrects_derived_results(self):
        self.row()
        task = coverage_ai.schedule(self.sid)

        def model(*_, **__):
            with store.get() as db:
                coverage_ai.clear(db, self.sid)
            store.delete_session_records(self.sid)
            return self.response()

        with patch("coverage_ai.kimi._chat", side_effect=model):
            worker.run_once(handlers={"coverage": coverage_ai.handler})
        self.assertEqual(job_queue.get(task["id"])["status"], "cancelled")
        with store.get() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM coverage_results").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM coverage_chunks").fetchone()[0], 0)

    def test_lease_cancel_before_result_transaction_rejects_publish(self):
        self.row()
        task = coverage_ai.schedule(self.sid)

        def model(*_, **__):
            job_queue.cancel(task["id"])
            return self.response()

        with patch("coverage_ai.kimi._chat", side_effect=model):
            worker.run_once(handlers={"coverage": coverage_ai.handler})
        self.assertEqual(coverage_ai.view(self.sid)["ratio"], 0)
        with store.get() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM coverage_results").fetchone()[0], 0)

    def test_no_outline_uses_seven_dimensions_without_keyword_completion(self):
        with store.get() as db:
            db.execute("UPDATE sessions SET outline='[]' WHERE id=?", (self.sid,))
        self.row("成本、政策、孩子、竞争。")
        result = coverage_ai.view(self.sid, {"cost", "policy", "family", "competition"})
        self.assertEqual(len(result["dims"]), 7)
        self.assertEqual(result["ratio"], 0)
        self.assertTrue(next(dim for dim in result["dims"] if dim["id"] == "cost")["manual_asked"])

    def test_merge_validates_evidence_against_both_original_and_allowed_quotes(self):
        self.row("租金一万元，人工两万元。")
        item = coverage_ai._snapshot(self.sid)["items"][0]
        source = [{"seq": 0, "start_ms": 0, "text": "租金一万元，人工两万元。"}]
        raw = json.loads(self.response("人工两万元"))
        validated = coverage_ai._validated(raw, [item], source, {(0, "租金一万元")})
        self.assertEqual(validated[0]["status"], "not_discussed")

    def test_hierarchical_merge_considers_contradictions_not_highest_chunk_status(self):
        self.row("一个月房租一万元。" + "无关内容。" * 800, 0)
        self.row("其实一个月房租两万元。" + "无关内容。" * 800, 1)
        merge_seen = []

        def model(messages, **_):
            data = json.loads(messages[1]["content"])
            if "各片段评估" in data:
                merge_seen.extend(data["各片段评估"])
                return self.response("其实一个月房租两万元。", 1, status="partial",
                    answer_quality="general", reason="前后租金口径矛盾，尚未澄清。", follow_up="一万元和两万元分别是什么口径？")
            texts = "".join(row["text"] for row in data["逐字稿"])
            if "其实一个月房租两万元。" in texts:
                return self.response("其实一个月房租两万元。", 1)
            if "一个月房租一万元。" in texts:
                return self.response()
            return json.dumps({"assessments": []})

        coverage_ai.schedule(self.sid)
        with patch("coverage_ai.kimi._chat", side_effect=model):
            worker.run_once(handlers={"coverage": coverage_ai.handler})
        self.assertEqual(len(merge_seen), 2)
        self.assertEqual(coverage_ai.view(self.sid)["dims"][0]["status"], "partial")
        self.assertIn("矛盾", coverage_ai.view(self.sid)["dims"][0]["reason"])

    def test_many_outline_questions_are_batched_without_silent_truncation(self):
        questions = [f"第{index}题的具体情况和原因是什么？" for index in range(100)]
        with store.get() as db:
            db.execute("UPDATE sessions SET outline=? WHERE id=?", (json.dumps(questions), self.sid))
        self.row()
        seen = set()

        def model(messages, **_):
            data = json.loads(messages[1]["content"])
            seen.update(item["id"] for item in data["问题"])
            self.assertLessEqual(len(data["问题"]), coverage_ai.ITEM_BATCH)
            return json.dumps({"assessments": []})

        coverage_ai.schedule(self.sid)
        with patch("coverage_ai.kimi._chat", side_effect=model):
            worker.run_once(handlers={"coverage": coverage_ai.handler})
        self.assertEqual(seen, {f"outline:{index}" for index in range(100)})
        self.assertEqual(len(coverage_ai.view(self.sid)["dims"]), 100)

    def test_source_changes_then_old_result_is_hidden_without_model_call(self):
        self.row()
        self.run_job()
        with store.get() as db:
            db.execute("DELETE FROM utterances WHERE session_id=?", (self.sid,))
        self.row("这个问题没有答案。")
        with patch("coverage_ai.kimi._chat") as model:
            result = coverage_ai.view(self.sid)
        model.assert_not_called()
        self.assertEqual(result["ratio"], 0)
        self.assertEqual(result["dims"][0]["evidence"], [])

    def test_last_utterance_between_handler_and_complete_is_rescheduled_after_success(self):
        self.row()
        first = coverage_ai.schedule(self.sid)
        original_complete = job_queue.complete
        injected = []

        def complete(*args, **kwargs):
            if not injected:
                injected.append(True)
                self.row("还有一笔新发生的支出。", 1)
                # 模拟真实 _handle_transcript：旧任务仍 running，提交会被合并。
                self.assertEqual(coverage_ai.schedule(self.sid)["id"], first["id"])
            return original_complete(*args, **kwargs)

        with patch("coverage_ai.kimi._chat", return_value=self.response()), patch("job_queue.complete", side_effect=complete):
            worker.run_once(handlers={"coverage": coverage_ai.handler},
                            on_success={"coverage": task_handlers.coverage_after_success})
        pending = job_queue.find(kind="coverage", statuses=("queued",))
        self.assertEqual(len(pending), 1)
        self.assertNotEqual(pending[0]["id"], first["id"])
        with patch("coverage_ai.kimi._chat", return_value=self.response()):
            worker.run_once(handlers={"coverage": coverage_ai.handler},
                            on_success={"coverage": task_handlers.coverage_after_success})
        result = coverage_ai.view(self.sid)
        self.assertEqual(result["analysis_status"], "ready")
        self.assertFalse(result["source_stale"])
        self.assertEqual(result["analyzed_utterances"], 2)
        self.assertEqual(len(job_queue.find(kind="coverage")), 2)
        self.assertEqual(job_queue.find(kind="coverage", statuses=coverage_ai.ACTIVE), [])

    def test_force_completion_without_new_source_does_not_schedule_forever(self):
        self.row()
        coverage_ai.schedule(self.sid, force=True)
        with patch("coverage_ai.kimi._chat", return_value=self.response()):
            worker.run_once(handlers={"coverage": coverage_ai.handler},
                            on_success={"coverage": task_handlers.coverage_after_success})
        self.assertEqual(len(job_queue.find(kind="coverage")), 1)
        self.assertEqual(coverage_ai.view(self.sid)["analysis_status"], "ready")
        self.assertFalse(worker.run_once(handlers={"coverage": coverage_ai.handler},
                                        on_success={"coverage": task_handlers.coverage_after_success}))

    def test_auto_naming_and_manual_title_changes_do_not_invalidate_actual_evidence(self):
        with store.get() as db:
            db.execute("UPDATE sessions SET subject='未命名访谈',subject_source='default' WHERE id=?", (self.sid,))
        self.row()
        self.run_job()
        fingerprint = coverage_ai._snapshot(self.sid)["fingerprint"]
        self.assertTrue(store.try_set_auto_subject(self.sid, "水果商户月租情况"))
        self.assertEqual(coverage_ai._snapshot(self.sid)["fingerprint"], fingerprint)
        self.assertEqual(coverage_ai.view(self.sid)["ratio"], 1)
        self.assertIsNone(coverage_ai.schedule(self.sid))
        store.rename_session(self.sid, "人工更正的标题")
        self.assertEqual(coverage_ai._snapshot(self.sid)["fingerprint"], fingerprint)
        self.assertEqual(coverage_ai.view(self.sid)["analysis_status"], "ready")

    def test_deletion_between_changed_source_check_and_enqueue_does_not_leave_orphan(self):
        self.row()
        snapshot = coverage_ai._snapshot(self.sid)
        original_enqueue = job_queue.enqueue

        def enqueue(*args, **kwargs):
            store.mark_session_deleting(self.sid)
            store.delete_session_records(self.sid)
            return original_enqueue(*args, **kwargs)

        with patch("coverage_ai.job_queue.enqueue", side_effect=enqueue):
            self.assertIsNone(coverage_ai._enqueue(self.sid, snapshot))
        self.assertEqual(job_queue.find(kind="coverage"), [])

    def test_orphan_recheck_never_deletes_a_reused_task(self):
        self.row()
        snapshot = coverage_ai._snapshot(self.sid)
        first = coverage_ai.schedule(self.sid)
        original_enqueue = job_queue.enqueue

        def enqueue(*args, **kwargs):
            store.mark_session_deleting(self.sid)
            store.delete_session_records(self.sid)
            return original_enqueue(*args, **kwargs)

        with patch("coverage_ai.job_queue.enqueue", side_effect=enqueue):
            self.assertIsNone(coverage_ai._enqueue(self.sid, snapshot))
        self.assertIsNotNone(job_queue.get(first["id"]))
        self.assertEqual(job_queue.get(first["id"])["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
