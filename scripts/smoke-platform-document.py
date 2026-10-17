"""Explicit paid smoke test of Kimi tool calls, using only synthetic project data."""
import json
import os
import sys
import uuid
from pathlib import Path

root = Path(__file__).resolve().parents[1]
directory = Path("D:/codex-deps/tmp") / ("platform-document-live-" + uuid.uuid4().hex)
os.environ["FANGTAN_DATA_DIR"] = str(directory)
sys.path.insert(0, str(root / "backend"))
import store
import research_platform as platform
import worker

store.init()
platform.init()
project = platform.create_project(platform.CreateProject(prompt="文档工具联调（合成数据）", scenario="market", request_id=uuid.uuid4().hex, mode="demo"))
with store.get() as db:
    state = platform._read(db, project["id"])
    state.update(mode="ai", stage=1)
    original = platform._artifact(state, 1, "背景与计划", "# 合成调研计划\n\n仅用于文档工具联调，无真实访谈事实。", origin="ai")
    platform._message(state, "# 访谈行程安排\n\n建议上午进行第一场访谈，下午整理问题。具体时间和对象均待确认。")
    platform._save(db, state)
project = platform.project(project["id"])
platform.command(project["id"], platform.Command(revision=project["revision"], action="message", selected_step=1,
    content="请把你上一条访谈行程安排作为一份新的独立文档，新增到背景与计划模块。保留已有的背景与计划文件。"))
worker.run_once(handlers={"research_platform": platform.execute_ai_job})
result = platform.project(project["id"])
created = [a for a in result["artifacts"] if a.get("kind") == "document"]
assert result["job"]["status"] == "ready", result["job"]["error"]
assert len(created) == 1 and created[0]["step"] == 1, "Model did not invoke the document tool"
assert result["stage"] == 1 and result["artifacts"][0] == original
assert result["messages"][-1]["artifact_id"] == created[0]["id"]
summary = {"success": True, "tool": "create_document", "target_module": "背景与计划", "new_title": created[0]["title"],
           "stage_unchanged": True, "original_preserved": True, "isolated_data_dir": str(directory)}
target = root / "data/qa/platform-ai/document-live.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False))
