"""角色锁定关键词的 AI 批量填充。

结合项目整体设定（画风/色彩基调）+ 角色档案，调用 LLM 为每个角色生成
视觉锁定关键词（CharacterDesign 的 9 个字段），并落库（重算 locked_prompt）。
"""
import logging
from datetime import datetime

from sqlmodel import Session, select

from app.llm import client, image_client, prompts
from app.models import Character, CharacterDesign, OutlineNode, Project
from app.services.generation import _extract_json

logger = logging.getLogger(__name__)

_FIELDS = ["gender", "age", "body_type", "color_tone", "art_style",
           "facial_features", "hair", "outfit", "extra"]


async def autofill_character_designs(session: Session, project_id: int,
                                     only_empty: bool = False) -> dict:
    """为项目下所有角色 AI 填充锁定关键词并落库。

    only_empty=True 时只处理"还没有设定"的角色（已有 design 的跳过），
    用于增量补全；默认 False 会刷新全部角色。
    返回 {updated: [character_id...], skipped: [...], total}。
    """
    project = session.get(Project, project_id)
    if not project:
        raise ValueError("项目不存在")

    characters = session.exec(
        select(Character).where(Character.project_id == project_id).order_by(Character.id)
    ).all()
    if not characters:
        raise ValueError("本项目还没有角色。")

    existing = {
        d.character_id: d
        for d in session.exec(
            select(CharacterDesign).where(CharacterDesign.project_id == project_id)
        ).all()
    }

    # 待处理角色（only_empty 时跳过已有设定的）
    targets = [c for c in characters
               if not (only_empty and c.id in existing)]
    if not targets:
        return {"updated": [], "skipped": [c.id for c in characters], "total": len(characters)}

    proj_dict = {
        "title": project.title, "premise": project.premise, "mood": project.mood,
        "style": project.style, "world_rules": project.world_rules,
    }
    char_dicts = [
        {"id": c.id, "name": c.name, "role": c.role,
         "description": c.description, "state": c.state}
        for c in targets
    ]

    logger.info(f"[CharDesign] AI 填充锁定词：project_id={project_id}, "
                f"待处理 {len(targets)} 个角色, only_empty={only_empty}")

    msgs = prompts.build_character_design_prompt(proj_dict, char_dicts)
    raw = await client.chat(msgs, temperature=0.5, max_tokens=4096)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict) or "designs" not in parsed:
        logger.error(f"[CharDesign] JSON 解析失败：{raw[:300]}")
        raise ValueError("AI 返回格式不合法，未能解析出角色设定。")

    by_id = {}
    for item in (parsed.get("designs") or []):
        if isinstance(item, dict) and "id" in item:
            try:
                by_id[int(item["id"])] = item
            except (TypeError, ValueError):
                continue

    updated: list[int] = []
    skipped: list[int] = []
    for c in targets:
        data = by_id.get(c.id)
        if not data:
            skipped.append(c.id)
            continue
        vals = {f: str(data.get(f, "") or "").strip() for f in _FIELDS}
        locked = image_client.build_locked_character_prompt(
            art_style=vals["art_style"], facial_features=vals["facial_features"],
            hair=vals["hair"], outfit=vals["outfit"], extra=vals["extra"],
            gender=vals["gender"], age=vals["age"], body_type=vals["body_type"],
            color_tone=vals["color_tone"],
        )
        d = existing.get(c.id)
        if d:
            for f in _FIELDS:
                setattr(d, f, vals[f])
            d.locked_prompt = locked
            d.updated_at = datetime.utcnow()
            session.add(d)
        else:
            d = CharacterDesign(project_id=project_id, character_id=c.id,
                                locked_prompt=locked, **vals)
            session.add(d)
        updated.append(c.id)

    session.commit()
    logger.info(f"[CharDesign] 填充完成：更新 {len(updated)} 个，跳过 {len(skipped)} 个")
    return {"updated": updated, "skipped": skipped, "total": len(characters)}


