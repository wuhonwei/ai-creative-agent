"""数据库迁移：为 CharacterDesign 和 Scene 表添加 kling_element_id 字段。

运行方式：
  python migrate_add_element_id.py
"""
import sqlite3
import sys
from pathlib import Path

# 设置输出编码为 UTF-8
sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

DB_PATH = Path(__file__).parent / "creative_agent.db"


def migrate():
    """添加 kling_element_id 字段到 characterdesign 和 scene 表。"""
    if not DB_PATH.exists():
        print(f"[X] Database file not found: {DB_PATH}")
        print("    If this is first run, DB will be auto-created on backend startup. No migration needed.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # 1. 检查并添加 CharacterDesign.kling_element_id
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='characterdesign'"
        )
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(characterdesign)")
            columns = [row[1] for row in cursor.fetchall()]

            if "kling_element_id" not in columns:
                print("[...] Adding kling_element_id to characterdesign table...")
                cursor.execute("ALTER TABLE characterdesign ADD COLUMN kling_element_id TEXT DEFAULT ''")
                print("[OK] characterdesign.kling_element_id added.")
            else:
                print("[OK] characterdesign.kling_element_id already exists.")
        else:
            print("[SKIP] characterdesign table does not exist.")

        # 2. 检查并添加 Scene.kling_element_id
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='scene'"
        )
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(scene)")
            columns = [row[1] for row in cursor.fetchall()]

            if "kling_element_id" not in columns:
                print("[...] Adding kling_element_id to scene table...")
                cursor.execute("ALTER TABLE scene ADD COLUMN kling_element_id TEXT DEFAULT ''")
                print("[OK] scene.kling_element_id added.")
            else:
                print("[OK] scene.kling_element_id already exists.")
        else:
            print("[SKIP] scene table does not exist.")

        conn.commit()
        print("\n[SUCCESS] Migration completed!")

    except Exception as e:
        print(f"[ERROR] Migration failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
