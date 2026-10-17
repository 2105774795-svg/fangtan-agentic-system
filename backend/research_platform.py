"""Unauthenticated local research workspace, phase one.

Projects use the existing projects table. Workspace snapshots are versioned to
reject stale browser writes. Demonstration artifacts never call cloud services.
"""
import json
import time
import uuid
import asyncio
import hashlib
import math
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError

import store
import config
import job_queue
import playbook
import employee_auth


async def workspace_access(request: Request):
    if not config.AUTH_ENABLED:
        return
    user = getattr(request.state, "user", None) or await employee_auth.current_user(request)
    request.state.user = user
    pid = request.path_params.get("project_id")
    if pid:
        with store.get() as db:
            row = db.execute("SELECT owner_id FROM projects WHERE id=?", (pid,)).fetchone()
            if not row or row["owner_id"] != user["id"]:
                raise HTTPException(404, "项目不存在或无权访问")


router = APIRouter(prefix="/api/platform", tags=["research-platform"], dependencies=[Depends(workspace_access)])
LOCAL_OWNER = -1

SCENARIOS = {
    "market": {
        "name": "用户与市场调研", "description": "理解使用场景，发现需求机会", "noun": "调研",
        "steps": ["定义调研目标", "研究背景与方案", "访谈提纲", "开展访谈", "洞察分析", "研究成果"],
        "fields": [
            {"key": "industry", "label": "关注哪个行业？", "options": ["综合 / 暂不限定", "消费零售", "制造业", "文旅服务", "农业与食品", "互联网科技"], "multiple": False},
            {"key": "goal", "label": "本次调研聚焦哪些研究问题？", "options": ["理解使用场景", "识别用户痛点", "探索需求机会", "验证研究假设", "了解市场现状", "发现经营问题", "寻找增长机会", "比较区域差异", "验证一个想法"], "multiple": True},
            {"key": "audience", "label": "本次计划访谈哪些目标人群？", "options": ["商户 / 店主", "消费者", "企业负责人", "行业专家", "园区 / 协会人员"], "multiple": True},
        ],
    },
    "enterprise": {
        "name": "企业访谈", "description": "通过对话，理解一家企业", "noun": "访谈",
        "steps": ["定义调研目标", "研究背景与方案", "访谈提纲", "开展访谈", "洞察分析", "研究成果"],
        "fields": [
            {"key": "industry", "label": "企业属于哪个行业？", "options": ["综合 / 暂不限定", "制造业", "消费零售", "互联网科技", "专业服务", "能源与环保"], "multiple": False},
            {"key": "goal", "label": "本次调研聚焦哪些业务问题？", "options": ["理解业务流程", "识别业务痛点", "验证需求假设", "业务与商业模式", "经营挑战", "组织与人才", "数字化转型", "发展战略"], "multiple": True},
            {"key": "audience", "label": "本次计划访谈哪些业务角色？", "options": ["创始人 / 企业家", "企业高管", "业务负责人", "一线员工", "合作伙伴"], "multiple": True},
        ],
    },
    "memoir": {
        "name": "口述史", "description": "留住经历，让记忆有迹可循", "noun": "讲述",
        "steps": ["讲述目标", "人生时间线", "讲述提纲", "多次讲述", "线索与章节", "审阅归档"],
        "fields": [
            {"key": "industry", "label": "想从哪段人生开始？", "options": ["完整人生", "童年与故乡", "求学岁月", "工作与事业", "家庭与亲友", "一段特别的经历"], "multiple": False},
            {"key": "goal", "label": "有哪些想留下来的主题？", "options": ["人生转折", "时代变迁", "家族记忆", "职业经历", "日常生活与感受"], "multiple": True},
            {"key": "audience", "label": "希望最后整理成什么？", "options": ["个人回忆录", "家庭故事集", "主题口述记录", "人生时间线"], "multiple": False},
        ],
    },
}
from interview_prep.workspace import SCENARIO as INTERVIEW_SCENARIO
SCENARIOS['interview'] = INTERVIEW_SCENARIO


