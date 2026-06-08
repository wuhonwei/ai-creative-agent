"""镜头图生视频路由（方案 C：跳过独立文生首帧）。

图生视频种子图：上一镜尾帧 > 角色参考图（首帧基准）。
出片完成后用 ffmpeg 从视频提取首帧/尾帧供前端展示。

生成耗时分钟级，采用后台任务 + 轮询：
  POST /shots/{id}/video        图生视频（后台异步）
  GET  /shots/{id}/video        查当前镜头视频状态
  GET  /videos/{id}/file        取视频文件
  GET  /videos/{id}/poster      取首帧图（从成片提取）
  GET  /videos/{id}/last-frame  取尾帧图（从成片提取）
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

from app.config import resolve_video_duration, settings
from app.db import get_session, engine
from app.llm import kling_client, image_client
from app.models import (
    Character, CharacterDesign, CharacterRefImage, Scene,
    Shot, Storyboard, Video,
)
from app.services import compose, video_frames

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
    video_url: str
    poster_url: str = ""
    last_frame_url: str = ""


def _video_to_resp(v: Video) -> VideoResponse:
    done = v.status == "done"
    return VideoResponse(
        id=v.id, project_id=v.project_id, video_type=v.video_type, ref_id=v.ref_id,
        prompt=v.prompt, duration=v.duration, status=v.status, error=v.error,
        video_url=f"/api/videos/{v.id}/file" if done and v.video_path else "",
        poster_url=f"/api/videos/{v.id}/poster" if done and v.poster_path else "",
        last_frame_url=f"/api/videos/{v.id}/last-frame" if done and v.last_frame_path else "",
    )


def _unlink_video_assets(v: Video) -> None:
    if v.video_path:
        _safe_unlink(VIDEOS_DIR / v.video_path)
    if v.poster_path:
        _safe_unlink(VIDEOS_DIR / v.poster_path)
    if getattr(v, "last_frame_path", "") and v.last_frame_path:
        _safe_unlink(VIDEOS_DIR / v.last_frame_path)


def _get_prev_shot_last_frame_name(session: Session, shot: Shot) -> str:
    """取同一分镜中、排序更靠前的最近一镜的尾帧文件名。"""
    prev_shots = session.exec(
        select(Shot).where(Shot.storyboard_id == shot.storyboard_id)
        .where(Shot.order_index < shot.order_index)
        .order_by(Shot.order_index.desc())
    ).all()
    for prev in prev_shots:
        pv = session.exec(
            select(Video).where(Video.video_type == "shot").where(Video.ref_id == prev.id)
            .where(Video.status == "done").order_by(Video.created_at.desc())
        ).first()
        if pv and pv.last_frame_path and (VIDEOS_DIR / pv.last_frame_path).exists():
            return pv.last_frame_path
    return ""


def _resolve_seed_image(
    session: Session, shot: Shot, ref_image_names: list[str],
) -> tuple[Path, str]:
    """方案 C 种子图：上一镜尾帧优先，否则角色参考图。"""
    prev_last = _get_prev_shot_last_frame_name(session, shot)
    if prev_last:
        return VIDEOS_DIR / prev_last, "上一镜尾帧"
    for name in ref_image_names:
        p = VIDEOS_DIR / name
        if p.exists():
            return p, "角色参考图"
    raise RuntimeError(
        "无法找到图生视频种子图：请为出场角色生成参考图，或先生成前一镜头视频"
    )


def _can_resolve_seed(session: Session, shot: Shot, ref_image_names: list[str]) -> tuple[bool, str]:
    if _get_prev_shot_last_frame_name(session, shot):
        return True, ""
    if any((VIDEOS_DIR / n).exists() for n in ref_image_names if n):
        return True, ""
    return False, (
        "无法找到图生视频种子图：请为出场角色生成参考图（首帧基准），"
        "或先生成排序更靠前镜头的视频以衔接尾帧"
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


async def _run_generation(
    video_id: int, shot_id: int,
    kling_prompt: str, element_ids: list[str], ref_image_names: list[str],
    motion_prompt: str, duration: int,
):
    """后台任务：种子图 → 图生视频 → 提取首尾帧。"""
    with Session(engine) as session:
        v = session.get(Video, video_id)
        if not v:
            return
        shot = session.get(Shot, shot_id)
        try:
            if not shot:
                raise RuntimeError("镜头不存在")
            seed_path, seed_source = _resolve_seed_image(session, shot, ref_image_names)
            logger.info(f"[Videos] 镜头 {shot_id} 图生视频种子：{seed_source} → {seed_path.name}")

            filename = f"shot_{shot_id}_{uuid.uuid4().hex[:10]}.mp4"
            save_path = VIDEOS_DIR / filename
            result = await kling_client.image_to_video(
                image_path=seed_path,
                prompt=kling_prompt or motion_prompt,
                duration=duration,
                element_list=element_ids if element_ids else None,
                negative_prompt=kling_client.KLING_AVOID_TEXT_NEGATIVE,
                save_path=save_path,
            )

            first_name, last_name = video_frames.extract_first_and_last_frames(
                save_path, VIDEOS_DIR, shot_id,
            )
            if v.poster_path and v.poster_path not in (first_name, last_name):
                _safe_unlink(VIDEOS_DIR / v.poster_path)
            if v.last_frame_path and v.last_frame_path not in (first_name, last_name):
                _safe_unlink(VIDEOS_DIR / v.last_frame_path)

            v.video_path = filename
            v.poster_path = first_name
            v.last_frame_path = last_name
            v.duration = result["duration"]
            v.task_id = result["task_id"]
            v.status = "done"
            v.error = ""
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


def _append_unique(target: list[str], value: str) -> None:
    if value and value not in target:
        target.append(value)


def _match_project_scene(session: Session, project_id: int, scene_name: str) -> Scene | None:
    """把镜头 scene 字段匹配到项目已登记场景。"""
    s = (scene_name or "").strip()
    if not s:
        return None
    scenes = session.exec(
        select(Scene).where(Scene.project_id == project_id)
    ).all()
    for sc in scenes:
        if sc.name == s:
            return sc
    for sc in scenes:
        if sc.name and (sc.name in s or s in sc.name):
            return sc
    return None


def _build_kling_video_prompt(
    session: Session,
    shot: Shot,
    board: Storyboard,
    chars: list[dict],
) -> tuple[str, list[str]]:
    """构建可灵图生视频 prompt，并收集 element_id 列表。"""
    element_ids: list[str] = []
    entities: list[tuple[str, str]] = []
    desc = shot.description

    for c in chars:
        eid = (c.get("kling_element_id") or "").strip()
        if eid:
            _append_unique(element_ids, eid)
            entities.append((c["name"], eid))

    if shot.scene:
        scene = _match_project_scene(session, board.project_id, shot.scene)
        if scene and scene.kling_element_id:
            eid = scene.kling_element_id.strip()
            _append_unique(element_ids, eid)
            entities.append((scene.name, eid))

    desc = kling_client.apply_element_tags(desc, element_ids, entities)

    dialogue = (shot.dialogue or "").strip()
    if dialogue and dialogue not in desc and chars:
        speaker = chars[0]
        eid = (speaker.get("kling_element_id") or "").strip()
        name_part = (
            kling_client.entity_prompt_tag(speaker["name"], eid) if eid else speaker["name"]
        )
        speech = f'{name_part}说："{dialogue}"'
        desc = f"{desc}，{speech}" if desc else speech
    desc = f"{desc}，{kling_client.KLING_AVOID_TEXT_POSITIVE}" if desc else kling_client.KLING_AVOID_TEXT_POSITIVE

    return desc, element_ids


def _collect_shot_characters(session: Session, shot: Shot) -> tuple[list[dict], str]:
    """收集本镜所有出场角色的设定与可灵 ID，按 character_ids 顺序。

    只纳入"有锁定设定（外貌词）"的角色——没设定的没法保证形象一致，跳过它。
    返回 (chars, reason)：chars 为空时 reason 给出原因。
    每项：{"id", "name", "appearance", "ref_image", "kling_element_id", "kling_voice_id"}。
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
        kling_vid = d.kling_voice_id if d and d.kling_voice_id else ""

        chars.append({
            "id": cid,
            "name": name,
            "appearance": appearance,
            "ref_image": _primary_ref_image_name(session, cid),
            "kling_element_id": kling_eid,
            "kling_voice_id": kling_vid,
        })

    if not chars:
        who = "、".join(missing) if missing else "出场角色"
        return [], f"出场角色还没有锁定设定（画风/五官/发色/服饰）：{who}"
    return chars, ""


