"""导出：把全书（已确认 + 草稿）拼成 txt / md 文件下载。"""
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.db import get_session
from app.models import Chapter, OutlineNode, Project

router = APIRouter(prefix="/api", tags=["export"])


def _compose(session: Session, project_id: int, fmt: str,
             only_confirmed: bool) -> str:
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    q = (
        select(Chapter)
        .where(Chapter.project_id == project_id)
        .order_by(Chapter.order_index)
    )
    if only_confirmed:
        q = q.where(Chapter.status == "confirmed")
    chapters = session.exec(q).all()

    lines: list[str] = []
    if fmt == "md":
        lines.append(f"# {project.title}\n")
        if project.premise:
            lines.append(f"> {project.premise}\n")
        for ch in chapters:
            lines.append(f"\n## {ch.title}\n")
            lines.append(ch.content)
    else:  # txt
        lines.append(project.title)
        lines.append("=" * len(project.title) + "\n")
        for ch in chapters:
            lines.append(f"\n{ch.title}\n{'-' * 20}\n")
            lines.append(ch.content)

    return "\n".join(lines)


@router.get("/projects/{project_id}/export")
def export_project(project_id: int, fmt: str = "txt",
                   only_confirmed: bool = False,
                   session: Session = Depends(get_session)):
    """fmt=txt|md；only_confirmed=true 仅导出已确认章节。"""
    if fmt not in ("txt", "md"):
        raise HTTPException(400, "fmt 仅支持 txt 或 md")

    text = _compose(session, project_id, fmt, only_confirmed)
    project = session.get(Project, project_id)
    filename = f"{project.title or 'novel'}.{fmt}"

    buf = io.BytesIO(text.encode("utf-8"))
    # RFC 5987 文件名编码，兼容中文
    from urllib.parse import quote
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    media = "text/markdown" if fmt == "md" else "text/plain"
    return StreamingResponse(
        buf, media_type=f"{media}; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


@router.get("/projects/{project_id}/stats")
def project_stats(project_id: int, session: Session = Depends(get_session)):
    """字数统计：总字数、已确认字数、章节数等。"""
    chapters = session.exec(
        select(Chapter).where(Chapter.project_id == project_id)
    ).all()
    total = sum(c.word_count for c in chapters)
    confirmed = sum(c.word_count for c in chapters if c.status == "confirmed")
    return {
        "total_words": total,
        "confirmed_words": confirmed,
        "chapter_count": len(chapters),
        "confirmed_chapters": sum(1 for c in chapters if c.status == "confirmed"),
    }
