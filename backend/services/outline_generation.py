"""先检索地点公开资料，再生成问题；模型不能替换来源或伪造联网结果。"""
import kimi
import place_research
from services.materials import location_text


def generate(params, ctx=None):
    if ctx:
        ctx.check_cancelled()
        ctx.progress("正在联网检索地点资料与来源", 0.12)
    research = place_research.research(params.get("location") or {}, ctx=ctx)
    if ctx:
        ctx.check_cancelled()
        ctx.progress("正在结合地点资料生成采访问题", 0.40)
    # 只传受控的公开摘录，绝不把检索页面当作可执行指令。
    context = {"status": research.get("status"), "background": research.get("background"),
               "sources": [{key: source.get(key) for key in ("id", "title", "published_at")}
                           for source in research.get("sources", [])]}
    brief = kimi.gen_outline_brief(params["subject"], params["interviewee_type"], params["industry"],
        params.get("focus", ""), location_text(params.get("location")), research=context)
    return {"questions": brief["questions"], "background": research["background"],
            "background_note": research["background_note"], "sources": research.get("sources") or [],
            "claims": research.get("claims") or [],
            "research_status": research["status"], "retrieved_at": research.get("retrieved_at") or ""}
