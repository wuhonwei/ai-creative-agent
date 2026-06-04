"""分镜编排：把章节正文拆成镜头并落库。

复用 generation._extract_json 的 JSON 解析容错；调用 LLM 走非流式 client.chat。
"""
import logging

from sqlmodel import Session, select

from app.llm import client, prompts
from app.models import Chapter, Character, ChapterScene, Scene, Shot, Storyboard
from app.services.generation import _extract_json

logger = logging.getLogger(__name__)


def _snap_scene(raw_scene: str, planned: list[dict]) -> str:
    """把 LLM 给的 scene 归一到本章已规划的场景名（强制名称一致）。

    优先完全匹配；其次包含匹配（LLM 可能加了修饰词）；都不中则回退到第一个已规划场景。
    保证镜头的 scene 一定是某个已规划场景的精确名称。
    """
    names = [p["name"] for p in planned]
    if not names:
        return raw_scene.strip()
    s = (raw_scene or "").strip()
    if s in names:
        return s
    for n in names:
        if n and (n in s or s in n):
            return n
    return names[0]


async def generate_storyboard(session: Session, project_id: int,
                              chapter: Chapter) -> Storyboard:
    """为某章生成分镜：调用 LLM 拆镜头 → 写入 Storyboard + Shot。

    若该章已有分镜，先删除旧镜头再重建（重新拆分镜）。
    镜头的 scene 只能取本章已规划场景（ChapterScene），名称强制与场景一致。
    """
    if not chapter.content.strip():
        raise ValueError("本章还没有正文，无法拆分镜。")

    # 已登记角色（供模型标注出场角色 id）
    characters = session.exec(
        select(Character).where(Character.project_id == project_id)
    ).all()
    char_dicts = [{"id": c.id, "name": c.name} for c in characters]
    valid_ids = {c.id for c in characters}

    # 本章已规划场景（拆分镜只能用这些场景，名称必须一致）
    links = session.exec(
        select(ChapterScene).where(ChapterScene.chapter_id == chapter.id)
    ).all()
    planned_scenes: list[dict] = []
    for link in links:
        sc = session.get(Scene, link.scene_id)
        if sc:
            planned_scenes.append({"name": sc.name, "description": sc.description})
    if not planned_scenes:
        raise ValueError("本章还没有规划场景。请先到「场景规划」为本章规划场景，再拆分镜。")

    logger.info(f"[Storyboard] 拆分镜：project_id={project_id}, chapter_id={chapter.id}, "
                f"正文 {len(chapter.content)} 字，角色 {len(char_dicts)} 个，"
                f"可用场景 {len(planned_scenes)} 个")

    msgs = prompts.build_storyboard_prompt(
        chapter.title, chapter.content, char_dicts, planned_scenes
    )
    raw = await client.chat(msgs, temperature=0.4, max_tokens=8192)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict) or "shots" not in parsed:
        logger.error(f"[Storyboard] 分镜 JSON 解析失败：{raw[:200]}")
        raise ValueError("分镜生成失败：模型未返回合法的镜头列表。")

    shots = parsed.get("shots") or []
    if not shots:
        raise ValueError("分镜生成失败：镜头列表为空。")

    # 找到或新建 Storyboard，清掉旧镜头
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

    # 写入镜头
    for i, sh in enumerate(shots):
        sh = sh or {}
        # 过滤出有效的角色 id
        cids = [str(c) for c in (sh.get("character_ids") or []) if c in valid_ids]
        try:
            duration = float(sh.get("duration", 5) or 5)
        except (TypeError, ValueError):
            duration = 5.0
        session.add(Shot(
            storyboard_id=board.id,
            order_index=i,
            shot_size=str(sh.get("shot_size", "")).strip(),
            camera=str(sh.get("camera", "")).strip(),
            description=str(sh.get("description", "")).strip(),
            dialogue=str(sh.get("dialogue", "")).strip(),
            sound=str(sh.get("sound", "")).strip(),
            lighting=str(sh.get("lighting", "")).strip(),
            scene=_snap_scene(str(sh.get("scene", "")), planned_scenes),
            duration=duration,
            character_ids=",".join(cids),
        ))

    session.commit()
    session.refresh(board)
    logger.info(f"[Storyboard] 分镜完成：storyboard_id={board.id}, {len(shots)} 个镜头")
    return board
