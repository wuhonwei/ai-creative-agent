"""根据已生成的大纲，补充还没有档案的角色。不改已有角色。"""
import logging

from sqlmodel import Session, select

from app.llm import client, prompts
from app.models import Character, Project
from app.services.character_design import _build_outline_text
from app.services.generation import _extract_json

logger = logging.getLogger(__name__)


async def supplement_characters_from_outline(session: Session, project_id: int) -> dict:
    """扫描大纲，为尚未建档的人物写入角色档案。

    返回 {"created": [姓名...], "error": ""}。
    大纲生成已经成功时调用；这里失败只记在 error 里，不往外抛。
    """
    project = session.get(Project, project_id)
    if not project:
        return {"created": [], "error": "项目不存在"}

    outline_text = _build_outline_text(session, project_id)
    if not outline_text.strip():
        return {"created": [], "error": ""}

    existing = session.exec(
        select(Character).where(Character.project_id == project_id)
    ).all()
    existing_names = {c.name.strip() for c in existing if c.name and c.name.strip()}
    existing_payload = [
        {"name": c.name, "role": c.role, "description": (c.description or "")[:120]}
        for c in existing if c.name and c.name.strip()
    ]
    dianwen = prompts.resolve_dianwen(None, project.dianwen)
    msgs = prompts.build_supplement_characters_prompt(
        {
            "title": project.title,
            "premise": project.premise,
            "mood": project.mood,
            "style": project.style,
            "world_rules": project.world_rules,
        },
        existing_payload,
        outline_text,
        dianwen,
    )
    try:
        raw = await client.chat(msgs, temperature=0.4, max_tokens=16384)
    except client.LLMError as e:
        logger.error("[characters] 补充角色档案失败：%s", e)
        return {"created": [], "error": str(e)}

    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        logger.error("[characters] 补充角色档案无法解析：%s", raw[:800])
        return {"created": [], "error": "模型没有返回可用的角色列表"}

    created: list[str] = []
    seen = set(existing_names)
    for item in parsed.get("characters") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or len(name) > 30 or name in seen:
            continue
        session.add(Character(
            project_id=project_id,
            name=name,
            role=str(item.get("role") or "").strip(),
            description=str(item.get("description") or "").strip(),
            state=str(item.get("state") or "").strip(),
        ))
        seen.add(name)
        created.append(name)

    if created:
        session.commit()
        logger.info("[characters] 已补充角色档案：%s", "、".join(created))
    return {"created": created, "error": ""}
