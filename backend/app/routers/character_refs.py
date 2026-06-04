"""角色视觉设定锁定 + 参考图（正/侧/斜/全身立绘）生成与管理。

一致性核心：
- CharacterDesign 存一份"锁定提示词"(locked_prompt)，全剧统一复用。
- 据此生成多视角参考图，其中一张设为 is_primary，作为后续镜头图生视频的首帧基准。

参考图与视频统一存放在 backend/videos/ 目录（与图片资产的 assets/ 分开）。
"""
import logging
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session
from app.llm import image_client
from app.models import Character, CharacterDesign, CharacterRefImage, Project
from app.services import character_design as character_design_service

router = APIRouter(prefix="/api", tags=["character_refs"])
logger = logging.getLogger(__name__)

# 视频流水线产物根目录（参考图 / 镜头片段 / 成片）
VIDEOS_DIR = Path(__file__).parent.parent.parent / "videos"
VIDEOS_DIR.mkdir(exist_ok=True)

# 默认生成的 5 个视角（顺序即展示顺序）：脸部特写 / 正 / 侧 / 背 / 全身
DEFAULT_VIEWS = ("face_closeup", "front", "side", "back", "full_body")
# 接受集：多含旧的斜视图（three_quarter），兼容历史已生成图的重生成
VALID_VIEWS = ("face_closeup", "front", "side", "back", "three_quarter", "full_body")


def _safe_unlink(path: Path) -> None:
    """删除文件，删不掉不报错（Windows 下文件可能被浏览器预览占用）。"""
    try:
        if path.exists():
            path.unlink()
    except OSError as e:
        logger.warning(f"[CharRef] 删除文件失败（已忽略，可能被占用）：{path} - {e}")


# --------------------------- 请求 / 响应模型 ---------------------------
class DesignRequest(BaseModel):
    """保存/更新角色锁定设定。"""
    gender: str = ""
    age: str = ""
    body_type: str = ""
    color_tone: str = ""
    art_style: str = ""
    facial_features: str = ""
    hair: str = ""
    outfit: str = ""
    extra: str = ""


class DesignResponse(BaseModel):
    id: int
    project_id: int
    character_id: int
    gender: str
    age: str
    body_type: str
    color_tone: str
    art_style: str
    facial_features: str
    hair: str
    outfit: str
    extra: str
    locked_prompt: str


class RefImageResponse(BaseModel):
    id: int
    character_id: int
    view: str
    view_label: str
    prompt: str
    is_primary: bool
    width: int
    height: int
    image_url: str


class GenerateRefRequest(BaseModel):
    """生成参考图。views 为空则生成全部四视角。"""
    views: list[str] = []


class GenerateRefResult(BaseModel):
    """批量生成参考图结果：成功的图 + 被跳过的视角及原因。"""
    images: list["RefImageResponse"]
    skipped: list[dict]      # {view, view_label, reason}


def _design_to_resp(d: CharacterDesign) -> DesignResponse:
    return DesignResponse(
        id=d.id, project_id=d.project_id, character_id=d.character_id,
        gender=d.gender, age=d.age, body_type=d.body_type, color_tone=d.color_tone,
        art_style=d.art_style, facial_features=d.facial_features,
        hair=d.hair, outfit=d.outfit, extra=d.extra, locked_prompt=d.locked_prompt,
    )


def _ref_to_resp(r: CharacterRefImage) -> RefImageResponse:
    return RefImageResponse(
        id=r.id, character_id=r.character_id, view=r.view,
        view_label=image_client.REF_VIEW_LABELS.get(r.view, r.view),
        prompt=r.prompt, is_primary=r.is_primary,
        width=r.width, height=r.height,
        image_url=f"/api/character-refs/{r.id}/image",
    )


def _get_character(session: Session, project_id: int, character_id: int) -> Character:
    ch = session.get(Character, character_id)
    if not ch or ch.project_id != project_id:
        raise HTTPException(404, "角色不存在")
    return ch


# --------------------------- 锁定设定 ---------------------------
@router.get("/projects/{project_id}/characters/{character_id}/design")
def get_design(project_id: int, character_id: int,
               session: Session = Depends(get_session)) -> Optional[DesignResponse]:
    """读取角色锁定设定（没有则返回 null）。"""
    _get_character(session, project_id, character_id)
    d = session.exec(
        select(CharacterDesign).where(CharacterDesign.character_id == character_id)
    ).first()
    return _design_to_resp(d) if d else None


