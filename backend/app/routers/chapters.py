"""章节：列出 / 读取 / 手动编辑 / 确认（触发记忆回写）。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.db import get_session
from app.models import Chapter, OutlineNode
from app.schemas import ChapterConfirmRequest, ChapterEditRequest
from app.services import generation

router = APIRouter(prefix="/api", tags=["chapters"])


@router.get("/projects/{project_id}/chapters")
def list_chapters(project_id: int, session: Session = Depends(get_session)):
    """章节列表（不含正文，减轻传输）。"""
    rows = session.exec(
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.order_index)
    ).all()
    return [
        {
            "id": c.id, "outline_node_id": c.outline_node_id,
            "order_index": c.order_index, "title": c.title,
            "word_count": c.word_count, "status": c.status,
            "has_summary": bool(c.summary),
        }
        for c in rows
    ]


@router.get("/chapters/{chapter_id}")
def get_chapter(chapter_id: int, session: Session = Depends(get_session)):
    ch = session.get(Chapter, chapter_id)
    if not ch:
        raise HTTPException(404, "章节不存在")
    return ch


@router.get("/outline/nodes/{node_id}/chapter")
def get_chapter_by_node(node_id: int, session: Session = Depends(get_session)):
    """按大纲节点取对应章节（可能尚未生成，返回 null）。"""
    ch = session.exec(
        select(Chapter).where(Chapter.outline_node_id == node_id)
    ).first()
    return ch  # 可能为 None


@router.patch("/chapters/{chapter_id}")
def edit_chapter(chapter_id: int, body: ChapterEditRequest,
                 session: Session = Depends(get_session)):
    """手动编辑正文/标题。编辑后状态回到 draft（需重新确认以更新记忆）。"""
    ch = session.get(Chapter, chapter_id)
    if not ch:
        raise HTTPException(404, "章节不存在")
    if body.title is not None:
        ch.title = body.title
    if body.content is not None:
        ch.content = body.content
        ch.word_count = len(body.content)
        ch.status = "draft"
    ch.updated_at = datetime.utcnow()
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


@router.post("/chapters/{chapter_id}/confirm")
async def confirm_chapter(chapter_id: int, body: ChapterConfirmRequest,
                          session: Session = Depends(get_session)):
    """确认接受本章 → 标记 confirmed → 生成摘要并回写角色/伏笔记忆。"""
    ch = session.get(Chapter, chapter_id)
    if not ch:
        raise HTTPException(404, "章节不存在")
    if not ch.content.strip():
        raise HTTPException(400, "本章还没有正文，无法确认。")

    ch.status = "confirmed"
    ch.updated_at = datetime.utcnow()
    session.add(ch)
    session.commit()

    report = {"summary": ch.summary, "character_updates": [],
              "new_threads": [], "resolved_threads": []}
    if body.update_memory:
        report = await generation.update_memory_after_chapter(
            session, ch.project_id, ch
        )
        session.refresh(ch)

    return {"chapter": ch, "memory_report": report}


@router.delete("/chapters/{chapter_id}")
def delete_chapter(chapter_id: int, session: Session = Depends(get_session)):
    """删除章节正文（重置为未生成状态），但保留大纲节点。

    用于重新生成章节内容。删除后可以再次点击"生成正文"按钮。
    """
    ch = session.get(Chapter, chapter_id)
    if not ch:
        raise HTTPException(404, "章节不存在")

    # 删除章节记录（Chapter表），但保留对应的大纲节点（OutlineNode表）
    session.delete(ch)
    session.commit()

    return {"message": "章节已重置，可重新生成", "outline_node_id": ch.outline_node_id}
