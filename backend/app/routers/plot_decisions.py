"""剧情决策：三个方向供选择，选定后进入决策链。"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.db import get_session
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


def _candidates(session: Session, project_id: int) -> list[PlotDecision]:
    return session.exec(
        select(PlotDecision)
        .where(PlotDecision.project_id == project_id)
        .where(PlotDecision.status == "candidate")
        .order_by(PlotDecision.id)
    ).all()


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
                                session: Session = Depends(get_session)):
    """生成三个后续方向。刷新时替换尚未选定的选项，已选决策保留。"""
    idea = (body.idea or "").strip()
    if not idea:
        raise HTTPException(400, "请先写下开头或思路")
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    chosen = _chosen(session, project_id)
    _earlier, pending, recent = _split_history(
        chosen, project.plot_decision_digest_through or 0
    )
    digest = await _refresh_digest(project, pending)
    session.add(project)
    session.commit()
    through = project.plot_decision_digest_through or 0
    if through:
        pending = [row for row in pending if row.order_index > through]
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
    try:
        raw = await client.chat(messages, temperature=0.9, max_tokens=budget)
    except client.LLMError as e:
        raise HTTPException(502, str(e)) from e

    options = _parse_options(raw)
    if len(options) < 3:
        logger.error("剧情决策不足三个可用方向：%s", raw[:1000])
        raise HTTPException(502, "模型没有给出三个不同方向，请再刷新一次")

    project.opening_idea = idea
    session.add(project)
    for row in _candidates(session, project_id):
        session.delete(row)
    for item in options:
        session.add(PlotDecision(project_id=project_id, status="candidate", **item))
    session.commit()
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
    session.commit()
    return _payload(session, project_id)