@router.put("/projects/{project_id}/characters/{character_id}/design")
def save_design(project_id: int, character_id: int, req: DesignRequest,
                session: Session = Depends(get_session)) -> DesignResponse:
    """保存/更新角色锁定设定，自动重算 locked_prompt。"""
    _get_character(session, project_id, character_id)
    locked = image_client.build_locked_character_prompt(
        req.art_style, req.facial_features, req.hair, req.outfit, req.extra,
        gender=req.gender, age=req.age, body_type=req.body_type,
        color_tone=req.color_tone,
    )
    from datetime import datetime
    d = session.exec(
        select(CharacterDesign).where(CharacterDesign.character_id == character_id)
    ).first()
    if d:
        d.gender = req.gender
        d.age = req.age
        d.body_type = req.body_type
        d.color_tone = req.color_tone
        d.art_style = req.art_style
        d.facial_features = req.facial_features
        d.hair = req.hair
        d.outfit = req.outfit
        d.extra = req.extra
        d.locked_prompt = locked
        d.updated_at = datetime.utcnow()
    else:
        d = CharacterDesign(
            project_id=project_id, character_id=character_id,
            gender=req.gender, age=req.age, body_type=req.body_type,
            color_tone=req.color_tone,
            art_style=req.art_style, facial_features=req.facial_features,
            hair=req.hair, outfit=req.outfit, extra=req.extra, locked_prompt=locked,
        )
        session.add(d)
    session.commit()
    session.refresh(d)
    logger.info(f"[CharRef] 保存锁定设定：character_id={character_id}")
    return _design_to_resp(d)


class AutofillRequest(BaseModel):
    """AI 批量填充锁定关键词。only_empty=True 时只补还没设定的角色。"""
    only_empty: bool = False


class AutofillResult(BaseModel):
    updated: list[int]       # 已填充的 character_id
    skipped: list[int]       # 跳过的 character_id
    total: int
    designs: list[DesignResponse]   # 填充后全项目的最新设定


@router.post("/projects/{project_id}/characters/designs/autofill")
async def autofill_designs(project_id: int, req: AutofillRequest,
                           session: Session = Depends(get_session)) -> AutofillResult:
    """结合大纲设定 + 角色档案，AI 批量填充各角色锁定关键词并落库。"""
    try:
        res = await character_design_service.autofill_character_designs(
            session, project_id, only_empty=req.only_empty
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"[CharRef] AI 填充失败：{e}", exc_info=True)
        raise HTTPException(500, f"AI 填充失败：{e}")

    designs = session.exec(
        select(CharacterDesign).where(CharacterDesign.project_id == project_id)
    ).all()
    return AutofillResult(
        updated=res["updated"], skipped=res["skipped"], total=res["total"],
        designs=[_design_to_resp(d) for d in designs],
    )


class ScanResult(BaseModel):
    created: list[str]       # 新建的角色名
    filled: list[str]        # 已填充设定的角色名
    skipped: list[str]       # 未能填充（AI 没给视觉字段）的角色名
    total_found: int         # 大纲里扫描到的人物总数


@router.post("/projects/{project_id}/characters/scan-from-outline")
async def scan_characters_from_outline(project_id: int,
                                       session: Session = Depends(get_session)) -> ScanResult:
    """扫描大纲所有具名人物 → 自动建缺失角色 → 填充各角色锁定设定。"""
    try:
        res = await character_design_service.scan_and_fill_characters(session, project_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"[CharRef] 扫描大纲人物失败：{e}", exc_info=True)
        raise HTTPException(500, f"扫描大纲人物失败：{e}")
    return ScanResult(
        created=res["created"], filled=res["filled"],
        skipped=res["skipped"], total_found=res["total_found"],
    )


# --------------------------- 参考图 ---------------------------
@router.get("/projects/{project_id}/characters/{character_id}/ref-images")
def list_ref_images(project_id: int, character_id: int,
                    session: Session = Depends(get_session)) -> list[RefImageResponse]:
    """列出某角色的所有参考图。"""
    _get_character(session, project_id, character_id)
    rows = session.exec(
        select(CharacterRefImage)
        .where(CharacterRefImage.character_id == character_id)
        .order_by(CharacterRefImage.created_at)
    ).all()
    return [_ref_to_resp(r) for r in rows]


