"""分镜脚本路由：从章节正文拆镜头、读取、编辑单个镜头、删除。"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import resolve_video_duration
from app.db import get_session
from app.models import Chapter, Project, Shot, Storyboard
from app.services import storyboard as storyboard_service

router = APIRouter(prefix="/api", tags=["storyboard"])
logger = logging.getLogger(__name__)


class ShotResponse(BaseModel):
    id: int
    order_index: int
    shot_size: str
    camera: str
    character_ids: list[int]
    description: str
    dialogue: str
    sound: str
    lighting: str
    scene: str
    duration: float


class StoryboardResponse(BaseModel):
    id: int
    project_id: int
    chapter_id: Optional[int]
    title: str
    status: str
    shots: list[ShotResponse]


class ShotEditRequest(BaseModel):
    shot_size: Optional[str] = None
    camera: Optional[str] = None
    character_ids: Optional[list[int]] = None
    description: Optional[str] = None
    dialogue: Optional[str] = None
    sound: Optional[str] = None
    lighting: Optional[str] = None
    scene: Optional[str] = None
    duration: Optional[float] = None


def _parse_ids(s: str) -> list[int]:
    if not s:
        return []
    out = []
    for part in s.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def _shot_to_resp(s: Shot) -> ShotResponse:
    return ShotResponse(
        id=s.id, order_index=s.order_index, shot_size=s.shot_size,
        camera=s.camera, character_ids=_parse_ids(s.character_ids),
        description=s.description, dialogue=s.dialogue, sound=s.sound,
        lighting=s.lighting, scene=s.scene, duration=s.duration,
    )


def _board_to_resp(session: Session, board: Storyboard) -> StoryboardResponse:
    shots = session.exec(
        select(Shot).where(Shot.storyboard_id == board.id).order_by(Shot.order_index)
    ).all()
    return StoryboardResponse(
        id=board.id, project_id=board.project_id, chapter_id=board.chapter_id,
        title=board.title, status=board.status,
        shots=[_shot_to_resp(s) for s in shots],
    )


@router.post("/chapters/{chapter_id}/storyboard/generate")
async def generate_storyboard(chapter_id: int,
                              session: Session = Depends(get_session)) -> StoryboardResponse:
    """为某章拆分镜（已有分镜会重建）。"""
    chapter = session.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "章节不存在")
    try:
        board = await storyboard_service.generate_storyboard(
            session, chapter.project_id, chapter
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"[Storyboard] 生成失败：{e}", exc_info=True)
        raise HTTPException(500, f"分镜生成失败：{e}")
    return _board_to_resp(session, board)


@router.get("/chapters/{chapter_id}/storyboard")
def get_storyboard(chapter_id: int,
                   session: Session = Depends(get_session)) -> Optional[StoryboardResponse]:
    """读取某章的分镜（没有则返回 null）。"""
    board = session.exec(
        select(Storyboard).where(Storyboard.chapter_id == chapter_id)
    ).first()
    return _board_to_resp(session, board) if board else None


@router.patch("/shots/{shot_id}")
def edit_shot(shot_id: int, req: ShotEditRequest,
              session: Session = Depends(get_session)) -> ShotResponse:
    """编辑单个镜头。"""
    shot = session.get(Shot, shot_id)
    if not shot:
        raise HTTPException(404, "镜头不存在")
    if req.shot_size is not None:
        shot.shot_size = req.shot_size
    if req.camera is not None:
        shot.camera = req.camera
    if req.character_ids is not None:
        shot.character_ids = ",".join(str(i) for i in req.character_ids)
    if req.description is not None:
        shot.description = req.description
    if req.dialogue is not None:
        shot.dialogue = req.dialogue
    if req.sound is not None:
        shot.sound = req.sound
    if req.lighting is not None:
        shot.lighting = req.lighting
    if req.scene is not None:
        shot.scene = req.scene
    if req.duration is not None:
        shot.duration = float(resolve_video_duration(req.duration))
    session.add(shot)
    session.commit()
    session.refresh(shot)
    return _shot_to_resp(shot)


@router.delete("/shots/{shot_id}")
def delete_shot(shot_id: int, session: Session = Depends(get_session)) -> dict:
    """删除单个镜头。"""
    shot = session.get(Shot, shot_id)
    if not shot:
        raise HTTPException(404, "镜头不存在")
    session.delete(shot)
    session.commit()
    return {"message": "镜头已删除"}


@router.delete("/chapters/{chapter_id}/storyboard")
def delete_storyboard(chapter_id: int,
                      session: Session = Depends(get_session)) -> dict:
    """删除某章的整个分镜（含所有镜头）。"""
    board = session.exec(
        select(Storyboard).where(Storyboard.chapter_id == chapter_id)
    ).first()
    if not board:
        raise HTTPException(404, "分镜不存在")
    shots = session.exec(select(Shot).where(Shot.storyboard_id == board.id)).all()
    for s in shots:
        session.delete(s)
    session.delete(board)
    session.commit()
    return {"message": "分镜已删除"}
