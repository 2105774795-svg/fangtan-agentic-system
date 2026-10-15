import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import job_queue
import store
import worker


class JobQueueTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(store, "DB_PATH", root / "tasks.db"))

    def enqueue(self, **kwargs):
        return job_queue.enqueue("outline", {"subject": "市场调研"}, **kwargs)

    def test_idempotent_submit_and_owner_scope(self):
        first = self.enqueue(owner_id=1, dedupe_key="request-1")
        second = job_queue.enqueue("outline", {"subject": "不应替换原始请求"},
                                   owner_id=1, dedupe_key="request-1")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["payload"], first["payload"])
        self.assertNotEqual(self.enqueue(owner_id=2, dedupe_key="request-1")["id"], first["id"])
        claimed = job_queue.claim("w1")
        self.assertTrue(job_queue.complete(claimed["id"], claimed["lease_owner"], {"ok": True}))
        self.assertEqual(self.enqueue(owner_id=1, dedupe_key="request-1")["id"], first["id"])

    def test_two_workers_cannot_claim_same_task(self):
        task = self.enqueue()
        barrier = threading.Barrier(3)
        results, errors = [], []

        def take(name):
            try:
                barrier.wait()
                results.append(job_queue.claim(name))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=take, args=(f"w{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        claimed = [item for item in results if item]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["id"], task["id"])
        self.assertEqual(claimed[0]["attempts"], 1)

    def test_expired_lease_recovered_and_old_execution_fenced(self):
        self.enqueue()
        with patch("job_queue.time.time", return_value=1000):
            # 调整时间前提交的任务还没有到可执行时间。
            self.assertIsNone(job_queue.claim("same-worker"))
        with patch("job_queue.time.time", return_value=2000000000):
            first = job_queue.claim("same-worker", lease_seconds=10)
        with patch("job_queue.time.time", return_value=2000000011):
            second = job_queue.claim("same-worker", lease_seconds=10)
            self.assertEqual(second["id"], first["id"])
            self.assertNotEqual(second["lease_owner"], first["lease_owner"])
            self.assertEqual(second["attempts"], 2)
            self.assertFalse(job_queue.heartbeat(first["id"], first["lease_owner"]))
            self.assertFalse(job_queue.progress(first["id"], first["lease_owner"], stage="late"))
            self.assertFalse(job_queue.complete(first["id"], first["lease_owner"], {"late": True}))
            self.assertFalse(job_queue.fail(first["id"], first["lease_owner"], "late"))
            with self.assertRaises(job_queue.LeaseLost):
                job_queue.assert_running(first["id"], first["lease_owner"])
            self.assertTrue(job_queue.complete(second["id"], second["lease_owner"], {"new": True}))
        self.assertEqual(job_queue.get(first["id"])["result"], {"new": True})

    def test_heartbeat_cannot_revive_expired_lease(self):
        self.enqueue()
        with patch("job_queue.time.time", return_value=2000000000):
            task = job_queue.claim("w", lease_seconds=10)
        with patch("job_queue.time.time", return_value=2000000010):
            self.assertFalse(job_queue.heartbeat(task["id"], task["lease_owner"]))
            self.assertFalse(job_queue.complete(task["id"], task["lease_owner"], "late"))

    def test_retry_backoff_and_exhaustion(self):
        self.enqueue(max_attempts=2)
        with patch("job_queue.time.time", return_value=2000000000):
            first = job_queue.claim("w")
            self.assertTrue(job_queue.fail(first["id"], first["lease_owner"], "网络暂不可用"))
            self.assertIsNone(job_queue.claim("w"))
            self.assertEqual(job_queue.get(first["id"])["available_at"], 2000000005)
        with patch("job_queue.time.time", return_value=2000000005):
            second = job_queue.claim("w")
            self.assertTrue(job_queue.fail(second["id"], second["lease_owner"], "仍不可用"))
            self.assertIsNone(job_queue.claim("w"))
        final = job_queue.get(first["id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["attempts"], 2)
        self.assertIsNotNone(final["finished_at"])

    def test_crashed_final_attempt_is_failed(self):
        self.enqueue(max_attempts=1)
        with patch("job_queue.time.time", return_value=2000000000):
            task = job_queue.claim("w", lease_seconds=1)
        with patch("job_queue.time.time", return_value=2000000002):
            self.assertEqual(job_queue.recover_expired(), 1)
            self.assertIsNone(job_queue.claim("w2"))
        self.assertEqual(job_queue.get(task["id"])["status"], "failed")

    def test_cancellation_and_resource_owner_scope(self):
        first = self.enqueue(owner_id=1, resource_id="session1")
        second = self.enqueue(owner_id=2, resource_id="session1")
        task = job_queue.claim("w")
        self.assertFalse(job_queue.cancel(first["id"], owner_id=9))
        self.assertEqual(job_queue.cancel_resource("session1", owner_id=1), 1)
        self.assertEqual(job_queue.get(second["id"])["status"], "queued")
        with self.assertRaises(job_queue.TaskCancelled):
            job_queue.assert_running(task["id"], task["lease_owner"])
        self.assertFalse(job_queue.complete(task["id"], task["lease_owner"], "迟到结果"))
        self.assertTrue(job_queue.cancel(second["id"]))
        self.assertIsNone(job_queue.claim("w"))

    def test_old_worker_does_not_own_cancel_cleanup(self):
        self.enqueue()
        with patch("job_queue.time.time", return_value=2000000000):
            old = job_queue.claim("w", lease_seconds=1)
        with patch("job_queue.time.time", return_value=2000000002):
            new = job_queue.claim("w2")
            job_queue.cancel(new["id"])
            with self.assertRaises(job_queue.LeaseLost):
                job_queue.assert_running(old["id"], old["lease_owner"])
            with self.assertRaises(job_queue.TaskCancelled):
                job_queue.assert_running(new["id"], new["lease_owner"])

    def test_ready_result_survives_restart_and_schema_does_not_commit_outer_transaction(self):
        self.enqueue()
        task = job_queue.claim("w")
        self.assertTrue(job_queue.progress(task["id"], task["lease_owner"], stage="生成纪要", progress=.5))
        with store.get() as db:
            db.execute("BEGIN IMMEDIATE")
            job_queue.init(db)
            current = job_queue.assert_running(task["id"], task["lease_owner"], db)
            self.assertEqual(current["stage"], "生成纪要")
            db.execute("UPDATE tasks SET stage='应回滚' WHERE id=?", (task["id"],))
            db.rollback()
        self.assertEqual(job_queue.get(task["id"])["stage"], "生成纪要")
        job_queue.complete(task["id"], task["lease_owner"], {"text": "总访谈记录"})
        job_queue.init()
        saved = job_queue.get(task["id"])
        self.assertEqual(saved["status"], "ready")
        self.assertEqual(saved["result"], {"text": "总访谈记录"})
        self.assertEqual(saved["progress"], 1)
        self.assertEqual(job_queue.find(owner_id=0, statuses=["ready"])[0]["id"], task["id"])

    def test_binary_and_invalid_input_rejected(self):
        with self.assertRaises(TypeError):
            job_queue.enqueue("audio", {"blob": b"recording"})
        with self.assertRaises(ValueError):
            self.enqueue(max_attempts=0)
        with self.assertRaises(ValueError):
            job_queue.enqueue("outline", {"invalid": float("nan")})

    def test_cancel_cleanup_recovery_fences_original_worker(self):
        self.enqueue()
        with patch("job_queue.time.time", return_value=2000000000):
            task = job_queue.claim("w", lease_seconds=1)
            job_queue.cancel(task["id"])
            self.assertIsNone(job_queue.claim_cancelled("cleanup"))
        with patch("job_queue.time.time", return_value=2000000002):
            cleanup = job_queue.claim_cancelled("cleanup")
            self.assertEqual(cleanup["id"], task["id"])
            self.assertFalse(job_queue.finish_cancel(task["id"], task["lease_owner"]))
            self.assertTrue(job_queue.finish_cancel(cleanup["id"], cleanup["lease_owner"]))
            self.assertIsNone(job_queue.claim_cancelled("cleanup"))
        self.assertIsNone(job_queue.get(task["id"])["lease_owner"])


class WorkerExecutionTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(store, "DB_PATH", root / "tasks.db"))
        self.enterContext(patch.object(store, "DATA_DIR", root))

    def test_handler_progress_result_and_success_hook(self):
        task = job_queue.enqueue("outline", {"subject": "市场"})
        seen = []

        def handler(payload, context):
            self.assertEqual(context.task_id, task["id"])
            self.assertEqual(context.token, context.lease_owner)
            context.progress("分析中", .5)
            return {"questions": [payload["subject"]]}

        def cleanup(payload):
            self.assertEqual(job_queue.get(task["id"])["status"], "ready")
            seen.append(payload)

        self.assertTrue(worker.run_once(handlers={"outline": handler}, on_success={"outline": cleanup}))
        self.assertEqual(seen, [{"subject": "市场"}])
        self.assertEqual(job_queue.get(task["id"])["result"], {"questions": ["市场"]})
        self.assertFalse(worker.run_once(handlers={"outline": handler}))

    def test_error_is_retried_and_success_hook_not_called(self):
        task = job_queue.enqueue("outline", {})
        cleanup = Mock()
        worker.run_once(handlers={"outline": Mock(side_effect=RuntimeError("网络暂不可用"))},
                        on_success={"outline": cleanup})
        self.assertEqual(job_queue.get(task["id"])["status"], "retry")
        cleanup.assert_not_called()

    def test_user_cancel_invokes_cleanup_only_for_current_owner(self):
        task = job_queue.enqueue("import", {"upload": "引用路径"})
        cleanup = Mock()

        def handler(_payload, context):
            job_queue.cancel(task["id"])
            context.check_cancelled()

        worker.run_once(handlers={"import": handler}, on_cancel={"import": cleanup})
        cleanup.assert_called_once_with({"upload": "引用路径"})
        self.assertEqual(job_queue.get(task["id"])["status"], "cancelled")
        self.assertIsNone(job_queue.get(task["id"])["lease_owner"])

    def test_orphaned_cancel_cleanup_does_not_run_handler(self):
        task = job_queue.enqueue("import", {"upload": "引用路径"})
        with patch("job_queue.time.time", return_value=2000000000):
            job_queue.claim("crashed", lease_seconds=1)
            job_queue.cancel(task["id"])
        handler, cleanup = Mock(), Mock()
        with patch("job_queue.time.time", return_value=2000000002):
            self.assertTrue(worker.run_once(handlers={"import": handler}, on_cancel={"import": cleanup}))
        handler.assert_not_called()
        cleanup.assert_called_once_with({"upload": "引用路径"})
        self.assertIsNone(job_queue.get(task["id"])["lease_owner"])

    def test_queued_cancel_survives_restart_and_is_cleaned_once(self):
        task = job_queue.enqueue("import", {"upload": "引用路径"})
        job_queue.cancel(task["id"])
        job_queue.init()
        handler, cleanup = Mock(), Mock()
        self.assertTrue(worker.run_once(handlers={"import": handler}, on_cancel={"import": cleanup}))
        handler.assert_not_called()
        cleanup.assert_called_once_with({"upload": "引用路径"})
        self.assertFalse(worker.run_once(handlers={"import": handler}, on_cancel={"import": cleanup}))

    def test_lost_lease_does_not_clean_material_or_override_new_worker(self):
        task = job_queue.enqueue("import", {})
        cleanup = Mock()

        def handler(_payload, context):
            with store.get() as db:
                db.execute("UPDATE tasks SET lease_owner='new-worker' WHERE id=?", (task["id"],))
            context.check_cancelled()

        worker.run_once(handlers={"import": handler}, on_cancel={"import": cleanup})
        cleanup.assert_not_called()
        self.assertEqual(job_queue.get(task["id"])["lease_owner"], "new-worker")

    def test_safe_stop_requeues_current_task(self):
        task = job_queue.enqueue("outline", {}, max_attempts=1)
        stop = threading.Event()

        def handler(_payload, context):
            stop.set()
            context.check_cancelled()

        worker.run_once(handlers={"outline": handler}, stop_event=stop)
        self.assertEqual(job_queue.get(task["id"])["status"], "retry")
        self.assertEqual(job_queue.get(task["id"])["attempts"], 0)
        self.assertFalse(worker.run_once(handlers={"outline": handler}, stop_event=stop))
        self.assertIsNotNone(job_queue.claim("new-worker"))

    def test_periodic_heartbeat_renews_lease(self):
        job_queue.enqueue("outline", {})
        beat = threading.Event()
        original = job_queue.heartbeat

        def heartbeat(*args, **kwargs):
            result = original(*args, **kwargs)
            if result:
                beat.set()
            return result

        def handler(_payload, context):
            self.assertTrue(beat.wait(2))
            context.check_cancelled()
            return {"alive": True}

        with patch("job_queue.heartbeat", side_effect=heartbeat):
            self.assertTrue(worker.run_once(handlers={"outline": handler}, lease_seconds=.6))
        self.assertEqual(job_queue.find(statuses=["ready"])[0]["result"], {"alive": True})

    def test_once_without_tasks_returns_zero(self):
        with patch("worker._handlers", return_value=({"outline": Mock()}, {}, {})), \
                patch("worker.signal.signal"), patch("worker.logging.basicConfig"):
            self.assertEqual(worker.main(["--once"]), 0)


if __name__ == "__main__":
    unittest.main()