def init():
    with store.get() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS research_workspace_runs(
            project_id TEXT PRIMARY KEY REFERENCES projects(id),
            revision INTEGER NOT NULL, snapshot TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS research_workspace_jobs(
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL,
            fingerprint TEXT NOT NULL, output TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS research_workspace_layouts(
            project_id TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 0,
            positions TEXT NOT NULL DEFAULT '{}'
        )""")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(research_workspace_runs)")}
        if "deleted_at" not in columns:
            db.execute("ALTER TABLE research_workspace_runs ADD COLUMN deleted_at REAL NOT NULL DEFAULT 0")
        job_queue.init(db)


class CreateProject(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    scenario: str | None = None
    request_id: str = Field(min_length=8, max_length=80)
    mode: str = "ai"
    source_project_id: str | None = None
    interview_config: dict | None = None


class Command(BaseModel):
    revision: int = Field(ge=1)
    action: str
    scenario: str | None = None
    answers: dict[str, list[str]] | None = None
    custom_answers: dict[str, str] = Field(default_factory=dict)
    context: str = Field(default="", max_length=1000)
    content: str = Field(default="", max_length=100000)
    artifact_id: str | None = None
    title: str = Field(default="", max_length=120)
    location: dict[str, str] | None = None
    selected_questions: list[str] = Field(default_factory=list, max_length=80)
    outline_version: int | None = None
    session_id: str | None = None
    request_id: str = Field(default="", max_length=80)
    selected_step: int | None = Field(default=None, ge=0, le=5)
    scene_command: dict | None = None
    chat_task_id: str | None = Field(default=None, min_length=1, max_length=80)


class Layout(BaseModel):
    version: int = Field(ge=0)
    positions: dict[str, dict[str, float]]


def _save(db, state):
    state["revision"] += 1
    state["updated_at"] = time.time()
    db.execute("UPDATE research_workspace_runs SET revision=?,snapshot=? WHERE project_id=?",
               (state["revision"], json.dumps(state, ensure_ascii=False), state["id"]))
    db.execute("UPDATE projects SET updated_at=? WHERE id=?", (state["updated_at"], state["id"]))


def _public(db, state):
    if state['scenario'] == 'interview':
        from interview_prep import workspace
        return workspace.public(db, state)
    layout = db.execute("SELECT version,positions FROM research_workspace_layouts WHERE project_id=?", (state["id"],)).fetchone()
    result = dict(state)
    result["experience"] = "conversation" if state["scenario"] == "memoir" and state.get("mode") == "ai" else "workspace"
    if state.get("mode") == "ai" and state["stage"] == 0:
        industry = known_industry(state)
        result["known_industry"] = industry
        result["answers"] = dict(state["answers"])
        if industry and not result["answers"].get("industry"):
            result["answers"]["industry"] = [industry]
        result["checklist"] = intake_fields(state)
    result["layout"] = {"version": layout["version"], "positions": json.loads(layout["positions"])} if layout else {"version": 0, "positions": {}}
    result["mode"] = state.get("mode", "demo")
    outline = next((a for a in reversed(state["artifacts"]) if a["step"] == 2 and a.get("kind") not in {"question_selection", "document"}), None)
    result["outline_questions"] = outline_questions(outline["content"]) if outline else []
    result["interviews"] = interview_inventory(db, state)
    result["job"] = None
    if state.get("job_id"):
        row = db.execute("""SELECT j.id,j.kind,j.output,j.error,t.status,t.stage,t.progress
            FROM research_workspace_jobs j JOIN tasks t ON j.id=t.id WHERE j.id=? AND j.project_id=?""", (state["job_id"], state["id"])).fetchone()
        if row:
            result["job"] = dict(row)
            if row["status"] == "failed" and not row["error"]:
                result["job"]["error"] = "任务中断或执行时间超限，请重试"
    return result


def interview_inventory(db, state):
    items = [dict(row) for row in db.execute("""SELECT s.id,s.subject,s.status,s.outline,s.created_at,s.recording_state,
        s.recorded_ms, (s.audio_path!='') AS has_audio,
        (SELECT COUNT(*) FROM utterances u WHERE u.session_id=s.id) AS utterance_count,
        EXISTS(SELECT 1 FROM reports r WHERE r.session_id=s.id) AS has_report
        FROM sessions s WHERE s.project_id=? AND s.status!='deleting' ORDER BY s.created_at,s.id""", (state["id"],)).fetchall()]
    for number, item in enumerate(items, 1):
        item["outline"] = json.loads(item["outline"] or "[]")
        item["number"] = number
        item["synced"] = any(a.get("kind") == "material" and a.get("source_session") == item["id"] for a in state["artifacts"])
        if item["status"] == "done":
            item["recording_state"] = "ended"
    return items


def _active(db, state):
    row = db.execute("SELECT status FROM tasks WHERE id=?", (state.get("job_id", ""),)).fetchone()
    return row and row["status"] in {"queued", "running", "retry"}


def _fingerprint(state):
    data = {key: state.get(key) for key in ("prompt", "scenario", "answers", "context", "location", "artifacts", "messages")}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _schedule(db, state, kind):
    if not config.KIMI_API_KEY:
        raise HTTPException(503, "请先在 backend/.env 配置 KIMI_API_KEY 并重启服务")
    if kind in {"analysis", "report"}:
        missing = [item for item in interview_inventory(db, state) if item["status"] == "done" and item["utterance_count"] and not item["synced"]]
        if missing:
            raise HTTPException(409, f"还有{len(missing)}场已结束访谈尚未同步，请到访谈页同步原始证据后再汇总")
    task_id, now = uuid.uuid4().hex, time.time()
    state["job_id"] = task_id
    payload = json.dumps({"project_id": state["id"], "job_id": task_id}, ensure_ascii=False)
    # Enqueue in the same SQLite transaction as the workspace update.
    db.execute("""INSERT INTO tasks(id,kind,owner_id,resource_id,payload,max_attempts,available_at,created_at,updated_at)
        VALUES(?,'research_platform',?,?,?,?,?,?,?)""", (task_id, LOCAL_OWNER, state["id"], payload, 1, now, now, now))
    db.execute("INSERT INTO research_workspace_jobs(id,project_id,kind,fingerprint,updated_at) VALUES(?,?,?,?,?)",
               (task_id, state["id"], kind, _fingerprint(state), now))


@router.get("/capabilities")
def capabilities():
    return {"model_configured": bool(config.KIMI_API_KEY), "model": config.KIMI_MODEL, "transcription_configured": bool(config.DASHSCOPE_API_KEY)}


@router.get("/projects/{project_id}/artifacts/{artifact_id}/export")
def export_report(project_id: str, artifact_id: str):
    from research_report import render_report
    from urllib.parse import quote
    with store.get() as db:
        state = _read(db, project_id)
    artifact = next((a for a in state["artifacts"] if a["id"] == artifact_id and a["step"] == 5), None)
    if not artifact:
        raise HTTPException(404, "没有可导出的汇总报告")
    if artifact.get("stale"):
        raise HTTPException(409, "原始材料已更新，请重新生成或审阅汇总报告后再导出")
    return Response(render_report(state["title"] + "调研整合报告", artifact["content"]),
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(state["title"] + "调研整合报告.docx")})


@router.put("/projects/{project_id}/layout")
def save_layout(project_id: str, body: Layout):
    if len(body.positions) > 7:
        raise HTTPException(422, "节点位置数量无效")
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        state = _read(db, project_id)
        current_stage = _public(db, state)['stage'] if state['scenario'] == 'interview' else state['stage']
        allowed = {"project", *(str(i) for i in range(current_stage + 1))}
        for node_id, point in body.positions.items():
            if node_id not in allowed or set(point) != {"x", "y"} or any(not math.isfinite(v) or abs(v) > 100000 for v in point.values()):
                raise HTTPException(422, "节点位置无效")
        row = db.execute("SELECT version,positions FROM research_workspace_layouts WHERE project_id=?", (project_id,)).fetchone()
        version = row["version"] if row else 0
        if version != body.version:
            raise HTTPException(409, "画布已在其他页面调整，请刷新后再拖动")
        positions = json.loads(row["positions"]) if row else {}
        positions.update(body.positions)
        db.execute("INSERT OR REPLACE INTO research_workspace_layouts VALUES(?,?,?)", (project_id, version + 1, json.dumps(positions)))
        return {"version": version + 1, "positions": positions}


@router.get("/projects/{project_id}/events")
async def project_events(project_id: str, request: Request):
    initial = project(project_id)
    if initial['scenario'] == 'interview':
        from interview_prep import workspace
        return workspace.events(project_id, request)
    async def events():
        last = None
        ticks = 0
        while not await request.is_disconnected():
            try:
                state = await run_in_threadpool(project, project_id)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                yield "event: deleted\ndata: {}\n\n"
                return
            encoded = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
            if encoded != last:
                yield "event: snapshot\ndata: " + encoded + "\n\n"
                last = encoded
            ticks += 1
            if ticks % 30 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(.5)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _read(db, project_id):
    row = db.execute("SELECT snapshot FROM research_workspace_runs WHERE project_id=? AND deleted_at=0", (project_id,)).fetchone()
    if not row:
        raise HTTPException(404, "项目不存在")
    return json.loads(row["snapshot"])


def _message(state, text, role="assistant"):
    state["messages"].append({"id": uuid.uuid4().hex, "role": role, "text": text, "at": time.time()})


def _grounded_industries(state):
    """Current model label plus original-prompt keyword matches, most specific first."""
    if state["scenario"] == "memoir":
        return []
    prompt = state["prompt"]
    candidate = state.get("known_industry", "")
    candidates = [candidate] if isinstance(candidate, str) and 1 < len(candidate) <= 64 and candidate in prompt else []
    candidates += ["新能源汽车", "电动汽车", "汽车", "半导体", "消费电子", "餐饮", "制造业", "消费零售", "互联网科技", "文旅", "农业", "食品", "房地产", "医药", "教育", "金融", "便利店", "咖啡"]
    matches = []
    for value in sorted(set(candidates), key=lambda value: (-len(value), value)):
        for match in re.finditer(re.escape(value), prompt):
            prefix = prompt[max(0, match.start() - 8):match.start()]
            if not re.search(r"(?:不是|不做|不调研|不考虑|排除|不研究)[^，。；]*$", prefix):
                matches.append(value)
                break
    return matches


def known_industry(state):
    return next(iter(_grounded_industries(state)), "")


def intake_fields(state):
    fields = [dict(field) for field in SCENARIOS[state["scenario"]]["fields"]]
    industry = known_industry(state) if state.get("mode") == "ai" else ""
    for field in fields:
        field["allow_custom"] = field["key"] in {"goal", "audience"}
        if field["key"] == "industry" and industry:
            field["label"] = "调研行业 / 产品"
            field["resolved_value"] = industry
            field["options"] = list(dict.fromkeys([industry, *field["options"]]))
    return fields


def outline_questions(markdown):
    """Extract actual questions from the saved outline, never substitute examples."""
    questions = []
    for raw in markdown.splitlines():
        if raw.lstrip().startswith(("#", ">", "```")):
            continue
        text = re.sub(r"^\s*(?:[-*+]\s+(?:\[[ xX]\]\s*)?|\d+[.、)）]\s*)", "", raw).replace("**", "").strip()
        if text and len(text) <= 200 and (re.search(r"[？?]", text) or re.match(r"^(请(?:介绍|谈|讲|描述|回忆)|能否|如何|为什么|是否)", text)) and text not in questions:
            questions.append(text)
    return questions[:80]


def _artifact(state, step, title, content, *, kind="draft", origin="demo", **metadata):
    item = {"id": uuid.uuid4().hex, "step": step, "title": title,
        "content": content, "version": 1, "versions": [], "demo": origin == "demo", "origin": origin,
        "kind": kind, "updated_at": time.time(), **metadata}
    state["artifacts"].append(item)
    return item


@router.get("/scenarios")
def scenarios():
    return SCENARIOS


@router.get("/projects")
def projects(request: Request):
    with store.get() as db:
        owner_id = getattr(request.state, "user", {}).get("id", LOCAL_OWNER)
        rows = db.execute("SELECT r.snapshot FROM research_workspace_runs r JOIN projects p ON p.id=r.project_id WHERE r.deleted_at=0 AND (?=0 OR p.owner_id=?) ORDER BY p.updated_at DESC", (config.AUTH_ENABLED, owner_id)).fetchall()
        values = []
        for row in rows:
            value = json.loads(row['snapshot'])
            if value['scenario'] == 'interview':
                from interview_prep import workspace
                try: value = workspace.public(db, value)
                except HTTPException as exc:
                    if exc.status_code == 404: continue
                    raise
            values.append(value)
    return [{**{key: value[key] for key in ("id", "title", "scenario", "stage", "updated_at")},
             "mode": value.get("mode", "demo")} for value in values]


@router.post("/projects", status_code=201)
def create_project(body: CreateProject, request: Request):
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(422, "请先写下想调研的主题")
    if body.scenario and body.scenario not in SCENARIOS:
        raise HTTPException(422, "不支持的场景")
    if body.mode not in {"ai", "demo"}:
        raise HTTPException(422, "运行模式无效")
    if body.mode == "ai" and not config.KIMI_API_KEY:
        raise HTTPException(503, "模型尚未配置，请填写 backend/.env 后重启服务")
    if body.scenario == 'interview':
        if body.mode != 'ai' or body.source_project_id: raise HTTPException(422, '面试项目使用真实准备流程')
        from interview_prep import api as interview_api, domain, workspace
        try:
            cfg = interview_api.PreparationConfig.model_validate(body.interview_config or {
                'goal': prompt, 'template': 'thesis' if any(w in prompt for w in ('论文', '答辩', '开题', '硕士', '博士')) else 'job'})
        except ValidationError:
            raise HTTPException(422, '面试配置不符合要求') from None
        owner_id = getattr(request.state, 'user', {}).get('id', LOCAL_OWNER)
        value = domain.create(owner_id, prompt[:120], body.request_id, cfg.model_dump())
        pid = value['workspace_project_id']
        with store.get() as db:
            db.execute('BEGIN IMMEDIATE')
            p, state = workspace._state(db, pid)
            workspace.enqueue_chat(db, p, state, prompt, 'initial:' + body.request_id[:72])
            return workspace.public(db, state)
    # UUID5 makes a retried first submission resolve to the same persisted project.
    project_id = uuid.uuid5(uuid.NAMESPACE_URL, "fieldwork:" + body.request_id).hex
    scenario = body.scenario or ("memoir" if any(w in prompt for w in ("口述", "回忆", "人生", "个人史", "自传", "往事", "童年", "爷爷", "奶奶", "姥姥", "姥爷", "父亲", "母亲")) else
                                "enterprise" if any(w in prompt for w in ("企业", "公司", "创始人", "高管")) else "market")
    now = time.time()
    state = {"id": project_id, "title": prompt[:60], "prompt": prompt, "scenario": scenario,
        "stage": 0, "answers": {}, "context": "", "artifacts": [], "messages": [],
        "revision": 1, "updated_at": now, "mode": body.mode, "location": {}, "scenario_confirmed": bool(body.scenario)}
    _message(state, prompt, "user")
    if body.mode == "demo":
        _message(state, f"我们从这次{SCENARIOS[scenario]['noun']}的目标开始。我建议采用「{SCENARIOS[scenario]['name']}」流程，你也可以切换场景。先选择几个关注方向，我会把它们整理到中央画布。")
    owner_id = getattr(request.state, "user", {}).get("id", LOCAL_OWNER)
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT snapshot,deleted_at FROM research_workspace_runs WHERE project_id=?", (project_id,)).fetchone()
        if old:
            if old["deleted_at"]:
                raise HTTPException(410, "项目已删除，请新建项目")
            if config.AUTH_ENABLED:
                owner = db.execute("SELECT owner_id FROM projects WHERE id=?", (project_id,)).fetchone()
                if owner and owner["owner_id"] != owner_id:
                    raise HTTPException(404, "项目不存在")
            return _public(db, json.loads(old["snapshot"]))
        if body.source_project_id:
            if config.AUTH_ENABLED:
                owner = db.execute("SELECT owner_id FROM projects WHERE id=?", (body.source_project_id,)).fetchone()
                if not owner or owner["owner_id"] != owner_id:
                    raise HTTPException(404, "来源项目不存在")
            source = _read(db, body.source_project_id)
            if body.mode != "ai" or source.get("mode", "demo") != "demo":
                raise HTTPException(422, "仅支持从历史示例新建真实 AI 调研")
            if source["scenario"] != scenario or source["prompt"] != prompt:
                raise HTTPException(422, "请沿用原项目的主题与场景")
            # Copy only user inputs. Example messages, files and research are never evidence.
            state.update(answers=source["answers"], custom_answers=source.get("custom_answers", {}), context=source["context"],
                         location=source.get("location", {}), source_project_id=source["id"])
        db.execute("INSERT INTO projects(id,name,owner_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                   (project_id, prompt[:60] + " · " + project_id[:6], owner_id, now, now))
        db.execute("INSERT INTO research_workspace_runs(project_id,revision,snapshot) VALUES(?,?,?)", (project_id, 1, json.dumps(state, ensure_ascii=False)))
        if body.mode == "ai" and scenario != "memoir":
            _schedule(db, state, "intake")
            db.execute("UPDATE research_workspace_runs SET snapshot=? WHERE project_id=?", (json.dumps(state, ensure_ascii=False), project_id))
        return _public(db, state)


@router.get("/projects/{project_id}")
def project(project_id: str):
    with store.get() as db:
        # Keep snapshot revision, task status and interview list in one read view.
        # Otherwise a just-finished task can be paired with the previous revision.
        db.execute("BEGIN")
        return _public(db, _read(db, project_id))


@router.delete("/projects/{project_id}")
def delete_project(project_id: str):
    # Keep a tombstone to stop stale create retries and workers resurrecting deleted projects.
    # Original interview sessions and their recordings have a separate lifecycle.
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT deleted_at FROM research_workspace_runs WHERE project_id=?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "项目不存在")
        now = time.time()
        db.execute("UPDATE research_workspace_runs SET deleted_at=? WHERE project_id=? AND deleted_at=0", (now, project_id))
        db.execute("UPDATE tasks SET status='cancelled',stage='cancelled',updated_at=?,finished_at=? "
                   "WHERE kind IN ('research_platform','conversation_reply','conversation_archive','conversation_book') AND resource_id=? AND status IN ('queued','running','retry')",
                   (now, now, project_id))
        db.execute("DELETE FROM research_workspace_layouts WHERE project_id=?", (project_id,))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='interview_preparations'").fetchone():
            prep = db.execute('SELECT id FROM interview_preparations WHERE workspace_project_id=?', (project_id,)).fetchone()
            if prep:
                db.execute('UPDATE interview_preparations SET deleted_at=? WHERE id=?', (now, prep['id']))
                owner = db.execute('SELECT owner_id FROM projects WHERE id=?', (project_id,)).fetchone()['owner_id']
                db.execute("UPDATE tasks SET status='cancelled',stage='cancelled',updated_at=?,finished_at=? WHERE owner_id=? AND resource_id IN (?,?) AND status IN ('queued','running','retry')", (now, now, owner, project_id, prep['id']))
                for table in ('workspace_chat_messages', 'workspace_chat_runs', 'workspace_events'):
                    db.execute(f'DELETE FROM {table} WHERE project_id=?', (project_id,))
        # 新派生内容不随逻辑删除继续可检索；原始对谈仍按现有生命周期保留。
        tables = {r["name"] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("memoir_agent_memory", "memoir_agent_plans", "memoir_agent_runs", "memoir_agent_diagnostics", "memoir_books", "memoir_book_versions", "memoir_book_steps", "memoir_book_settings"):
            if table in tables:
                if table == "memoir_agent_memory" and "memoir_memory_versions" in tables:
                    db.execute("DELETE FROM memoir_memory_versions WHERE memory_id IN (SELECT id FROM memoir_agent_memory WHERE project_id=?)", (project_id,))
                db.execute(f"DELETE FROM {table} WHERE project_id=?", (project_id,))
    return {"id": project_id, "deleted": True}


@router.post("/projects/{project_id}/commands")
def command(project_id: str, body: Command, request: Request):
    with store.get() as db:
        state = _read(db, project_id)
        interview = state['scenario'] == 'interview'
    if interview:
        from interview_prep import workspace, api as interview_api
        if body.scene_command is not None:
            with store.get() as db: prep = workspace.preparation(db, project_id)
            # 复用原端点的音频上传锁、校对确认和原音拼装，不能绕过逐题状态机。
            try: command_body = interview_api.Command.model_validate(body.scene_command)
            except ValidationError: raise HTTPException(422, '面试操作参数不符合要求') from None
            interview_api.command(prep['id'], command_body, request)
            return project(project_id)
        with store.get() as db:
            db.execute('BEGIN IMMEDIATE')
            state = _read(db, project_id)
            return workspace.chat_command(db, state, body)
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        state = _read(db, project_id)
        if state["revision"] != body.revision:
            raise HTTPException(409, "项目已在其他页面更新，请刷新后重试")
        spec = SCENARIOS[state["scenario"]]
        ai = state.get("mode") == "ai"
        if body.action == "cancel":
            if _active(db, state):
                db.execute("UPDATE tasks SET status='cancelled',stage='cancelled',updated_at=?,finished_at=? WHERE id=?", (time.time(), time.time(), state["job_id"]))
            _save(db, state)
            return _public(db, state)
        if _active(db, state):
            raise HTTPException(409, "AI 正在处理，请等待完成或取消当前任务")
        if body.action == "retry":
            old = db.execute("SELECT j.kind,t.status FROM research_workspace_jobs j JOIN tasks t ON j.id=t.id WHERE j.id=?", (state.get("job_id", ""),)).fetchone()
            if not ai or not old or old["status"] not in {"failed", "cancelled"}:
                raise HTTPException(409, "没有可重试的任务")
            _schedule(db, state, old["kind"])
            _save(db, state)
            return _public(db, state)
        if body.action == "scenario":
            if body.scenario not in SCENARIOS:
                raise HTTPException(422, "不支持的场景")
            if body.scenario == 'interview':
                raise HTTPException(409, '面试使用专业练习流程，请新建面试项目；现有资料保持不变')
            if state["stage"] != 0:
                raise HTTPException(409, "已有成果的项目请通过新建项目更换场景")
            state.update(scenario=body.scenario, answers={})
            state["custom_answers"] = {}
            state.pop("checklist", None)
            state["scenario_confirmed"] = True
            _message(state, f"已切换为「{SCENARIOS[body.scenario]['name']}」。选择项与流程已同步更新。")
        elif body.action in {"draft", "brief"}:
            answers = {key: list(values) for key, values in (body.answers or {}).items()}
            fields = intake_fields(state)
            industry = known_industry(state) if ai else ""
            if industry and not answers.get("industry"):
                answers["industry"] = [industry]
            allowed_keys = {field["key"] for field in fields}
            if set(answers) - allowed_keys:
                raise HTTPException(422, "包含未知选择项")
            if set(body.custom_answers) - {"goal", "audience"}:
                raise HTTPException(422, "自定义选项字段无效")
            custom_answers = {}
            for field in fields:
                selected = answers.get(field["key"], [])
                # The first snapshot can resolve a broad keyword before the model
                # refines it. Accept only grounded single-value aliases and store
                # the current canonical label; unrelated/multiple values stay invalid.
                if field["key"] == "industry" and industry and len(selected) == 1 and selected[0] in _grounded_industries(state):
                    selected = [industry]
                custom = body.custom_answers.get(field["key"], "").strip()
                if "其他" in selected:
                    if not field["allow_custom"] or not custom or len(custom) > 160 or any(ord(char) < 32 for char in custom):
                        raise HTTPException(422, "请填写 1 到 160 字的其他目的或对象")
                    selected = [custom if value == "其他" else value for value in selected]
                    custom_answers[field["key"]] = custom
                previous_custom = state.get("custom_answers", {}).get(field["key"])
                allowed = [*field["options"], *([custom_answers[field["key"]]] if field["key"] in custom_answers else []), *([previous_custom] if previous_custom else [])]
                if previous_custom and previous_custom in selected:
                    custom_answers[field["key"]] = previous_custom
                if len(selected) != len(set(selected)) or any(value not in allowed for value in selected) or (not field["multiple"] and len(selected) > 1):
                    raise HTTPException(422, f"「{field['label']}」选择项不合法，请重新选择")
                answers[field["key"]] = selected
                if body.action == "brief" and not selected:
                    raise HTTPException(422, "请先补齐调研方向的选择")
            if state["stage"] != 0:
                raise HTTPException(409, "调研目标已确认")
            state.update(answers=answers, custom_answers=custom_answers, context=body.context.strip())
            if body.location is not None:
                if set(body.location) - {"city", "market"} or any(len(v) > 120 or "\n" in v or "\r" in v for v in body.location.values()):
                    raise HTTPException(422, "地点字段无效")
                state["location"] = {k: v.strip() for k, v in body.location.items()}
            if body.action == "brief":
                details = "\n".join(f"- {field['label']} {'、'.join(answers[field['key']])}" for field in fields)
                if ai:
                    _message(state, "已确认调研目标与范围：\n" + details + ("\n补充：" + state["context"] if state["context"] else ""), "user")
                    _artifact(state, 0, "调研目标与范围" if state["scenario"] != "memoir" else "讲述目标确认", f"# {state['title']}\n\n{details}\n\n补充：{state['context']}\n\n地点：{json.dumps(state.get('location', {}), ensure_ascii=False)}", origin="user")
                    state["stage"] = 1
                    _schedule(db, state, "background")
                    _save(db, state)
                    return _public(db, state)
                _artifact(state, 0, "调研目标与范围" if state["scenario"] != "memoir" else "讲述目标确认", f"# {state['title']}\n\n## 目标与范围\n{details}\n\n## 补充信息\n{state['context'] or '暂未补充，可在后续访谈中进一步明确。'}\n\n## 执行方式\n采用{spec['name']}流程。当前为流程示例，尚未执行联网研究或模型分析。")
                _artifact(state, 1, spec["steps"][1] + " · 示例草稿", _background(state))
                state["stage"] = 1
                _message(state, "调研目标与研究范围已整理。我已生成一份研究方案示例，并挂到画布上。你可以打开查看、修改，确认后继续生成提纲示例。\n\n当前尚未执行真实联网检索，示例内容不作为调研事实。")
        elif body.action == "advance":
            if ai:
                if state["stage"] == 1:
                    if not any(a["step"] == 1 and a.get("kind") != "document" and a.get("origin") == "ai" and not a.get("stale") for a in state["artifacts"]):
                        raise HTTPException(409, "请先完成并审阅研究方案")
                    state["stage"] = 2
                    _schedule(db, state, "outline")
                elif state["stage"] == 2:
                    if not any(a["step"] == 2 and a.get("kind") != "document" and a.get("origin") == "ai" and not a.get("stale") for a in state["artifacts"]):
                        raise HTTPException(409, "请先完成提纲")
                    state["stage"] = 3
                    _message(state, "提纲已确认。现在可以粘贴或导入 TXT / Markdown 原始材料，保存后生成基于原文的分析。不要把 AI 提纲当作访谈材料。")
                    state["messages"][-1]["step"] = 3
                elif state["stage"] == 3:
                    if not any(a.get("kind") == "material" for a in state["artifacts"]):
                        raise HTTPException(422, "请先添加原始访谈材料")
                    state["stage"] = 4
                    _schedule(db, state, "analysis")
                elif state["stage"] == 4:
                    if not any(a["step"] == 4 and a.get("kind") != "document" and a.get("origin") == "ai" and not a.get("stale") for a in state["artifacts"]):
                        raise HTTPException(409, "请先完成材料分析")
                    state["stage"] = 5
                    _schedule(db, state, "report")
                elif state["stage"] == 5:
                    if not any(a["step"] == 5 and a.get("kind") != "document" and not a.get("stale") for a in state["artifacts"]):
                        raise HTTPException(409, "请先完成报告草稿")
                    state["completed"] = True
                    _message(state, "已按你的确认归档。报告、原始材料和版本保存在本项目中，可以继续查看或导出。")
                _save(db, state)
                return _public(db, state)
            if state["stage"] == 1:
                state["stage"] = 2
                _artifact(state, 2, spec["steps"][2] + " · 示例草稿", _outline(state))
                _message(state, "提纲示例已准备好，包含开场、主题问题与追问。点击画布上的文件即可编辑。确认提纲后，我们进入采集准备。")
            elif state["stage"] == 2:
                state["stage"] = 3
                _artifact(state, 3, "访谈准备清单", "# 访谈准备清单\n\n- 确认访谈对象、地点和预计时长。\n- 说明访谈目的并取得录音同意。\n- 检查提纲与设备电量。\n- 记录现场观察与需要核实的事实。\n\n## 采集能力\n此项目是历史流程示例，不启动录音或生成访谈事实。新建研究项目后，可使用访谈录音、资料导入及洞察分析。")
                _message(state, "已进入采集准备，提纲与准备清单会保存在这个项目里。第一轮先完成平台骨架，录音、手机接续与真实材料分析尚未接入。你可以继续编辑或导出当前成果。")
            else:
                raise HTTPException(409, "当前阶段暂无可自动执行的下一步")
        elif body.action == "select_interview":
            if not db.execute("SELECT 1 FROM sessions WHERE id=? AND project_id=? AND status!='deleting'", (body.session_id, project_id)).fetchone():
                raise HTTPException(404, "本项目中没有这场访谈")
            state["active_session_id"] = body.session_id
        elif body.action == "start_interview":
            outline = next((a for a in reversed(state["artifacts"]) if a["step"] == 2 and a.get("kind") not in {"question_selection", "document"}), None)
            if not ai or state["stage"] < 2 or not outline or outline.get("stale"):
                raise HTTPException(409, "请先完成并审阅有效的访谈提纲")
            if body.outline_version != outline["version"]:
                raise HTTPException(409, "提纲已更新，请重新勾选问题")
            choices = body.selected_questions
            allowed = outline_questions(outline["content"])
            if not choices or len(set(choices)) != len(choices) or any(q not in allowed for q in choices):
                raise HTTPException(422, "请从实际提纲中至少勾选一个问题")
            if len(body.request_id) < 8:
                raise HTTPException(422, "缺少本场访谈请求编号")
            sid = uuid.uuid5(uuid.NAMESPACE_URL, f"interview:{project_id}:{body.request_id}").hex[:12]
            existing = db.execute("SELECT id,project_id FROM sessions WHERE id=?", (sid,)).fetchone()
            if existing and existing["project_id"] != project_id:
                raise HTTPException(409, "访谈编号冲突，请重试")
            if not existing:
                audience = (state["answers"].get("audience") or [""])[0]
                itype = {"商户 / 店主": "商户", "企业负责人": "企业家", "园区 / 协会人员": "政府/协会人士",
                         "创始人 / 企业家": "企业家", "管理层": "企业高管", "员工": "一线员工"}.get(audience, audience)
                if itype not in {"商户", "企业家", "企业高管", "一线员工", "行业专家", "投资人", "政府/协会人士", "消费者"}:
                    itype = "消费者" if state["scenario"] == "memoir" else "企业高管"
                industry = (state["answers"].get("industry") or [""])[0]
                if industry not in {"金融业", "汽车业", "制造业", "食品饮料", "消费零售", "互联网科技", "房地产建筑", "能源化工", "医药健康", "农业", "交通物流", "文化传媒", "教育服务"}:
                    industry = "综合/不限"
                db.execute("""INSERT INTO sessions(id,subject,scene,interviewee_type,industry,focus,user_name,
                    outline,status,created_at,subject_source,owner_id,location,project_id,project_name)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (sid, state["title"][:80], spec["name"], itype, industry,
                    "、".join(state["answers"].get("goal", []))[:500], "本地使用者", json.dumps(choices, ensure_ascii=False),
                    "live", time.time(), "user", db.execute("SELECT owner_id FROM projects WHERE id=?", (project_id,)).fetchone()["owner_id"], json.dumps(state.get("location", {}), ensure_ascii=False), project_id, state["title"][:80]))
                number = db.execute("SELECT COUNT(*) FROM sessions WHERE project_id=? AND status!='deleting'", (project_id,)).fetchone()[0]
                label = body.title.strip()[:80] or f"第{number}场访谈"
                db.execute("UPDATE sessions SET subject=? WHERE id=?", (label, sid))
                db.execute("INSERT INTO suggestions(session_id,kind,payload,created_at) VALUES(?,?,?,?)",
                           (sid, "playbook", json.dumps(playbook.opening_payload(itype), ensure_ascii=False), time.time()))
                _artifact(state, 2, "本场访谈问题清单", "# 本场访谈问题\n\n" + "\n".join("- " + q for q in choices),
                          origin="user", kind="question_selection", source_session=sid, outline_version=outline["version"])
                _message(state, f"已确认本场 {len(choices)} 个问题，现场录音页已准备好。也可以导入已有录音或文字稿。")
                state["messages"][-1]["step"] = 3
            state.update(active_session_id=sid, stage=max(3, state["stage"]), completed=False)
        elif body.action == "sync_session":
            session = db.execute("SELECT * FROM sessions WHERE id=? AND project_id=? AND status!='deleting'", (body.session_id, project_id)).fetchone()
            if not ai or not session:
                raise HTTPException(404, "本项目中没有这场访谈")
            if session["status"] != "done":
                raise HTTPException(409, "请先结束访谈或等待材料完成归档")
            rows = db.execute("SELECT speaker,text FROM utterances WHERE session_id=? ORDER BY seq,id", (body.session_id,)).fetchall()
            content = "\n".join((row["speaker"] + "：" if row["speaker"] and not row["text"].startswith((row["speaker"] + "：", row["speaker"] + ":")) else "") + row["text"] for row in rows if row["text"].strip())
            if not content:
                raise HTTPException(409, "尚无可同步的原始文字，请等待转写完成")
            existing = next((a for a in state["artifacts"] if a.get("kind") == "material" and a.get("source_session") == body.session_id), None)
            total = sum(len(a["content"]) for a in state["artifacts"] if a.get("kind") == "material" and a is not existing)
            if len(content) > 20000 or total + len(content) > 50000:
                raise HTTPException(422, "逐字稿超过工作台材料容量，请分段导入；完整原文仍保存在访谈记录中")
            digest = hashlib.sha256(content.encode()).hexdigest()
            changed = not existing or existing.get("source_digest") != digest
            if changed:
                if existing:
                    if hashlib.sha256(existing["content"].encode()).hexdigest() != existing.get("source_digest"):
                        raise HTTPException(409, "工作台原文已人工编辑，请先导出备份，再分开导入更新后的逐字稿")
                    existing["versions"].append({"version": existing["version"], "content": existing["content"], "at": existing["updated_at"]})
                    existing.update(content=content, version=existing["version"] + 1, updated_at=time.time(), source_digest=digest)
                else:
                    _artifact(state, 3, session["subject"] + " · 原始逐字稿", content, kind="material", origin="user", source_session=body.session_id, source_digest=digest)
                for item in state["artifacts"]:
                    if item["step"] >= 4:
                        item["stale"] = True
                state.update(stage=max(3, state["stage"]), completed=False)
                _message(state, "访谈原始逐字稿已同步到画布，可据此继续分析；AI 纪要不作为原始访谈证据。")
                state["messages"][-1]["step"] = 3
            report = db.execute("SELECT markdown FROM reports WHERE session_id=?", (body.session_id,)).fetchone()
            if session["audio_path"] and not any(a.get("kind") == "recording" and a.get("source_session") == body.session_id for a in state["artifacts"]):
                _artifact(state, 3, session["subject"] + " · 原始录音", "# 原始录音\n\n本场实际保存的录音，可在此回听或下载。逐字稿单独保存为分析材料。",
                          origin="user", kind="recording", source_session=body.session_id)
            if report and report["markdown"]:
                memo = next((a for a in state["artifacts"] if a.get("kind") == "session_report" and a.get("source_session") == body.session_id), None)
                if not memo:
                    _artifact(state, 3, session["subject"] + " · 访谈纪要", report["markdown"], origin="ai", kind="session_report", source_session=body.session_id)
            state["active_session_id"] = body.session_id
        elif body.action == "material":
            content = body.content.strip()
            if not ai or state["stage"] < 1 or not content or len(content) > 20000:
                raise HTTPException(422, "请确认调研目标后添加 1 到 20000 字的原始文字材料")
            if sum(len(a["content"]) for a in state["artifacts"] if a.get("kind") == "material") + len(content) > 50000:
                raise HTTPException(422, "本项目原始材料总量上限为 50000 字，请拆分项目")
            _artifact(state, 3, body.title.strip() or f"原始材料 {sum(a.get('kind') == 'material' for a in state['artifacts']) + 1}", content, kind="material", origin="user")
            state["stage"] = max(3, state["stage"])
            for item in state["artifacts"]:
                if item["step"] >= 4:
                    item["stale"] = True
            state["completed"] = False
            _message(state, "原始材料已保存。它将作为分析和报告的事实来源，引用会定位到材料段落。")
        elif body.action == "regenerate_report":
            if not ai or state["stage"] < 4 or not any(a.get("kind") == "material" for a in state["artifacts"]):
                raise HTTPException(409, "请先完成原始材料分析")
            if not any(a["step"] == 4 and not a.get("stale") and a.get("kind") != "document" for a in state["artifacts"]):
                raise HTTPException(409, "原始材料有更新，请先重新分析后再汇总")
            state.update(stage=5, completed=False)
            for item in state["artifacts"]:
                if item["step"] == 5:
                    item["stale"] = True
            _schedule(db, state, "report")
        elif body.action == "regenerate":
            if not ai or state["stage"] < 4 or not any(a.get("kind") == "material" for a in state["artifacts"]):
                raise HTTPException(409, "请先添加原始材料")
            state["stage"] = 4
            state["completed"] = False
            for item in state["artifacts"]:
                if item["step"] >= 4:
                    item["stale"] = True
            _schedule(db, state, "analysis")
        elif body.action == "artifact":
            item = next((a for a in state["artifacts"] if a["id"] == body.artifact_id), None)
            if not item:
                raise HTTPException(404, "成果不存在")
            if not body.content.strip():
                raise HTTPException(422, "成果正文不能为空")
            item["versions"].append({"version": item["version"], "content": item["content"], "at": item["updated_at"]})
            item.update(content=body.content, version=item["version"] + 1, updated_at=time.time())
            if ai:
                state["completed"] = False
            if item.get("kind") == "material" and (len(body.content) > 20000 or sum(len(a["content"]) for a in state["artifacts"] if a.get("kind") == "material") > 50000):
                raise HTTPException(422, "原始材料超过本项目处理上限")
            for downstream in state["artifacts"]:
                if downstream["step"] > item["step"]:
                    downstream["stale"] = True
        elif body.action == "message":
            text = body.content.strip()
            if not text or len(text) > 2000:
                raise HTTPException(422, "消息长度应在 1 到 2000 字之间")
            _message(state, text, "user")
            if body.selected_step is not None:
                if body.selected_step > state["stage"]:
                    raise HTTPException(422, "所选模块尚未进入")
                state["messages"][-1]["selected_step"] = body.selected_step
            if ai:
                _schedule(db, state, "chat")
            else:
                _message(state, "我已记录这条补充。第一轮暂不接通自由对话模型；你可以通过上方选择卡片确认研究范围，或打开成果直接编辑。真实 Agent 对话将在下一轮接入。")
        else:
            raise HTTPException(422, "不支持的操作")
        _save(db, state)
        return _public(db, state)


def execute_ai_job(payload, ctx):
    import platform_ai
    job_id, project_id = payload["job_id"], payload["project_id"]
    init()
    with store.get() as db:
        state = _read(db, project_id)
        state["interview_inventory"] = interview_inventory(db, state)
        job = dict(db.execute("SELECT * FROM research_workspace_jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone())
    if state.get("job_id") != job_id:
        raise ValueError("任务已被替代")
    if any(a.get("job_id") == job_id for a in state["artifacts"]) or any(m.get("job_id") == job_id for m in state["messages"]):
        return {"project_id": project_id, "cached": True}
    if _fingerprint(state) != job["fingerprint"]:
        raise ValueError("生成输入已变更")
    ctx.check_cancelled()
    output, last_flush = [], 0.0
    def flush(force=False):
        nonlocal last_flush
        if not force and time.monotonic() - last_flush < .35:
            return
        with store.get() as db:
            ctx.check_cancelled(db)
            db.execute("UPDATE research_workspace_jobs SET output=?,updated_at=? WHERE id=?", ("".join(output), time.time(), job_id))
        last_flush = time.monotonic()
    def on_text(text):
        ctx.check_cancelled()
        output.append(text)
        flush()
    try:
        ctx.progress("正在梳理调研目标" if job["kind"] == "intake" else "正在准备模型上下文", .05)
        result = platform_ai.generate(job["kind"], state, ctx, on_text)
        flush(True)
        with store.get() as db:
            db.execute("BEGIN IMMEDIATE")
            ctx.check_cancelled(db)
            current = _read(db, project_id)
            if current.get("job_id") != job_id or _fingerprint(current) != job["fingerprint"]:
                raise ValueError("生成输入已变更")
            if job["kind"] == "intake":
                data = result["intake"]
                if not current.get("scenario_confirmed"):
                    current["scenario"] = data["scenario"]
                current["checklist"] = SCENARIOS[current["scenario"]]["fields"]
                candidate = data.get("industry")
                if isinstance(candidate, str) and 1 < len(candidate.strip()) <= 64 and candidate.strip() in current["prompt"]:
                    current["known_industry"] = candidate.strip()
                industry = known_industry(current)
                if industry and not current["answers"].get("industry"):
                    current["answers"]["industry"] = [industry]
                current["checklist"] = intake_fields(current)
                title = data.get("title")
                if isinstance(title, str) and title.strip():
                    current["title"] = title.strip()[:60]
                location = data.get("location")
                if isinstance(location, dict):
                    current["location"] = {k: str(location.get(k, ""))[:120] for k in ("city", "market")}
                _message(current, data["reply"])
                current["messages"][-1]["checklist"] = current["checklist"]
            elif job["kind"] == "chat":
                document = result.get("document")
                if document:
                    step, title = document.get("step"), document.get("title")
                    if type(step) is not int or not 0 <= step <= current["stage"] or not isinstance(title, str) or not title.strip() or len(title) > 80:
                        raise ValueError("invalid_document_result")
                    if not isinstance(result.get("content"), str) or not result["content"].strip() or len(result["content"]) > 60000:
                        raise ValueError("invalid_document_content")
                    _artifact(current, step, title.strip(), result["content"], origin="ai", kind="document", job_id=job_id,
                              source_ids=result.get("source_ids", []), tool_name="create_document")
                _message(current, result["content"])
                if document:
                    current["messages"][-1].update(artifact_id=current["artifacts"][-1]["id"], document_created=document)
            else:
                steps = {"background": 1, "outline": 2, "analysis": 4, "report": 5}
                step = steps[job["kind"]]
                _artifact(current, step, SCENARIOS[current["scenario"]]["steps"][step] + " · AI 草稿", result["content"], origin="ai", job_id=job_id, source_ids=result["source_ids"])
                if job["kind"] == "background":
                    current["research"] = result["research"]
                _message(current, result["content"])
                current["messages"][-1]["artifact_id"] = current["artifacts"][-1]["id"]
            current["messages"][-1]["job_id"] = job_id
            _save(db, current)
        return {"project_id": project_id}
    except (job_queue.TaskCancelled, job_queue.LeaseLost):
        raise
    except Exception as exc:
        error = platform_ai.safe_error(exc)
        with store.get() as db:
            ctx.check_cancelled(db)
            db.execute("UPDATE research_workspace_jobs SET error=?,updated_at=? WHERE id=?", (error, time.time(), job_id))
        raise RuntimeError(error) from None


def _background(state):
    goals = "、".join(state["answers"]["goal"])
    if state["scenario"] == "memoir":
        return f"# 人生时间线 · 准备草稿\n\n> 流程示例，尚未采集人物原始讲述。\n\n## 本次讲述\n{state['prompt']}\n\n## 主题范围\n{goals}\n\n## 建议的时间线\n1. 童年与故乡：家庭、生活环境和最早的记忆。\n2. 求学与成长：重要的人、选择与转折。\n3. 工作与家庭：亲历的事件及当时的感受。\n4. 回望与寄语：希望被记住的故事。\n\n## 待补充资料\n具体年份、人物关系、地点与可供印证的照片或文稿均待本人确认。\n\n## 来源\n仅依据本项目需求整理，没有调用外部来源。"
    return f"# {SCENARIOS[state['scenario']]['steps'][1]} · 准备草稿\n\n> 流程示例，未执行联网检索，不能作为事实背景。\n\n## 调研主题\n{state['prompt']}\n\n## 研究问题\n{goals}\n\n## 待验证假设\n围绕上述研究问题收集真实经历与行为证据；目前尚未验证任何需求或原因。\n\n## 建议准备的资料\n1. 地区或企业的公开基础信息与近期变化。\n2. 行业发展、经营环境与主要参与者。\n3. 与关注主题有关的政策、公开数据和既有研究。\n\n## 研究方法与执行安排\n明确目标人群与招募标准，记录具体场景、现有做法与痛点。先核对公开资料，再进行多对象访谈，最后交叉比较信息。将来源、发布日期和待核实事项分别记录。\n\n## 来源与待核实事项\n本轮未联网。地区数据、企业事实和行业判断都需要后续检索或访谈证实。"


def _outline(state):
    topics = state["answers"]["goal"]
    lines = [f"# {SCENARIOS[state['scenario']]['steps'][2]}", "> 根据选择项整理的流程示例，正式使用前请审阅。", "## 开场", "请简单介绍一下自己，以及与本次主题的关系。", "## 主题问题"]
    for i, topic in enumerate(topics, 1):
        question = f"围绕「{topic}」，您最想讲述哪段经历？" if state["scenario"] == "memoir" else f"围绕「{topic}」，您观察到哪些变化或具体问题？"
        lines += [f"### {i}. {topic}", question, "追问：可以举一个亲历的例子吗？当时发生了什么，后来有什么变化？"]
    lines += ["## 收尾", "还有哪些重要内容没有谈到？哪些细节需要后续核实或补充？", "## 现场记录", "记录原话、时间与上下文，区分当事人讲述和采访者判断。"]
    return "\n\n".join(lines)
