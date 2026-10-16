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
        response = self.client.post("/api/platform/projects", json={"prompt": "锦州调研", "scenario": scenario, "request_id": request_id})
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


if __name__ == "__main__":
    unittest.main()
