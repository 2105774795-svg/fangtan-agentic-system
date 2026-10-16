"""Explicit live smoke run: synthetic material, isolated D-drive database.

Uses configured credentials and makes a bounded set of real model/search calls.
Never logs credentials or writes to the daily database.
"""
import json
import os
import sys
import uuid
from pathlib import Path

root = Path(__file__).resolve().parents[1]
run_dir = Path("D:/codex-deps/tmp") / ("platform-ai-live-" + uuid.uuid4().hex)
run_dir.mkdir(parents=True)
os.environ["FANGTAN_DATA_DIR"] = str(run_dir)
os.environ["TEAMFLOW_USAGE_URL"] = ""
sys.path.insert(0, str(root / "backend"))

import store
import research_platform as platform
import worker

store.init()
platform.init()
state = platform.create_project(platform.CreateProject(prompt="锦州市社区便利店服务体验调研（合成材料联调，不作真实研究结论）", scenario="market", request_id="live-" + uuid.uuid4().hex))


def complete_job():
    global state
    print(json.dumps({"running": state["job"]["kind"]}, ensure_ascii=False), flush=True)
    worker.run_once(handlers={"research_platform": platform.execute_ai_job})
    state = platform.project(state["id"])
    if state["job"]["status"] != "ready":
        print(json.dumps({"failed": state["job"]["kind"], "error": state["job"]["error"]}, ensure_ascii=False), flush=True)
        raise SystemExit(1)
    print(json.dumps({"completed": state["job"]["kind"], "output_chars": len(state["job"]["output"])}, ensure_ascii=False), flush=True)


def command(action, **kwargs):
    global state
    state = platform.command(state["id"], platform.Command(revision=state["revision"], action=action, **kwargs))


complete_job()
command("brief", answers={"industry": ["消费零售"], "goal": ["了解市场现状"], "audience": ["商户 / 店主"]}, location={"city": "锦州", "market": ""}, context="这是软件联调，后续输入为合成材料，不是真实访谈。请简短输出。")
complete_job()
command("advance")
complete_job()
command("advance")
command("material", title="合成联调访谈（非真实人物）", content="采访者：你对便利店服务有什么观察？\n合成受访者：在这份虚构案例里，顾客更关心夜间能否买到热饮，但我没有统计人数。\n采访者：你有什么建议？\n合成受访者：可以先试行一周夜间热饮服务，并记录咨询次数。这里只是演练建议，不是已经执行的行动。")
command("advance")
complete_job()
command("advance")
complete_job()
command("advance")
result = {"success": state.get("completed") is True, "project_id": state["id"], "isolated_data_dir": str(run_dir), "artifacts": [{"title": a["title"], "origin": a.get("origin"), "chars": len(a["content"])} for a in state["artifacts"]], "research_status": state.get("research", {}).get("status"), "public_sources": len(state.get("research", {}).get("sources", []))}
output = root / "data/qa/platform-ai-live.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False), flush=True)
