"""场景管理 + 按章节规划场景（变化检测）+ 可灵文生场景图 + 多图合成主体。"""
import logging
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session, engine
from app.llm import image_client, kling_client
from app.models import Asset, Chapter, ChapterScene, Project, Scene, SceneRefImage
from app.services import scene_planner

router = APIRouter(prefix="/api", tags=["scenes"])
logger = logging.getLogger(__name__)

VIDEOS_DIR = Path(__file__).parent.parent.parent / "videos"
VIDEOS_DIR.mkdir(exist_ok=True)


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError as e:
        logger.warning(f"[Scenes] 删除文件失败（已忽略）：{path} - {e}")


class SceneImageResponse(BaseModel):
    id: int
    scene_id: int
    status: str
    error: str
    image_url: str


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
    kling_element_id: str = ""
    status: str
    image_url: str
    images: list[SceneImageResponse]
    chapter_ids: list[int]


def _scene_image_resp(img: SceneRefImage) -> SceneImageResponse:
    return SceneImageResponse(
        id=img.id,
        scene_id=img.scene_id,
        status=img.status,
        error=img.error or "",
        image_url=f"/api/scene-images/{img.id}/file" if img.status == "done" and img.image_path else "",
    )


def _list_scene_images(session: Session, scene_id: int) -> list[SceneRefImage]:
    return session.exec(
        select(SceneRefImage)
        .where(SceneRefImage.scene_id == scene_id)
        .order_by(SceneRefImage.created_at)
    ).all()


def _scene_status_from_images(images: list[SceneRefImage], legacy_path: str) -> str:
    if any(i.status == "processing" for i in images):
        return "processing"
    if any(i.status == "done" for i in images) or legacy_path:
        return "done"
    if images and all(i.status == "failed" for i in images):
        return "failed"
    return "draft"


def _scene_to_resp(session: Session, s: Scene) -> SceneResponse:
    links = session.exec(
        select(ChapterScene).where(ChapterScene.scene_id == s.id)
    ).all()
    images = _list_scene_images(session, s.id)
    img_resps = [_scene_image_resp(i) for i in images]
    first_url = ""
    for r in img_resps:
        if r.image_url:
            first_url = r.image_url
            break
    if not first_url and s.image_path:
        first_url = f"/api/scenes/{s.id}/image"
    return SceneResponse(
        id=s.id, project_id=s.project_id, name=s.name, description=s.description,
        style=s.style, time_weather=s.time_weather, color_tone=s.color_tone,
        space_desc=s.space_desc, materials=s.materials, vista=s.vista,
        light_source=s.light_source, light_tech=s.light_tech,
        viewpoint=s.viewpoint, composition=s.composition,
        locked_prompt=s.locked_prompt, kling_element_id=s.kling_element_id or "",
        status=_scene_status_from_images(images, s.image_path),
        image_url=first_url,
        images=img_resps,
        chapter_ids=[link.chapter_id for link in links],
    )


def _scene_prompt(s: Scene) -> str:
    return s.locked_prompt or image_client.build_scene_prompt(
        style=s.style, time_weather=s.time_weather, color_tone=s.color_tone,
        space_desc=s.space_desc, materials=s.materials, vista=s.vista,
        light_source=s.light_source, light_tech=s.light_tech,
        viewpoint=s.viewpoint, composition=s.composition,
    )


def _collect_scene_image_paths(session: Session, s: Scene) -> list[Path]:
    """收集可用于合成主体的场景图路径（新多图 + 旧单图兼容）。"""
    paths: list[Path] = []
    images = session.exec(
        select(SceneRefImage)
        .where(SceneRefImage.scene_id == s.id)
        .where(SceneRefImage.status == "done")
        .order_by(SceneRefImage.created_at)
    ).all()
    for img in images:
        p = VIDEOS_DIR / img.image_path
        if img.image_path and p.exists():
            paths.append(p)
    if not paths and s.image_path:
        legacy = VIDEOS_DIR / s.image_path
        if legacy.exists():
            paths.append(legacy)
    return paths


# --------------------------- 列表 / 章节规划 ---------------------------
@router.get("/projects/{project_id}/scenes")
def list_scenes(project_id: int,
                session: Session = Depends(get_session)) -> list[SceneResponse]:
    scenes = session.exec(
        select(Scene).where(Scene.project_id == project_id).order_by(Scene.id)
    ).all()
    return [_scene_to_resp(session, s) for s in scenes]


