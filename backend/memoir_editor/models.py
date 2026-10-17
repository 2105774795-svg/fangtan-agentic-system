"""传记编辑 API 的输入约束。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class BookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal['continue', 'sample', 'polish'] = 'continue'


class BookSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    action: Literal['settings', 'approve_style', 'feedback', 'focus_gap', 'skip_gap', 'resolve_gap']
    title: str = Field(default='', max_length=120)
    voice_note: str = Field(default='', max_length=2000)
    chapter_id: str = Field(default='', max_length=80)
    kind: str = Field(default='', max_length=20)
    note: str = Field(default='', max_length=1000)
    gap_id: str = Field(default='', max_length=80)
