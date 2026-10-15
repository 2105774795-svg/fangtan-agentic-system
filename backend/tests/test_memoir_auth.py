import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("FANGTAN_DATA_DIR", tempfile.mkdtemp(prefix="memoir-auth-import-"))
import config
import employee_auth
import store
from memoir import auth


class MemoirAuthTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory(prefix="memoir-auth-")
        self.patches = [patch.object(store, "DB_PATH", Path(self.root.name) / "test.db"),
                        patch.object(store, "DATA_DIR", Path(self.root.name)),
                        patch.object(config, "PUBLIC_BASE", "https://example.cn/fangtan"),
                        patch.object(config, "AUTH_ENABLED", False)]
        for item in self.patches:
            item.start()
        store.init()
        auth.init()
        auth._attempts.clear()
        app = FastAPI()

        @app.get("/private")
        async def private(user=Depends(auth.require_user)):
            return user

        @app.post("/private")
        async def write(user=Depends(auth.require_user)):
            return user

        @app.post("/issue/{uid}")
        async def issue(uid: int):
            response = JSONResponse({"ok": True})
            auth.issue_session(response, {"id": uid, "name": "测试员工", "type": "测试"})
            return response

        @app.post("/logout")
        async def logout(request: Request):
            response = JSONResponse({"ok": True})
            auth.revoke_session(request, response)
            return response

        self.client = TestClient(app, base_url="https://example.cn")

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.root.cleanup()

    def test_auth_false_is_not_private_login(self):
        response = self.client.get("/private")
        self.assertEqual(response.status_code, 401)
        self.client.cookies.set(employee_auth.COOKIE_NAME, "old-employee-token")
        self.assertEqual(self.client.get("/private").status_code, 401)

    def test_positive_identity_separate_cookie_and_token_hashed(self):
        response = self.client.post("/issue/42")
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=strict", cookie)
        token = self.client.cookies[auth.COOKIE_NAME]
        self.assertEqual(self.client.get("/private").json()["id"], 42)
        with store.get() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0], 0)
            row = dict(db.execute("SELECT * FROM memoir_auth_sessions").fetchone())
        self.assertNotEqual(token, row["token_hash"])
        self.assertEqual(auth._hash(token), row["token_hash"])

    def test_negative_local_identity_refused(self):
        self.assertEqual(self.client.post("/issue/-1").status_code, 401)

    def test_expired_session_deleted(self):
        self.client.post("/issue/42")
        with store.get() as db:
            db.execute("UPDATE memoir_auth_sessions SET expires_at=?", (time.time() - 1,))
        self.assertEqual(self.client.get("/private").status_code, 401)
        with store.get() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memoir_auth_sessions").fetchone()[0], 0)

    def test_invalid_upstream_account_revokes_private_cookie_only(self):
        self.client.post("/issue/42")
        with store.get() as db:
            db.execute("UPDATE memoir_auth_sessions SET checked_at=0")
        with patch.object(employee_auth, "upstream", AsyncMock(return_value={"success": False})):
            self.assertEqual(self.client.get("/private").status_code, 401)
        with store.get() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM memoir_auth_sessions").fetchone()[0], 0)

    def test_upstream_failure_is_closed(self):
        from fastapi import HTTPException
        self.client.post("/issue/42")
        with store.get() as db:
            db.execute("UPDATE memoir_auth_sessions SET checked_at=0")
        with patch.object(employee_auth, "upstream", AsyncMock(side_effect=HTTPException(503, "员工中心暂不可用"))):
            self.assertEqual(self.client.get("/private").status_code, 503)

    def test_other_account_from_revalidation_is_rejected(self):
        self.client.post("/issue/42")
        with store.get() as db:
            db.execute("UPDATE memoir_auth_sessions SET checked_at=0")
        data = {"success": True, "data": {"id": 43, "name": "测试员工"}}
        with patch.object(employee_auth, "upstream", AsyncMock(return_value=data)):
            self.assertEqual(self.client.get("/private").status_code, 401)

    def test_csrf_cross_origin_and_fetch_site_rejected(self):
        self.client.post("/issue/42")
        for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"},
                        {"Origin": "https://example.cn.evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.client.post("/private", headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/private", headers={"Origin": "https://example.cn"}).status_code, 200)

    def test_logout_revokes_only_private_token(self):
        self.client.post("/issue/42")
        self.client.cookies.set(employee_auth.COOKIE_NAME, "untouched-old-session")
        self.client.post("/logout")
        self.assertEqual(self.client.get("/private").status_code, 401)
        self.assertEqual(self.client.cookies[employee_auth.COOKIE_NAME], "untouched-old-session")

    def test_login_rate_is_bounded_and_stores_no_password(self):
        scope = {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)}
        request = Request(scope)
        for _ in range(8):
            auth.check_login_rate(request, "测试员工")
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as error:
            auth.check_login_rate(request, "测试员工")
        self.assertEqual(error.exception.status_code, 429)
        self.assertTrue(all(len(key) == 64 for key in auth._attempts))
        self.assertLessEqual(len(auth._attempts), auth._MAX_RATE_KEYS)


if __name__ == "__main__":
    unittest.main()
