"""角色视觉设定锁定 + 参考图（脸部特写/正/侧/背/全身）生成与管理。

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
from app.llm import image_client, kling_client
from app.models import Character, CharacterDesign, CharacterRefImage, Project
from app.services import character_design as character_design_service

router = APIRouter(prefix="/api", tags=["character_refs"])
logger = logging.getLogger(__name__)

# 视频流水线产物根目录（参考图 / 镜头片段 / 成片）
VIDEOS_DIR = Path(__file__).parent.parent.parent / "videos"
VIDEOS_DIR.mkdir(exist_ok=True)

# 默认生成的 5 个视角（顺序即展示顺序）：脸部特写 / 正 / 侧 / 背 / 全身
DEFAULT_VIEWS = ("face_closeup", "front", "side", "back", "full_body")
# 合成主体 ID 必须齐备的五视角（本地全部生成，保证设定完整）
REQUIRED_ELEMENT_VIEWS = ("face_closeup", "front", "side", "back", "full_body")
# 提交可灵时的选图优先级（从五视角中最多取 3 张传给 API）
ELEMENT_SUBMIT_PRIORITY = ("front", "side", "back", "face_closeup", "full_body")
MAX_ELEMENT_SUBMIT_IMAGES = 3
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
    kling_voice_id: str = ""


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
    kling_voice_id: str = ""
    kling_element_id: str = ""


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
    """生成参考图。views 为空则生成全部五视角。"""
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
        kling_voice_id=d.kling_voice_id or "",
        kling_element_id=d.kling_element_id or "",
    )


def _ref_to_resp(r: CharacterRefImage) -> RefImageResponse:
    return RefImageResponse(
        id=r.id, character_id=r.character_id, view=r.view,
        view_label=image_client.REF_VIEW_LABELS.get(r.view, r.view),
        prompt=r.prompt, is_primary=r.is_primary,
        width=r.width, height=r.height,
        image_url=f"/api/character-refs/{r.id}/image",
    )


def _kling_ref_negative(view: str) -> str:
    """可灵参考图负向词：视角专属压制 + 禁止画面文字。"""
    opts = image_client.ref_view_gen_opts(view)
    return f"{opts['negative_prompt']}，{kling_client.KLING_AVOID_TEXT_NEGATIVE}"


def _append_no_text_positive(prompt: str) -> str:
    return f"{prompt}，{kling_client.KLING_AVOID_TEXT_POSITIVE}"


def _ref_by_view(refs: list[CharacterRefImage], view: str) -> Optional[CharacterRefImage]:
    return next((r for r in refs if r.view == view), None)


def _ref_path_for_view(refs: list[CharacterRefImage], view: str) -> Optional[Path]:
    ref = _ref_by_view(refs, view)
    if not ref or not ref.image_path:
        return None
    p = VIDEOS_DIR / ref.image_path
    return p if p.exists() else None


def _collect_element_submit_images(
    refs: list[CharacterRefImage],
) -> tuple[Path, list[Path], list[str]]:
    """按优先级从五视角中选取最多 3 张提交可灵。

    优先级：正视图 → 侧视图 → 背视图 → 脸部特写 → 全身立绘。
    第 1 张作 frontal_image，其余作 refer_images（refer 最多 2 张，合计不超过 3 张）。
    若侧/背缺失，依次用脸部特写、全身替补。
    """
    selected_views: list[str] = []
    for view in ELEMENT_SUBMIT_PRIORITY:
        if len(selected_views) >= MAX_ELEMENT_SUBMIT_IMAGES:
            break
        if _ref_path_for_view(refs, view):
            selected_views.append(view)

    if not selected_views or selected_views[0] != "front":
        raise HTTPException(400, "正视图参考图不存在，请重新生成")

    frontal = _ref_path_for_view(refs, "front")
    assert frontal is not None

    refer_paths: list[Path] = []
    for view in selected_views[1:]:
        p = _ref_path_for_view(refs, view)
        if p:
            refer_paths.append(p)

    if not refer_paths:
        raise HTTPException(400, "除正视图外至少需要一张参考图（侧视/背视优先）")

    return frontal, refer_paths, selected_views


def _ensure_front_primary(session: Session, character_id: int) -> None:
    """正视图存在时设为首帧基准（用于镜头出片复用）。"""
    front = session.exec(
        select(CharacterRefImage)
        .where(CharacterRefImage.character_id == character_id)
        .where(CharacterRefImage.view == "front")
    ).first()
    if not front:
        return
    others = session.exec(
        select(CharacterRefImage)
        .where(CharacterRefImage.character_id == character_id)
        .where(CharacterRefImage.is_primary == True)  # noqa: E712
    ).all()
    for r in others:
        if r.id != front.id:
            r.is_primary = False
            session.add(r)
    if not front.is_primary:
        front.is_primary = True
        session.add(front)


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
        d.kling_voice_id = req.kling_voice_id.strip()
        d.locked_prompt = locked
        d.updated_at = datetime.utcnow()
    else:
        d = CharacterDesign(
            project_id=project_id, character_id=character_id,
            gender=req.gender, age=req.age, body_type=req.body_type,
            color_tone=req.color_tone,
            art_style=req.art_style, facial_features=req.facial_features,
            hair=req.hair, outfit=req.outfit, extra=req.extra,
            kling_voice_id=req.kling_voice_id.strip(),
            locked_prompt=locked,
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


class CreateElementRequest(BaseModel):
    voice_id: str = ""


@router.post("/projects/{project_id}/characters/{character_id}/element")
async def create_character_element(
    project_id: int,
    character_id: int,
    req: CreateElementRequest,
    session: Session = Depends(get_session),
) -> DesignResponse:
    """用五视角参考图（脸部特写/正/侧/背/全身）+ 音色 ID 调用可灵 API 合成主体 ID。"""
    if not settings.kling_configured:
        raise HTTPException(400, "未配置可灵 AccessKey / SecretKey")

    character = _get_character(session, project_id, character_id)
    design = session.exec(
        select(CharacterDesign).where(CharacterDesign.character_id == character_id)
    ).first()
    if not design or not design.locked_prompt:
        raise HTTPException(400, "请先保存该角色的锁定设定")

    voice_id = (req.voice_id or design.kling_voice_id or "").strip()
    if not voice_id:
        raise HTTPException(400, "请填写音色 ID")

    refs = session.exec(
        select(CharacterRefImage)
        .where(CharacterRefImage.character_id == character_id)
        .order_by(CharacterRefImage.created_at)
    ).all()
    missing_views = [
        v for v in REQUIRED_ELEMENT_VIEWS if not _ref_by_view(refs, v)
    ]
    if missing_views:
        labels = [image_client.REF_VIEW_LABELS.get(v, v) for v in missing_views]
        raise HTTPException(
            400,
            f"合成主体需要完整五视角参考图，缺少：{'、'.join(labels)}。请先一键生成全套。",
        )

    frontal_path, refer_paths, selected_views = _collect_element_submit_images(refs)
    selected_labels = [
        image_client.REF_VIEW_LABELS.get(v, v) for v in selected_views
    ]
    logger.info(
        f"[CharRef] 主体合成选图：character_id={character_id}, "
        f"frontal=正视图, refer={len(refer_paths)} 张 "
        f"（{'、'.join(selected_labels)}）"
    )

    desc = design.locked_prompt or character.description or character.name
    try:
        result = await kling_client.create_and_poll_custom_element(
            element_name=character.name,
            element_description=desc,
            frontal_image_path=frontal_path,
            refer_image_paths=refer_paths,
            voice_id=voice_id,
            tag_list=kling_client.character_element_tags(),
        )
    except kling_client.KlingError as e:
        raise HTTPException(502, str(e)) from e

    from datetime import datetime
    design.kling_voice_id = voice_id
    design.kling_element_id = result["element_id"]
    design.updated_at = datetime.utcnow()
    session.add(design)
    session.commit()
    session.refresh(design)
    logger.info(
        f"[CharRef] 主体合成成功：character_id={character_id}, "
        f"element_id={design.kling_element_id}"
    )
    return _design_to_resp(design)


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
    """可灵文生图生成角色参考图。必须先保存锁定设定。

    views 为空时生成全部五视角；同一视角重复生成会替换旧图。
    正视图生成后自动设为首帧基准。
    """
    if not settings.kling_configured:
        raise HTTPException(400, "未配置可灵 AccessKey / SecretKey")

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
        prompt = _append_no_text_positive(prompt)
        filename = f"ref_{character_id}_{view}_{uuid.uuid4().hex[:10]}.png"
        file_path = VIDEOS_DIR / filename
        label = image_client.REF_VIEW_LABELS.get(view, view)
        w, h = image_client.ref_view_size(view, settings.image_width)
        aspect_ratio = image_client.ref_view_aspect_ratio(view)
        negative_prompt = _kling_ref_negative(view)
        logger.info(
            f"[CharRef] 可灵文生参考图：{character.name} / {view} "
            f"({aspect_ratio}, 记录尺寸 {w}x{h})"
        )
        try:
            await kling_client.generate_image(
                prompt=prompt,
                negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio,
                save_path=file_path,
            )
        except Exception as e:
            logger.error(f"[CharRef] 参考图生成失败({view}): {e}", exc_info=True)
            _safe_unlink(file_path)
            reason = str(e)[:120]
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
            is_primary=False,
            width=w, height=h,
        )
        session.add(ref)
        session.commit()
        session.refresh(ref)
        results.append(ref)

    if results:
        _ensure_front_primary(session, character_id)
        session.commit()

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
