"""项目（含故事圣经）与角色的 CRUD。"""
import json
import logging
import re
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from app.db import engine, get_session
from app.llm import client, prompts
from app.models import (
    Chapter, Character, CharacterDesign, CharacterRefImage, OutlineNode,
    PlotDecision, PlotThread, Project,
)
from app.schemas import (
    BibleAutofillRequest, BibleFromSynopsisRequest, CharacterCreate, CharacterUpdate,
    ProjectCreate, ProjectUpdate, SettingsUpdate, SynopsisGenerateRequest,
)
from app.services.app_settings import get_dianwen, save_dianwen
from app.services.generation import _extract_json

router = APIRouter(prefix="/api", tags=["projects"])
logger = logging.getLogger(__name__)


# ---------------- Projects ----------------
@router.get("/settings")
def read_settings(session: Session = Depends(get_session)):
    return {"dianwen": get_dianwen(session)}


@router.patch("/settings")
def update_settings(body: SettingsUpdate, session: Session = Depends(get_session)):
    if body.dianwen is not None:
        save_dianwen(session, body.dianwen)
    return {"dianwen": get_dianwen(session)}


@router.get("/projects")
def list_projects(session: Session = Depends(get_session)):
    return session.exec(select(Project).order_by(Project.updated_at.desc())).all()


