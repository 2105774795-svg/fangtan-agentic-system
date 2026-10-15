"""HTTP 兼容视图：领域结果与持久任务状态分开，绝不暴露文件路径或租约。"""
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import config
import job_queue
import store

router = APIRouter()
ACTIVE = {"queued", "running", "retry"}


def public_stage(task):
    if not task:
        return ""
    labels = {"queued": "等待后台处理", "running": "正在后台处理", "retry": "临时失败，等待自动重试",
              "ready": "已完成", "failed": "处理未完成，可重试", "cancelled": "正在取消并清理"}
    return labels.get(task["stage"], task["stage"])


def latest(kind, resource_id):
    rows = job_queue.find(kind=kind, resource_id=resource_id, limit=1)
    return rows[0] if rows else None


def report_task(sid):
    # 导入包含解析/转写与纪要，是同一项流水线；不能另排纪要抢跑空原文。
    importing = latest("import", sid)
    if importing and importing["status"] in ACTIVE:
        return importing
    return latest("report", sid) or importing


def report_status(sid, fallback=None):
    if store.get_report(sid) is not None:
        return "ready"
    task = report_task(sid)
    if task:
        return "generating" if task["status"] in ACTIVE else "failed"
    return store.get_report_status(sid, fallback)


def outline_view(job):
    task = latest("outline", job["id"])
    status = job["status"]
    error = job["error"]
    if task and task["status"] in ACTIVE and status != "ready":
        status, error = "generating", ""
    elif task and task["status"] in {"failed", "cancelled"} and status != "ready":
        status, error = "failed", "生成未完成，可重试；已填写内容仍保留"
    elif not task and status == "generating":
        status, error = "failed", "旧任务执行已中断，请重新生成；设置仍保留"
    return {"job_id": job["id"], "status": status, "questions": json.loads(job["questions"]),
            "background": job.get("background", ""), "background_note": job.get("background_note", ""),
            "sources": json.loads(job.get("sources") or "[]"), "research_status": job.get("research_status", "legacy"),
            "claims": json.loads(job.get("claims") or "[]"),
            "retrieved_at": job.get("retrieved_at", ""),
            "error": error, "settings": json.loads(job["params"]), "created_at": job["created_at"],
            "stage": public_stage(task), "progress": round(task["progress"] * 100) if task else 0}


def import_view(task):
    payload = task["payload"]
    result = task.get("result") or {}
    sid = payload.get("session_id")
    has_transcript = bool(sid and store.list_utterances(sid, last_n=1))
    status = {"running": "processing", "retry": "queued"}.get(task["status"], task["status"])
    if status == "cancelled" and (task.get("lease_owner") or task["stage"] == "cancelled"):
        status = "cancelling"
    return {"job_id": task["id"], "status": status, "stage": public_stage(task),
            "progress": round(task["progress"] * 100), "filename": payload.get("filename", ""),
            "kind": payload["kind"], "session_id": (result.get("session_id") or sid) if has_transcript else None,
            "error": "处理暂时失败，可重试已归档访谈的纪要" if status == "failed" else None,
            "created_at": task["created_at"], "updated_at": task["updated_at"]}


def project_view(record):
    task = latest("project_record", record["id"])
    if task and task["status"] in ACTIVE and record["status"] != "ready":
        record.update(status="generating", stage=public_stage(task) or "等待后台处理", error="")
    elif task and task["status"] in {"failed", "cancelled"} and record["status"] == "generating":
        record.update(status="failed", stage="生成未完成", error="后台处理未完成，请重新选择材料生成")
    elif not task and record["status"] == "generating":
        record.update(status="failed", stage="旧任务已中断", error="请重新选择材料生成")
    return record


def _authorized(task_id, request, write=False):
    task = job_queue.get(task_id)
    # 私密口述史的任务只能从独立认证入口读取，旧共享工作区一律不可见。
    if task and task["kind"].startswith("memoir_"):
        raise HTTPException(404, "任务不存在")
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "请先登录")
    if not task or (config.AUTH_ENABLED and task["owner_id"] != user["id"]):
        raise HTTPException(404, "任务不存在")
    if task["kind"] in {"report", "speaker", "import", "coverage"} and task.get("resource_id"):
        summary = store.get_session_summary(task["resource_id"])
        access = store.session_access(task["resource_id"], user["id"]) if config.AUTH_ENABLED else "owner"
        if not summary or not access or (write and access != "owner"):
            raise HTTPException(404, "任务对应访谈不存在或无权访问")
    return task


@router.get("/api/tasks/{task_id}")
async def task_status(task_id: str, request: Request):
    task = _authorized(task_id, request)
    result = {key: task[key] for key in
              ("id", "kind", "status", "stage", "progress", "attempts", "max_attempts", "created_at", "updated_at")}
    result["stage"] = public_stage(task)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
