"""数据库迁移：为 Video 表添加 last_frame_path 字段。

运行方式：
  python migrate_add_last_frame_path.py
"""
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

DB_PATH = Path(__file__).parent / "creative_agent.db"


def migrate():
    if not DB_PATH.exists():
        print(f"[X] Database file not found: {DB_PATH}")
        print("    If this is first run, DB will be auto-created on backend startup. No migration needed.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='video'"
        )
        if not cursor.fetchone():
            print("[OK] video table does not exist yet. No migration needed.")
            return

        cursor.execute("PRAGMA table_info(video)")
        columns = [row[1] for row in cursor.fetchall()]

        if "last_frame_path" not in columns:
            print("[...] Adding last_frame_path to video table...")
            cursor.execute(
                "ALTER TABLE video ADD COLUMN last_frame_path TEXT DEFAULT ''"
            )
            print("[OK] video.last_frame_path added.")
        else:
            print("[OK] video.last_frame_path already exists.")

        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
