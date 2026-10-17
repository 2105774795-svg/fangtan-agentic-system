"""Agent 工具白名单。项目/会话由运行时绑定，模型不能传入项目编号。"""
import json
import re
import time
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .state import Decision, InterviewPlan
from memory import repository, retrieval


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=300)


class Read(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_ids: list[str] = Field(min_length=1, max_length=3)
    start: int = Field(default=0, ge=0, le=12000)
    max_chars: int = Field(default=4000, ge=100, le=6000)


class Evidence(BaseModel):
    model_config = ConfigDict(extra='forbid')
    turn_id: str = Field(min_length=1, max_length=80, description='本项目本人原话的稳定编号')
    quote: str = Field(min_length=2, max_length=1600, description='逐字复制同一轮连续原文，不改写、不拼接')


class Candidate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    category: Literal['event', 'person', 'place', 'thread', 'preference'] = 'event'
    title: str = Field(min_length=1, max_length=3000)
    description: str = Field(min_length=1, max_length=3000)
    date_text: str = Field(default='未提及', max_length=100, description='必须逐字出现在本条evidence.quote中；没有明确时间则填写未提及，不推算或概括')
    status: Literal['candidate', 'uncertain'] = 'candidate'
    evidence: list[Evidence] = Field(min_length=1, max_length=4)


class Candidates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memories: list[Candidate] = Field(min_length=1, max_length=5)


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)


class EvidenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence: list[Evidence] = Field(min_length=1, max_length=4)


SCHEMAS = {"search_memories": Search, "read_original_turns": Read,
           "save_memory_candidates": Candidates, "update_interview_plan": InterviewPlan,
           "create_story_draft": Draft, "validate_story_evidence": EvidenceCheck, "respond": Decision}
DESCRIPTIONS = {
    "search_memories": "检索当前项目相关人物、经历与本人原话；存疑或冲突不能当作已确定事实。",
    "read_original_turns": "按稳定轮次编号读取当前项目本人原话，核对记忆依据。",
    "save_memory_candidates": "保存最多5条候选记忆。每条包含category(event/person/place/thread/preference)、title、description、date_text(原话时间或未提及)、evidence[{turn_id,quote}]；不能标为confirmed。更正建立新候选，旧记忆等待本人确认。",
    "update_interview_plan": "更新当前主题、待展开线索及明确跳过的话题；只记录用户明确意愿，不推断情绪或强迫完成提纲。",
    "create_story_draft": "仅在本人本轮明确要求写故事、自传或书稿时启动传记后台任务；先写样章，认可写法后逐步续写，不结束聊天、不覆盖已定稿文字。",
    "validate_story_evidence": "检查引用确为当前项目本人的连续原话；不等于语义或事实核验。",
    "respond": "结束本轮Agent执行，向本人说一段简短自然回应或一个问题，并可保存更新后的访谈计划。不能自行结束对谈。",
}


def definitions():
    return [{"type": "function", "function": {"name": name, "description": DESCRIPTIONS[name],
             "parameters": schema.model_json_schema()}} for name, schema in SCHEMAS.items()]


def execute(name, arguments, db, pid, sid, turn_id):
    if name not in SCHEMAS or name == "respond":
        raise ValueError("工具不在允许范围内")
    value = SCHEMAS[name].model_validate(arguments)
    if name == "search_memories":
        return retrieval.search(db, pid, value.query)
    if name == "read_original_turns":
        return [{**r, "text": r["text"][value.start:value.start + value.max_chars], "start": value.start,
                 "truncated": value.start > 0 or len(r["text"]) > value.max_chars}
                for r in repository.sources(db, pid, value.turn_ids)]
    if name == "save_memory_candidates":
        return [{key: saved[key] for key in ("id", "title", "status", "revision")}
                for saved in [repository.save_candidate(db, pid, item.model_dump()) for item in value.memories]]
    if name == "update_interview_plan":
        save_plan(db, pid, sid, turn_id, value)
        return value.model_dump()
    if name == "validate_story_evidence":
        return {"valid": True, "evidence": repository.validate_evidence(db, pid, [e.model_dump() for e in value.evidence])}
    if name == "create_story_draft":
        human = db.execute("SELECT text FROM conversation_turns WHERE session_id=? AND role='human' AND seq<(SELECT seq FROM conversation_turns WHERE id=?) ORDER BY seq DESC LIMIT 1", (sid, turn_id)).fetchone()
        if not human or not re.search(r"(?:整理|生成|写|汇总).{0,12}(?:故事|章节|回忆录|传记|自传|书稿)|(?:故事|章节|回忆录|自传|书稿).{0,12}(?:整理|生成)", human["text"]):
            raise ValueError("本人尚未明确要求整理")
        return enqueue_book(db, pid, request_key="agent-book:" + turn_id)


def save_plan(db, pid, sid, turn_id, plan):
    seq = db.execute("SELECT seq FROM conversation_turns WHERE id=? AND session_id=?", (turn_id, sid)).fetchone()["seq"]
    db.execute("""INSERT INTO memoir_agent_plans VALUES(?,?,1,?,?) ON CONFLICT(session_id)
        DO UPDATE SET revision=revision+1,plan=excluded.plan,source_seq=excluded.source_seq
        WHERE excluded.source_seq>=source_seq""", (sid, pid, plan.model_dump_json(), seq))


def failure_result(cause):
    """只返回已知校验的纠正办法，绝不回显异常原文中的输入。"""
    code, correction = 'tool_invalid', '工具参数无效，本轮暂缓该工具；直接根据已有原话回应，不能声称执行成功。'
    if isinstance(cause, ValidationError):
        code, correction = 'tool_schema', '参数必须符合工具JSON结构；本轮暂缓整理，直接回应，不声称保存成功。'
    elif type(cause) is ValueError:
        if str(cause) == '时间没有原话依据':
            code, correction = 'memory_date', 'date_text必须逐字出现在本条evidence.quote中；没有明确时间应为未提及。不能推算年份，本轮暂缓记忆整理，先回应本人。'
        elif str(cause) in {'引用必须是本项目本人连续原话', '原话不存在或不属于当前项目', '原话编号无效', '记忆必须提供原话依据', '原话格式无效'}:
            code, correction = 'memory_quote', '引用必须使用本项目role=human的真实turn_id和逐字连续quote，不得概括或改写。本轮暂缓记忆整理，先回应本人。'
    return {'error': correction, 'code': code, 'saved': False, 'continue_conversation': True}


def enqueue_book(db, pid, request_key=None, mode='continue'):
    repository.require_project(db, pid)
    if request_key:
        previous = db.execute("SELECT id,status FROM tasks WHERE kind='conversation_book' AND resource_id=? AND dedupe_key=?", (pid, request_key)).fetchone()
        if previous:
            return {"task_id": previous["id"], "status": previous["status"]}
    old = db.execute("SELECT id FROM tasks WHERE resource_id=? AND kind='conversation_book' AND status IN ('queued','running','retry') ORDER BY created_at DESC LIMIT 1", (pid,)).fetchone()
    if old:
        return {"task_id": old["id"], "status": "queued"}
    tid, now = uuid.uuid4().hex, time.time()
    db.execute("""INSERT INTO tasks(id,kind,owner_id,resource_id,payload,dedupe_key,max_attempts,available_at,created_at,updated_at)
        VALUES(?,'conversation_book',-1,?,?,?,1,?,?,?)""", (tid, pid, json.dumps({"pid": pid, "mode": mode}), request_key, now, now, now))
    return {"task_id": tid, "status": "queued"}
