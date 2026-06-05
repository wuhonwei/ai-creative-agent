"""镜头图生视频路由。

以"首帧基准图"作首帧调用可灵，杜绝跨镜头变脸：
  镜头有出场角色 → 取该角色的首帧基准参考图(is_primary)做首帧；
  没有出场角色 → 需调用方提供首帧图 asset。

生成耗时分钟级，采用后台任务 + 轮询：
  POST /shots/{id}/video        立即建 Video(status=processing) 并后台跑，返回该记录
  GET  /shots/{id}/video        查当前镜头视频状态
  GET  /videos/{id}/file        取视频文件
"""
import logging
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session, engine
from app.llm import kling_client, image_client
from app.models import Character, CharacterDesign, CharacterRefImage, Shot, Storyboard, Video
from app.services import compose

router = APIRouter(prefix="/api", tags=["videos"])
logger = logging.getLogger(__name__)

VIDEOS_DIR = Path(__file__).parent.parent.parent / "videos"
VIDEOS_DIR.mkdir(exist_ok=True)


def _safe_unlink(path: Path) -> None:
    """删除文件，删不掉不报错（Windows 下文件可能被播放器/句柄占用）。

    视频文件名是 uuid 唯一的，删不掉旧文件不影响新文件生成，
    顶多留个孤儿文件，不该阻断重新生成流程。
    """
    try:
        if path.exists():
            path.unlink()
    except OSError as e:
        logger.warning(f"[Videos] 删除文件失败（已忽略，可能被占用）：{path} - {e}")


class VideoResponse(BaseModel):
    id: int
    project_id: int
    video_type: str
    ref_id: Optional[int]
    prompt: str
    duration: float
    status: str
    error: str
    video_url: str        # 本地文件服务路径（done 时有效）


def _video_to_resp(v: Video) -> VideoResponse:
    return VideoResponse(
        id=v.id, project_id=v.project_id, video_type=v.video_type, ref_id=v.ref_id,
        prompt=v.prompt, duration=v.duration, status=v.status, error=v.error,
        video_url=f"/api/videos/{v.id}/file" if v.status == "done" else "",
    )


def _parse_all_ids(s: str) -> list[int]:
    """解析 character_ids（逗号分隔）中所有角色 id，按出现顺序、去重。"""
    ids: list[int] = []
    for part in (s or "").split(","):
        part = part.strip()
        if part.isdigit():
            cid = int(part)
            if cid not in ids:
                ids.append(cid)
    return ids


