"""在同一 SQLite 快照内读取原文，避免拼出不同版本的材料。"""
import json

import config
import project_records
import store


def location_text(location):
    if isinstance(location, str):
        try:
            location = json.loads(location)
        except ValueError:
            return ""
    if not isinstance(location, dict):
        return ""
    # 保留历史细地址的只读兼容，新界面仅收集城市和市场。
    return " · ".join(str(location[key]) for key in
                      ("city", "district", "town", "market", "address") if location.get(key))


def read_session(db, sid):
    row = db.execute("SELECT * FROM sessions WHERE id=? AND status!='deleting'", (sid,)).fetchone()
    if not row:
        raise ValueError("访谈已删除或不存在")
    session = dict(row)
    transcript = [dict(item) for item in db.execute(
        "SELECT * FROM utterances WHERE session_id=? ORDER BY seq,id", (sid,)).fetchall()]
    row = db.execute("SELECT markdown FROM reports WHERE session_id=?", (sid,)).fetchone()
    return session, transcript, row["markdown"] if row else None


def report_source_fingerprint(session, transcript):
    # 纪要并非自身的事实来源，不参与生成前后的原文版本比较。
    return project_records.source_fingerprint(session, transcript, "")


def load_project_materials(project_id, session_ids, user_id, *, db=None):
    """完整读取原文与已有纪要，不以页面最近 N 条代替原文。"""
    if db is None:
        with store.get() as connection:
            connection.execute("BEGIN")
            return load_project_materials(project_id, session_ids, user_id, db=connection)
    materials, sources = [], []
    for sid in session_ids:
        session, transcript, report = read_session(db, sid)
        shared = db.execute("SELECT 1 FROM session_shares WHERE session_id=? AND user_id=?",
                            (sid, user_id)).fetchone()
        if (session["status"] != "done" or session.get("project_id") != project_id
                or (config.AUTH_ENABLED and session.get("owner_id") != user_id and not shared)):
            raise ValueError("所选访谈尚未结束、已移出项目或无权访问，请刷新项目")
        if not transcript or not any((row["text"] or "").strip() for row in transcript):
            raise ValueError("所选访谈缺少逐字稿，不能只根据纪要合并")
        materials.append({"session": session, "transcript": transcript, "report": report or ""})
    materials.sort(key=lambda item: (item["session"]["created_at"], item["session"]["id"]))
    if sum(len(row["text"] or "") for item in materials for row in item["transcript"]) + sum(
            len(item["report"]) for item in materials) > project_records.MAX_SOURCE_CHARS:
        raise ValueError("所选材料超过 200 万字，请减少勾选数量；系统不会截断原文")
    for index, item in enumerate(materials, 1):
        sources.append({"id": item["session"]["id"], "reference": f"访谈{index:02d}",
                        "subject": item["session"]["subject"], "utterance_count": len(item["transcript"]),
                        "has_report": bool(item["report"]),
                        "fingerprint": project_records.source_fingerprint(
                            item["session"], item["transcript"], item["report"])})
    return materials, sources
