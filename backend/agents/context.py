"""按预算装配原话，未进入窗口的资料保留清单并可按编号读取。"""
import json

from memory import repository, retrieval
from memoir_editor.biography import interview_context


def build(db, pid, sid, turn_id):
    project = repository.require_project(db, pid)
    current = db.execute("SELECT seq FROM conversation_turns WHERE id=? AND session_id=?", (turn_id, sid)).fetchone()
    if not current:
        raise ValueError("轮次不存在")
    # 本人原话始终留存，窗口预算之外提供编号清单，不伪装为完整上下文。
    rows = [dict(r) for r in db.execute("SELECT id,role,text,seq FROM conversation_turns WHERE session_id=? AND seq<? AND text<>'' ORDER BY seq", (sid, current["seq"]))]
    humans = [r for r in rows if r["role"] == "human"]
    last = humans[-1]["text"] if humans else ""
    recent, used = [], 0
    for row in reversed(rows):
        if recent and used + len(row["text"]) > 12000:
            break
        recent.insert(0, row)
        used += len(row["text"])
    visible = {r["id"] for r in recent}
    plan_row = db.execute("SELECT plan FROM memoir_agent_plans WHERE session_id=? AND project_id=?", (sid, pid)).fetchone()
    person = db.execute("SELECT preferred_name FROM conversation_people WHERE project_id=?", (pid,)).fetchone()
    related = retrieval.search(db, pid, last[-300:] or project["prompt"][:300], limit=3)
    for original in related["originals"]:
        if len(original["text"]) > 1200:
            original["text"] = original["text"][:1200]
            original["truncated"] = True
    material = {"topic": project["prompt"], "preferred_name": person["preferred_name"] if person else "",
            "plan": json.loads(plan_row["plan"]) if plan_row else {},
            "recent_dialogue": recent,
            "earlier_source_manifest": [{"id": r["id"], "excerpt": r["text"][:60]} for r in humans if r["id"] not in visible][-40:],
            "earlier_source_count": sum(r["id"] not in visible for r in humans),
            "related_history": related,
            "biography": interview_context(db, pid, last[-300:]),
            "memory_notice": "候选、存疑、已确认均为本人讲述状态，不等于独立事实核验；存疑项只能用于澄清。"}
    # 保留最新本人轮次；其余内容按预算检索，不把截取的窗口说成全部历史。
    while len(json.dumps(material, ensure_ascii=False)) > 20000:
        if related["originals"]:
            related["originals"].pop()
        elif related["memories"]:
            related["memories"].pop()
        elif material["earlier_source_manifest"]:
            material["earlier_source_manifest"].pop(0)
        elif len(recent) > 1:
            recent.pop(0)
        elif material['biography']['gaps']:
            material['biography']['gaps'].pop()
        elif material['biography']['chapters']:
            material['biography']['chapters'].pop()
        else:
            break
    return material