def _prepare_shot_media(
    session: Session, shot: Shot, board: Storyboard,
) -> tuple[Optional[dict], str]:
    """准备镜头图生视频所需的提示词与角色信息。"""
    if not shot.description.strip():
        return None, "镜头没有画面描述"

    chars, reason = _collect_shot_characters(session, shot)
    if not chars:
        return None, reason

    kling_prompt, element_ids = _build_kling_video_prompt(session, shot, board, chars)
    ref_image_names = [c["ref_image"] for c in chars if c["ref_image"]]
    ok, seed_reason = _can_resolve_seed(session, shot, ref_image_names)
    if not ok:
        return None, seed_reason

    motion = shot.description
    if shot.camera:
        motion = f"{motion}，运镜：{shot.camera}"

    return {
        "chars": chars,
        "kling_prompt": kling_prompt,
        "element_ids": element_ids,
        "ref_image_names": ref_image_names,
        "motion": motion,
    }, ""


def _queue_shot_video(session: Session, shot: Shot, board: Storyboard,
                      background: BackgroundTasks) -> tuple[Optional[Video], str]:
    """准备并排队单个镜头的图生视频任务（方案 C：种子图直出，完成后提取首尾帧）。"""
    params, reason = _prepare_shot_media(session, shot, board)
    if not params:
        return None, reason

    olds = session.exec(
        select(Video).where(Video.video_type == "shot").where(Video.ref_id == shot.id)
    ).all()
    for o in olds:
        _unlink_video_assets(o)
        session.delete(o)
    session.commit()

    duration = resolve_video_duration(shot.duration)
    v = Video(project_id=board.project_id, video_type="shot", ref_id=shot.id,
              prompt=params["motion"], status="processing", duration=0.0)
    session.add(v)
    session.commit()
    session.refresh(v)

    background.add_task(
        _run_generation, v.id, shot.id,
        params["kling_prompt"], params["element_ids"], params["ref_image_names"],
        params["motion"], duration,
    )
    names = "、".join(c["name"] for c in params["chars"])
    element_info = f"，element_ids={len(params['element_ids'])}" if params["element_ids"] else ""
    logger.info(f"[Videos] 镜头 {shot.id} 开始后台出片：video_id={v.id}"
                f"，duration={duration}s，出场角色=[{names}]{element_info}")
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