@router.post("/projects/{project_id}/characters/{character_id}/ref-images/generate")
async def generate_ref_images(project_id: int, character_id: int,
                              req: GenerateRefRequest,
                              session: Session = Depends(get_session)) -> GenerateRefResult:
    """生成角色参考图。必须先保存锁定设定。

    views 为空时生成全部四视角；同一视角重复生成会替换旧图。
    第一张生成的图若该角色还没有首帧基准，自动设为 is_primary。
    """
    character = _get_character(session, project_id, character_id)
    design = session.exec(
        select(CharacterDesign).where(CharacterDesign.character_id == character_id)
    ).first()
    if not design or not design.locked_prompt:
        raise HTTPException(400, "请先保存该角色的锁定设定（画风/五官/发色/服饰）。")

    views = req.views or list(DEFAULT_VIEWS)
    bad = [v for v in views if v not in VALID_VIEWS]
    if bad:
        raise HTTPException(400, f"不支持的视角：{bad}")

    has_primary = session.exec(
        select(CharacterRefImage)
        .where(CharacterRefImage.character_id == character_id)
        .where(CharacterRefImage.is_primary == True)  # noqa: E712
    ).first() is not None

    results: list[CharacterRefImage] = []
    skipped: list[dict] = []
    for view in views:
        # 脸部特写用专属提示词（不含服饰/体型，防被画成全身）；其余视角用 locked_prompt + 视角词
        if view == "face_closeup":
            prompt = image_client.build_face_closeup_prompt(
                art_style=design.art_style, facial_features=design.facial_features,
                hair=design.hair, gender=design.gender, age=design.age,
                color_tone=design.color_tone,
            )
        else:
            prompt = image_client.build_ref_image_prompt(design.locked_prompt, view)
        filename = f"ref_{character_id}_{view}_{uuid.uuid4().hex[:10]}.png"
        file_path = VIDEOS_DIR / filename
        label = image_client.REF_VIEW_LABELS.get(view, view)
        # 各视角专属画布比例：特写用正方形、全身类用竖图（比提示词更硬的构图约束）
        w, h = image_client.ref_view_size(view, settings.image_width)
        # 各视角附加参数：特写关掉智能扩写（防被扩成全身）+ 负向词压制阴影/下半身
        opts = image_client.ref_view_gen_opts(view)
        logger.info(f"[CharRef] 生成参考图：{character.name} / {view} ({w}x{h}) "
                    f"extend={opts['prompt_extend']}")
        try:
            result = await image_client.generate_image(
                prompt=prompt, style=settings.image_style,
                width=w, height=h,
                save_path=file_path,
                negative_prompt=opts["negative_prompt"],
                prompt_extend=opts["prompt_extend"],
            )
        except Exception as e:
            # 某视角失败（如通义内容审核 DataInspectionFailed）不应让整批崩溃，
            # 跳过该视角、记录原因，其余视角照常生成。
            logger.error(f"[CharRef] 参考图生成失败({view}): {e}", exc_info=True)
            _safe_unlink(file_path)
            msg = str(e)
            if "DataInspectionFailed" in msg or "inappropriate" in msg:
                reason = "被通义内容审核拦截（换描述或重试）"
            else:
                reason = msg[:120]
            skipped.append({"view": view, "view_label": label, "reason": reason})
            continue

        # 同视角旧图替换：删除记录与文件
        olds = session.exec(
            select(CharacterRefImage)
            .where(CharacterRefImage.character_id == character_id)
            .where(CharacterRefImage.view == view)
        ).all()
        for o in olds:
            _safe_unlink(VIDEOS_DIR / o.image_path)
            session.delete(o)

        ref = CharacterRefImage(
            project_id=project_id, character_id=character_id, view=view,
            prompt=prompt, image_path=filename,
            is_primary=(not has_primary),  # 第一张自动设为首帧基准
            width=result["width"], height=result["height"],
        )
        if not has_primary:
            has_primary = True
        session.add(ref)
        session.commit()
        session.refresh(ref)
        results.append(ref)

    # 全部视角都失败才报错；否则返回成功的 + 跳过的
    if not results and skipped:
        reasons = "；".join(f"{s['view_label']}：{s['reason']}" for s in skipped)
        raise HTTPException(502, f"参考图全部生成失败。{reasons}")

    return GenerateRefResult(
        images=[_ref_to_resp(r) for r in results], skipped=skipped,
    )


@router.post("/character-refs/{ref_id}/set-primary")
def set_primary(ref_id: int, session: Session = Depends(get_session)) -> RefImageResponse:
    """把某张参考图设为首帧基准（同角色其它图取消基准）。"""
    ref = session.get(CharacterRefImage, ref_id)
    if not ref:
        raise HTTPException(404, "参考图不存在")
    siblings = session.exec(
        select(CharacterRefImage)
        .where(CharacterRefImage.character_id == ref.character_id)
    ).all()
    for s in siblings:
        s.is_primary = (s.id == ref_id)
        session.add(s)
    session.commit()
    session.refresh(ref)
    logger.info(f"[CharRef] 设为首帧基准：ref_id={ref_id}")
    return _ref_to_resp(ref)


@router.get("/character-refs/{ref_id}/image")
def get_ref_image(ref_id: int, session: Session = Depends(get_session)) -> FileResponse:
    """获取参考图文件。"""
    ref = session.get(CharacterRefImage, ref_id)
    if not ref:
        raise HTTPException(404, "参考图不存在")
    file_path = VIDEOS_DIR / ref.image_path
    if not file_path.exists():
        raise HTTPException(404, "参考图文件不存在")
    return FileResponse(file_path, media_type="image/png")


@router.delete("/character-refs/{ref_id}")
def delete_ref_image(ref_id: int, session: Session = Depends(get_session)) -> dict:
    """删除参考图（记录 + 文件）。"""
    ref = session.get(CharacterRefImage, ref_id)
    if not ref:
        raise HTTPException(404, "参考图不存在")
    _safe_unlink(VIDEOS_DIR / ref.image_path)
    session.delete(ref)
    session.commit()
    return {"message": "参考图已删除"}
