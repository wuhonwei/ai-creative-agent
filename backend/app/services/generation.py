"""生成编排：摘要抽取 + 角色/伏笔状态回写。

正文的流式生成在路由层直接消费 llm.chat_stream（便于 SSE），
本模块负责"确认章节后"的记忆更新（非流式、结构化）。
"""
import json
import logging

from sqlmodel import Session, select

from app.llm import client, prompts
from app.models import Chapter, Character, PlotThread
from app.services import context_builder

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> dict | list | None:
    """从模型输出中尽量解析出 JSON（容忍 ```json 包裹、前后噪声、被 max_tokens 截断）。"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        # 去掉 ```json ... ``` 围栏
        t = t.split("```", 2)
        t = t[1] if len(t) > 1 else text
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # 退而求其次：截取第一个 { 到最后一个 }
        start = t.find("{")
        end = t.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(t[start:end + 1])
            except json.JSONDecodeError:
                pass
        # 最后兜底：JSON 可能被 max_tokens 截断（最后一个对象没闭合），
        # 用括号配平把已完整的部分救回来（数组里末尾的残缺对象会被丢弃）。
        salvaged = _salvage_truncated_json(t)
        if salvaged is not None:
            return salvaged
    return None


def _salvage_truncated_json(t: str) -> dict | list | None:
    """救援被截断的 JSON：从第一个 { 起按括号配平，丢弃末尾未闭合的残片。

    逐字符扫描（跳过字符串与转义），记录每次"回到顶层且刚闭合一个元素"的位置，
    在该位置补齐缺失的 ] 和 } 后尝试解析。能救回截断前已完整的对象/数组元素。
    """
    start = t.find("{")
    if start == -1:
        return None
    stack: list[str] = []
    in_str = False
    escape = False
    # 记录"在某个数组内、刚结束一个元素(} 或值后的 ,)"时的安全截断点
    best_cut = -1
    best_close = ""
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
            # 顶层对象的某个数组元素刚闭合：此处截断 + 配平是安全的。
            # 结构为 { "key": [ {...}<-here ... ]，闭合该对象后栈回到 ["{","["]。
            if ch == "}" and stack == ["{", "["]:
                best_cut = i + 1
                best_close = "]}"
    if best_cut == -1:
        return None
    candidate = t[start:best_cut] + best_close
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


async def summarize_chapter(title: str, content: str) -> str:
    """生成章节压缩摘要。失败时回退为正文前 150 字。"""
    logger.info(f"开始生成摘要：章节「{title}」，正文 {len(content)} 字")
    try:
        msgs = prompts.build_summary_prompt(title, content)
        summary = await client.chat(msgs, temperature=0.3, max_tokens=512)
        logger.info(f"摘要生成成功：{len(summary)} 字")
        return summary.strip()
    except client.LLMError as e:
        logger.warning(f"摘要生成失败，回退到正文前150字：{e}")
        return content.strip()[:150]


async def update_memory_after_chapter(session: Session, project_id: int,
                                      chapter: Chapter) -> dict:
    """章节确认后：生成摘要 + 抽取角色状态/伏笔变动并回写数据库。

    返回一个变更概要，供前端展示"记忆已更新"。
    """
    logger.info(f"更新章节记忆：project_id={project_id}, chapter_id={chapter.id}, title={chapter.title}")
    report = {"summary": "", "character_updates": [], "new_threads": [],
              "resolved_threads": []}

    # 1) 摘要
    summary = await summarize_chapter(chapter.title, chapter.content)
    chapter.summary = summary
    session.add(chapter)
    report["summary"] = summary

    # 2) 状态/伏笔抽取
    characters = context_builder.characters_to_dicts(session, project_id)
    open_threads = context_builder.open_threads_to_dicts(session, project_id)
    logger.info(f"状态抽取：已知角色 {len(characters)} 个，开放伏笔 {len(open_threads)} 个")
    try:
        msgs = prompts.build_state_extraction_prompt(
            chapter.content, characters, open_threads
        )
        raw = await client.chat(msgs, temperature=0.2, max_tokens=1024)
        parsed = _extract_json(raw)
    except client.LLMError as e:
        logger.warning(f"状态抽取失败：{e}")
        parsed = None

    if isinstance(parsed, dict):
        # 角色状态更新
        for upd in parsed.get("character_updates", []) or []:
            name = (upd or {}).get("name")
            new_state = (upd or {}).get("state")
            if not name or not new_state:
                continue
            row = session.exec(
                select(Character)
                .where(Character.project_id == project_id)
                .where(Character.name == name)
            ).first()
            if row:
                row.state = new_state
                session.add(row)
                report["character_updates"].append({"name": name, "state": new_state})
                logger.info(f"更新角色状态：{name} → {new_state[:50]}")

        # 新伏笔
        for th in parsed.get("new_threads", []) or []:
            name = (th or {}).get("name")
            if not name:
                continue
            exists = session.exec(
                select(PlotThread)
                .where(PlotThread.project_id == project_id)
                .where(PlotThread.name == name)
            ).first()
            if not exists:
                session.add(PlotThread(
                    project_id=project_id, name=name,
                    detail=(th or {}).get("detail", ""),
                    status="open", opened_at_chapter=chapter.order_index,
                ))
                report["new_threads"].append(name)
                logger.info(f"新增伏笔：{name}")

        # 回收伏笔
        for name in parsed.get("resolved_threads", []) or []:
            row = session.exec(
                select(PlotThread)
                .where(PlotThread.project_id == project_id)
                .where(PlotThread.name == name)
            ).first()
            if row and row.status == "open":
                row.status = "resolved"
                session.add(row)
                report["resolved_threads"].append(name)
                logger.info(f"回收伏笔：{name}")

    session.commit()
    logger.info(f"记忆更新完成：{len(report['character_updates'])} 个角色状态更新，"
                f"{len(report['new_threads'])} 个新伏笔，{len(report['resolved_threads'])} 个伏笔回收")
    return report
