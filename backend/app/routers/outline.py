"""分层大纲：生成（供检查点确认/编辑）+ 手动 CRUD。"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.db import get_session
from app.llm import client, prompts
from app.models import Chapter, OutlineNode, PlotThread, Project
from app.schemas import (
    OutlineGenerateRequest, OutlineNodeCreate, OutlineNodeUpdate,
    VolumeExpandRequest,
)
from app.services import context_builder, generation

router = APIRouter(prefix="/api", tags=["outline"])
logger = logging.getLogger(__name__)


def _sse(event: str, data: dict) -> str:
    """构造 SSE 事件消息。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/projects/{project_id}/outline")
def get_outline(project_id: int, session: Session = Depends(get_session)):
    """返回该项目的全部大纲节点（前端自行按 parent_id 组装成树）。"""
    return session.exec(
        select(OutlineNode)
        .where(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.order_index)
    ).all()


@router.post("/projects/{project_id}/outline/generate")
async def generate_outline(project_id: int, body: OutlineGenerateRequest,
                           replace: bool = True, force: bool = False,
                           session: Session = Depends(get_session)):
    """调用模型生成分层大纲（流式 SSE）。

    replace=True 时清空已有大纲后重建（用于"重新生成/换方向"）。
    force=True 时连同已生成的正文章节及其抽取的情节线一并清空（破坏性，
    用于用户明确选择"强制重新生成、放弃已有正文"）。
    注意：这是检查点之前的一步——生成后由用户在前端确认或编辑。
    """
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    # 先检查 409 保护
    if replace:
        has_content = session.exec(
            select(Chapter)
            .where(Chapter.project_id == project_id)
            .where(Chapter.content != "")
        ).first()
        if has_content and not force:
            raise HTTPException(
                409, "已存在已生成正文的章节，不能整体替换大纲。请改为局部编辑，"
                     "或确认放弃已有正文后强制重新生成。"
            )

    characters = context_builder.characters_to_dicts(session, project_id)
    dianwen = prompts.resolve_dianwen(body.dianwen, project.dianwen)
    msgs = prompts.build_outline_prompt(
        context_builder.project_to_dict(project), characters,
        body.instruction, body.num_volumes, body.chapters_per_volume,
        dianwen=dianwen,
    )

    logger.info(f"开始流式生成大纲：project_id={project_id}, num_volumes={body.num_volumes}, "
                f"chapters_per_volume={body.chapters_per_volume}, replace={replace}, force={force}")

    async def event_stream():
        collected: list[str] = []
        try:
            async for delta in client.chat_stream(msgs, temperature=0.8, max_tokens=16384):
                collected.append(delta)
                logger.info(f"[outline] delta: {delta[:80]}")
                yield _sse("delta", {"text": delta})
        except client.LLMError as e:
            logger.error(f"生成大纲失败：{e}")
            yield _sse("error", {"message": str(e)})
            return

        # 流式收完,解析 JSON
        raw = "".join(collected)
        logger.info(f"[outline] 收到完整输出 {len(raw)} 字符，前100字: {raw[:100]}")
        parsed = generation._extract_json(raw)
        if not isinstance(parsed, dict) or "volumes" not in parsed:
            logger.error(f"[outline] JSON 解析失败，原始输出片段：{raw[:300]}")
            yield _sse("error", {"message": f"模型未返回预期的大纲 JSON，原始输出片段：{raw[:300]}"})
            return

        # 写库
        from app.db import engine
        from sqlmodel import Session as SQLSession
        with SQLSession(engine) as db_session:
            proj = db_session.get(Project, project_id)
            if not proj:
                yield _sse("error", {"message": "项目不存在"})
                return

            if replace:
                # 清空角色状态（无论是否force，只要replace就清空状态）
                from app.models import Character
                for char in db_session.exec(
                    select(Character).where(Character.project_id == project_id)
                ).all():
                    char.state = ""
                    db_session.add(char)
                db_session.commit()
                logger.info(f"[outline] 已清空角色状态")

                if force:
                    # 清空章节
                    for ch in db_session.exec(
                        select(Chapter).where(Chapter.project_id == project_id)
                    ).all():
                        db_session.delete(ch)
                    # 清空伏笔
                    for thread in db_session.exec(
                        select(PlotThread).where(PlotThread.project_id == project_id)
                    ).all():
                        db_session.delete(thread)
                    db_session.commit()
                    logger.info(f"[outline] 已清空章节和伏笔")

                # 清空大纲节点
                for node in db_session.exec(
                    select(OutlineNode).where(OutlineNode.project_id == project_id)
                ).all():
                    db_session.delete(node)
                db_session.commit()
                logger.info(f"[outline] 已清空旧大纲")

            # 落库：卷 -> 章
            vol_order = 0
            total_chapters = 0
            for vol in parsed.get("volumes", []):
                volume = OutlineNode(
                    project_id=project_id, parent_id=None, level="volume",
                    order_index=vol_order, title=vol.get("title", f"第{vol_order + 1}卷"),
                    synopsis=vol.get("synopsis", ""),
                )
                db_session.add(volume)
                db_session.commit()
                db_session.refresh(volume)
                vol_order += 1

                ch_order = 0
                for ch in vol.get("chapters", []):
                    db_session.add(OutlineNode(
                        project_id=project_id, parent_id=volume.id, level="chapter",
                        order_index=ch_order, title=ch.get("title", f"第{ch_order + 1}章"),
                        synopsis=ch.get("synopsis", ""),
                    ))
                    ch_order += 1
                    total_chapters += 1
                db_session.commit()

            logger.info(f"[outline] 已生成 {vol_order} 卷，共 {total_chapters} 章")

            # 返回完整大纲
            all_nodes = db_session.exec(
                select(OutlineNode)
                .where(OutlineNode.project_id == project_id)
                .order_by(OutlineNode.order_index)
            ).all()
            result = [
                {
                    "id": n.id, "project_id": n.project_id, "parent_id": n.parent_id,
                    "level": n.level, "order_index": n.order_index,
                    "title": n.title, "synopsis": n.synopsis,
                    "created_at": n.created_at.isoformat() if n.created_at else None,
                }
                for n in all_nodes
            ]
            yield _sse("done", {"outline": result, "volumes": vol_order, "chapters": total_chapters})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/outline/nodes/{volume_id}/expand")