async def _run_generation(video_id: int, shot_id: int, frame_prompt: str,
                          edit_prompt: str, ref_image_names: list[str],
                          motion_prompt: str, duration: int,
                          element_ids: list[str], kling_prompt: str):
    """后台任务：先出"符合本镜场景"的首帧 → 再图生视频 → 回写 Video。

    首帧生成两条路：
      有角色锁定参考图 → 用 edit_image(qwen-image-edit) 以参考图重绘，
        角色长相严格锁定参考图，姿势/环境按本镜场景（edit_prompt）；
        多人物镜头会把所有出场角色的参考图一起喂进去，各自锁形象。
      没有参考图 → 退回纯文生图（frame_prompt，场景优先+外貌词）。
    motion_prompt：运动/运镜描述，传给可灵控制动态。
    element_ids：可灵 element_id 列表（角色/场景一致性 ID）。
    kling_prompt：带 <<element_id>> 标记的提示词（用于可灵 API）。
    用独立 session（脱离请求生命周期）。
    """
    with Session(engine) as session:
        v = session.get(Video, video_id)
        if not v:
            return
        frame_path = None
        try:
            # 1) 生成符合本镜场景的首帧图
            frame_name = f"frame_{shot_id}_{uuid.uuid4().hex[:10]}.png"
            frame_path = VIDEOS_DIR / frame_name
            ref_paths = [VIDEOS_DIR / n for n in (ref_image_names or [])]
            ref_paths = [p for p in ref_paths if p.exists()]
            if ref_paths:
                # 以角色锁定参考图重绘：长相锁定，姿势/场景按本镜
                refs_label = ", ".join(p.name for p in ref_paths)
                logger.info(f"[Videos] 镜头 {shot_id} 参考图重绘首帧"
                            f"（{len(ref_paths)} 张 refs=[{refs_label}]）：{edit_prompt[:80]}")
                await image_client.edit_image(
                    prompt=edit_prompt,
                    ref_image_paths=ref_paths,
                    width=settings.image_width, height=settings.image_height,
                    save_path=frame_path,
                )
            else:
                # 无参考图，退回纯文生图（场景优先 + 外貌词）
                logger.info(f"[Videos] 镜头 {shot_id} 文生首帧（无参考图）："
                            f"{frame_prompt[:80]}")
                await image_client.generate_image(
                    prompt=frame_prompt,
                    width=settings.image_width, height=settings.image_height,
                    save_path=frame_path,
                )
            v.poster_path = frame_name  # 存首帧图作封面

            # 2) 图生视频：以该首帧出片，使用 element_id 确保角色/场景一致性
            filename = f"shot_{shot_id}_{uuid.uuid4().hex[:10]}.mp4"
            save_path = VIDEOS_DIR / filename
            result = await kling_client.image_to_video(
                image_path=frame_path,
                prompt=kling_prompt or motion_prompt,  # 优先使用带 element_id 标记的 prompt
                duration=duration,
                element_list=element_ids if element_ids else None,
                save_path=save_path,
            )
            v.video_path = filename
            v.duration = result["duration"]
            v.task_id = result["task_id"]
            v.status = "done"
            v.updated_at = datetime.utcnow()
            logger.info(f"[Videos] 镜头 {shot_id} 出片完成：video_id={video_id}")
        except Exception as e:
            v.status = "failed"
            v.error = str(e)[:500]
            v.updated_at = datetime.utcnow()
            logger.error(f"[Videos] 镜头 {shot_id} 出片失败：{e}", exc_info=True)
        session.add(v)
        session.commit()


def _primary_ref_image_name(session: Session, character_id: int) -> Optional[str]:
    """取角色的首帧基准参考图文件名（is_primary）；没有则取任意一张；都没有返回 None。"""
    primary = session.exec(
        select(CharacterRefImage)
        .where(CharacterRefImage.character_id == character_id)
        .where(CharacterRefImage.is_primary == True)  # noqa: E712
    ).first()
    if primary and primary.image_path:
        return primary.image_path
    # 没设首帧基准时，退而取该角色任意一张参考图
    any_ref = session.exec(
        select(CharacterRefImage)
        .where(CharacterRefImage.character_id == character_id)
        .order_by(CharacterRefImage.id)
    ).first()
    return any_ref.image_path if any_ref and any_ref.image_path else None


def _character_appearance(session: Session, character_id: int) -> Optional[str]:
    """取某角色的外貌锁定词（来自 CharacterDesign），没有设定则返回 None。"""
    d = session.exec(
        select(CharacterDesign).where(CharacterDesign.character_id == character_id)
    ).first()
    if not d:
        return None
    return image_client.build_appearance_prompt(
        d.art_style, d.facial_features, d.hair, d.outfit, d.extra,
        gender=d.gender, age=d.age, body_type=d.body_type, color_tone=d.color_tone,
    )


def _collect_shot_characters(session: Session, shot: Shot) -> tuple[list[dict], str]:
    """收集本镜所有出场角色的 {name, appearance, ref_image, kling_element_id}，按 character_ids 顺序。

    只纳入"有锁定设定（外貌词）"的角色——没设定的没法保证形象一致，跳过它。
    返回 (chars, reason)：chars 为空时 reason 给出原因。
    每项：{"id", "name", "appearance", "ref_image"(可能 None), "kling_element_id"}。
    ref_image 顺序与 appearance 顺序严格对应，供 edit_image 多图按序锁脸。
    """
    cids = _parse_all_ids(shot.character_ids)
    if not cids:
        return [], "没有出场角色"

    chars: list[dict] = []
    missing: list[str] = []
    for cid in cids:
        appearance = _character_appearance(session, cid)
        ch = session.get(Character, cid)
        name = ch.name if ch else f"角色{cid}"
        if appearance is None:
            missing.append(name)
            continue

        # 获取 kling_element_id
        d = session.exec(
            select(CharacterDesign).where(CharacterDesign.character_id == cid)
        ).first()
        kling_eid = d.kling_element_id if d and d.kling_element_id else ""

        chars.append({
            "id": cid,
            "name": name,
            "appearance": appearance,
            "ref_image": _primary_ref_image_name(session, cid),
            "kling_element_id": kling_eid,
        })

    if not chars:
        who = "、".join(missing) if missing else "出场角色"
        return [], f"出场角色还没有锁定设定（画风/五官/发色/服饰）：{who}"
    return chars, ""


