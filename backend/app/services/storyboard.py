"""分镜编排：把章节正文拆成镜头并落库。

复用 generation._extract_json 的 JSON 解析容错；调用 LLM 走非流式 client.chat。
"""
import logging

from sqlmodel import Session, select

from app.config import resolve_video_duration, settings
from app.llm import client, kling_client, prompts
from app.models import (
    Chapter, Character, CharacterDesign, Scene, Shot, Storyboard,
)
from app.services.generation import _extract_json

logger = logging.getLogger(__name__)


def _match_scene(scene_name: str, scene_list: list[dict]) -> dict | None:
    """把镜头 scene 字段匹配到已登记场景（完全匹配优先，其次包含匹配）。"""
    s = (scene_name or "").strip()
    if not s:
        return None
    for sc in scene_list:
        if sc["name"] == s:
            return sc
    for sc in scene_list:
        n = sc["name"]
        if n and (n in s or s in n):
            return sc
    return None


def _finalize_shot_description(
    desc: str,
    dialogue: str,
    cids: list[str],
    char_map: dict[int, dict],
    scene_name: str = "",
    scene_list: list[dict] | None = None,
) -> str:
    """合并台词进画面描述，并为有主体 ID 的角色/场景补上 名称<<<element_id>>>。"""
    desc = (desc or "").strip()
    dialogue = (dialogue or "").strip()
    element_ids: list[str] = []
    entities: list[tuple[str, str]] = []

    for cid in cids:
        c = char_map.get(int(cid))
        if not c:
            continue
        eid = (c.get("kling_element_id") or "").strip()
        if eid and eid not in element_ids:
            element_ids.append(eid)
            entities.append((c["name"], eid))

    matched = _match_scene(scene_name, scene_list or [])
    if matched:
        eid = (matched.get("kling_element_id") or "").strip()
        if eid and eid not in element_ids:
            element_ids.append(eid)
            entities.append((matched["name"], eid))

    if dialogue and dialogue not in desc:
        speaker_name, speaker_eid = "", ""
        for cid in cids:
            c = char_map.get(int(cid))
            if c:
                speaker_name = c["name"]
                speaker_eid = (c.get("kling_element_id") or "").strip()
                break
        if speaker_name:
            name_part = (
                kling_client.entity_prompt_tag(speaker_name, speaker_eid)
                if speaker_eid else speaker_name
            )
            speech = f'{name_part}说："{dialogue}"'
            desc = f"{desc}，{speech}" if desc else speech

    return kling_client.apply_element_tags(desc, element_ids, entities)


async def generate_storyboard(session: Session, project_id: int,
                              chapter: Chapter) -> Storyboard:
    """为某章生成分镜：调用 LLM 拆镜头 → 写入 Storyboard + Shot。"""
    if not chapter.content.strip():
        raise ValueError("本章还没有正文，无法拆分镜。")

    characters = session.exec(
        select(Character).where(Character.project_id == project_id)
    ).all()
    char_dicts: list[dict] = []
    char_map: dict[int, dict] = {}
    for c in characters:
        d = session.exec(
            select(CharacterDesign).where(CharacterDesign.character_id == c.id)
        ).first()
        entry = {
            "id": c.id,
            "name": c.name,
            "kling_element_id": (d.kling_element_id if d else "") or "",
        }
        char_dicts.append(entry)
        char_map[c.id] = entry
    valid_ids = {c.id for c in characters}

    project_scenes = session.exec(
        select(Scene).where(Scene.project_id == project_id)
    ).all()
    scene_list = [
        {
            "name": s.name,
            "description": s.description,
            "kling_element_id": s.kling_element_id or "",
        }
        for s in project_scenes
    ]

    logger.info(f"[Storyboard] 拆分镜：project_id={project_id}, chapter_id={chapter.id}, "
                f"正文 {len(chapter.content)} 字，角色 {len(char_dicts)} 个，"
                f"已登记场景 {len(scene_list)} 个")

    msgs = prompts.build_storyboard_prompt(
        chapter.title, chapter.content, char_dicts, scene_list
    )
    raw = await client.chat(msgs, temperature=0.4, max_tokens=8192)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict) or "shots" not in parsed:
        logger.error(f"[Storyboard] 分镜 JSON 解析失败：{raw[:200]}")
        raise ValueError("分镜生成失败：模型未返回合法的镜头列表。")

    shots = parsed.get("shots") or []
    if not shots:
        raise ValueError("分镜生成失败：镜头列表为空。")

    board = session.exec(
        select(Storyboard).where(Storyboard.chapter_id == chapter.id)
    ).first()
    if board:
        old = session.exec(select(Shot).where(Shot.storyboard_id == board.id)).all()
        for s in old:
            session.delete(s)
        board.title = chapter.title
        board.status = "draft"
    else:
        board = Storyboard(project_id=project_id, chapter_id=chapter.id,
                           title=chapter.title, status="draft")
        session.add(board)
        session.commit()
        session.refresh(board)

    for i, sh in enumerate(shots):
        sh = sh or {}
        cids = [str(c) for c in (sh.get("character_ids") or []) if c in valid_ids]
        duration = float(resolve_video_duration(sh.get("duration", settings.video_duration)))
        raw_scene = str(sh.get("scene", "")).strip()
        raw_desc = str(sh.get("description", "")).strip()
        raw_dialogue = str(sh.get("dialogue", "")).strip()
        description = _finalize_shot_description(
            raw_desc, raw_dialogue, cids, char_map, raw_scene, scene_list,
        )
        session.add(Shot(
            storyboard_id=board.id,
            order_index=i,
            shot_size=str(sh.get("shot_size", "")).strip(),
            camera=str(sh.get("camera", "")).strip(),
            description=description,
            dialogue="",
            sound=str(sh.get("sound", "")).strip(),
            lighting=str(sh.get("lighting", "")).strip(),
            scene=raw_scene,
            duration=duration,
            character_ids=",".join(cids),
        ))

    session.commit()
    session.refresh(board)
    logger.info(f"[Storyboard] 分镜完成：storyboard_id={board.id}, {len(shots)} 个镜头")
    return board
