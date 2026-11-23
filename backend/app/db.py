"""数据库引擎与会话。"""
import logging
from collections.abc import Generator

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

logger = logging.getLogger(__name__)

# SQLite + 多线程（FastAPI 默认线程池）需要关闭 same-thread 检查
engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


# 给已存在的表补新列（SQLite 的 ALTER TABLE ADD COLUMN，非破坏性）。
# create_all 只建新表、不改旧表结构，所以新增字段要在这里手动补。
# 格式：表名 -> {列名: "列定义（带默认值）"}
_COLUMN_MIGRATIONS = {
    "project": {
        "dianwen": "TEXT NOT NULL DEFAULT ''",
        "opening_idea": "TEXT NOT NULL DEFAULT ''",
        "core_character_count": "INTEGER NOT NULL DEFAULT 0",
        "full_synopsis": "TEXT NOT NULL DEFAULT ''",
    },
    "shot": {
        "shot_size": "TEXT NOT NULL DEFAULT ''",
        "sound": "TEXT NOT NULL DEFAULT ''",
        "lighting": "TEXT NOT NULL DEFAULT ''",
        "scene": "TEXT NOT NULL DEFAULT ''",
    },
    "characterdesign": {
        "gender": "TEXT NOT NULL DEFAULT ''",
        "age": "TEXT NOT NULL DEFAULT ''",
        "body_type": "TEXT NOT NULL DEFAULT ''",
        "color_tone": "TEXT NOT NULL DEFAULT ''",
        "kling_voice_id": "TEXT NOT NULL DEFAULT ''",
        "kling_element_id": "TEXT NOT NULL DEFAULT ''",
    },
    "scene": {
        "style": "TEXT NOT NULL DEFAULT ''",
        "time_weather": "TEXT NOT NULL DEFAULT ''",
        "color_tone": "TEXT NOT NULL DEFAULT ''",
        "space_desc": "TEXT NOT NULL DEFAULT ''",
        "materials": "TEXT NOT NULL DEFAULT ''",
        "vista": "TEXT NOT NULL DEFAULT ''",
        "light_source": "TEXT NOT NULL DEFAULT ''",
        "light_tech": "TEXT NOT NULL DEFAULT ''",
        "viewpoint": "TEXT NOT NULL DEFAULT ''",
        "composition": "TEXT NOT NULL DEFAULT ''",
        "locked_prompt": "TEXT NOT NULL DEFAULT ''",
        "image_path": "TEXT NOT NULL DEFAULT ''",
        "status": "TEXT NOT NULL DEFAULT 'draft'",
        "updated_at": "TEXT",
    },
}


def _run_column_migrations() -> None:
    """为已存在的表补齐缺失的列（仅 SQLite，幂等）。"""
    with engine.connect() as conn:
        for table, columns in _COLUMN_MIGRATIONS.items():
            # 表不存在就跳过（create_all 会建好新表，自带全部列）
            exists = conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=:t"
            ), {"t": table}).first()
            if not exists:
                continue
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for col, ddl in columns.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
                    logger.info(f"[DB] 迁移：{table} 表新增列 {col}")
        conn.commit()


def init_db() -> None:
    # 确保所有表已被导入后再建表
    import app.models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _run_column_migrations()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