@router.post("/projects")
def create_project(body: ProjectCreate, session: Session = Depends(get_session)):
    project = Project(**body.model_dump())
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@router.get("/projects/{project_id}")
def get_project(project_id: int, session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    return project


@router.patch("/projects/{project_id}")
def update_project(project_id: int, body: ProjectUpdate,
                   session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(project, k, v)
    # 开头思路、人数和全文概述是边写边存的草稿，不把作品顶到列表最前。
    if set(data) - {"opening_idea", "core_character_count", "full_synopsis"}:
        project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


@router.post("/projects/{project_id}/bible/autofill")
async def autofill_bible(project_id: int, body: BibleAutofillRequest,
                         session: Session = Depends(get_session)):
    """用开头思路填充故事圣经和角色档案。生成时参考癫文宝经。"""
    idea = (body.idea or "").strip()
    if not idea:
        raise HTTPException(400, "请先写下开头思路")

    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    existing = session.exec(
        select(Character).where(Character.project_id == project_id)
    ).all()
    has_bible = any(
        (getattr(project, key) or "").strip()
        for key in ("premise", "mood", "style", "world_rules")
    )
    if (has_bible or existing) and not body.replace:
        raise HTTPException(
            409, "已有故事圣经或角色档案。确认后将覆盖这些内容，并删除现有角色的形象设定。"
        )

    dianwen = prompts.resolve_dianwen(None, get_dianwen(session))
    count = body.core_character_count if body.core_character_count and body.core_character_count > 0 else 17
    msgs = prompts.build_bible_fill_prompt(idea, project.title, dianwen, count)
    try:
        # 推理模型会先消耗大量 token；人数多时再抬高上限，避免角色列表被截断。
        raw = await client.chat(msgs, temperature=0.8, max_tokens=min(32768, 8192 + count * 1200))
    except client.LLMError as e:
        raise HTTPException(502, str(e)) from e

    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        logger.error("自动填充故事圣经的模型输出无法解析：%s", raw[:1000])
        raise HTTPException(502, "模型未返回可用的故事圣经，请再试一次")

    premise = _as_text(parsed.get("premise"))
    mood = _as_text(parsed.get("mood"))
    style = _as_text(parsed.get("style"))
    world_rules = _as_text(parsed.get("world_rules"))
    if not premise:
        raise HTTPException(502, "模型未写好故事背景，请换个开头思路再试")

    new_characters: list[Character] = []
    for item in parsed.get("characters") or []:
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get("name"))
        if not name:
            continue
        new_characters.append(Character(
            project_id=project_id,
            name=name,
            role=_as_text(item.get("role")),
            description=_as_text(item.get("description")),
        ))
    if not new_characters:
        raise HTTPException(502, "模型未返回有效角色")

    project.premise = premise
    project.mood = mood
    project.style = style
    project.world_rules = world_rules
    new_title = _as_text(parsed.get("title"))
    if new_title and project.title.strip() in ("", "未命名作品"):
        project.title = new_title
    project.updated_at = datetime.utcnow()

    char_ids = [c.id for c in existing]
    if char_ids:
        for model in (CharacterRefImage, CharacterDesign):
            for row in session.exec(
                select(model).where(model.character_id.in_(char_ids))
            ).all():
                session.delete(row)
        for ch in existing:
            session.delete(ch)
        session.commit()

    session.add(project)
    for ch in new_characters:
        session.add(ch)
    session.commit()
    session.refresh(project)
    for ch in new_characters:
        session.refresh(ch)
    return {"project": project, "characters": new_characters}


def _clean_prose(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def _characters_from_json(project_id: int, items) -> list[Character]:
    found: list[Character] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get("name"))
        if not name:
            continue
        found.append(Character(
            project_id=project_id,
            name=name,
            role=_as_text(item.get("role")),
            description=_as_text(item.get("description")),
        ))
    return found


def _delete_characters(session: Session, existing: list[Character]) -> None:
    char_ids = [c.id for c in existing]
    if not char_ids:
        return
    for model in (CharacterRefImage, CharacterDesign):
        for row in session.exec(select(model).where(model.character_id.in_(char_ids))).all():
            session.delete(row)
    for ch in existing:
        session.delete(ch)
    session.commit()


_NO_THINKING = {"thinking": {"type": "disabled"}}


_SPEECH_RE = re.compile(
    r"(?:说|道|问|喊|笑道|回答|开口)[：:]|[“「\"][^”」\"]{6,}[”」\"]"
)


def _too_scenic(text: str) -> bool:
    if _SPEECH_RE.search(text):
        return True
    return sum(text.count(ch) for ch in "“”「」\"") >= 2


def _strip_quotes(text: str) -> str:
    """去掉引号。像对白的内容删掉，短专名只去掉引号本身。"""

    def _repl(match: re.Match) -> str:
        inner = match.group(1).strip()
        if re.search(r"[我你吗呢吧啊。！？，]", inner) or len(inner) > 12:
            return ""
        return inner

    text = re.sub(r"[“「\"]([^”」\"]*)[”」\"]", _repl, text)
    text = re.sub(r"(说|道|问|喊|笑道|回答|开口)[：:]\s*", "", text)
    text = re.sub(r"[，、]{2,}", "，", text)
    text = re.sub(r"。{2,}", "。", text)
    return text.strip()


async def _restyle_one(text: str) -> str:
    messages = prompts.build_synopsis_restyle_prompt(text)
    raw = await client.chat(messages, temperature=0.2, max_tokens=12288, extra=_NO_THINKING)
    restyled = _clean_prose(raw)
    if len(restyled) < 200:
        return text
    return restyled


def _synopsis_chunks(text: str, limit: int = 1800) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n+", text) if part.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if buf and len(buf) + len(para) > limit:
            chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n{para}".strip()
    if buf:
        chunks.append(buf)
    return chunks or [text]


async def _restyle_synopsis(text: str) -> str:
    if not _too_scenic(text):
        return text
    parts = []
    for chunk in _synopsis_chunks(text):
        part = await _restyle_one(chunk)
        if _too_scenic(part):
            part = await _restyle_one(part)
        parts.append(_strip_quotes(part) if _too_scenic(part) else part)
    return "\n".join(parts).strip()


async def _write_long_synopsis(idea: str, title: str, dianwen: str) -> str:
    messages = prompts.build_full_synopsis_prompt(idea, title, dianwen)
    raw = await client.chat(
        messages, temperature=0.4, max_tokens=24576, extra=_NO_THINKING,
    )
    text = _clean_prose(raw)
    for _ in range(3):
        if len(text) >= prompts.SYNOPSIS_MIN_CHARS:
            break
        room = prompts.SYNOPSIS_MAX_CHARS - len(text)
        if room < 400:
            break
        need = prompts.SYNOPSIS_MIN_CHARS - len(text)
        follow = prompts.build_synopsis_continue_prompt(text, need, room)
        more = _clean_prose(await client.chat(
            follow, temperature=0.5, max_tokens=min(16384, max(room * 2, 2048)),
            extra=_NO_THINKING,
        ))
        if len(more) < 200:
            break
        text = text.rstrip() + "\n" + more
    if _too_scenic(text):
        text = _strip_quotes(await _restyle_synopsis(text))
    return text.strip()


def synopsis_mark(text: str) -> str:
    """和前端同一套 32 位滚动哈希，用来判断概述有没有被改过。"""
    value = 0
    for ch in (text or "").strip():
        value = (value * 31 + ord(ch)) & 0xFFFFFFFF
    return format(value, "x")


def _synopsis_cache_key(idea: str, title: str, dianwen: str) -> str:
    return f"{idea.strip()}\n{(title or '').strip()}\n{(dianwen or '').strip()}"


def _clamp_score(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(10, number))


def _normalize_score(parsed: dict, rules: list[str], text: str) -> dict:
    verbosity = parsed.get("verbosity") if isinstance(parsed, dict) else None
    if not isinstance(verbosity, dict):
        verbosity = {}
    score = _clamp_score(verbosity.get("score"))
    note = _as_text(verbosity.get("note"))[:200]
    if _too_scenic(text):
        score = 7 if score is None else max(score, 7)
        if "对白" not in note and "引号" not in note:
            note = (note + " 文中仍有对白或引号。").strip()
    if score is None:
        score = 0
        note = note or "这次没评上啰嗦程度。"
    by_index = {}
    raw_rules = parsed.get("rules") if isinstance(parsed, dict) else None
    if isinstance(raw_rules, list):
        for item in raw_rules:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index") or 0)
            except (TypeError, ValueError):
                continue
            by_index[index] = item
    scored_rules = []
    for index, rule in enumerate(rules, 1):
        item = by_index.get(index, {})
        rule_score = _clamp_score(item.get("score"))
        rule_note = _as_text(item.get("note"))[:160] or "这次没评上。"
        scored_rules.append({
            "index": index,
            "rule": rule,
            "score": rule_score,
            "note": rule_note,
        })
    return {
        "mark": synopsis_mark(text),
        "verbosity": {"score": score, "note": note or "未说明"},
        "rules": scored_rules,
    }


async def _score_synopsis(text: str, dianwen: str) -> str:
    rules = prompts.split_dianwen_rules(dianwen)
    messages = prompts.build_synopsis_score_prompt(text, rules)
    parsed = None
    last_raw = ""
    try:
        for _ in range(2):
            last_raw = await client.chat(
                messages, temperature=0.2, max_tokens=4096, extra=_NO_THINKING,
            )
            candidate = _extract_json(last_raw)
            if isinstance(candidate, dict):
                parsed = candidate
                if isinstance(candidate.get("rules"), list) or not rules:
                    break
    except client.LLMError as e:
        logger.error("全文概述评分失败：%s", e)
    if parsed is None:
        logger.error("全文概述评分无法解析：%s", last_raw[:800])
        parsed = {}
    return json.dumps(_normalize_score(parsed, rules, text), ensure_ascii=False)


def _take_synopsis_cache(project: Project, cache_key: str) -> bool:
    """缓存和当前思路、书名、癫文宝经一致时，换成正在看的这篇。"""
    cached = (project.synopsis_cache or "").strip()
    if project.synopsis_cache_key != cache_key or len(cached) < 1000:
        if project.synopsis_cache_key and project.synopsis_cache_key != cache_key:
            project.synopsis_cache = ""
            project.synopsis_cache_score = ""
            project.synopsis_cache_key = ""
        return False
    project.full_synopsis = cached
    project.synopsis_score = project.synopsis_cache_score or ""
    project.synopsis_cache = ""
    project.synopsis_cache_score = ""
    project.synopsis_cache_key = ""
    return True


_synopsis_warming: set[tuple[int, str]] = set()


async def _warm_synopsis_cache(project_id: int, idea: str, title: str, cache_key: str) -> None:
    """再写一篇概述放进备份，不改当前正在看的那篇。"""
    token = (project_id, cache_key)
    if token in _synopsis_warming:
        return
    _synopsis_warming.add(token)
    try:
        with Session(engine) as session:
            project = session.get(Project, project_id)
            if not project:
                return
            dianwen = prompts.resolve_dianwen(None, get_dianwen(session))
            if _synopsis_cache_key(idea, project.title, dianwen) != cache_key:
                return
            if (project.synopsis_cache_key or "") == cache_key and len((project.synopsis_cache or "").strip()) >= 1000:
                return
            try:
                text = await _write_long_synopsis(idea, title or project.title, dianwen)
            except client.LLMError as e:
                logger.error("[synopsis] 预备下一篇失败：%s", e)
                return
            if len(text) < 1000:
                logger.error("[synopsis] 预备的概述过短：%s 字", len(text))
                return
            score = await _score_synopsis(text, dianwen)
            project = session.get(Project, project_id)
            if not project:
                return
            dianwen = prompts.resolve_dianwen(None, get_dianwen(session))
            if _synopsis_cache_key(idea, project.title, dianwen) != cache_key:
                return
            if (project.synopsis_cache_key or "") == cache_key and len((project.synopsis_cache or "").strip()) >= 1000:
                return
            project.synopsis_cache = text
            project.synopsis_cache_score = score
            project.synopsis_cache_key = cache_key
            session.add(project)
            session.commit()
            logger.info("[synopsis] 已备好下一篇 project=%s len=%s", project_id, len(text))
    finally:
        _synopsis_warming.discard(token)


@router.post("/projects/{project_id}/synopsis/generate")
async def generate_full_synopsis(project_id: int, body: SynopsisGenerateRequest,
                                 background: BackgroundTasks,
                                 session: Session = Depends(get_session)):
    """用开头思路和癫文宝经写 8000 到 10000 字的全文概述，并打分。有备份就直接换上。"""
    idea = (body.idea or "").strip()
    if not idea:
        raise HTTPException(400, "请先写下开头或思路")
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    dianwen = prompts.resolve_dianwen(None, get_dianwen(session))
    cache_key = _synopsis_cache_key(idea, project.title, dianwen)
    from_backup = _take_synopsis_cache(project, cache_key)
    if not from_backup:
        try:
            text = await _write_long_synopsis(idea, project.title, dianwen)
        except client.LLMError as e:
            raise HTTPException(502, str(e)) from e
        if len(text) < 1000:
            logger.error("全文概述过短：%s 字", len(text))
            raise HTTPException(502, "模型没写出可用的全文概述，请再试一次")
        project.full_synopsis = text
        project.synopsis_score = await _score_synopsis(text, dianwen)
    project.opening_idea = idea
    project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)
    background.add_task(_warm_synopsis_cache, project.id, idea, project.title, cache_key)
    return {
        "project": project,
        "length": len(project.full_synopsis or ""),
        "from_backup": from_backup,
    }


