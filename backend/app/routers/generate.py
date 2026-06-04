"""章节正文的流式生成（SSE）。

前端发起 POST，服务端用 text/event-stream 持续推送增量文本；
生成结束后把整章正文写库为 draft（status=draft，待用户确认）。

SSE 事件：
  event: delta  data: {"text": "..."}      —— 增量正文
  event: done   data: {"chapter_id": 12, "word_count": 2103}
  event: error  data: {"message": "..."}
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.db import engine, get_session
from app.llm import client, prompts
from app.models import Chapter, OutlineNode, Project
from app.schemas import ChapterGenerateRequest
from app.services import context_builder
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

    logger.info(f"开始生成章节：project_id={project_id}, node_id={body.outline_node_id}, "
                f"title={node.title}, action={body.action}, instruction={body.instruction[:50] if body.instruction else 'None'}")

    # 计算本章的全书顺序号（用 parent 卷顺序 * 1000 + 章顺序，保证全局有序）
    parent = session.get(OutlineNode, node.parent_id) if node.parent_id else None
    order_index = (parent.order_index if parent else 0) * 1000 + node.order_index

    # 组装记忆上下文
    char_dicts = context_builder.characters_to_dicts(session, project_id)
    open_threads = context_builder.open_threads_to_dicts(session, project_id)
    memory = context_builder.build_memory(session, project_id, order_index)
    all_outlines = context_builder.get_all_chapter_outlines(session, project_id, order_index)

    logger.info(f"上下文：{len(char_dicts)} 个角色，{len(open_threads)} 个开放伏笔，"
                f"历史摘要 {len(memory['history_summary'])} 字符，最近正文 {len(memory['recent_text'])} 字符，"
                f"全书大纲 {len(all_outlines)} 字符")

    messages = prompts.build_chapter_prompt(
        context_builder.project_to_dict(project), char_dicts,
        memory["history_summary"], memory["recent_text"], open_threads,
        node.title, node.synopsis, body.action, body.instruction,
        target_words=client.settings.chapter_target_words,
        all_chapter_outlines=all_outlines,
    )

    async def event_stream():
        MAX_RETRIES = 2  # 最多重试2次
        retry_count = 0
        current_messages = messages  # 闭包变量,重试时更新

        while retry_count <= MAX_RETRIES:
            collected: list[str] = []
            try:
                async for delta in client.chat_stream(
                    current_messages, temperature=client.settings.llm_temperature
                ):
                    collected.append(delta)
                    yield _sse("delta", {"text": delta})
            except client.LLMError as e:
                logger.error(f"生成失败：{e}")
                yield _sse("error", {"message": str(e)})
                return
            except Exception as e:  # noqa: BLE001 防止连接异常吞掉
                logger.error(f"生成中断：{e}")
                yield _sse("error", {"message": f"生成中断：{e}"})
                return

            full_text = "".join(collected).strip()
            if not full_text:
                logger.warning("模型未返回任何内容")
                yield _sse("error", {"message": "模型未返回任何内容。"})
                return

            word_count = len(full_text)
            logger.info(f"生成完成，字数：{word_count}")

            # 自动质量审查
            logger.info(f"开始审查章节质量（第 {retry_count + 1} 次生成）")
            yield _sse("review", {"message": "正在审查章节质量（字数、逻辑、衔接）..."})

            # 1. 字数检查（硬性要求：1700-3000）
            if word_count < 1700 or word_count > 3000:
                issues = f"字数不符合要求：当前 {word_count} 字，要求 1700-3000 字。"
                logger.warning(f"字数审查未通过（第 {retry_count + 1} 次）：{issues}")

                if retry_count < MAX_RETRIES:
                    retry_count += 1
                    yield _sse("retry", {
                        "message": f"字数不合格，正在重新生成（第 {retry_count + 1}/{MAX_RETRIES + 1} 次）",
                        "issues": issues,
                        "retry": retry_count
                    })
                    # 强调字数要求
                    current_messages = prompts.build_chapter_prompt(
                        context_builder.project_to_dict(project), char_dicts,
                        memory["history_summary"], memory["recent_text"], open_threads,
                        node.title, node.synopsis, "regenerate",
                        f"上一版字数不符合要求（{word_count} 字），请严格控制在 1700-3000 字之间。",
                        target_words=2000,  # 重新生成时目标字数设为中间值
                    )
                    continue  # 重新生成
                else:
                    # 超过重试上限
                    logger.warning(f"已达重试上限，字数仍不合格，使用最后一次生成的内容")
                    yield _sse("review", {
                        "message": f"已重试 {MAX_RETRIES} 次，字数仍不合格（{word_count} 字），使用最后一次生成的内容",
                        "pass": False,
                        "issues": issues
                    })
                    break

            # 2. 内容质量审查（逻辑、衔接）
            review_msgs = prompts.build_review_prompt(
                context_builder.project_to_dict(project),
                node.title, full_text, node.synopsis,
                memory["history_summary"], memory["recent_text"]
            )
            try:
                review_raw = await client.chat(review_msgs, temperature=0.3, max_tokens=500)
                review_result = generation._extract_json(review_raw)
                logger.info(f"审查结果：{review_result}")

                if review_result.get("pass"):
                    # 审查通过
                    logger.info(f"审查通过，章节质量合格")
                    yield _sse("review", {"message": "审查通过", "pass": True})
                    break
                else:
                    # 审查不通过
                    issues = review_result.get("issues", "未说明具体问题")
                    logger.warning(f"审查未通过（第 {retry_count + 1} 次）：{issues}")

                    if retry_count < MAX_RETRIES:
                        retry_count += 1
                        yield _sse("retry", {
                            "message": f"审查未通过，正在重新生成（第 {retry_count + 1}/{MAX_RETRIES + 1} 次）",
                            "issues": issues,
                            "retry": retry_count
                        })
                        # 把审查意见加入 messages，让重新生成时参考
                        current_messages = prompts.build_chapter_prompt(
                            context_builder.project_to_dict(project), char_dicts,
                            memory["history_summary"], memory["recent_text"], open_threads,
                            node.title, node.synopsis, "regenerate",
                            f"上一版审查未通过，问题：{issues}",
                            target_words=client.settings.chapter_target_words,
                        )
                        continue  # 重新生成
                    else:
                        # 超过重试上限
                        logger.warning(f"已达重试上限，使用最后一次生成的内容")
                        yield _sse("review", {
                            "message": f"已重试 {MAX_RETRIES} 次，审查仍未完全通过，使用最后一次生成的内容",
                            "pass": False,
                            "issues": issues
                        })
                        break
            except Exception as e:
                logger.error(f"审查过程出错：{e}，跳过审查直接使用生成内容")
                yield _sse("review", {"message": f"审查过程出错，跳过审查", "pass": None})
                break

        # 写库为 draft（新建或覆盖该节点已有的草稿）
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
            chapter_id = chapter.id
            wc = chapter.word_count

        logger.info(f"生成完成：chapter_id={chapter_id}, word_count={wc}, retry_count={retry_count}")
        yield _sse("word_count", {"word_count": wc, "message": f"本章字数：{wc} 字"})
        yield _sse("done", {"chapter_id": chapter_id, "word_count": wc, "retry_count": retry_count})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
