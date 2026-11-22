"""项目（含故事圣经）与角色的 CRUD。"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.db import get_session
from app.llm import client, prompts
from app.models import (
    Chapter, Character, CharacterDesign, CharacterRefImage, OutlineNode,
    PlotThread, Project,
)
from app.schemas import (
    BibleAutofillRequest, CharacterCreate, CharacterUpdate, ProjectCreate,
    ProjectUpdate,
)
from app.services.generation import _extract_json

router = APIRouter(prefix="/api", tags=["projects"])
logger = logging.getLogger(__name__)


# ---------------- Projects ----------------
@router.get("/projects")
def list_projects(session: Session = Depends(get_session)):
    return session.exec(select(Project).order_by(Project.updated_at.desc())).all()


@router.post("/projects")
def create_project(body: ProjectCreate, session: Session = Depends(get_session)):
    project = Project(**body.model_dump())
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@router.get("/projects/{project_id}")
def get_project(project_id: int, session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    return project


@router.patch("/projects/{project_id}")
def update_project(project_id: int, body: ProjectUpdate,
                   session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(project, k, v)
    project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


@router.post("/projects/{project_id}/bible/autofill")
async def autofill_bible(project_id: int, body: BibleAutofillRequest,
                         session: Session = Depends(get_session)):
    """用开头思路填充故事圣经和角色档案。生成时参考癫文宝经。"""
    idea = (body.idea or "").strip()
    if not idea:
        raise HTTPException(400, "请先写下开头思路")

    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    existing = session.exec(
        select(Character).where(Character.project_id == project_id)
    ).all()
    has_bible = any(
        (getattr(project, key) or "").strip()
        for key in ("premise", "mood", "style", "world_rules")
    )
    if (has_bible or existing) and not body.replace:
        raise HTTPException(
            409, "已有故事圣经或角色档案。确认后将覆盖这些内容，并删除现有角色的形象设定。"
        )

    dianwen = prompts.resolve_dianwen(None, project.dianwen)
    msgs = prompts.build_bible_fill_prompt(idea, project.title, dianwen)
    try:
        # 推理模型会先消耗大量 token，预算过小会把 JSON 截断。
        raw = await client.chat(msgs, temperature=0.8, max_tokens=16384)
    except client.LLMError as e:
        raise HTTPException(502, str(e)) from e

    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        logger.error("自动填充故事圣经的模型输出无法解析：%s", raw[:1000])
        raise HTTPException(502, "模型未返回可用的故事圣经，请再试一次")

    premise = _as_text(parsed.get("premise"))
    mood = _as_text(parsed.get("mood"))
    style = _as_text(parsed.get("style"))
    world_rules = _as_text(parsed.get("world_rules"))
    if not premise:
        raise HTTPException(502, "模型未写好故事背景，请换个开头思路再试")

    new_characters: list[Character] = []
    for item in parsed.get("characters") or []:
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get("name"))
        if not name:
            continue
        new_characters.append(Character(
            project_id=project_id,
            name=name,
            role=_as_text(item.get("role")),
            description=_as_text(item.get("description")),
        ))
    if not new_characters:
        raise HTTPException(502, "模型未返回有效角色")

    project.premise = premise
    project.mood = mood
    project.style = style
    project.world_rules = world_rules
    new_title = _as_text(parsed.get("title"))
    if new_title and project.title.strip() in ("", "未命名作品"):
        project.title = new_title
    project.updated_at = datetime.utcnow()

    char_ids = [c.id for c in existing]
    if char_ids:
        for model in (CharacterRefImage, CharacterDesign):
            for row in session.exec(
                select(model).where(model.character_id.in_(char_ids))
            ).all():
                session.delete(row)
        for ch in existing:
            session.delete(ch)
        session.commit()

    session.add(project)
    for ch in new_characters:
        session.add(ch)
    session.commit()
    session.refresh(project)
    for ch in new_characters:
        session.refresh(ch)
    return {"project": project, "characters": new_characters}


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    # 级联删除关联数据
    for model in (Chapter, OutlineNode, Character, PlotThread):
        for row in session.exec(
            select(model).where(model.project_id == project_id)
        ).all():
            session.delete(row)
    session.delete(project)
    session.commit()
    return {"ok": True}


# ---------------- Characters ----------------
@router.get("/projects/{project_id}/characters")
def list_characters(project_id: int, session: Session = Depends(get_session)):
    return session.exec(
        select(Character).where(Character.project_id == project_id)
    ).all()


@router.post("/projects/{project_id}/characters")
def create_character(project_id: int, body: CharacterCreate,
                     session: Session = Depends(get_session)):
    if not session.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    ch = Character(project_id=project_id, **body.model_dump())
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


@router.patch("/characters/{character_id}")
def update_character(character_id: int, body: CharacterUpdate,
                     session: Session = Depends(get_session)):
    ch = session.get(Character, character_id)
    if not ch:
        raise HTTPException(404, "角色不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(ch, k, v)
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


@router.delete("/characters/{character_id}")
def delete_character(character_id: int, session: Session = Depends(get_session)):
    ch = session.get(Character, character_id)
    if not ch:
        raise HTTPException(404, "角色不存在")
    session.delete(ch)
    session.commit()
    return {"ok": True}


# ---------------- Plot threads（伏笔，只读 + 手动管理）----------------
@router.get("/projects/{project_id}/threads")
def list_threads(project_id: int, session: Session = Depends(get_session)):
    return session.exec(
        select(PlotThread).where(PlotThread.project_id == project_id)
    ).all()