@router.post("/projects/{project_id}/bible/from-synopsis")
async def bible_from_synopsis(project_id: int, body: BibleFromSynopsisRequest,
                              session: Session = Depends(get_session)):
    """用作者改过的全文概述填写故事圣经和角色。不改写这篇概述。"""
    synopsis = (body.synopsis or "").strip()
    if not synopsis:
        raise HTTPException(400, "请先写好全文概述")
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    existing = session.exec(select(Character).where(Character.project_id == project_id)).all()
    has_bible = any((getattr(project, key) or "").strip() for key in ("premise", "mood", "style", "world_rules"))
    if (has_bible or existing) and not body.replace:
        raise HTTPException(
            409, "已有故事圣经或角色档案。确认后将按这篇概述覆盖，并删除现有角色的形象设定。全文概述保持不变。"
        )
    dianwen = prompts.resolve_dianwen(None, get_dianwen(session))
    count = body.core_character_count if body.core_character_count and body.core_character_count > 0 else 17
    messages = prompts.build_bible_from_synopsis_prompt(synopsis, project.title, dianwen, count)
    budget = client.output_token_budget(sum(len(m.get("content") or "") for m in messages))
    try:
        raw = await client.chat(messages, temperature=0.4, max_tokens=min(budget, 32768))
    except client.LLMError as e:
        raise HTTPException(502, str(e)) from e
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        logger.error("按概述填充故事圣经无法解析：%s", raw[:1000])
        raise HTTPException(502, "模型未返回可用的故事圣经，请再试一次")
    premise = _as_text(parsed.get("premise"))
    if not premise:
        raise HTTPException(502, "模型未写好故事背景，请再试一次")
    new_characters = _characters_from_json(project_id, parsed.get("characters"))
    if not new_characters:
        raise HTTPException(502, "模型未返回有效角色")
    project.full_synopsis = synopsis
    project.premise = premise
    project.mood = _as_text(parsed.get("mood"))
    project.style = _as_text(parsed.get("style"))
    project.world_rules = _as_text(parsed.get("world_rules"))
    new_title = _as_text(parsed.get("title"))
    if new_title and project.title.strip() in ("", "未命名作品"):
        project.title = new_title
    project.updated_at = datetime.utcnow()
    _delete_characters(session, existing)
    session.add(project)
    for ch in new_characters:
        session.add(ch)
    session.commit()
    session.refresh(project)
    for ch in new_characters:
        session.refresh(ch)
    return {"project": project, "characters": new_characters}


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    # 级联删除关联数据
    for model in (Chapter, OutlineNode, Character, PlotThread, PlotDecision):
        for row in session.exec(
            select(model).where(model.project_id == project_id)
        ).all():
            session.delete(row)
    session.delete(project)
    session.commit()
    return {"ok": True}


