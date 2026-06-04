"""场景管理 + 按章节规划场景（变化检测）+ 无角色场景图生成。

核心能力：
- 按章节抽取场景，与已有场景比对：同一地点复用、有变化新建（scene_planner）。
- 章节 ↔ 场景多对多（ChapterScene）：一章一组场景，一个场景可跨多章复用。
- 场景视觉模板（核心设定/空间结构/光影镜头 + 固定技术约束），生成"无角色"场景图。
"""
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session, engine
from app.llm import image_client
from app.models import Asset, Chapter, ChapterScene, Project, Scene
from app.services import scene_planner

router = APIRouter(prefix="/api", tags=["scenes"])
logger = logging.getLogger(__name__)

VIDEOS_DIR = Path(__file__).parent.parent.parent / "videos"
VIDEOS_DIR.mkdir(exist_ok=True)

_SCENE_FIELDS = ["style", "time_weather", "color_tone", "space_desc", "materials",
                 "vista", "light_source", "light_tech", "viewpoint", "composition"]


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError as e:
        logger.warning(f"[Scenes] 删除文件失败（已忽略）：{path} - {e}")


class SceneResponse(BaseModel):
    id: int
    project_id: int
    name: str
    description: str
    style: str
    time_weather: str
    color_tone: str
    space_desc: str
    materials: str
    vista: str
    light_source: str
    light_tech: str
    viewpoint: str
    composition: str
    locked_prompt: str
    status: str
    image_url: str          # 场景图服务路径（有图时有效）
    chapter_ids: list[int]  # 关联的章节


def _scene_to_resp(session: Session, s: Scene) -> SceneResponse:
    links = session.exec(
        select(ChapterScene).where(ChapterScene.scene_id == s.id)
    ).all()
    return SceneResponse(
        id=s.id, project_id=s.project_id, name=s.name, description=s.description,
        style=s.style, time_weather=s.time_weather, color_tone=s.color_tone,
        space_desc=s.space_desc, materials=s.materials, vista=s.vista,
        light_source=s.light_source, light_tech=s.light_tech,
        viewpoint=s.viewpoint, composition=s.composition,
        locked_prompt=s.locked_prompt, status=s.status,
        image_url=f"/api/scenes/{s.id}/image" if s.image_path else "",
        chapter_ids=[link.chapter_id for link in links],
    )


# --------------------------- 列表 / 章节规划 ---------------------------
@router.get("/projects/{project_id}/scenes")
def list_scenes(project_id: int,
                session: Session = Depends(get_session)) -> list[SceneResponse]:
    """列出项目下所有场景（含关联章节）。"""
    scenes = session.exec(
        select(Scene).where(Scene.project_id == project_id).order_by(Scene.id)
    ).all()
    return [_scene_to_resp(session, s) for s in scenes]


class SceneCreate(BaseModel):
    """手动创建场景（兼容资产页）。"""
    name: str
    description: str = ""


@router.post("/projects/{project_id}/scenes")
def create_scene(project_id: int, body: SceneCreate,
                 session: Session = Depends(get_session)) -> SceneResponse:
    """手动创建一个场景（资产页用）。视觉字段留空，可后续编辑或重规划补全。"""
    if not session.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    s = Scene(project_id=project_id, name=body.name, description=body.description,
              status="draft")
    session.add(s)
    session.commit()
    session.refresh(s)
    logger.info(f"[Scenes] 手动创建场景：{s.name}, project_id={project_id}")
    return _scene_to_resp(session, s)


class PlanResult(BaseModel):
    reused: list[int]        # 复用的已有场景 id
    created: list[int]       # 新建的场景 id
    scenes: list[SceneResponse]   # 本章关联的全部场景


@router.post("/chapters/{chapter_id}/scenes/plan")
async def plan_chapter_scenes(chapter_id: int,
                              session: Session = Depends(get_session)) -> PlanResult:
    """按本章正文抽取场景并做变化检测：同一地点复用、有变化新建。"""
    chapter = session.get(Chapter, chapter_id)
    if not chapter:
        raise HTTPException(404, "章节不存在")
    try:
        res = await scene_planner.plan_chapter_scenes(
            session, chapter.project_id, chapter
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"[Scenes] 章节场景规划失败：{e}", exc_info=True)
        raise HTTPException(500, f"场景规划失败：{e}")
    return PlanResult(
        reused=res["reused"], created=res["created"],
        scenes=[_scene_to_resp(session, s) for s in res["scenes"]],
    )


@router.get("/chapters/{chapter_id}/scenes")
def get_chapter_scenes(chapter_id: int,
                       session: Session = Depends(get_session)) -> list[SceneResponse]:
    """查本章关联的场景。"""
    links = session.exec(
        select(ChapterScene).where(ChapterScene.chapter_id == chapter_id)
    ).all()
    scenes = []
    for link in links:
        s = session.get(Scene, link.scene_id)
        if s:
            scenes.append(_scene_to_resp(session, s))
    return scenes


