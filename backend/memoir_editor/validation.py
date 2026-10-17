"""引用的结构校验与覆盖清单；语义仍需人工核对。"""
from memoir.handlers import _evidence


def paragraphs(values, sources):
    if not isinstance(values, list) or not 1 <= len(values) <= 30:
        raise ValueError("章节段落格式不正确")
    result = []
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("text"), str) or not 1 <= len(value["text"].strip()) <= 5000:
            raise ValueError("章节正文格式不正确")
        result.append({"text": value["text"].strip(), "evidence": _evidence(value.get("evidence"), sources, 4)})
    return result


def coverage(turns, chapters):
    cited = {e["turn_id"] for c in chapters for p in c["paragraphs"] for e in p["evidence"]}
    missing = [t["id"] for t in turns if t["id"] not in cited]
    return {"source_turns": len(turns), "cited_turns": len(cited), "uncited_turn_ids": missing,
            "notice": "引用校验通过不等于语义或事实核验；未引用的讲述请在原话清单中核对。"}
