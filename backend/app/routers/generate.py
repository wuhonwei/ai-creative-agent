"""章节正文的流式生成（SSE）。

三路并行生成 → 每路 humanize → 评分 → 取最优 → 写库。

SSE 事件：
  event: parallel   data: {"message": "...", "candidates": 3}
  event: candidate  data: {"index": 1, "stage": "generate|humanize|score|done", ...}
  event: select     data: {"winner": 2, "score": 87, "scores": [...], "message": "..."}
  event: replace    data: {"text": "..."}      —— 最优正文
  event: done       data: {"chapter_id": 12, "word_count": 2103, "score": 87, "winner": 2}
  event: error      data: {"message": "..."}
"""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.db import engine, get_session
from app.llm import client, prompts
from app.models import Chapter, OutlineNode, Project
from app.schemas import ChapterGenerateRequest
from app.services import chapter_generation, context_builder
from sqlmodel import Session as SQLSession

router = APIRouter(prefix="/api", tags=["generate"])
logger = logging.getLogger(__name__)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/projects/{project_id}/generate")
async def generate_chapter(project_id: int, body: ChapterGenerateRequest,
                           session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    node = session.get(OutlineNode, body.outline_node_id)
    if not node or node.project_id != project_id:
        raise HTTPException(404, "大纲节点不存在")
    if node.level != "chapter":
        raise HTTPException(400, "只能针对'章'级大纲节点生成正文。")

    if not client.settings.llm_configured:
        raise HTTPException(400, "未配置 DEEPSEEK_API_KEY，无法生成。请在 backend/.env 填入。")

    logger.info(
        "开始并行生成章节：project_id=%s, node_id=%s, title=%s, action=%s",
        project_id, body.outline_node_id, node.title, body.action,
    )

    parent = session.get(OutlineNode, node.parent_id) if node.parent_id else None
    order_index = (parent.order_index if parent else 0) * 1000 + node.order_index

    char_dicts = context_builder.characters_to_dicts(session, project_id)
    open_threads = context_builder.open_threads_to_dicts(session, project_id)
    memory = context_builder.build_memory(session, project_id, order_index)
    all_outlines = context_builder.get_all_chapter_outlines(session, project_id, order_index)
    project_dict = context_builder.project_to_dict(project)

    messages = prompts.build_chapter_prompt(
        project_dict, char_dicts,
        memory["history_summary"], memory["recent_text"], open_threads,
        node.title, node.synopsis, body.action, body.instruction,
        target_words=client.settings.chapter_target_words,
        all_chapter_outlines=all_outlines,
    )

    async def event_stream():
        status_queue: asyncio.Queue = asyncio.Queue()

        async def run_parallel():
            try:
                best, _all = await chapter_generation.generate_best_parallel(
                    messages, project_dict, node.title, node.synopsis, memory,
                    queue=status_queue,
                )
                await status_queue.put({"_done": True, "best": best})
            except Exception as e:
                logger.exception("并行生成失败")
                await status_queue.put({"_error": str(e)})

        worker = asyncio.create_task(run_parallel())
        best = None

        try:
            while True:
                if worker.done() and status_queue.empty():
                    break
                try:
                    msg = await asyncio.wait_for(status_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

                if "_error" in msg:
                    yield _sse("error", {"message": msg["_error"]})
                    return
                if "_done" in msg:
                    best = msg["best"]
                    break

                event = msg.pop("event", "candidate")
                yield _sse(event, msg)

            if best is None:
                if not worker.done():
                    await worker
                yield _sse("error", {"message": "并行生成未返回结果"})
                return

            full_text = best.text
            yield _sse("replace", {"text": full_text})

            with SQLSession(engine) as s:
                existing = s.exec(
                    select(Chapter).where(Chapter.outline_node_id == node.id)
                ).first()
                if existing:
                    existing.content = full_text
                    existing.word_count = len(full_text)
                    existing.title = node.title
                    existing.status = "draft"
                    chapter = existing
                else:
                    chapter = Chapter(
                        project_id=project_id, outline_node_id=node.id,
                        order_index=order_index, title=node.title,
                        content=full_text, word_count=len(full_text), status="draft",
                    )
                s.add(chapter)
                s.commit()
                s.refresh(chapter)

            wc = chapter.word_count
            logger.info(
                "并行生成完成：chapter_id=%s, winner=%s, score=%s, word_count=%s",
                chapter.id, best.index, best.score, wc,
            )
            yield _sse("word_count", {
                "word_count": wc,
                "message": f"本章 {wc} 字（候选 {best.index}，评分 {best.score:.0f}）",
            })
            yield _sse("done", {
                "chapter_id": chapter.id,
                "word_count": wc,
                "score": best.score,
                "winner": best.index,
                "rationale": best.rationale,
            })
        finally:
            if not worker.done():
                worker.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
