"""上下文构建：在固定预算内，把"哪些记忆喂给模型"组装出来。

这是支撑千万字一致性的核心。策略（分层记忆）：
  - 故事圣经 + 角色档案：始终全量带上（体量小、最重要）。
  - 最近 N 章正文：在字符预算内尽量多带（短期记忆，保证文风/衔接自然）。
  - 更早的章节：用各章压缩摘要拼成"历史提要"（长期记忆，亚线性增长）。
  - 未回收伏笔：结构化带上，提醒模型呼应。

[扩展点] 章节数很多时（>100 章），历史摘要本身也会变长。
后续可在此处接入向量检索：只召回与"本章梗概"语义相关的历史摘要，
而非全部拼接。当前实现按时间顺序拼接，足以支撑起步与中等体量。
"""
from sqlmodel import Session, select

from app.config import settings
from app.models import Chapter, Character, OutlineNode, PlotThread, Project


def project_to_dict(project: Project) -> dict:
    return {
        "title": project.title,
        "premise": project.premise,
        "mood": project.mood,
        "style": project.style,
        "world_rules": project.world_rules,
        "full_synopsis": project.full_synopsis,
    }


def characters_to_dicts(session: Session, project_id: int) -> list[dict]:
    rows = session.exec(
        select(Character).where(Character.project_id == project_id)
    ).all()
    return [
        {"name": c.name, "role": c.role, "description": c.description, "state": c.state}
        for c in rows
    ]


def open_threads_to_dicts(session: Session, project_id: int) -> list[dict]:
    rows = session.exec(
        select(PlotThread)
        .where(PlotThread.project_id == project_id)
        .where(PlotThread.status == "open")
    ).all()
    return [{"name": t.name, "detail": t.detail} for t in rows]


def build_memory(session: Session, project_id: int, before_order: int) -> dict:
    """组装写"第 before_order 章"时所需的历史记忆。

    返回 {history_summary, recent_text}：
      recent_text   —— 紧邻的若干章正文（预算内，倒序取最近）。
      history_summary —— 更早章节的摘要按时间顺序拼接。
    """
    confirmed = session.exec(
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .where(Chapter.order_index < before_order)
        .where(Chapter.status == "confirmed")
        .order_by(Chapter.order_index)
    ).all()

    if not confirmed:
        return {"history_summary": "", "recent_text": ""}

    budget = settings.recent_chapters_char_budget
    recent_chunks: list[str] = []
    used = 0
    # 从最近一章往前取正文，直到耗尽预算
    cutoff_index = len(confirmed)
    for i in range(len(confirmed) - 1, -1, -1):
        ch = confirmed[i]
        text = ch.content or ""
        if used + len(text) > budget and recent_chunks:
            cutoff_index = i + 1
            break
        recent_chunks.append(f"【{ch.title}】\n{text}")
        used += len(text)
        cutoff_index = i
    recent_chunks.reverse()
    recent_text = "\n\n".join(recent_chunks)

    # cutoff_index 之前的章节用摘要
    history_parts = []
    for ch in confirmed[:cutoff_index]:
        s = ch.summary or ""
        if s:
            history_parts.append(f"{ch.title}：{s}")
    history_summary = "\n".join(history_parts)

    return {"history_summary": history_summary, "recent_text": recent_text}


def get_all_chapter_outlines(session: Session, project_id: int, current_order: int) -> str:
    """获取全书所有章节的大纲结构，标注当前进度。

    用于让模型了解整体剧情走向，防止提前输出后续章节内容。
    """
    # 获取所有卷
    volumes = session.exec(
        select(OutlineNode)
        .where(OutlineNode.project_id == project_id)
        .where(OutlineNode.level == "volume")
        .order_by(OutlineNode.order_index)
    ).all()

    if not volumes:
        return ""

    lines = []
    for vol in volumes:
        lines.append(f"\n{vol.title}")
        if vol.synopsis:
            lines.append(f"  主题：{vol.synopsis}")

        # 获取本卷的章节
        chapters = session.exec(
            select(OutlineNode)
            .where(OutlineNode.project_id == project_id)
            .where(OutlineNode.parent_id == vol.id)
            .where(OutlineNode.level == "chapter")
            .order_by(OutlineNode.order_index)
        ).all()

        for ch in chapters:
            # 计算章节的全书顺序号
            ch_order = vol.order_index * 1000 + ch.order_index
            marker = " ← 当前正在创作" if ch_order == current_order else ""
            status_marker = " [已完成]" if ch_order < current_order else ""

            lines.append(f"  {ch.title}{status_marker}{marker}")
            if ch.synopsis:
                lines.append(f"    梗概：{ch.synopsis}")

    return "\n".join(lines)
