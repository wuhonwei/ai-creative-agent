"""剧情决策：三个方向供选择，选定后进入决策链。

生成一组方向后，后台再准备下一组缓存。缓存不替换当前选项；
用户刷新时若缓存已就绪，立即换成那一组，并再补一份新缓存。
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from app.db import engine, get_session
from app.llm import client, prompts
from app.models import PlotDecision, Project
from app.schemas import PlotChooseRequest, PlotOptionsRequest
from app.services.app_settings import get_dianwen
from app.services.generation import _extract_json

router = APIRouter(prefix="/api", tags=["plot-decisions"])
logger = logging.getLogger(__name__)

_EMPTY = {"无", "没有", "暂无", "无。", "没有新人物", "没有新势力"}
RECENT_FULL = 12
FOLD_BATCH = 4
_warming: set[tuple[int, str]] = set()


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _is_empty_cast(text: str) -> bool:
    return not text or text in _EMPTY


def _chosen(session: Session, project_id: int) -> list[PlotDecision]:
    return session.exec(
        select(PlotDecision)
        .where(PlotDecision.project_id == project_id)
        .where(PlotDecision.status == "chosen")
        .order_by(PlotDecision.order_index)
    ).all()


def _by_status(session: Session, project_id: int, status: str) -> list[PlotDecision]:
    return session.exec(
        select(PlotDecision)
        .where(PlotDecision.project_id == project_id)
        .where(PlotDecision.status == status)
        .order_by(PlotDecision.id)
    ).all()


def _candidates(session: Session, project_id: int) -> list[PlotDecision]:
    return _by_status(session, project_id, "candidate")


def _cached(session: Session, project_id: int) -> list[PlotDecision]:
    return _by_status(session, project_id, "cached")


def _cache_key(idea: str, chosen: list[PlotDecision]) -> str:
    chain = ",".join(f"{row.id}:{row.order_index}" for row in chosen)
    return f"{idea.strip()}\n{chain}"


def _schedule_warm(background: BackgroundTasks, project_id: int, idea: str, cache_key: str) -> None:
    background.add_task(_warm_plot_cache, project_id, idea, cache_key)


async def _produce_options(session: Session, project: Project, idea: str) -> tuple[list[dict], str]:
    """按当前已选决策生成三个方向。返回选项和对应的缓存键。"""
    chosen = _chosen(session, project.id)
    _earlier, pending, recent = _split_history(
        chosen, project.plot_decision_digest_through or 0
    )
    digest = await _refresh_digest(project, pending)
    session.add(project)
    session.commit()
    through = project.plot_decision_digest_through or 0
    if through:
        pending = [row for row in pending if row.order_index > through]
    chosen = _chosen(session, project.id)
    dianwen = prompts.resolve_dianwen(None, get_dianwen(session))
    messages = prompts.build_plot_options_prompt(
        idea,
        [_decision_dict(row) for row in recent],
        dianwen,
        roster=[_decision_dict(row) for row in chosen],
        earlier_digest=digest,
        pending_earlier=[_decision_dict(row) for row in pending],
    )
    budget = client.output_token_budget(sum(len(m.get("content") or "") for m in messages))
    raw = await client.chat(messages, temperature=0.9, max_tokens=budget)
    options = _parse_options(raw)
    if len(options) < 3:
        logger.error("剧情决策不足三个可用方向：%s", raw[:1000])
        raise HTTPException(502, "模型没有给出三个不同方向，请再刷新一次")
    return options, _cache_key(idea, chosen)


def _save_rows(session: Session, project_id: int, options: list[dict], status: str, cache_key: str = "") -> None:
    for item in options:
        session.add(PlotDecision(
            project_id=project_id,
            status=status,
            cache_key=cache_key,
            **item,
        ))


def _promote_cache(session: Session, project: Project, idea: str, cache_key: str) -> bool:
    """缓存对得上当前思路和已选决策时，换成正在看的三个方向。对不上就丢掉。"""
    cached = _cached(session, project.id)
    matched = [row for row in cached if row.cache_key == cache_key]
    stale = [row for row in cached if row.cache_key != cache_key]
    for row in stale:
        session.delete(row)
    if len(matched) < 3:
        session.commit()
        return False
    for row in _candidates(session, project.id):
        session.delete(row)
    for row in matched[:3]:
        row.status = "candidate"
        row.cache_key = ""
        session.add(row)
    for row in matched[3:]:
        session.delete(row)
    project.opening_idea = idea
    session.add(project)
    session.commit()
    return True


async def _warm_plot_cache(project_id: int, idea: str, cache_key: str) -> None:
    """再生成一组方向放进缓存，不改当前正在看的选项。"""
    token = (project_id, cache_key)
    if token in _warming:
        return
    _warming.add(token)
    try:
        with Session(engine) as session:
            project = session.get(Project, project_id)
            if not project:
                return
            chosen = _chosen(session, project_id)
            if _cache_key(idea, chosen) != cache_key:
                return
            ready = [row for row in _cached(session, project_id) if row.cache_key == cache_key]
            if len(ready) >= 3:
                return
            try:
                options, produced_key = await _produce_options(session, project, idea)
            except (HTTPException, client.LLMError) as e:
                logger.error("[plot] 预备下一组方向失败：%s", getattr(e, "detail", e))
                return
            if produced_key != cache_key or _cache_key(idea, _chosen(session, project_id)) != cache_key:
                return
            for row in _cached(session, project_id):
                session.delete(row)
            _save_rows(session, project_id, options, "cached", cache_key)
            session.commit()
            logger.info("[plot] 已备好下一组方向 project=%s", project_id)
    finally:
        _warming.discard(token)


def _payload(session: Session, project_id: int) -> dict:
    return {"chosen": _chosen(session, project_id), "options": _candidates(session, project_id)}


def _parse_options(raw: str) -> list[dict]:
    parsed = _extract_json(raw)
    items = parsed.get("options") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        return []
    options = []
    for item in items:
        if not isinstance(item, dict):
            continue
        plot = _text(item.get("plot"))
        merits = _text(item.get("merits"))
        drawbacks = _text(item.get("drawbacks"))
        characters = _text(item.get("new_characters"))
        factions = _text(item.get("new_factions"))
        if not plot or not merits or not drawbacks:
            continue
        if _is_empty_cast(characters) and _is_empty_cast(factions):
            continue
        options.append({
            "title": _text(item.get("title")) or "未命名方向",
            "plot": plot,
            "new_characters": characters or "无",
            "new_factions": factions or "无",
            "merits": merits,
            "drawbacks": drawbacks,
        })
        if len(options) == 3:
            break
    return options


def _decision_dict(row: PlotDecision) -> dict:
    return {
        "order_index": row.order_index,
        "title": row.title,
        "plot": row.plot,
        "new_characters": row.new_characters,
        "new_factions": row.new_factions,
    }


def _split_history(chosen: list[PlotDecision], digest_through: int):
    """最近若干步保留全文；更早的步骤能摘要的摘要，摘要还没跟上的仍带原文。"""
    if len(chosen) <= RECENT_FULL:
        return [], [], list(chosen)
    earlier = list(chosen[:-RECENT_FULL])
    recent = list(chosen[-RECENT_FULL:])
    pending = [row for row in earlier if row.order_index > digest_through]
    return earlier, pending, recent


async def _refresh_digest(project: Project, pending: list[PlotDecision]) -> str:
    """每积够一批较早决策，就并入一段情节摘要。失败时沿用旧摘要，原文仍会送进下一步。"""
    if len(pending) < FOLD_BATCH:
        return project.plot_decision_digest or ""
    covered = pending[:FOLD_BATCH]
    try:
        text = await client.chat(
            prompts.build_decision_digest_prompt(
                project.plot_decision_digest or "",
                [_decision_dict(row) for row in covered],
            ),
            temperature=0.3,
            max_tokens=16384,
        )
    except client.LLMError as e:
        logger.error("[plot] 较早决策摘要失败：%s", e)
        return project.plot_decision_digest or ""
    digest = (text or "").strip()
    if not digest:
        return project.plot_decision_digest or ""
    project.plot_decision_digest = digest
    project.plot_decision_digest_through = covered[-1].order_index
    return digest


@router.get("/projects/{project_id}/plot-decisions")
def list_plot_decisions(project_id: int, session: Session = Depends(get_session)):
    if not session.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    return _payload(session, project_id)


@router.post("/projects/{project_id}/plot-decisions/options")
async def generate_plot_options(project_id: int, body: PlotOptionsRequest,
                                background: BackgroundTasks,
                                session: Session = Depends(get_session)):
    """生成三个后续方向。有缓存时刷新立即换上，并在后台再备一组。"""
    idea = (body.idea or "").strip()
    if not idea:
        raise HTTPException(400, "请先写下开头或思路")
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    chosen = _chosen(session, project_id)
    key = _cache_key(idea, chosen)
    if _promote_cache(session, project, idea, key):
        _schedule_warm(background, project_id, idea, key)
        return _payload(session, project_id)

    try:
        options, key = await _produce_options(session, project, idea)
    except client.LLMError as e:
        raise HTTPException(502, str(e)) from e

    project.opening_idea = idea
    session.add(project)
    for row in _candidates(session, project_id):
        session.delete(row)
    _save_rows(session, project_id, options, "candidate")
    session.commit()
    _schedule_warm(background, project_id, idea, key)
    return _payload(session, project_id)


@router.post("/projects/{project_id}/plot-decisions/choose")
def choose_plot_option(project_id: int, body: PlotChooseRequest,
                       session: Session = Depends(get_session)):
    """选定一个方向，加入决策列表，并清掉其余待选项。"""
    if not session.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    option = session.get(PlotDecision, body.option_id)
    if not option or option.project_id != project_id or option.status != "candidate":
        raise HTTPException(404, "这个方向已经不在待选里")

    chosen = _chosen(session, project_id)
    option.status = "chosen"
    option.order_index = (chosen[-1].order_index + 1) if chosen else 1
    session.add(option)
    for row in _candidates(session, project_id):
        if row.id != option.id:
            session.delete(row)
    for row in _cached(session, project_id):
        session.delete(row)
    session.commit()
    return _payload(session, project_id)


@router.delete("/projects/{project_id}/plot-decisions/latest")
def undo_latest_decision(project_id: int, session: Session = Depends(get_session)):
    """撤回最近一条已选决策，并清掉基于它生成的待选项。"""
    if not session.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    chosen = _chosen(session, project_id)
    if not chosen:
        raise HTTPException(400, "还没有已选决策")
    project = session.get(Project, project_id)
    removed = chosen[-1]
    if (project.plot_decision_digest_through or 0) >= removed.order_index:
        project.plot_decision_digest = ""
        project.plot_decision_digest_through = 0
        session.add(project)
    session.delete(removed)
    for row in _candidates(session, project_id):
        session.delete(row)
    for row in _cached(session, project_id):
        session.delete(row)
    session.commit()
    return _payload(session, project_id)
