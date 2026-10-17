"""统一工作台的面试详情与资料接口，兼容原领域端点。"""
from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, HTTPException
from pydantic import BaseModel, Field
from research_platform import workspace_access, project
import store
from . import workspace, api as legacy

router = APIRouter(prefix='/api/platform/projects/{project_id}', dependencies=[Depends(workspace_access)])


def resolve(project_id):
    with store.get() as db: return workspace.preparation(db, project_id)


@router.get('/interview')
def detail(project_id: str, request: Request):
    return legacy.project(resolve(project_id)['id'], request)


@router.get('/practices/{practice_id}')
def practice(project_id: str, practice_id: str, request: Request):
    value = detail(project_id, request)
    found = next((p for p in value['practices'] if p['id'] == practice_id), None)
    if not found: raise HTTPException(404, '练习不存在')
    return found


@router.post('/materials')
async def material(project_id: str, request: Request, file: UploadFile = File(...), revision: int = Form(...), request_id: str = Form(...), kind: str = Form('background')):
    await legacy.material(resolve(project_id)['id'], request, file, revision, request_id, kind)
    return project(project_id)


class Message(BaseModel):
    request_id: str = Field(min_length=8, max_length=80)
    content: str = Field(min_length=1, max_length=12000)


@router.post('/messages', status_code=202)
def message(project_id: str, body: Message):
    with store.get() as db:
        db.execute('BEGIN IMMEDIATE')
        p, state = workspace._state(db, project_id)
        workspace.enqueue_chat(db, p, state, body.content, body.request_id)
        view = workspace.public(db, state)
    return view