@router.get("/videos/{video_id}/poster")
def get_video_poster(video_id: int,
                     session: Session = Depends(get_session)) -> FileResponse:
    """取首帧图（从成片提取）。"""
    v = session.get(Video, video_id)
    if not v or not v.poster_path:
        raise HTTPException(404, "首帧图不存在")
    fp = VIDEOS_DIR / v.poster_path
    if not fp.exists():
        raise HTTPException(404, "首帧图文件不存在")
    return FileResponse(fp, media_type="image/png")


@router.get("/videos/{video_id}/last-frame")
def get_video_last_frame(video_id: int,
                         session: Session = Depends(get_session)) -> FileResponse:
    """取尾帧图（从成片提取）。"""
    v = session.get(Video, video_id)
    if not v or not v.last_frame_path:
        raise HTTPException(404, "尾帧图不存在")
    fp = VIDEOS_DIR / v.last_frame_path
    if not fp.exists():
        raise HTTPException(404, "尾帧图文件不存在")
    return FileResponse(fp, media_type="image/png")


@router.delete("/videos/{video_id}")
def delete_video(video_id: int, session: Session = Depends(get_session)) -> dict:
    """删除视频（记录 + 文件）。"""
    v = session.get(Video, video_id)
    if not v:
        raise HTTPException(404, "视频不存在")
    _unlink_video_assets(v)
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
