import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_import_data = tempfile.TemporaryDirectory(prefix="fangtan-auth-import-")
os.environ.setdefault("FANGTAN_DATA_DIR", _import_data.name)

import app
import store


class EmployeeAuthTests(unittest.TestCase):
    def test_disconnected_employee_service_gives_guidance_without_session(self):
        def disconnected(request):
            raise httpx.ConnectError("connection refused: sensitive-upstream-detail", request=request)

        original_client = httpx.AsyncClient
        with tempfile.TemporaryDirectory(prefix="fangtan-auth-test-") as root, \
                patch.object(app.config, "AUTH_ENABLED", True), \
                patch.object(store, "DATA_DIR", Path(root)), \
                patch.object(store, "DB_PATH", Path(root) / "test.db"), \
                patch("employee_auth.httpx.AsyncClient", side_effect=lambda **kwargs:
                      original_client(transport=httpx.MockTransport(disconnected), **kwargs)), \
                TestClient(app.app) as client:
            store.init()
            response = client.post("/api/auth/login", json={"name": "测试员工", "password": "private-password"})

            self.assertEqual(response.status_code, 503)
            detail = response.json()["detail"]
            self.assertIn("员工中心服务", detail)
            self.assertIn("AUTH_API_URL", detail)
            self.assertNotIn("sensitive-upstream-detail", response.text)
            self.assertNotIn("private-password", response.text)
            self.assertNotIn("set-cookie", response.headers)
            self.assertNotIn(app.employee_auth.COOKIE_NAME, client.cookies)
            with store.get() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