class SceneCreate(BaseModel):
    name: str
    description: str = ""


@router.post("/projects/{project_id}/scenes")
def create_scene(project_id: int, body: SceneCreate,
                 session: Session = Depends(get_session)) -> SceneResponse:
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
    reused: list[int]
    created: list[int]
    scenes: list[SceneResponse]


@router.post("/chapters/{chapter_id}/scenes/plan")
async def plan_chapter_scenes(chapter_id: int,
                              session: Session = Depends(get_session)) -> PlanResult:
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


# --------------------------- 可灵文生场景图（多图） ---------------------------
async def _run_scene_ref_image(ref_image_id: int):
    """后台任务：可灵文生图生成单张场景参考图。"""
    with Session(engine) as session:
        img = session.get(SceneRefImage, ref_image_id)
        if not img:
            return
        s = session.get(Scene, img.scene_id)
        if not s:
            img.status = "failed"
            img.error = "场景不存在"
            session.add(img)
            session.commit()
            return
        try:
            prompt = img.prompt or _scene_prompt(s)
            filename = f"scene_{s.id}_{uuid.uuid4().hex[:10]}.png"
            file_path = VIDEOS_DIR / filename
            await kling_client.generate_image(prompt=prompt, save_path=file_path)
            img.image_path = filename
            img.prompt = prompt
            img.status = "done"
            img.error = ""
            s.updated_at = datetime.utcnow()
            session.add(s)
            logger.info(f"[Scenes] 场景图完成：scene_id={s.id}, ref_id={ref_image_id}")
        except Exception as e:
            img.status = "failed"
            img.error = str(e)[:500]
            logger.error(f"[Scenes] 场景图失败 ref_id={ref_image_id}：{e}", exc_info=True)
        session.add(img)
        session.commit()


@router.get("/scenes/{scene_id}/images")
def list_scene_images_api(scene_id: int,
                          session: Session = Depends(get_session)) -> list[SceneImageResponse]:
    s = session.get(Scene, scene_id)
    if not s:
        raise HTTPException(404, "场景不存在")
    return [_scene_image_resp(i) for i in _list_scene_images(session, scene_id)]


@router.post("/scenes/{scene_id}/images")
def generate_scene_image(scene_id: int, background: BackgroundTasks,
                         session: Session = Depends(get_session)) -> SceneImageResponse:
    """可灵文生图生成一张场景图（可重复调用生成多张）。"""
    if not settings.kling_configured:
        raise HTTPException(400, "未配置可灵 AccessKey / SecretKey")

    s = session.get(Scene, scene_id)
    if not s:
        raise HTTPException(404, "场景不存在")

    prompt = _scene_prompt(s)
    img = SceneRefImage(
        project_id=s.project_id,
        scene_id=s.id,
        prompt=prompt,
        status="processing",
    )
    session.add(img)
    s.updated_at = datetime.utcnow()
    session.add(s)
    session.commit()
    session.refresh(img)

    background.add_task(_run_scene_ref_image, img.id)
    logger.info(f"[Scenes] 提交场景文生图：scene_id={scene_id}, ref_id={img.id}")
    return _scene_image_resp(img)


@router.post("/scenes/{scene_id}/image")
def generate_scene_image_legacy(scene_id: int, background: BackgroundTasks,
                                session: Session = Depends(get_session)) -> SceneResponse:
    """兼容旧端点：等同新增一张场景图。"""
    generate_scene_image(scene_id, background, session)
    s = session.get(Scene, scene_id)
    return _scene_to_resp(session, s)


@router.get("/scene-images/{image_id}/file")
def get_scene_ref_image_file(image_id: int,
                             session: Session = Depends(get_session)) -> FileResponse:
    img = session.get(SceneRefImage, image_id)
    if not img or not img.image_path:
        raise HTTPException(404, "场景图不存在")
    fp = VIDEOS_DIR / img.image_path
    if not fp.exists():
        raise HTTPException(404, "场景图文件不存在")
    return FileResponse(fp, media_type="image/png")


@router.delete("/scene-images/{image_id}")
def delete_scene_ref_image(image_id: int,
                           session: Session = Depends(get_session)) -> dict:
    img = session.get(SceneRefImage, image_id)
    if not img:
        raise HTTPException(404, "场景图不存在")
    if img.image_path:
        _safe_unlink(VIDEOS_DIR / img.image_path)
    session.delete(img)
    session.commit()
    return {"message": "场景图已删除"}


