"""Isolated deterministic model fixture for browser tests, never production."""
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
os.environ["FANGTAN_DATA_DIR"] = str(Path("D:/codex-deps/tmp") / ("platform-browser-" + uuid.uuid4().hex))
os.environ["KIMI_API_KEY"] = "browser-fixture-never-sent"
os.environ["DASHSCOPE_API_KEY"] = ""
os.environ["FANGTAN_AUTH_ENABLED"] = "false"
os.environ["TEAMFLOW_USAGE_URL"] = ""
sys.path.insert(0, str(root / "backend"))
import app
import worker
import research_platform
import task_handlers
import uvicorn


def model_fixture(kind, state, ctx, on_text):
    if kind == "intake":
        reply = "已识别锦州。请选择行业、目标和受访对象。"
        if '新能源汽车' in state['prompt']:
            reply = '## 调研方向\n已理解你要了解**上海新能源汽车市场**。请选择调研目标与受访对象。'
        for start in range(0, len(reply), 3):
            ctx.check_cancelled()
            on_text(reply[start:start + 3])
            time.sleep(.2)
        return {"intake": {"scenario": "market", "title": "上海新能源汽车市场调研" if "新能源汽车" in state["prompt"] else "锦州调研 · 隔离验收", "reply": reply, "location": {"city": "上海" if "新能源汽车" in state["prompt"] else "锦州", "market": ""}}}
    if kind == "chat" and "新加文档" in state["messages"][-1]["text"]:
        content = "# 访谈行程安排\n\n建议安排两场访谈，具体时间待确认。"
        for part in (content[:10], content[10:]):
            ctx.check_cancelled()
            on_text(part)
            time.sleep(.4)
        return {"content": content, "document": {"step": 1, "title": "访谈行程安排"}, "source_ids": []}
    content = f"# {kind} 测试草稿\n\n" + "依据本项目材料进行整理。" * 10
    if '新能源汽车' in state['prompt']:
        content += "\n\n## 研究方向\n这是**重点内容**。\n\n- 目标一\n- 目标二\n\n| 项目 | 状态 |\n| --- | --- |\n| 资料 | 待核实 |\n\n```text\n保留代码里的 **字面符号**\n```"
    if kind == "outline":
        content += "\n- 您经营多久了？\n- 最近顾客有哪些变化？\n- 可以举一个亲历的例子吗？"
    if kind in {"analysis", "report"}:
        content += "\n\n材料中的受访者提出延长营业时间的建议 [M1-P1]。"
    for start in range(0, len(content), 8):
        ctx.check_cancelled()
        on_text(content[start:start + 8])
        time.sleep(.09)
    return {"content": content, "research": {"status": "not_requested", "note": "隔离测试未联网", "sources": []}, "source_ids": [a["id"] for a in state["artifacts"] if a.get("kind") == "material"]}


stop = threading.Event()
def work():
    while not stop.is_set():
        if not worker.run_once(handlers={"research_platform": research_platform.execute_ai_job, "report": task_handlers.handle_report, "import": task_handlers.handle_import}):
            stop.wait(.1)


if __name__ == "__main__":
    with patch("platform_ai.generate", side_effect=model_fixture), patch("kimi.gen_report", return_value="# 合成纪要\n\n仅为隔离测试。"), patch("kimi.gen_followup", return_value=None), patch("asr_dashscope.transcribe", return_value="受访者：最近顾客提出延长营业时间的建议。这是合成音频转写。"):

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        try:
            uvicorn.run(app.app, host="127.0.0.1", port=18998)
        finally:
            stop.set()
            thread.join(timeout=5)
