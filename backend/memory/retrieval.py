"""中文短语/二元词召回，返回相关原话；无需新增服务或向量库。"""
import re

from . import repository


def tokens(query):
    terms = set(re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", query.lower()))
    for term in list(terms):
        if re.search(r"[\u4e00-\u9fff]", term):
            terms.update(term[i:i + 2] for i in range(len(term) - 1))
    return terms


def search(db, pid, query, limit=6):
    repository.require_project(db, pid)
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 300:
        raise ValueError("检索词无效")
    terms = tokens(query)
    def score(text):
        return sum(min(8, len(t)) for t in terms if t in text.lower())
    # 项目最大原话 20 万字；在当前规模扫描原话能保留中文召回，避免仅搜摘要。
    raw = db.execute("""SELECT t.id,t.text,t.session_id,t.seq FROM conversation_turns t
        JOIN conversation_sessions s ON s.id=t.session_id WHERE s.project_id=? AND s.kind='memoir'
        AND t.role='human' AND t.control='' ORDER BY s.created_at DESC,t.seq DESC""", (pid,)).fetchall()
    ranked = sorted(((score(r["text"]), dict(r)) for r in raw), key=lambda v: v[0], reverse=True)
    originals = [{**r, "text": r["text"][:4000], "truncated": len(r["text"]) > 4000} for rank, r in ranked if rank > 0][:limit]
    rows = repository.memories(db, pid, 1000)
    for original in originals:
        original["memory_states"] = [{"status": m["status"], "note": m["note"], "related_ids": m["related_ids"]}
                                     for m in rows if any(e["turn_id"] == original["id"] for e in m["evidence"])]
    remembered = sorted((m for m in rows if m["status"] not in {"dismissed", "superseded"}
                          and score(m["title"] + m["description"] + m["date_text"]) > 0),
                         key=lambda m: score(m["title"] + m["description"]), reverse=True)[:limit]
    return {"memories": remembered, "originals": originals}
