"""真实短命子进程验收：独立 worker、租约恢复和跨进程一次性音频凭据。

所有数据库位于临时目录；缓存处理器不调用模型，子进程额外禁止网络连接。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import audio_access
import job_queue
import store
import task_handlers
import worker


NETWORK_GUARD = """
import socket
def no_network(*args, **kwargs):
    raise AssertionError('process test must not access the network')
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
socket.create_connection = no_network
"""

WORKER_CLI = NETWORK_GUARD + """
import json, runpy, sys
sys.argv = ['worker.py', '--once']
try:
    runpy.run_path('worker.py', run_name='__main__')
except SystemExit as exc:
    exit_code = exc.code
else:
    exit_code = 0
print(json.dumps({'exit_code': exit_code, 'api_imported': 'app' in sys.modules}))
raise SystemExit(exit_code)
"""


class WorkerProcessTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="worker-process-test-")))
        self.enterContext(patch.object(store, "DATA_DIR", self.root))
        self.enterContext(patch.object(store, "DB_PATH", self.root / "fangtan.db"))
        self.env = os.environ.copy()
        self.env.update({"FANGTAN_DATA_DIR": str(self.root), "FANGTAN_AUTH_ENABLED": "false",
                         "KIMI_API_KEY": "", "DASHSCOPE_API_KEY": "", "PYTHONUTF8": "1"})

    def child(self, code, *args):
        result = subprocess.run([sys.executable, "-c", code, *args], cwd=BACKEND,
                                env=self.env, capture_output=True, text=True,
                                encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def cached_outline(self):
        store.init()
        params = {"subject": "市场经营情况", "interviewee_type": "商户", "industry": "食品饮料",
                  "focus": "经营变化", "location": {"city": "重庆", "market": "测试市场"}}
        store.create_outline_job("cached-outline", 1, "cached-fingerprint", params)
        store.finish_outline_job("cached-outline", ["这个市场今年客流有什么变化？"],
                                 background="这是离线测试背景。", background_note="测试资料")
        return job_queue.enqueue("outline", {"job_id": "cached-outline", "params": params},
                                 owner_id=1, resource_id="cached-outline", available_at=0)

    def test_standalone_once_initializes_domain_schema_without_api(self):
        # 空数据库首次运行，不能假定 API 已经初始化领域表。
        result = self.child(WORKER_CLI)
        self.assertEqual(result, {"exit_code": 0, "api_imported": False})
        with store.get() as db:
            tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"sessions", "outline_jobs", "tasks"} <= tables)

    def test_real_once_process_completes_cached_task_and_preserves_result(self):
        task = self.cached_outline()
        result = self.child(WORKER_CLI)
        self.assertFalse(result["api_imported"])
        saved = job_queue.get(task["id"])
        self.assertEqual(saved["status"], "ready")
        self.assertEqual(saved["attempts"], 1)
        self.assertEqual(saved["result"]["questions"], ["这个市场今年客流有什么变化？"])
        # 再次启动独立进程没有重复执行，已提交结果仍然保留。
        self.child(WORKER_CLI)
        self.assertEqual(job_queue.get(task["id"])["attempts"], 1)
        self.assertEqual(job_queue.get(task["id"])["result"], saved["result"])

    def test_restarted_worker_recovers_expired_lease(self):
        task = self.cached_outline()
        with patch("job_queue.time.time", return_value=time.time() - 120):
            old = job_queue.claim("terminated-worker", lease_seconds=5)
        self.assertEqual(old["id"], task["id"])
        self.child(WORKER_CLI)
        saved = job_queue.get(task["id"])
        self.assertEqual(saved["status"], "ready")
        self.assertEqual(saved["attempts"], 2)
        self.assertFalse(job_queue.complete(task["id"], old["lease_owner"], {"stale": True}))

    def test_real_context_runs_model_adapter_and_persists_progress(self):
        task = self.cached_outline()
        with store.get() as db:
            db.execute("UPDATE outline_jobs SET status='generating' WHERE id='cached-outline'")
        brief = {"questions": ["今年采购渠道有什么变化？"], "background": "模型适配器离线测试。",
                 "background_note": "未检索，仅测试"}
        with patch("task_handlers.kimi.gen_outline_brief", return_value=brief) as model:
            self.assertTrue(worker.run_once(handlers={"outline": task_handlers.handle_outline}))
        model.assert_called_once()
        saved = job_queue.get(task["id"])
        self.assertEqual(saved["status"], "ready")
        self.assertEqual(saved["result"]["questions"], brief["questions"])
        self.assertEqual(saved["progress"], 1)

    def test_audio_token_registered_in_one_process_consumed_once_in_another(self):
        store.init()
        source = str(self.root / "test-recording.wav")
        issued = self.child(NETWORK_GUARD + """
import audio_access, json, sys
token = audio_access.register(sys.argv[1])
print(json.dumps({'token': token, 'api_imported': 'app' in sys.modules}))
""", source)
        self.assertFalse(issued["api_imported"])
        token = issued["token"]
        with store.get() as db:
            digest = db.execute("SELECT token_hash FROM audio_access_tokens").fetchone()["token_hash"]
        self.assertNotEqual(digest, token)
        consumed = self.child(NETWORK_GUARD + """
import audio_access, json, sys
print(json.dumps([audio_access.consume(sys.argv[1]), audio_access.consume(sys.argv[1])]))
""", token)
        self.assertEqual(consumed, [source, None])
        self.assertIsNone(audio_access.consume(token))


if __name__ == "__main__":
    unittest.main()
