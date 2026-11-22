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


class BibleAutofillRequest(BaseModel):
    """用开头思路自动填充故事圣经和角色档案。"""
    idea: str
    replace: bool = False      # 已有圣经或角色时，需明确确认才覆盖
    core_character_count: Optional[int] = None  # 不填或小于 1 时按 17 人生成


class ProjectUpdate(BaseModel):
    title: Optional[str] = None
    premise: Optional[str] = None
    mood: Optional[str] = None
    style: Optional[str] = None
    world_rules: Optional[str] = None
    dianwen: Optional[str] = None
    opening_idea: Optional[str] = None
    core_character_count: Optional[int] = None
    target_words: Optional[int] = None


class SettingsUpdate(BaseModel):
    """全局设置。癫文宝经对所有作品生效。"""
    dianwen: Optional[str] = None


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
    # None：沿用项目里已保存的癫文宝经（空则用内置默认）
    # ""：这次生成不参考癫文宝经
    dianwen: Optional[str] = None


class VolumeExpandRequest(BaseModel):
    """为单独一卷展开详细章节大纲。"""
    count: int = 0                 # 本卷目标章数，0 = 由模型决定
    instruction: str = ""          # 额外指引
    replace: bool = True           # True = 先清空本卷已有章节节点再生成
    dianwen: Optional[str] = None  # 同 OutlineGenerateRequest.dianwen


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
