"""Unauthenticated local research workspace, phase one.

Projects use the existing projects table. Workspace snapshots are versioned to
reject stale browser writes. Demonstration artifacts never call cloud services.
"""
import json
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import store

router = APIRouter(prefix="/api/platform", tags=["research-platform"])
LOCAL_OWNER = -1

SCENARIOS = {
    "market": {
        "name": "市场调研", "description": "走进市场，看见真实需求", "noun": "调研",
        "steps": ["需求确认", "背景与计划", "调研提纲", "现场采集", "综合分析", "成果归档"],
        "fields": [
            {"key": "industry", "label": "关注哪个行业？", "options": ["综合 / 暂不限定", "消费零售", "制造业", "文旅服务", "农业与食品", "互联网科技"], "multiple": False},
            {"key": "goal", "label": "这次调研希望了解什么？", "options": ["了解市场现状", "发现经营问题", "寻找增长机会", "比较区域差异", "验证一个想法"], "multiple": True},
            {"key": "audience", "label": "准备和谁聊一聊？", "options": ["商户 / 店主", "消费者", "企业负责人", "行业专家", "园区 / 协会人员"], "multiple": True},
        ],
    },
    "enterprise": {
        "name": "企业访谈", "description": "通过对话，理解一家企业", "noun": "访谈",
        "steps": ["访谈目标", "企业背景", "访谈提纲", "访谈采集", "纪要与洞察", "成果归档"],
        "fields": [
            {"key": "industry", "label": "企业属于哪个行业？", "options": ["综合 / 暂不限定", "制造业", "消费零售", "互联网科技", "专业服务", "能源与环保"], "multiple": False},
            {"key": "goal", "label": "最想理解企业的哪些方面？", "options": ["业务与商业模式", "经营挑战", "组织与人才", "数字化转型", "发展战略"], "multiple": True},
            {"key": "audience", "label": "这次主要访谈谁？", "options": ["创始人 / 企业家", "企业高管", "业务负责人", "一线员工", "合作伙伴"], "multiple": True},
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


def init():
    with store.get() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS research_workspace_runs(
            project_id TEXT PRIMARY KEY REFERENCES projects(id),
            revision INTEGER NOT NULL, snapshot TEXT NOT NULL
        )""")


class CreateProject(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    scenario: str | None = None
    request_id: str = Field(min_length=8, max_length=80)


class Command(BaseModel):
    revision: int = Field(ge=1)
    action: str
    scenario: str | None = None
    answers: dict[str, list[str]] | None = None
    context: str = Field(default="", max_length=1000)
    content: str = Field(default="", max_length=100000)
    artifact_id: str | None = None


def _read(db, project_id):
    row = db.execute("SELECT snapshot FROM research_workspace_runs WHERE project_id=?", (project_id,)).fetchone()
    if not row:
        raise HTTPException(404, "项目不存在")
    return json.loads(row["snapshot"])


def _message(state, text, role="assistant"):
    state["messages"].append({"id": uuid.uuid4().hex, "role": role, "text": text, "at": time.time()})


def _artifact(state, step, title, content):
    state["artifacts"].append({"id": uuid.uuid4().hex, "step": step, "title": title,
        "content": content, "version": 1, "versions": [], "demo": True, "updated_at": time.time()})


@router.get("/scenarios")
def scenarios():
    return SCENARIOS


@router.get("/projects")
def projects():
    with store.get() as db:
        rows = db.execute("SELECT r.snapshot FROM research_workspace_runs r JOIN projects p ON p.id=r.project_id ORDER BY p.updated_at DESC").fetchall()
    values = [json.loads(row["snapshot"]) for row in rows]
    return [{key: value[key] for key in ("id", "title", "scenario", "stage", "updated_at")} for value in values]


@router.post("/projects", status_code=201)
def create_project(body: CreateProject):
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(422, "请先写下想调研的主题")
    if body.scenario and body.scenario not in SCENARIOS:
        raise HTTPException(422, "不支持的场景")
    # UUID5 makes a retried first submission resolve to the same persisted project.
    project_id = uuid.uuid5(uuid.NAMESPACE_URL, "fieldwork:" + body.request_id).hex
    scenario = body.scenario or ("memoir" if any(w in prompt for w in ("口述", "回忆", "人生", "爷爷", "奶奶", "父亲", "母亲")) else
                                "enterprise" if any(w in prompt for w in ("企业", "公司", "创始人", "高管")) else "market")
    now = time.time()
    state = {"id": project_id, "title": prompt[:60], "prompt": prompt, "scenario": scenario,
        "stage": 0, "answers": {}, "context": "", "artifacts": [], "messages": [],
        "revision": 1, "updated_at": now}
    _message(state, prompt, "user")
    _message(state, f"我们从这次{SCENARIOS[scenario]['noun']}的目标开始。我建议采用「{SCENARIOS[scenario]['name']}」流程，你也可以切换场景。先选择几个关注方向，我会把它们整理到中央画布。")
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT snapshot FROM research_workspace_runs WHERE project_id=?", (project_id,)).fetchone()
        if old:
            return json.loads(old["snapshot"])
        db.execute("INSERT INTO projects(id,name,owner_id,created_at,updated_at) VALUES(?,?,?,?,?)",
                   (project_id, prompt[:60] + " · " + project_id[:6], LOCAL_OWNER, now, now))
        db.execute("INSERT INTO research_workspace_runs VALUES(?,?,?)", (project_id, 1, json.dumps(state, ensure_ascii=False)))
    return state


@router.get("/projects/{project_id}")
def project(project_id: str):
    with store.get() as db:
        return _read(db, project_id)


@router.post("/projects/{project_id}/commands")
def command(project_id: str, body: Command):
    with store.get() as db:
        db.execute("BEGIN IMMEDIATE")
        state = _read(db, project_id)
        if state["revision"] != body.revision:
            raise HTTPException(409, "项目已在其他页面更新，请刷新后重试")
        spec = SCENARIOS[state["scenario"]]
        if body.action == "scenario":
            if body.scenario not in SCENARIOS:
                raise HTTPException(422, "不支持的场景")
            if state["stage"] != 0:
                raise HTTPException(409, "已有成果的项目请通过新建项目更换场景")
            state.update(scenario=body.scenario, answers={})
            _message(state, f"已切换为「{SCENARIOS[body.scenario]['name']}」。选择项与流程已同步更新。")
        elif body.action in {"draft", "brief"}:
            answers = body.answers or {}
            allowed_keys = {field["key"] for field in spec["fields"]}
            if set(answers) - allowed_keys:
                raise HTTPException(422, "包含未知选择项")
            for field in spec["fields"]:
                selected = answers.get(field["key"], [])
                if len(selected) != len(set(selected)) or any(value not in field["options"] for value in selected) or (not field["multiple"] and len(selected) > 1):
                    raise HTTPException(422, "选择项不合法")
                if body.action == "brief" and not selected:
                    raise HTTPException(422, "请先补齐三个方向的选择")
            if state["stage"] != 0:
                raise HTTPException(409, "需求已确认")
            state.update(answers=answers, context=body.context.strip())
            if body.action == "brief":
                details = "\n".join(f"- {field['label']} {'、'.join(answers[field['key']])}" for field in spec["fields"])
                _artifact(state, 0, "需求确认单", f"# {state['title']}\n\n## 目标与范围\n{details}\n\n## 补充信息\n{state['context'] or '暂未补充，可在后续访谈中进一步明确。'}\n\n## 执行方式\n采用{spec['name']}流程。当前为流程示例，尚未执行联网研究或模型分析。")
                _artifact(state, 1, spec["steps"][1] + " · 示例草稿", _background(state))
                state["stage"] = 1
                _message(state, "需求已经整理好了。我已生成一份准备计划示例，并挂到画布上。你可以打开查看、修改，确认后继续生成提纲示例。\n\n当前尚未执行真实联网检索，示例内容不作为调研事实。")
        elif body.action == "advance":
            if state["stage"] == 1:
                state["stage"] = 2
                _artifact(state, 2, spec["steps"][2] + " · 示例草稿", _outline(state))
                _message(state, "提纲示例已准备好，包含开场、主题问题与追问。点击画布上的文件即可编辑。确认提纲后，我们进入采集准备。")
            elif state["stage"] == 2:
                state["stage"] = 3
                _artifact(state, 3, "现场采集准备清单", "# 现场采集准备清单\n\n- 确认访谈对象、地点和预计时长。\n- 说明访谈目的并取得录音同意。\n- 检查提纲与设备电量。\n- 记录现场观察与需要核实的事实。\n\n## 采集能力\n当前交付为工作台第一阶段。真实录音、材料导入、手机扫码采集与后续分析将在下一阶段接入，本页面不会启动麦克风或生成虚构逐字稿。")
                _message(state, "已进入采集准备，提纲与准备清单会保存在这个项目里。第一轮先完成平台骨架，录音、手机接续与真实材料分析尚未接入。你可以继续编辑或导出当前成果。")
            else:
                raise HTTPException(409, "当前阶段暂无可自动执行的下一步")
        elif body.action == "artifact":
            item = next((a for a in state["artifacts"] if a["id"] == body.artifact_id), None)
            if not item:
                raise HTTPException(404, "成果不存在")
            if not body.content.strip():
                raise HTTPException(422, "成果正文不能为空")
            item["versions"].append({"version": item["version"], "content": item["content"], "at": item["updated_at"]})
            item.update(content=body.content, version=item["version"] + 1, updated_at=time.time())
            for downstream in state["artifacts"]:
                if downstream["step"] > item["step"]:
                    downstream["stale"] = True
        elif body.action == "message":
            text = body.content.strip()
            if not text or len(text) > 2000:
                raise HTTPException(422, "消息长度应在 1 到 2000 字之间")
            _message(state, text, "user")
            _message(state, "我已记录这条补充。第一轮暂不接通自由对话模型；你可以通过上方选择卡片确认需求，或打开成果直接编辑。真实 Agent 对话将在下一轮接入。")
        else:
            raise HTTPException(422, "不支持的操作")
        state["revision"] += 1
        state["updated_at"] = time.time()
        db.execute("UPDATE research_workspace_runs SET revision=?,snapshot=? WHERE project_id=?",
                   (state["revision"], json.dumps(state, ensure_ascii=False), project_id))
        db.execute("UPDATE projects SET updated_at=? WHERE id=?", (state["updated_at"], project_id))
        return state


def _background(state):
    goals = "、".join(state["answers"]["goal"])
    if state["scenario"] == "memoir":
        return f"# 人生时间线 · 准备草稿\n\n> 流程示例，尚未采集人物原始讲述。\n\n## 本次讲述\n{state['prompt']}\n\n## 主题范围\n{goals}\n\n## 建议的时间线\n1. 童年与故乡：家庭、生活环境和最早的记忆。\n2. 求学与成长：重要的人、选择与转折。\n3. 工作与家庭：亲历的事件及当时的感受。\n4. 回望与寄语：希望被记住的故事。\n\n## 待补充资料\n具体年份、人物关系、地点与可供印证的照片或文稿均待本人确认。\n\n## 来源\n仅依据本项目需求整理，没有调用外部来源。"
    return f"# {SCENARIOS[state['scenario']]['steps'][1]} · 准备草稿\n\n> 流程示例，未执行联网检索，不能作为事实背景。\n\n## 调研主题\n{state['prompt']}\n\n## 研究重点\n{goals}\n\n## 建议准备的资料\n1. 地区或企业的公开基础信息与近期变化。\n2. 行业发展、经营环境与主要参与者。\n3. 与关注主题有关的政策、公开数据和既有研究。\n\n## 执行计划\n先核对公开资料，再进行多对象访谈，最后交叉比较信息。将来源、发布日期和待核实事项分别记录。\n\n## 来源与待核实事项\n本轮未联网。地区数据、企业事实和行业判断都需要后续检索或访谈证实。"


def _outline(state):
    topics = state["answers"]["goal"]
    lines = [f"# {SCENARIOS[state['scenario']]['steps'][2]}", "> 根据选择项整理的流程示例，正式使用前请审阅。", "## 开场", "请简单介绍一下自己，以及与本次主题的关系。", "## 主题问题"]
    for i, topic in enumerate(topics, 1):
        question = f"围绕「{topic}」，您最想讲述哪段经历？" if state["scenario"] == "memoir" else f"围绕「{topic}」，您观察到哪些变化或具体问题？"
        lines += [f"### {i}. {topic}", question, "追问：可以举一个亲历的例子吗？当时发生了什么，后来有什么变化？"]
    lines += ["## 收尾", "还有哪些重要内容没有谈到？哪些细节需要后续核实或补充？", "## 现场记录", "记录原话、时间与上下文，区分当事人讲述和采访者判断。"]
    return "\n\n".join(lines)
