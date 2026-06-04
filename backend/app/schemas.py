"""API 请求 / 响应模型。"""
from typing import Optional

from pydantic import BaseModel


# ---------- Project / Bible ----------
class ProjectCreate(BaseModel):
    title: str
    premise: str = ""
    mood: str = ""
    style: str = ""
    world_rules: str = ""
    target_words: int = 0


class ProjectUpdate(BaseModel):
    title: Optional[str] = None
    premise: Optional[str] = None
    mood: Optional[str] = None
    style: Optional[str] = None
    world_rules: Optional[str] = None
    target_words: Optional[int] = None


# ---------- Character ----------
class CharacterCreate(BaseModel):
    name: str
    role: str = ""
    description: str = ""
    state: str = ""


class CharacterUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    description: Optional[str] = None
    state: Optional[str] = None


# ---------- Outline ----------
class OutlineNodeCreate(BaseModel):
    parent_id: Optional[int] = None
    level: str = "chapter"
    order_index: int = 0
    title: str = ""
    synopsis: str = ""


class OutlineNodeUpdate(BaseModel):
    parent_id: Optional[int] = None
    level: Optional[str] = None
    order_index: Optional[int] = None
    title: Optional[str] = None
    synopsis: Optional[str] = None


class OutlineGenerateRequest(BaseModel):
    """请求模型生成（或重新生成）大纲。"""
    instruction: str = ""          # 额外指引，例如"分 3 卷，主线偏悬疑"
    num_volumes: int = 0           # 0 = 由模型自行决定
    chapters_per_volume: int = 0   # 0 = 由模型自行决定


class VolumeExpandRequest(BaseModel):
    """为单独一卷展开详细章节大纲。"""
    count: int = 0                 # 本卷目标章数，0 = 由模型决定
    instruction: str = ""          # 额外指引
    replace: bool = True           # True = 先清空本卷已有章节节点再生成


# ---------- Chapter generation ----------
class ChapterGenerateRequest(BaseModel):
    """生成某个章节大纲节点对应的正文。"""
    outline_node_id: int
    # 决策动作：
    #   new       —— 首次生成
    #   regenerate—— 不满意，重写（可附 instruction）
    #   redirect  —— 换个发展方向（必须给 instruction）
    action: str = "new"
    instruction: str = ""


class ChapterEditRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None


class ChapterConfirmRequest(BaseModel):
    """确认接受本章：触发摘要 + 角色/伏笔状态抽取并回写。"""
    update_memory: bool = True
