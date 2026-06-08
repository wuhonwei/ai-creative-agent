"""定制音色管理：通过声音 URL 调用可灵 API 生成音色 ID。"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import settings
from app.db import engine, get_session
from app.llm import kling_client
from app.models import CustomVoice, Project

router = APIRouter(prefix="/api", tags=["custom-voices"])
logger = logging.getLogger(__name__)


class VoiceResponse(BaseModel):
    id: int
    project_id: int
    voice_name: str
    voice_url: str
    voice_id: str
    task_id: str
    trial_url: str
    status: str
    error: str
    created_at: datetime
    updated_at: datetime


def _voice_to_resp(v: CustomVoice) -> VoiceResponse:
    return VoiceResponse(
        id=v.id,
        project_id=v.project_id,
        voice_name=v.voice_name,
        voice_url=v.voice_url,
        voice_id=v.voice_id,
        task_id=v.task_id,
        trial_url=v.trial_url,
        status=v.status,
        error=v.error,
        created_at=v.created_at,
        updated_at=v.updated_at,
    )


class VoiceCreate(BaseModel):
    voice_name: str
    voice_url: str


class PresetVoiceItem(BaseModel):
    voice_id: str
    voice_name: str
    trial_url: str
    owned_by: str


class PresetVoiceListResponse(BaseModel):
    voices: list[PresetVoiceItem]
    page_num: int
    page_size: int
    has_more: bool
    total_on_page: int
    total: Optional[int] = None


async def _run_voice_generation(voice_id: int) -> None:
    """后台轮询可灵音色任务，更新数据库记录。"""
    with Session(engine) as session:
        voice = session.get(CustomVoice, voice_id)
        if not voice:
            return
        voice.status = "processing"
        voice.updated_at = datetime.utcnow()
        session.add(voice)
        session.commit()

        try:
            result = await kling_client.create_and_poll_custom_voice(
                voice_name=voice.voice_name,
                voice_url=voice.voice_url,
            )
            voice.task_id = result["task_id"]
            voice.voice_id = result["voice_id"]
            voice.trial_url = result.get("trial_url", "")
            voice.status = "done"
            voice.error = ""
            logger.info(f"[Voices] 音色生成成功：id={voice_id}, voice_id={voice.voice_id}")
        except kling_client.KlingError as e:
            voice.status = "failed"
            voice.error = str(e)
            logger.error(f"[Voices] 音色生成失败：id={voice_id}, error={e}")
        except Exception as e:
            voice.status = "failed"
            voice.error = str(e)
            logger.exception(f"[Voices] 音色生成异常：id={voice_id}")
        voice.updated_at = datetime.utcnow()
        session.add(voice)
        session.commit()


@router.get("/preset-voices", response_model=PresetVoiceListResponse)
async def list_preset_voices(page_num: int = 1, page_size: int = 30) -> PresetVoiceListResponse:
    """查询可灵官方预置音色列表。"""
    if not settings.kling_configured:
        raise HTTPException(400, "未配置可灵 AccessKey / SecretKey")
    if page_num < 1:
        raise HTTPException(400, "page_num 必须 >= 1")
    if page_size < 1 or page_size > 100:
        raise HTTPException(400, "page_size 须在 1~100 之间")

    try:
        result = await kling_client.list_preset_voices(page_num=page_num, page_size=page_size)
    except kling_client.KlingError as e:
        raise HTTPException(502, str(e)) from e

    return PresetVoiceListResponse(
        voices=[PresetVoiceItem(**v) for v in result["voices"]],
        page_num=result["page_num"],
        page_size=result["page_size"],
        has_more=result["has_more"],
        total_on_page=result["total_on_page"],
        total=result.get("total"),
    )


@router.get("/projects/{project_id}/voices")
def list_voices(project_id: int,
                session: Session = Depends(get_session)) -> list[VoiceResponse]:
    """列出项目下所有定制音色。"""
    if not session.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    voices = session.exec(
        select(CustomVoice)
        .where(CustomVoice.project_id == project_id)
        .order_by(CustomVoice.id.desc())
    ).all()
    return [_voice_to_resp(v) for v in voices]


@router.post("/projects/{project_id}/voices")
def create_voice(project_id: int,
                 body: VoiceCreate,
                 background_tasks: BackgroundTasks,
                 session: Session = Depends(get_session)) -> VoiceResponse:
    """创建定制音色记录并后台调用可灵 API 生成音色 ID。"""
    if not session.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    if not body.voice_name.strip():
        raise HTTPException(400, "请填写声音名称")
    if not body.voice_url.strip():
        raise HTTPException(400, "请填写声音 URL")
    if not settings.kling_configured:
        raise HTTPException(400, "未配置可灵 AccessKey / SecretKey")

    voice = CustomVoice(
        project_id=project_id,
        voice_name=body.voice_name.strip(),
        voice_url=body.voice_url.strip(),
        status="processing",
    )
    session.add(voice)
    session.commit()
    session.refresh(voice)

    background_tasks.add_task(_run_voice_generation, voice.id)
    logger.info(f"[Voices] 创建音色任务：id={voice.id}, name={voice.voice_name}")
    return _voice_to_resp(voice)


@router.get("/voices/{voice_id}")
def get_voice(voice_id: int,
              session: Session = Depends(get_session)) -> VoiceResponse:
    """查询单条音色记录（含任务状态）。"""
    voice = session.get(CustomVoice, voice_id)
    if not voice:
        raise HTTPException(404, "音色记录不存在")
    return _voice_to_resp(voice)


@router.post("/voices/{voice_id}/retry")
def retry_voice(voice_id: int,
                background_tasks: BackgroundTasks,
                session: Session = Depends(get_session)) -> VoiceResponse:
    """重新提交音色生成任务。"""
    voice = session.get(CustomVoice, voice_id)
    if not voice:
        raise HTTPException(404, "音色记录不存在")
    if not settings.kling_configured:
        raise HTTPException(400, "未配置可灵 AccessKey / SecretKey")

    voice.status = "processing"
    voice.error = ""
    voice.voice_id = ""
    voice.task_id = ""
    voice.trial_url = ""
    voice.updated_at = datetime.utcnow()
    session.add(voice)
    session.commit()
    session.refresh(voice)

    background_tasks.add_task(_run_voice_generation, voice.id)
    logger.info(f"[Voices] 重试音色任务：id={voice_id}")
    return _voice_to_resp(voice)


@router.delete("/voices/{voice_id}")
def delete_voice(voice_id: int,
                 session: Session = Depends(get_session)) -> None:
    """删除音色记录。"""
    voice = session.get(CustomVoice, voice_id)
    if not voice:
        raise HTTPException(404, "音色记录不存在")
    session.delete(voice)
    session.commit()
    logger.info(f"[Voices] 删除音色记录：id={voice_id}")