# --------------------------- 场景主体合成 ---------------------------
@router.post("/scenes/{scene_id}/element")
async def create_scene_element(scene_id: int,
                               session: Session = Depends(get_session)) -> SceneResponse:
    """用多张场景参考图合成场景主体 ID（标签：场景 o_106）。"""
    if not settings.kling_configured:
        raise HTTPException(400, "未配置可灵 AccessKey / SecretKey")

    s = session.get(Scene, scene_id)
    if not s:
        raise HTTPException(404, "场景不存在")

    paths = _collect_scene_image_paths(session, s)
    if not paths:
        raise HTTPException(400, "请先生成至少一张场景图，再合成主体 ID")

    frontal = paths[0]
    refer = paths[1:4]
    desc = (_scene_prompt(s) or s.description or s.name)[:100]
    try:
        result = await kling_client.create_and_poll_custom_element(
            element_name=s.name,
            element_description=desc,
            frontal_image_path=frontal,
            refer_image_paths=refer,
            voice_id="",
            tag_list=kling_client.scene_element_tags(),
        )
    except kling_client.KlingError as e:
        raise HTTPException(502, str(e)) from e

    s.kling_element_id = result["element_id"]
    s.updated_at = datetime.utcnow()
    session.add(s)
    session.commit()
    session.refresh(s)
    logger.info(
        f"[Scenes] 场景主体合成成功：scene_id={scene_id}, "
        f"element_id={s.kling_element_id}, refs={len(paths)}"
    )
    return _scene_to_resp(session, s)


@router.post("/projects/{project_id}/scenes/generate-images")
def generate_all_scene_images(project_id: int, background: BackgroundTasks,
                              session: Session = Depends(get_session)) -> dict:
    """为尚无场景图的场景各提交一张可灵文生图。"""
    if not settings.kling_configured:
        raise HTTPException(400, "未配置可灵 AccessKey / SecretKey")

    scenes = session.exec(
        select(Scene).where(Scene.project_id == project_id)
    ).all()
    queued = 0
    for s in scenes:
        done_count = session.exec(
            select(SceneRefImage)
            .where(SceneRefImage.scene_id == s.id)
            .where(SceneRefImage.status == "done")
        ).all()
        if done_count or s.image_path:
            continue
        prompt = _scene_prompt(s)
        img = SceneRefImage(
            project_id=s.project_id, scene_id=s.id, prompt=prompt, status="processing",
        )
        session.add(img)
        background.add_task(_run_scene_ref_image, img.id)
        queued += 1
    session.commit()
    return {"queued": queued, "total": len(scenes)}


@router.get("/scenes/{scene_id}/image")
def get_scene_image_legacy(scene_id: int,
                           session: Session = Depends(get_session)) -> FileResponse:
    """兼容旧端点：返回首张场景图。"""
    s = session.get(Scene, scene_id)
    if not s:
        raise HTTPException(404, "场景不存在")
    images = session.exec(
        select(SceneRefImage)
        .where(SceneRefImage.scene_id == scene_id)
        .where(SceneRefImage.status == "done")
        .order_by(SceneRefImage.created_at)
    ).all()
    if images and images[0].image_path:
        fp = VIDEOS_DIR / images[0].image_path
        if fp.exists():
            return FileResponse(fp, media_type="image/png")
    if not s.image_path:
        raise HTTPException(404, "场景图不存在")
    fp = VIDEOS_DIR / s.image_path
    if not fp.exists():
        raise HTTPException(404, "场景图文件不存在")
    return FileResponse(fp, media_type="image/png")


@router.delete("/scenes/{scene_id}")
def delete_scene(scene_id: int, session: Session = Depends(get_session)) -> dict:
    s = session.get(Scene, scene_id)
    if not s:
        raise HTTPException(404, "场景不存在")
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
    ref_images = session.exec(
        select(SceneRefImage).where(SceneRefImage.scene_id == scene_id)
    ).all()
    for img in ref_images:
        if img.image_path:
            _safe_unlink(VIDEOS_DIR / img.image_path)
        session.delete(img)
    if s.image_path:
        _safe_unlink(VIDEOS_DIR / s.image_path)
    session.delete(s)
    session.commit()
    return {"message": "场景已删除"}
