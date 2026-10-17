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
import conversation_engine
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
        return {"intake": {"industry": "精品咖啡" if "精品咖啡" in state["prompt"] else "", "scenario": "market", "title": "上海新能源汽车市场调研" if "新能源汽车" in state["prompt"] else "锦州调研 · 隔离验收", "reply": reply, "location": {"city": "上海" if "新能源汽车" in state["prompt"] else "锦州", "market": ""}}}
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
        materials = [a for a in state['artifacts'] if a.get('kind') == 'material']
        content += '\n\n' + '\n'.join(f"材料{i+1}中的原文待结合上下文核实 [M{i+1}-P1]。" for i, a in enumerate(materials))
    if kind == "report":
        content = "# 多场访谈调研整合报告\n\n报告性质：内部工作讨论稿\n资料范围：以已同步原文为准，访谈场次不等于独立受访人数。\n\n## 执行摘要\n仅依据合成材料整理，不作为真实调查事实。\n\n"
        content += '\n\n'.join('## ' + heading + '\n' + '待结合材料核实。' for heading in ['一 研究范围与判读方法', '二 受访对象现状与需求归纳', '三 需求整合清单'])
        content += '\n\n| 需求 | 来源 | 核实状态 |\n| --- | --- | --- |\n| 营业时间建议 | [M1-P1] | 待核实 |\n\n## 四 逐场访谈与实地记录\n'
        content += '\n'.join(f"### 材料{i+1} {a['title']}\n{a['content'].splitlines()[0]} [M{i+1}-P1]" for i,a in enumerate(materials))
        content += '\n\n## 五 需要进一步补充的数据\n独立受访人数待核对。\n\n## 六 关键分歧与研究风险\n合成测试资料，不能外推。\n\n## 材料索引与来源\n'
        content += '\n'.join(f"- M{i+1}：{a['title']} [M{i+1}-P1]" for i,a in enumerate(materials))
    for start in range(0, len(content), 8):
        ctx.check_cancelled()
        on_text(content[start:start + 8])
        time.sleep(.09)
    return {"content": content, "research": {"status": "not_requested", "note": "隔离测试未联网", "sources": []}, "source_ids": [a["id"] for a in state["artifacts"] if a.get("kind") == "material"]}


def conversation_stream(messages, on_text, **kwargs):
    text = "您提到和母亲赶集的往事，那时最盼着吃到什么？"
    for start in range(0, len(text), 3):
        on_text(text[start:start + 3])
        time.sleep(.12)
    return text


def conversation_archive(system, block):
    evidence = [{"turn_id": row["turn_id"], "quote": row["text"]} for row in block[:3]]
    if 'events' in system:
        return {"events": [{"title": "童年赶集", "description": "记录了和母亲赶集的回忆。", "date_text": "未提及", "status": "candidate", "evidence": evidence}]}
    return {"paragraphs": [{"text": "小时候，母亲经常带我去赶集。", "evidence": evidence}]}


stop = threading.Event()
def work():
    while not stop.is_set():
        if not worker.run_once(handlers={"research_platform": research_platform.execute_ai_job, "report": task_handlers.handle_report, "import": task_handlers.handle_import, **conversation_engine.HANDLERS}):
            stop.wait(.1)


if __name__ == "__main__":
    with patch("platform_ai.stream", side_effect=conversation_stream), patch("memoir.handlers._chat", side_effect=conversation_archive), patch("platform_ai.generate", side_effect=model_fixture), patch("kimi.gen_report", return_value="# 合成纪要\n\n仅为隔离测试。"), patch("kimi.gen_followup", return_value=None), patch("asr_dashscope.transcribe", return_value="受访者：最近顾客提出延长营业时间的建议。这是合成音频转写。"):

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        try:
            uvicorn.run(app.app, host="127.0.0.1", port=int(os.environ.get("PLATFORM_TEST_PORT", "18998")))
        finally:
            stop.set()
            thread.join(timeout=5)