def _queue_shot_video(session: Session, shot: Shot, board: Storyboard,
                      background: BackgroundTasks) -> tuple[Optional[Video], str]:
    """准备并排队单个镜头的图生视频任务。

    流程：收集本镜所有出场角色（设定词 + 锁定参考图）→ 文生图/参考图重绘首帧 → 图生视频。
    多人物镜头会把所有出场角色的参考图一起喂进 edit_image，各自锁形象。
    成功返回 (Video(processing), "")；无法生成返回 (None, 原因)。
    供单镜头端点（失败抛错）和批量端点（失败软跳过）共用。
    """
    if not shot.description.strip():
        return None, "镜头没有画面描述"

    # 收集所有出场角色的设定词 + 锁定参考图 + element_id（按出场顺序，跳过没设定的）
    chars, reason = _collect_shot_characters(session, shot)
    if not chars:
        return None, reason

    # 收集所有 element_id（角色 + 场景）
    element_ids = []
    kling_prompt_parts = []

    # 构建带 <<element_id>> 标记的 prompt
    # 例如：乔病病<<girl1>>冲进了<<place1>>
    desc = shot.description

    # 为每个角色添加 element_id 标记
    for c in chars:
        eid = c.get("kling_element_id", "")
        if eid:
            element_ids.append(eid)
            # 在角色名后添加 <<element_id>> 标记
            name = c["name"]
            if name in desc:
                desc = desc.replace(name, f"{name}<<{eid}>>", 1)

    # 获取场景的 element_id（如果有）
    if shot.scene:
        # 尝试从 Scene 表查找场景的 element_id
        from app.models import Scene, ChapterScene
        # 先找本章关联的场景
        board = session.get(Storyboard, shot.storyboard_id)
        if board and board.chapter_id:
            scene_links = session.exec(
                select(ChapterScene).where(ChapterScene.chapter_id == board.chapter_id)
            ).all()
            for link in scene_links:
                scene = session.get(Scene, link.scene_id)
                if scene and scene.kling_element_id and shot.scene in scene.name:
                    element_ids.append(scene.kling_element_id)
                    # 在场景名后添加标记
                    if scene.name in desc:
                        desc = desc.replace(scene.name, f"{scene.name}<<{scene.kling_element_id}>>", 1)
                    break

    kling_prompt = desc  # 带 element_id 标记的 prompt

    # 首帧图提示词：场景优先 + 外貌锁定（纯文生图回退用，拼所有出场角色外貌）
    appearance_all = "；".join(
        f"{c['name']}（{c['appearance']}）" if len(chars) > 1 else c["appearance"]
        for c in chars
    )
    frame_prompt = image_client.build_shot_frame_prompt(
        appearance=appearance_all, scene_desc=shot.description, camera=shot.camera,
        shot_size=shot.shot_size, scene=shot.scene, lighting=shot.lighting,
    )
    # 参考图重绘指令：锁定各角色形象，按本镜场景改姿势/环境（多人逐个点名）
    edit_prompt = image_client.build_shot_edit_prompt(
        scene_desc=shot.description, camera=shot.camera, characters=chars,
        shot_size=shot.shot_size, scene=shot.scene, lighting=shot.lighting,
    )
    # 各角色锁定参考图（顺序与 edit_prompt 中角色顺序一致），过滤掉没参考图的
    ref_image_names = [c["ref_image"] for c in chars if c["ref_image"]]
    # 运动提示词：画面描述 + 运镜
    motion = shot.description
    if shot.camera:
        motion = f"{motion}，运镜：{shot.camera}"

    # 删除该镜头的旧视频记录（重生成）
    olds = session.exec(
        select(Video).where(Video.video_type == "shot").where(Video.ref_id == shot.id)
    ).all()
    for o in olds:
        if o.video_path:
            _safe_unlink(VIDEOS_DIR / o.video_path)
        if o.poster_path:
            _safe_unlink(VIDEOS_DIR / o.poster_path)
        session.delete(o)
    session.commit()

    duration = int(shot.duration) if shot.duration in (5, 10) else settings.video_duration
    v = Video(project_id=board.project_id, video_type="shot", ref_id=shot.id,
              prompt=motion, status="processing", duration=0.0)
    session.add(v)
    session.commit()
    session.refresh(v)

    background.add_task(_run_generation, v.id, shot.id, frame_prompt, edit_prompt,
                        ref_image_names, motion, duration, element_ids, kling_prompt)
    names = "、".join(c["name"] for c in chars)
    element_info = f"，element_ids={len(element_ids)}" if element_ids else ""
    logger.info(f"[Videos] 镜头 {shot.id} 开始后台出片：video_id={v.id}"
                f"，出场角色=[{names}]，参考图 {len(ref_image_names)} 张"
                f"{'（纯文生图）' if not ref_image_names else ''}{element_info}")
    return v, ""


