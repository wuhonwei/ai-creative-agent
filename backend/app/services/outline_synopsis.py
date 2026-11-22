"""把生成结果里过短的章节梗概补到不少于 100 字。失败时保留原稿。"""
import logging

from app.llm import client, prompts
from app.services.generation import _extract_json

logger = logging.getLogger(__name__)

MIN_CHAPTER_SYNOPSIS_CHARS = 100


def _is_short(text: str) -> bool:
    return len((text or "").strip()) < MIN_CHAPTER_SYNOPSIS_CHARS


async def lengthen_chapter_synopses(chapters: list) -> list:
    """补长不足 100 字的章节梗概。只改 synopsis，不改标题和顺序。"""
    if not isinstance(chapters, list):
        return chapters
    result = []
    for ch in chapters:
        result.append(dict(ch) if isinstance(ch, dict) else ch)

    short_at = [
        i for i, ch in enumerate(result)
        if isinstance(ch, dict) and _is_short(ch.get("synopsis") or "")
    ]
    for start in range(0, len(short_at), 12):
        batch = short_at[start:start + 12]
        items = [
            {
                "index": n,
                "title": result[i].get("title") or "",
                "synopsis": (result[i].get("synopsis") or "").strip(),
            }
            for n, i in enumerate(batch)
        ]
        try:
            raw = await client.chat(
                prompts.build_lengthen_synopses_prompt(items),
                temperature=0.4,
                max_tokens=16384,
            )
        except client.LLMError as e:
            logger.error("[outline] 补长章节梗概失败：%s", e)
            continue
        parsed = _extract_json(raw)
        updated = parsed.get("chapters") if isinstance(parsed, dict) else None
        if not isinstance(updated, list):
            logger.error("[outline] 补长章节梗概无法解析：%s", raw[:400])
            continue
        by_index = {}
        for item in updated:
            if isinstance(item, dict) and isinstance(item.get("index"), int):
                by_index[item["index"]] = str(item.get("synopsis") or "").strip()
        for n, i in enumerate(batch):
            new_text = by_index.get(n, "")
            old_text = (result[i].get("synopsis") or "").strip()
            if len(new_text) > len(old_text):
                result[i]["synopsis"] = new_text
    return result


async def lengthen_outline_volumes(volumes: list) -> list:
    if not isinstance(volumes, list):
        return volumes
    for vol in volumes:
        if isinstance(vol, dict) and isinstance(vol.get("chapters"), list):
            vol["chapters"] = await lengthen_chapter_synopses(vol["chapters"])
    return volumes