def _build_outline_text(session: Session, project_id: int) -> str:
    """拼接项目大纲全文（卷/篇/章梗概，按层级与顺序），供扫描人物用。"""
    nodes = session.exec(
        select(OutlineNode).where(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.order_index)
    ).all()
    if not nodes:
        return ""
    # 按 parent_id 组织层级缩进
    by_parent: dict = {}
    for n in nodes:
        by_parent.setdefault(n.parent_id, []).append(n)
    for lst in by_parent.values():
        lst.sort(key=lambda x: x.order_index)

    lines: list[str] = []

    def walk(parent_id, depth):
        for n in by_parent.get(parent_id, []):
            indent = "  " * depth
            title = (n.title or "").strip()
            syn = (n.synopsis or "").strip()
            if title or syn:
                lines.append(f"{indent}{title}：{syn}" if syn else f"{indent}{title}")
            walk(n.id, depth + 1)

    walk(None, 0)
    return "\n".join(lines)


async def scan_and_fill_characters(session: Session, project_id: int) -> dict:
    """扫描大纲里所有具名人物 → 自动建缺失角色 → 填充各角色锁定设定。

    返回 {created: [name...], filled: [name...], skipped: [name...], total_found}。
    已存在的同名角色不重复创建，但其设定也会被填充/刷新。
    """
    project = session.get(Project, project_id)
    if not project:
        raise ValueError("项目不存在")

    outline_text = _build_outline_text(session, project_id)
    if not outline_text.strip():
        raise ValueError("本项目还没有大纲。请先在「大纲」里编写卷/篇/章梗概。")

    existing_chars = session.exec(
        select(Character).where(Character.project_id == project_id)
    ).all()
    existing_by_name = {c.name.strip(): c for c in existing_chars}

    logger.info(f"[CharScan] 扫描大纲人物：project_id={project_id}, "
                f"大纲 {len(outline_text)} 字，已有角色 {len(existing_chars)} 个")

    proj_dict = {
        "title": project.title, "premise": project.premise, "mood": project.mood,
        "style": project.style, "world_rules": project.world_rules,
    }
    msgs = prompts.build_scan_characters_prompt(
        proj_dict, outline_text, list(existing_by_name.keys())
    )
    raw = await client.chat(msgs, temperature=0.4, max_tokens=8192)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict) or "characters" not in parsed:
        logger.error(f"[CharScan] JSON 解析失败：{raw[:300]}")
        raise ValueError("AI 返回格式不合法，未能解析出人物列表。")

    items = parsed.get("characters") or []
    if not items:
        raise ValueError("未从大纲扫描到具名人物。")

    created: list[str] = []
    filled: list[str] = []
    skipped: list[str] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        if not name:
            continue
        # 建缺失角色（同名复用）
        ch = existing_by_name.get(name)
        if not ch:
            ch = Character(
                project_id=project_id, name=name,
                role=str(item.get("role", "") or "").strip(),
                description=str(item.get("description", "") or "").strip(),
            )
            session.add(ch)
            session.commit()
            session.refresh(ch)
            existing_by_name[name] = ch
            created.append(name)

        # 填充/刷新该角色的视觉锁定设定
        vals = {f: str(item.get(f, "") or "").strip() for f in _FIELDS}
        if not any(vals.values()):
            skipped.append(name)
            continue
        locked = image_client.build_locked_character_prompt(
            art_style=vals["art_style"], facial_features=vals["facial_features"],
            hair=vals["hair"], outfit=vals["outfit"], extra=vals["extra"],
            gender=vals["gender"], age=vals["age"], body_type=vals["body_type"],
            color_tone=vals["color_tone"],
        )
        d = session.exec(
            select(CharacterDesign).where(CharacterDesign.character_id == ch.id)
        ).first()
        if d:
            for f in _FIELDS:
                setattr(d, f, vals[f])
            d.locked_prompt = locked
            d.updated_at = datetime.utcnow()
            session.add(d)
        else:
            d = CharacterDesign(project_id=project_id, character_id=ch.id,
                                locked_prompt=locked, **vals)
            session.add(d)
        filled.append(name)

    session.commit()
    logger.info(f"[CharScan] 完成：新建 {len(created)} 个，填充 {len(filled)} 个，"
                f"跳过 {len(skipped)} 个")
    return {"created": created, "filled": filled, "skipped": skipped,
            "total_found": len(items)}