async def expand_volume(volume_id: int, body: VolumeExpandRequest,
                        session: Session = Depends(get_session)):
    """只为某一卷生成详细章节大纲（单卷单次，避免整书一次生成被截断）。

    适合 500 章这类大体量：先生成卷骨架，再逐卷点开展开。
    replace=True 时清空本卷已有章节节点后重建；若本卷已有章节写了正文则拒绝。
    """
    volume = session.get(OutlineNode, volume_id)
    if not volume or volume.level != "volume":
        raise HTTPException(404, "卷不存在")
    project = session.get(Project, volume.project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    # 本卷已有的章节节点
    existing = session.exec(
        select(OutlineNode)
        .where(OutlineNode.parent_id == volume_id)
        .where(OutlineNode.level == "chapter")
    ).all()

    if body.replace and existing:
        node_ids = [n.id for n in existing]
        has_content = session.exec(
            select(Chapter)
            .where(Chapter.outline_node_id.in_(node_ids))
            .where(Chapter.content != "")
        ).first()
        if has_content:
            raise HTTPException(
                409, "本卷已有章节生成了正文，不能整体替换本卷章节。请改为局部编辑。"
            )

    # 全书卷结构（按顺序），并标记本次展开的目标卷
    all_volumes_nodes = session.exec(
        select(OutlineNode)
        .where(OutlineNode.project_id == volume.project_id)
        .where(OutlineNode.level == "volume")
        .order_by(OutlineNode.order_index)
    ).all()
    all_volumes = [
        {"title": v.title, "synopsis": v.synopsis, "_is_target": v.id == volume_id}
        for v in all_volumes_nodes
    ]

    # 收集本卷之前各卷的章节梗概（供模型了解已发生的剧情，避免矛盾）
    previous_chapters = []
    for vol_node in all_volumes_nodes:
        if vol_node.order_index >= volume.order_index:
            break  # 只要本卷之前的
        vol_chapters = session.exec(
            select(OutlineNode)
            .where(OutlineNode.parent_id == vol_node.id)
            .where(OutlineNode.level == "chapter")
            .order_by(OutlineNode.order_index)
        ).all()
        for ch in vol_chapters:
            previous_chapters.append({
                "volume_title": vol_node.title,
                "chapter_title": ch.title,
                "synopsis": ch.synopsis,
            })

    characters = context_builder.characters_to_dicts(session, volume.project_id)
    dianwen = prompts.resolve_dianwen(body.dianwen, project.dianwen)
    msgs = prompts.build_volume_chapters_prompt(
        context_builder.project_to_dict(project), characters,
        all_volumes, {"title": volume.title, "synopsis": volume.synopsis},
        previous_chapters, body.count, body.instruction,
        dianwen=dianwen,
    )

    logger.info(f"开始流式展开卷：volume_id={volume_id}, title={volume.title}, "
                f"count={body.count}, previous_chapters={len(previous_chapters)}")

    async def event_stream():
        collected: list[str] = []
        try:
            async for delta in client.chat_stream(msgs, temperature=0.8, max_tokens=16384):
                collected.append(delta)
                logger.info(f"[expand] delta: {delta[:80]}")  # 日志打印每次返回的前80字符
                yield _sse("delta", {"text": delta})
        except client.LLMError as e:
            logger.error(f"展开卷失败：{e}")
            yield _sse("error", {"message": str(e)})
            return

        # 流式收完,组装完整 JSON
        raw = "".join(collected)
        logger.info(f"[expand] 收到完整输出 {len(raw)} 字符，前100字: {raw[:100]}")
        parsed = generation._extract_json(raw)
        if not isinstance(parsed, dict) or "chapters" not in parsed:
            logger.error(f"[expand] JSON 解析失败，原始输出片段：{raw[:300]}")
            yield _sse("error", {"message": f"模型未返回预期的章节 JSON，原始输出片段：{raw[:300]}"})
            return

        # 写库：清空旧章节(如果 replace),插入新章节
        from app.db import engine
        from sqlmodel import Session as SQLSession
        with SQLSession(engine) as db_session:
            vol = db_session.get(OutlineNode, volume_id)
            if not vol:
                yield _sse("error", {"message": "卷节点不存在"})
                return

            if body.replace:
                existing_nodes = db_session.exec(
                    select(OutlineNode)
                    .where(OutlineNode.parent_id == volume_id)
                    .where(OutlineNode.level == "chapter")
                ).all()
                for node in existing_nodes:
                    db_session.delete(node)
                db_session.commit()
                logger.info(f"[expand] 已清空本卷 {len(existing_nodes)} 个旧章节")

            ch_order = 0
            for ch in parsed.get("chapters", []):
                db_session.add(OutlineNode(
                    project_id=vol.project_id, parent_id=volume_id, level="chapter",
                    order_index=ch_order, title=ch.get("title", f"第{ch_order + 1}章"),
                    synopsis=ch.get("synopsis", ""),
                ))
                ch_order += 1
            db_session.commit()
            logger.info(f"[expand] 已插入 {ch_order} 个新章节")

            # 返回完整大纲
            all_nodes = db_session.exec(
                select(OutlineNode)
                .where(OutlineNode.project_id == vol.project_id)
                .order_by(OutlineNode.order_index)
            ).all()
            result = [
                {
                    "id": n.id, "project_id": n.project_id, "parent_id": n.parent_id,
                    "level": n.level, "order_index": n.order_index,
                    "title": n.title, "synopsis": n.synopsis,
                    "created_at": n.created_at.isoformat() if n.created_at else None,
                }
                for n in all_nodes
            ]
            yield _sse("done", {"outline": result, "chapters_added": ch_order})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/projects/{project_id}/outline/nodes")
def create_node(project_id: int, body: OutlineNodeCreate,
                session: Session = Depends(get_session)):
    if not session.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    node = OutlineNode(project_id=project_id, **body.model_dump())
    session.add(node)
    session.commit()
    session.refresh(node)
    return node


@router.patch("/outline/nodes/{node_id}")
def update_node(node_id: int, body: OutlineNodeUpdate,
                session: Session = Depends(get_session)):
    node = session.get(OutlineNode, node_id)
    if not node:
        raise HTTPException(404, "大纲节点不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(node, k, v)
    session.add(node)
    session.commit()
    session.refresh(node)
    return node


@router.delete("/outline/nodes/{node_id}")
def delete_node(node_id: int, session: Session = Depends(get_session)):
    node = session.get(OutlineNode, node_id)
    if not node:
        raise HTTPException(404, "大纲节点不存在")

    project_id = node.project_id
    parent_id = node.parent_id
    level = node.level
    deleted_order = node.order_index

    # 删除子节点
    for child in session.exec(
        select(OutlineNode).where(OutlineNode.parent_id == node_id)
    ).all():
        session.delete(child)
    session.delete(node)
    session.commit()

    # 重排同级节点的 order_index 和标题序号
    siblings = session.exec(
        select(OutlineNode)
        .where(OutlineNode.project_id == project_id)
        .where(OutlineNode.parent_id == parent_id)
        .where(OutlineNode.level == level)
        .order_by(OutlineNode.order_index)
    ).all()

    import re
    for idx, sibling in enumerate(siblings):
        sibling.order_index = idx
        # 自动修正标题里的序号（支持"第X卷"、"第X章"格式）
        if level == "volume":
            sibling.title = re.sub(r'^第\d+卷', f'第{idx + 1}卷', sibling.title)
        elif level == "chapter":
            sibling.title = re.sub(r'^第\d+章', f'第{idx + 1}章', sibling.title)
        session.add(sibling)
    session.commit()

    return {"ok": True}
