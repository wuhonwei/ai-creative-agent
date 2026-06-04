"""按章节抽取场景 + 变化检测（复用已有 / 新建有变化的场景）。

调用 LLM 把一章正文里的地点抽出来，与全项目已有场景比对：
  - 同一地点 → 复用（建立 ChapterScene 关联，跨章共用一张场景图）
  - 新地点 / 显著变化 → 新建 Scene（带完整视觉字段 + locked_prompt）
"""
import logging
from datetime import datetime

from sqlmodel import Session, select

from app.llm import client, image_client, prompts
from app.models import Chapter, ChapterScene, Project, Scene
from app.services.generation import _extract_json

logger = logging.getLogger(__name__)

_SCENE_FIELDS = ["style", "time_weather", "color_tone", "space_desc", "materials",
                 "vista", "light_source", "light_tech", "viewpoint", "composition"]


def _rebuild_locked(scene: Scene) -> str:
    return image_client.build_scene_prompt(
        style=scene.style, time_weather=scene.time_weather, color_tone=scene.color_tone,
        space_desc=scene.space_desc, materials=scene.materials, vista=scene.vista,
        light_source=scene.light_source, light_tech=scene.light_tech,
        viewpoint=scene.viewpoint, composition=scene.composition,
    )


async def plan_chapter_scenes(session: Session, project_id: int,
                              chapter: Chapter) -> dict:
    """为某章规划场景：LLM 抽取 + 变化检测，落库 Scene 与 ChapterScene 关联。

    重跑某章会先清空该章旧关联（不删场景本身，避免影响其它章复用），再重建。
    返回 {reused: [...], created: [...], scenes: [Scene...]}。
    """
    if not chapter.content.strip():
        raise ValueError("本章还没有正文，无法规划场景。")

    project = session.get(Project, project_id)
    if not project:
        raise ValueError("项目不存在")

    existing = session.exec(
        select(Scene).where(Scene.project_id == project_id).order_by(Scene.id)
    ).all()
    existing_dicts = [{"id": s.id, "name": s.name, "description": s.description}
                      for s in existing]
    existing_by_id = {s.id: s for s in existing}

    proj_dict = {
        "title": project.title, "premise": project.premise, "mood": project.mood,
        "style": project.style, "world_rules": project.world_rules,
    }

    logger.info(f"[ScenePlan] 规划场景：project_id={project_id}, chapter_id={chapter.id}, "
                f"已有场景 {len(existing)} 个")

    msgs = prompts.build_chapter_scene_prompt(
        proj_dict, chapter.title, chapter.content, existing_dicts
    )
    raw = await client.chat(msgs, temperature=0.4, max_tokens=8192)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict) or "scenes" not in parsed:
        logger.error(f"[ScenePlan] JSON 解析失败：{raw[:300]}")
        raise ValueError("场景规划失败：模型未返回合法的场景列表。")

    items = parsed.get("scenes") or []
    if not items:
        raise ValueError("场景规划失败：本章未抽取到场景。")

    # 清空本章旧关联（场景本体保留，供其它章复用）
    old_links = session.exec(
        select(ChapterScene).where(ChapterScene.chapter_id == chapter.id)
    ).all()
    for link in old_links:
        session.delete(link)
    session.commit()

    reused: list[int] = []
    created: list[int] = []
    result_scenes: list[Scene] = []
    linked_ids: set[int] = set()

    for item in items:
        item = item or {}
        match_id = item.get("match_id")
        scene = None
        # 复用已有场景
        if match_id is not None:
            try:
                mid = int(match_id)
            except (TypeError, ValueError):
                mid = None
            if mid is not None and mid in existing_by_id:
                scene = existing_by_id[mid]
                if scene.id not in reused:
                    reused.append(scene.id)

        # 新建场景
        if scene is None:
            vals = {f: str(item.get(f, "") or "").strip() for f in _SCENE_FIELDS}
            scene = Scene(
                project_id=project_id,
                name=str(item.get("name", "") or "未命名场景").strip(),
                description=str(item.get("description", "") or "").strip(),
                status="draft", **vals,
            )
            scene.locked_prompt = _rebuild_locked(scene)
            session.add(scene)
            session.commit()
            session.refresh(scene)
            created.append(scene.id)

        # 建立章节-场景关联（去重）
        if scene.id not in linked_ids:
            session.add(ChapterScene(project_id=project_id, chapter_id=chapter.id,
                                     scene_id=scene.id))
            linked_ids.add(scene.id)
            result_scenes.append(scene)

    session.commit()
    logger.info(f"[ScenePlan] 完成：复用 {len(reused)} 个，新建 {len(created)} 个")
    return {"reused": reused, "created": created, "scenes": result_scenes}