# ---------------- Characters ----------------
@router.get("/projects/{project_id}/characters")
def list_characters(project_id: int, session: Session = Depends(get_session)):
    return session.exec(
        select(Character).where(Character.project_id == project_id)
    ).all()


@router.post("/projects/{project_id}/characters")
def create_character(project_id: int, body: CharacterCreate,
                     session: Session = Depends(get_session)):
    if not session.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    ch = Character(project_id=project_id, **body.model_dump())
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


@router.patch("/characters/{character_id}")
def update_character(character_id: int, body: CharacterUpdate,
                     session: Session = Depends(get_session)):
    ch = session.get(Character, character_id)
    if not ch:
        raise HTTPException(404, "角色不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(ch, k, v)
    session.add(ch)
    session.commit()
    session.refresh(ch)
    return ch


@router.delete("/characters/{character_id}")
def delete_character(character_id: int, session: Session = Depends(get_session)):
    ch = session.get(Character, character_id)
    if not ch:
        raise HTTPException(404, "角色不存在")
    session.delete(ch)
    session.commit()
    return {"ok": True}


# ---------------- Plot threads（伏笔，只读 + 手动管理）----------------
@router.get("/projects/{project_id}/threads")
def list_threads(project_id: int, session: Session = Depends(get_session)):
    return session.exec(
        select(PlotThread).where(PlotThread.project_id == project_id)
    ).all()
