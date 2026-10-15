import io
import os
import sys
import tempfile
import time
import unittest
import uuid
import wave
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("FANGTAN_DATA_DIR", tempfile.mkdtemp(prefix="memoir-api-import-"))
import app
import config
import employee_auth
import job_queue
import store
from memoir import api, auth, services, store as ms


def wav_bytes(seconds=0.2, sample=0):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(sample.to_bytes(2, "little", signed=True) * int(16000 * seconds))
    return output.getvalue()


class MemoirApiTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory(prefix="memoir-api-")
        path = Path(self.root.name)
        self.patches = [patch.object(store, "DB_PATH", path / "test.db"),
                        patch.object(store, "DATA_DIR", path), patch.object(config, "DATA_DIR", path),
                        patch.object(config, "PUBLIC_BASE", "https://example.cn/fangtan"),
                        patch.object(config, "AUTH_ENABLED", False),
                        patch("memoir.services.kimi._chat", side_effect=RuntimeError("sensitive-cloud-detail"))]
        for item in self.patches:
            item.start()
        store.init()
        job_queue.init()
        ms.init()
        auth.init()
        auth._attempts.clear()
        self.a, self.b, self.anon = [TestClient(app.app, base_url="https://example.cn") for _ in range(3)]
        for client, uid in ((self.a, 41), (self.b, 42)):
            response = JSONResponse({})
            auth.issue_session(response, {"id": uid, "name": f"测试员工{uid}", "type": "测试"})
            cookies = SimpleCookie()
            cookies.load(response.headers["set-cookie"])
            client.cookies.set(auth.COOKIE_NAME, cookies[auth.COOKIE_NAME].value)
        self.pid = self.create_profile()
        response = self.a.post(f"/api/memoir/profiles/{self.pid}/sessions", json={"title": "童年讲述"})
        self.assertEqual(response.status_code, 201, response.text)
        self.sid = response.json()["session"]["id"]

    def tearDown(self):
        for client in (self.a, self.b, self.anon):
            client.close()
        for item in reversed(self.patches):
            item.stop()
        self.root.cleanup()

    def create_profile(self):
        response = self.a.post("/api/memoir/profiles", json={"name": "模拟讲述者", "description": "仅测试",
                         "cloud_consent": True, "recording_consent": True})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["profile"]["id"]

    def turn(self, text="我小时候住在重庆，1965年第一次去上学。", key="fixture-turn-0001"):
        return self.a.post(f"/api/memoir/profiles/{self.pid}/sessions/{self.sid}/turn",
                           data={"text": text, "client_turn_id": key})

    def test_private_login_never_claims_legacy_sessions(self):
        data = {"success": True, "data": {"id": 99, "name": "合法员工"}}
        with patch.object(employee_auth, "upstream", AsyncMock(return_value=data)) as upstream, \
                patch.object(store, "claim_legacy_sessions") as claim:
            response = self.anon.post("/api/memoir/auth/login", json={"name": "合法员工", "password": " password "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(upstream.await_args.args[1]["password"], " password ")
        claim.assert_not_called()
        self.assertEqual(self.anon.get("/api/memoir/auth/me").json()["user"]["id"], 99)
        with store.get() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0], 0)

    def test_anon_and_old_cookie_always_denied_auth_false(self):
        self.anon.cookies.set(employee_auth.COOKIE_NAME, "old-shared-session")
        paths = ["/auth/me", "/profiles", f"/profiles/{self.pid}", f"/profiles/{self.pid}/sessions/{self.sid}",
                 f"/profiles/{self.pid}/audio/nope", "/tasks/nope"]
        for path in paths:
            self.assertEqual(self.anon.get("/api/memoir" + path).status_code, 401, path)
        self.assertEqual(self.anon.post("/api/memoir/profiles", json={"name": "x"}).status_code, 401)

    def test_profile_requires_two_separate_boolean_consents(self):
        for body in ({"name": "x"}, {"name": "x", "cloud_consent": True},
                     {"name": "x", "cloud_consent": True, "recording_consent": False}):
            self.assertEqual(self.a.post("/api/memoir/profiles", json=body).status_code, 409)
        body = {"name": "x", "cloud_consent": "true", "recording_consent": True}
        self.assertEqual(self.a.post("/api/memoir/profiles", json=body).status_code, 422)

    def test_other_user_cannot_read_write_export_or_delete(self):
        self.assertEqual(self.b.get("/api/memoir/profiles").json(), {"profiles": []})
        for method, path in (("GET", f"/profiles/{self.pid}"), ("DELETE", f"/profiles/{self.pid}"),
                             ("GET", f"/profiles/{self.pid}/sessions/{self.sid}"),
                             ("DELETE", f"/profiles/{self.pid}/sessions/{self.sid}"),
                             ("POST", f"/profiles/{self.pid}/sessions/{self.sid}/finish"),
                             ("POST", f"/profiles/{self.pid}/events/fake/confirm"),
                             ("POST", f"/profiles/{self.pid}/chapters/fake/confirm"),
                             ("GET", f"/profiles/{self.pid}/chapters/fake/export"),
                             ("GET", f"/profiles/{self.pid}/audio/fake")):
            response = self.b.request(method, "/api/memoir" + path)
            self.assertEqual(response.status_code, 404, (path, response.text))

    def test_cloud_question_failure_keeps_original_and_no_legacy_rows(self):
        with patch.object(services, "next_question", side_effect=RuntimeError("secret-model-response")):
            response = self.turn()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["question"]["degraded"])
        self.assertNotIn("secret-model-response", response.text)
        self.assertEqual(ms.list_turns(self.pid, self.sid, 41)[-1]["text"], "我小时候住在重庆，1965年第一次去上学。")
        with store.get() as db:
            for table in ("sessions", "utterances", "reports", "projects", "session_shares"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_text_retry_idempotent_but_changed_source_conflicts(self):
        first = self.turn()
        second = self.turn()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["human_turn"]["id"], second.json()["human_turn"]["id"])
        self.assertEqual(self.turn("替换原话").status_code, 409)
        self.assertEqual(len([t for t in ms.list_turns(self.pid, self.sid, 41) if t["role"] == "human"]), 1)

    def test_retried_pause_retains_paused_semantics(self):
        first = self.turn("今天先暂停，我想休息。", "pause-fixture-0001")
        second = self.turn("今天先暂停，我想休息。", "pause-fixture-0001")
        self.assertTrue(first.json()["question"]["paused"])
        self.assertTrue(second.json()["question"]["paused"])
        self.assertEqual(first.json()["question"]["turn"]["id"], second.json()["question"]["turn"]["id"])

    def test_retried_cloud_fallback_retains_degraded_semantics(self):
        first = self.turn()
        second = self.turn()
        self.assertTrue(first.json()["question"]["degraded"])
        self.assertTrue(second.json()["question"]["degraded"])

    def test_audio_kept_verbatim_private_replay_and_changed_retry_refused(self):
        body = wav_bytes()
        url = f"/api/memoir/profiles/{self.pid}/sessions/{self.sid}/turn"
        with patch.object(api.asr_dashscope, "DASHSCOPE_API_KEY", "fixture-only"), \
                patch.object(api.asr_dashscope, "_transcribe_inline", return_value="小时候我在村里上学。") as asr, \
                patch.object(api.asr_dashscope, "register_audio") as public_callback:
            first = self.a.post(url, data={"client_turn_id": "audio-fixture-0001"}, files={"audio": ("source.wav", body, "audio/wav")})
            self.assertEqual(first.status_code, 200, first.text)
            repeat = self.a.post(url, data={"client_turn_id": "audio-fixture-0001"}, files={"audio": ("source.wav", body, "audio/wav")})
            different = self.a.post(url, data={"client_turn_id": "audio-fixture-0001"}, files={"audio": ("source.wav", wav_bytes(sample=100), "audio/wav")})
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(different.status_code, 409)
        self.assertEqual(asr.call_count, 1)
        public_callback.assert_not_called()
        turn = first.json()["human_turn"]
        self.assertNotIn("audio_path", turn)
        audio_url = "/" + turn["audio_url"]
        playback = self.a.get(audio_url)
        self.assertEqual(playback.status_code, 200)
        self.assertEqual(playback.content, body)
        self.assertIn("no-store", playback.headers["cache-control"])
        self.assertEqual(self.b.get(audio_url).status_code, 404)
        self.assertEqual(self.anon.get(audio_url).status_code, 401)
        saved = Path(ms.get_turn(self.pid, turn["id"], 41)["audio_path"])
        self.assertIn("memoir-private", saved.parts)
        self.assertEqual(list((Path(self.root.name) / "memoir-private" / "temporary").iterdir()), [])
        deleted = self.a.delete(f"/api/memoir/profiles/{self.pid}/sessions/{self.sid}")
        self.assertEqual(deleted.json()["audio_cleanup_pending"], 0)
        self.assertFalse(saved.exists())

    def test_audio_limits_and_ambiguous_source_refused(self):
        url = f"/api/memoir/profiles/{self.pid}/sessions/{self.sid}/turn"
        response = self.a.post(url, data={"client_turn_id": "too-long-0001"}, files={"audio": ("long.wav", wav_bytes(seconds=121), "audio/wav")})
        self.assertEqual(response.status_code, 413, response.text)
        response = self.a.post(url, data={"client_turn_id": "two-sources-0001", "text": "手写"}, files={"audio": ("short.wav", wav_bytes(), "audio/wav")})
        self.assertEqual(response.status_code, 422)
        playlist = b"#EXTM3U\nhttp://169.254.169.254/latest/meta-data/\n"
        response = self.a.post(url, data={"client_turn_id": "ssrf-fixture-0001"}, files={"audio": ("fake.mp3", playlist, "audio/mpeg")})
        self.assertEqual(response.status_code, 422)

    def test_private_tasks_do_not_leak_through_generic_workflow(self):
        self.turn()
        task = services.enqueue_memory(self.pid, 41)
        self.assertEqual(self.a.get(f"/api/memoir/tasks/{task['id']}").status_code, 200)
        self.assertEqual(self.b.get(f"/api/memoir/tasks/{task['id']}").status_code, 404)
        self.assertEqual(self.a.get(f"/api/tasks/{task['id']}").status_code, 404)
        self.assertEqual(self.anon.get(f"/api/tasks/{task['id']}").status_code, 404)
        detail = self.a.get(f"/api/memoir/profiles/{self.pid}").json()
        self.assertEqual(detail["tasks"][0]["id"], task["id"])
        self.assertNotIn("payload", self.a.get(f"/api/memoir/tasks/{task['id']}").json())

    def test_confirmed_chapter_export_rejects_updated_source(self):
        self.turn()
        task = services.enqueue_chapter(self.pid, 41, "童年")
        cid = task["payload"]["chapter_id"]
        human = next(t for t in ms.list_turns(self.pid, self.sid, 41) if t["role"] == "human")
        import json
        paragraphs = [{"text": "讲述者回忆小时候住在重庆。", "evidence": [{"turn_id": human["id"], "quote": "我小时候住在重庆"}]}]
        with store.get() as db:
            db.execute("UPDATE memoir_chapters SET status='draft',paragraphs=? WHERE id=?", (json.dumps(paragraphs, ensure_ascii=False), cid))
        path = f"/api/memoir/profiles/{self.pid}/chapters/{cid}"
        self.assertEqual(self.a.get(path + "/export").status_code, 409)
        self.assertEqual(self.a.post(path + "/confirm").status_code, 200)
        for fmt in ("md", "docx"):
            result = self.a.get(path + "/export?format=" + fmt)
            self.assertEqual(result.status_code, 200)
            self.assertIn("attachment", result.headers["content-disposition"])
        self.turn("后来我们搬家了。", "fixture-turn-0002")
        self.assertEqual(self.a.post(path + "/confirm").status_code, 409)
        self.assertEqual(self.a.get(path + "/export").status_code, 409)

    def test_delete_profile_hides_resource_and_its_tasks(self):
        self.turn()
        task = services.enqueue_memory(self.pid, 41)
        response = self.a.delete(f"/api/memoir/profiles/{self.pid}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["audio_cleanup_pending"], 0)
        self.assertEqual(self.a.get(f"/api/memoir/profiles/{self.pid}").status_code, 404)
        self.assertEqual(self.a.get(f"/api/memoir/tasks/{task['id']}").status_code, 404)

    def test_mutation_wrong_origin_denied_before_material_change(self):
        before = len(ms.list_turns(self.pid, self.sid, 41))
        response = self.a.post(f"/api/memoir/profiles/{self.pid}/sessions/{self.sid}/turn",
            data={"client_turn_id": "cross-origin-0001", "text": "不应保存"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(ms.list_turns(self.pid, self.sid, 41)), before)

    def test_validation_errors_do_not_echo_password_or_private_material(self):
        password = "fixture-password-must-not-echo"
        response = self.anon.post("/api/memoir/auth/login", json={"password": password})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(password, response.text)
        material = "fixture-private-material-must-not-echo"
        response = self.a.post("/api/memoir/profiles", json={"name": "人物", "description": material * 200})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(material, response.text)
        response = self.a.post(f"/api/memoir/profiles/{self.pid}/sessions/{self.sid}/turn",
                               data={"text": material * 1000, "client_turn_id": "overlong-text-0001"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(material, response.text)

    def test_empty_finish_does_not_mark_session_done(self):
        response = self.a.post(f"/api/memoir/profiles/{self.pid}/sessions/{self.sid}/finish")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(ms.get_session(self.pid, self.sid, 41)["status"], "live")


if __name__ == "__main__":
    unittest.main()
