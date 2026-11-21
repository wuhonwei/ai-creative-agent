# -*- coding: utf-8 -*-
"""Batch humanize chapters from 死亡蔓延 through 第二卷第53章, update DB."""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime

from sqlmodel import Session, select

# ensure backend on path
sys.path.insert(0, os.path.dirname(__file__))

from app.db import engine
from app.llm import client, prompts
from app.models import Chapter, Project

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "_humanize_batch.log"),
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("batch_humanize")

PROGRESS_FILE = os.path.join(os.path.dirname(__file__), "_humanize_progress.json")

# 第37章 死亡蔓延 (order_index=36) → 第二卷第53章 前哨站 (order_index=1052)
VOL1_START = 36
VOL1_END = 55
VOL2_START = 1000
VOL2_END = 1052


def chapter_in_range(order_index: int) -> bool:
    return (VOL1_START <= order_index <= VOL1_END) or (
        VOL2_START <= order_index <= VOL2_END
    )


def load_progress() -> set[int]:
    if not os.path.exists(PROGRESS_FILE):
        return set()
    with open(PROGRESS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return set(data.get("done_ids", []))


def save_progress(done_ids: set[int], last: dict | None = None) -> None:
    payload = {"done_ids": sorted(done_ids), "updated_at": datetime.now().isoformat()}
    if last:
        payload["last"] = last
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


async def humanize_one(project_dict: dict, ch: Chapter) -> tuple[str, dict]:
    """润色已有章节：不低于全局字数下限即可接受。"""
    text = ch.content
    original_len = len(text)
    word_min = max(client.settings.chapter_word_count_min, int(original_len * 0.85))

    msgs = prompts.build_humanizer_prompt(
        project_dict, ch.title, text, word_min=word_min,
    )
    raw = await client.chat(
        msgs, temperature=0.5, max_tokens=client.settings.llm_max_tokens,
    )
    humanized = prompts.strip_humanizer_output(raw)
    if not humanized:
        return text, {"skipped": True, "reason": "模型未返回润色内容"}

    hc = len(humanized)
    if hc >= client.settings.chapter_word_count_min:
        return humanized, {"skipped": False, "word_count": hc}

    return text, {
        "skipped": True,
        "reason": f"润色后字数 {hc} 低于下限 {client.settings.chapter_word_count_min}，保留原稿",
        "word_count": hc,
    }


async def main() -> None:
    if not client.settings.llm_configured:
        log.error("DEEPSEEK_API_KEY 未配置")
        sys.exit(1)

    done_ids = load_progress()

    with Session(engine) as session:
        project = session.exec(select(Project).where(Project.id == 1)).first()
        if not project:
            log.error("project_id=1 不存在")
            sys.exit(1)

        project_dict = {
            "title": project.title,
            "style": project.style,
            "mood": project.mood,
        }

        chapters = session.exec(
            select(Chapter)
            .where(Chapter.project_id == 1)
            .order_by(Chapter.order_index)
        ).all()

        targets = [
            c for c in chapters
            if chapter_in_range(c.order_index) and c.content.strip()
        ]
        log.info(
            "待处理 %d 章（已完成 %d 章，跳过）",
            len(targets),
            len(done_ids),
        )

        ok = skip = fail = 0
        for ch in targets:
            if ch.id in done_ids:
                log.info("跳过已完成 id=%s %s", ch.id, ch.title)
                continue

            log.info(
                "开始 id=%s order=%s %s 原文字数=%d",
                ch.id,
                ch.order_index,
                ch.title,
                len(ch.content),
            )
            try:
                new_text, meta = await humanize_one(project_dict, ch)
            except Exception as e:
                log.exception("失败 id=%s: %s", ch.id, e)
                fail += 1
                continue

            if meta.get("skipped"):
                log.warning(
                    "跳过 id=%s %s 原因=%s",
                    ch.id,
                    ch.title,
                    meta.get("reason", meta),
                )
                skip += 1
                done_ids.add(ch.id)
                save_progress(done_ids, {"id": ch.id, "skipped": True, **meta})
                continue

            ch.content = new_text
            ch.word_count = len(new_text)
            ch.updated_at = datetime.now()
            session.add(ch)
            session.commit()
            session.refresh(ch)

            ok += 1
            done_ids.add(ch.id)
            save_progress(
                done_ids,
                {
                    "id": ch.id,
                    "title": ch.title,
                    "word_count": ch.word_count,
                    "soft_accept": meta.get("soft_accept"),
                },
            )
            log.info(
                "完成 id=%s %s 新字数=%d%s",
                ch.id,
                ch.title,
                ch.word_count,
                " (soft_accept)" if meta.get("soft_accept") else "",
            )

        log.info("全部结束：成功=%d 跳过=%d 失败=%d", ok, skip, fail)


if __name__ == "__main__":
    asyncio.run(main())