@router.post("/shots/{shot_id}/video")
async def generate_shot_video(shot_id: int, background: BackgroundTasks,
                              session: Session = Depends(get_session)) -> VideoResponse:
    """为镜头生成视频（后台异步）。立即返回 processing 记录，前端轮询状态。"""
    if not settings.kling_configured:
        raise HTTPException(400, "未配置可灵 AccessKey / SecretKey")

    shot = session.get(Shot, shot_id)
    if not shot:
        raise HTTPException(404, "镜头不存在")
    board = session.get(Storyboard, shot.storyboard_id)
    if not board:
        raise HTTPException(404, "分镜不存在")

    v, reason = _queue_shot_video(session, shot, board, background)
    if not v:
        raise HTTPException(400, f"无法生成：{reason}。"
                            "请确认镜头有画面描述、指定了出场角色，且该角色已在「角色设定锁定」里保存设定。")
    return _video_to_resp(v)


class BatchVideoResponse(BaseModel):
    queued: list[VideoResponse]      # 已排队生成的镜头视频
    skipped: list[dict]              # 跳过的镜头 {shot_id, order_index, reason}
    total_shots: int


@router.post("/chapters/{chapter_id}/shots/generate-videos")
async def generate_chapter_shot_videos(chapter_id: int, background: BackgroundTasks,
                                       session: Session = Depends(get_session)) -> BatchVideoResponse:
    """一键为本章所有镜头生成视频（后台异步）。

    缺出场角色 / 缺基准参考图的镜头会被软跳过并在 skipped 中说明，
    其余镜头全部排队。前端逐镜轮询各自状态。
    """
    if not settings.kling_configured:
        raise HTTPException(400, "未配置可灵 AccessKey / SecretKey")

    board = session.exec(
        select(Storyboard).where(Storyboard.chapter_id == chapter_id)
    ).first()
    if not board:
        raise HTTPException(404, "本章还没有分镜。请先拆分镜。")

    shots = session.exec(
        select(Shot).where(Shot.storyboard_id == board.id).order_by(Shot.order_index)
    ).all()
    if not shots:
        raise HTTPException(400, "本章分镜没有镜头。")

    queued: list[VideoResponse] = []
    skipped: list[dict] = []
    for shot in shots:
        v, reason = _queue_shot_video(session, shot, board, background)
        if v:
            queued.append(_video_to_resp(v))
        else:
            skipped.append({"shot_id": shot.id, "order_index": shot.order_index,
                            "reason": reason})

    logger.info(f"[Videos] 章节 {chapter_id} 批量出片：排队 {len(queued)} 个，跳过 {len(skipped)} 个")
    return BatchVideoResponse(queued=queued, skipped=skipped, total_shots=len(shots))


@router.get("/shots/{shot_id}/video")
def get_shot_video(shot_id: int,
                   session: Session = Depends(get_session)) -> Optional[VideoResponse]:
    """查镜头当前视频状态（没有则 null）。"""
    v = session.exec(
        select(Video).where(Video.video_type == "shot").where(Video.ref_id == shot_id)
        .order_by(Video.created_at.desc())
    ).first()
    return _video_to_resp(v) if v else None


