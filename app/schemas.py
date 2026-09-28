"""Request and response contracts for projects and storyboards."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StoryboardBase(BaseModel):
    sequence: int = Field(ge=1)
    scene_description: str = Field(min_length=2, max_length=2000)
    dialogue: str = Field(default="", max_length=2000)
    emotion: str = Field(default="平静", min_length=1, max_length=50)
    duration: float = Field(default=3, ge=1, le=30)
    image_prompt: str = Field(default="", max_length=3000)


class StoryboardCreate(StoryboardBase):
    pass


class StoryboardUpdate(BaseModel):
    scene_description: str | None = Field(default=None, min_length=2, max_length=2000)
    dialogue: str | None = Field(default=None, max_length=2000)
    emotion: str | None = Field(default=None, min_length=1, max_length=50)
    duration: float | None = Field(default=None, ge=1, le=30)
    image_prompt: str | None = Field(default=None, max_length=3000)


class Storyboard(StoryboardBase):
    id: str
    project_id: str
    image_path: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    story_text: str = Field(min_length=100, max_length=3000)
    art_style: str = Field(default="日系赛璐璐", min_length=1, max_length=80)
    voice: str = Field(default="青年男声", min_length=1, max_length=80)
    target_duration: int = Field(default=120, ge=15, le=600)

    @field_validator("story_text")
    @classmethod
    def story_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 100:
            raise ValueError("剧情文本至少 100 字")
        return value


class ProjectSummary(BaseModel):
    id: str
    title: str
    art_style: str
    voice: str
    target_duration: int
    status: str
    error_message: str
    storyboard_count: int = 0
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectSummary):
    story_text: str
    storyboards: list[Storyboard] = []


class GenerateStoryboardsRequest(BaseModel):
    mode: Literal["ai", "demo"] = "ai"


class ManualStoryboardsRequest(BaseModel):
    storyboards: list[StoryboardCreate] = Field(min_length=3, max_length=100)

    @field_validator("storyboards")
    @classmethod
    def sequences_must_be_continuous(cls, rows: list[StoryboardCreate]) -> list[StoryboardCreate]:
        if [item.sequence for item in rows] != list(range(1, len(rows) + 1)):
            raise ValueError("分镜序号必须从 1 开始连续排列")
        return rows
