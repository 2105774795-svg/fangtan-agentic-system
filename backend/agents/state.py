"""可持久化的访谈决策；不存储模型内部推理。"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InterviewPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str = Field(default="", max_length=120)
    open_threads: list[str] = Field(default_factory=list, max_length=8)
    skipped_topics: list[str] = Field(default_factory=list, max_length=8)
    covered_topics: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("open_threads", "skipped_topics", "covered_topics")
    @classmethod
    def short_items(cls, values):
        if any(not isinstance(v, str) or not v.strip() or len(v) > 160 for v in values):
            raise ValueError("访谈线索格式不正确")
        return list(dict.fromkeys(values))


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["follow_up", "clarify", "recall", "switch_topic", "recap", "suggest_pause"]
    message: str = Field(min_length=1, max_length=500)
    plan: InterviewPlan | None = None

    @field_validator("message")
    @classmethod
    def one_question(cls, value):
        value = value.strip()
        if not value or value.count("？") + value.count("?") > 1:
            raise ValueError("回应一次最多提出一个问题")
        return value