@router.get("/videos/{video_id}/file")
def get_video_file(video_id: int,
                   session: Session = Depends(get_session)) -> FileResponse:
    """取视频文件。"""
    v = session.get(Video, video_id)
    if not v or not v.video_path:
        raise HTTPException(404, "视频不存在")
    fp = VIDEOS_DIR / v.video_path
    if not fp.exists():
        raise HTTPException(404, "视频文件不存在")
    return FileResponse(fp, media_type="video/mp4")


@router.delete("/videos/{video_id}")
def delete_video(video_id: int, session: Session = Depends(get_session)) -> dict:
    """删除视频（记录 + 文件）。"""
    v = session.get(Video, video_id)
    if not v:
        raise HTTPException(404, "视频不存在")
    if v.video_path:
        _safe_unlink(VIDEOS_DIR / v.video_path)
    session.delete(v)
    session.commit()
    return {"message": "视频已删除"}


# --------------------------- 章节合成 ---------------------------
async def _run_compose(video_id: int, storyboard_id: int):
    """后台任务：收集本章镜头片段 → ffmpeg 拼接 → 回写 Video 记录。"""
    with Session(engine) as session:
        v = session.get(Video, video_id)
        if not v:
            return
        try:
            clips = compose.collect_shot_clips(session, storyboard_id)
            if not clips:
                raise RuntimeError("本章还没有任何已完成的镜头视频，无法合成。")
            filename = f"chapter_{v.ref_id}_{uuid.uuid4().hex[:10]}.mp4"
            out_path = VIDEOS_DIR / filename
            duration = compose.concat_clips(clips, out_path)
            v.video_path = filename
            v.duration = duration
            v.status = "done"
            v.updated_at = datetime.utcnow()
            logger.info(f"[Compose] 章节成片完成：video_id={video_id}, {len(clips)} 段, {duration}s")
        except Exception as e:
            v.status = "failed"
            v.error = str(e)[:500]
            v.updated_at = datetime.utcnow()
            logger.error(f"[Compose] 章节合成失败：{e}", exc_info=True)
        session.add(v)
        session.commit()


@router.post("/chapters/{chapter_id}/compose")
def compose_chapter(chapter_id: int, background: BackgroundTasks,
                    session: Session = Depends(get_session)) -> VideoResponse:
    """把本章所有已出片的镜头按顺序合成为章节成片（后台异步）。"""
    board = session.exec(
        select(Storyboard).where(Storyboard.chapter_id == chapter_id)
    ).first()
    if not board:
        raise HTTPException(404, "本章还没有分镜，无法合成。")

    # 预检：至少有一个已完成的镜头片段
    clips = compose.collect_shot_clips(session, board.id)
    if not clips:
        raise HTTPException(400, "本章还没有任何已完成的镜头视频。请先逐镜生成视频。")

    # 删除旧的章节成片记录
    olds = session.exec(
        select(Video).where(Video.video_type == "chapter").where(Video.ref_id == chapter_id)
    ).all()
    for o in olds:
        if o.video_path:
            _safe_unlink(VIDEOS_DIR / o.video_path)
        session.delete(o)
    session.commit()

    v = Video(project_id=board.project_id, video_type="chapter", ref_id=chapter_id,
              prompt=f"{len(clips)} 个镜头合成", status="processing", duration=0.0)
    session.add(v)
    session.commit()
    session.refresh(v)

    background.add_task(_run_compose, v.id, board.id)
    logger.info(f"[Compose] 章节 {chapter_id} 开始后台合成：video_id={v.id}, {len(clips)} 段")
    return _video_to_resp(v)


@router.get("/chapters/{chapter_id}/compose")
def get_chapter_video(chapter_id: int,
                      session: Session = Depends(get_session)) -> Optional[VideoResponse]:
    """查本章成片状态（没有则 null）。"""
    v = session.exec(
        select(Video).where(Video.video_type == "chapter").where(Video.ref_id == chapter_id)
        .order_by(Video.created_at.desc())
    ).first()
    return _video_to_resp(v) if v else None