# --------------------------- 编辑 ---------------------------
class SceneEditRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    style: str | None = None
    time_weather: str | None = None
    color_tone: str | None = None
    space_desc: str | None = None
    materials: str | None = None
    vista: str | None = None
    light_source: str | None = None
    light_tech: str | None = None
    viewpoint: str | None = None
    composition: str | None = None


@router.patch("/scenes/{scene_id}")
def edit_scene(scene_id: int, req: SceneEditRequest,
               session: Session = Depends(get_session)) -> SceneResponse:
    """编辑场景字段，自动重算 locked_prompt。"""
    from datetime import datetime
    s = session.get(Scene, scene_id)
    if not s:
        raise HTTPException(404, "场景不存在")
    data = req.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(s, k, v if v is not None else "")
    s.locked_prompt = image_client.build_scene_prompt(
        style=s.style, time_weather=s.time_weather, color_tone=s.color_tone,
        space_desc=s.space_desc, materials=s.materials, vista=s.vista,
        light_source=s.light_source, light_tech=s.light_tech,
        viewpoint=s.viewpoint, composition=s.composition,
    )
    s.updated_at = datetime.utcnow()
    session.add(s)
    session.commit()
    session.refresh(s)
    return _scene_to_resp(session, s)


# --------------------------- 场景图生成 ---------------------------
async def _run_scene_image(scene_id: int):
    """后台任务：文生图出"无角色场景图" → 回写 Scene。"""
    from datetime import datetime
    with Session(engine) as session:
        s = session.get(Scene, scene_id)
        if not s:
            return
        try:
            prompt = s.locked_prompt or image_client.build_scene_prompt(
                style=s.style, time_weather=s.time_weather, color_tone=s.color_tone,
                space_desc=s.space_desc, materials=s.materials, vista=s.vista,
                light_source=s.light_source, light_tech=s.light_tech,
                viewpoint=s.viewpoint, composition=s.composition,
            )
            filename = f"scene_{scene_id}_{uuid.uuid4().hex[:10]}.png"
            file_path = VIDEOS_DIR / filename
            await image_client.generate_image(
                prompt=prompt, width=settings.image_width,
                height=settings.image_height, save_path=file_path,
            )
            if s.image_path:
                _safe_unlink(VIDEOS_DIR / s.image_path)
            s.image_path = filename
            s.status = "done"
            s.updated_at = datetime.utcnow()
            logger.info(f"[Scenes] 场景图完成：scene_id={scene_id}")
        except Exception as e:
            s.status = "failed"
            s.updated_at = datetime.utcnow()
            logger.error(f"[Scenes] 场景图失败：{e}", exc_info=True)
        session.add(s)
        session.commit()


@router.post("/scenes/{scene_id}/image")
def generate_scene_image(scene_id: int, background: BackgroundTasks,
                         session: Session = Depends(get_session)) -> SceneResponse:
    """为场景生成无角色场景图（后台异步），立即返回 processing 状态。"""
    s = session.get(Scene, scene_id)
    if not s:
        raise HTTPException(404, "场景不存在")
    s.status = "processing"
    session.add(s)
    session.commit()
    session.refresh(s)
    background.add_task(_run_scene_image, scene_id)
    return _scene_to_resp(session, s)


@router.post("/projects/{project_id}/scenes/generate-images")
def generate_all_scene_images(project_id: int, background: BackgroundTasks,
                              session: Session = Depends(get_session)) -> dict:
    """一键为项目下所有"还没出图"的场景批量生成场景图。"""
    scenes = session.exec(
        select(Scene).where(Scene.project_id == project_id)
    ).all()
    queued = 0
    for s in scenes:
        if s.image_path and s.status == "done":
            continue
        s.status = "processing"
        session.add(s)
        background.add_task(_run_scene_image, s.id)
        queued += 1
    session.commit()
    return {"queued": queued, "total": len(scenes)}


@router.get("/scenes/{scene_id}/image")
def get_scene_image(scene_id: int,
                    session: Session = Depends(get_session)) -> FileResponse:
    """取场景图文件。"""
    s = session.get(Scene, scene_id)
    if not s or not s.image_path:
        raise HTTPException(404, "场景图不存在")
    fp = VIDEOS_DIR / s.image_path
    if not fp.exists():
        raise HTTPException(404, "场景图文件不存在")
    return FileResponse(fp, media_type="image/png")


@router.delete("/scenes/{scene_id}")
def delete_scene(scene_id: int, session: Session = Depends(get_session)) -> dict:
    """删除场景（含章节关联、场景图文件）。"""
    s = session.get(Scene, scene_id)
    if not s:
        raise HTTPException(404, "场景不存在")
    # 旧资产保护：若有图片资产挂在该场景，提示先删
    assets = session.exec(
        select(Asset).where(Asset.asset_type == "scene").where(Asset.ref_id == scene_id)
    ).all()
    if assets:
        raise HTTPException(409, f"场景有 {len(assets)} 个关联图片资产，请先删除")
    links = session.exec(
        select(ChapterScene).where(ChapterScene.scene_id == scene_id)
    ).all()
    for link in links:
        session.delete(link)
    if s.image_path:
        _safe_unlink(VIDEOS_DIR / s.image_path)
    session.delete(s)
    session.commit()
    return {"message": "场景已删除"}
