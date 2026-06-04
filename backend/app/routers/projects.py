"""项目（含故事圣经）与角色的 CRUD。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.db import get_session
from app.models import Chapter, Character, OutlineNode, PlotThread, Project
from app.schemas import (
    CharacterCreate, CharacterUpdate, ProjectCreate, ProjectUpdate,
)

router = APIRouter(prefix="/api", tags=["projects"])


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
