"""全局设置。癫文宝经只有一份，所有作品读写同一份。"""
from sqlmodel import Session, select

from app.models import AppSetting, Project


def get_app_setting(session: Session) -> AppSetting:
    row = session.exec(select(AppSetting).order_by(AppSetting.id)).first()
    if row:
        return row
    # 第一次启用全局宝经时，沿用最近改过的那部作品里已保存的文本。
    seed = ""
    projects = session.exec(select(Project).order_by(Project.updated_at.desc())).all()
    for project in projects:
        text = (project.dianwen or "").strip()
        if text:
            seed = project.dianwen
            break
    row = AppSetting(dianwen=seed)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_dianwen(session: Session) -> str:
    return get_app_setting(session).dianwen or ""


def save_dianwen(session: Session, text: str) -> str:
    row = get_app_setting(session)
    row.dianwen = text
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.dianwen or ""
