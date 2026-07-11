"""并行章节生成：多路候选 → humanize → 评分择优。"""
import asyncio
import logging
from dataclasses import dataclass, field

from app.llm import client, prompts
from app.services import generation

logger = logging.getLogger(__name__)


@dataclass
class CandidateResult:
    index: int
    text: str = ""
    word_count: int = 0
    score: float = -1
    rationale: str = ""
    error: str | None = None
    humanize_skipped: bool = False
    scores: dict = field(default_factory=dict)


def _word_count_min() -> int:
    return client.settings.chapter_word_count_min


def _word_count_ok(count: int) -> bool:
    return count >= _word_count_min()


async def _humanize_chapter_text(project_dict: dict, title: str, text: str) -> tuple[str, dict]:
    """去 AI 味 + 压啰嗦润色。返回 (正文, 元信息)。"""
    original_len = len(text)
    word_min = max(_word_count_min(), int(original_len * 0.9))

    msgs = prompts.build_humanizer_prompt(
        project_dict, title, text, word_min=word_min,
    )
    raw = await client.chat(msgs, temperature=0.5, max_tokens=client.settings.llm_max_tokens)
    humanized = prompts.strip_humanizer_output(raw)
    if not humanized:
        return text, {"skipped": True, "reason": "模型未返回润色内容"}

    hc = len(humanized)
    if _word_count_ok(hc):
        return humanized, {"skipped": False, "word_count": hc}

    return text, {
        "skipped": True,
        "reason": f"润色后字数 {hc} 低于下限 {_word_count_min()}，保留原稿",
        "word_count": hc,
    }


async def _score_chapter(
    project_dict: dict,
    title: str,
    text: str,
    synopsis: str,
    history_summary: str,
    recent_text: str,
) -> tuple[float, str, dict]:
    msgs = prompts.build_chapter_score_prompt(
        project_dict, title, text, synopsis, history_summary, recent_text,
    )
    raw = await client.chat(msgs, temperature=0.2, max_tokens=600)
    parsed = generation._extract_json(raw)
    if not isinstance(parsed, dict):
        return 0.0, "评分解析失败", {}

    score = parsed.get("score", 0)
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0

    rationale = parsed.get("rationale", "") or ""
    dimensions = parsed.get("dimensions") if isinstance(parsed.get("dimensions"), dict) else {}
    return score, rationale, dimensions


async def _emit(queue: asyncio.Queue | None, event: str, data: dict) -> None:
    if queue is not None:
        await queue.put({"event": event, **data})


async def _run_single_candidate(
    index: int,
    messages: list[dict],
    project_dict: dict,
    title: str,
    synopsis: str,
    memory: dict,
    queue: asyncio.Queue | None = None,
) -> CandidateResult:
    try:
        await _emit(queue, "candidate", {
            "index": index, "stage": "generate",
            "message": f"候选 {index}：正在生成正文…",
        })
        raw = await client.chat(messages, temperature=client.settings.llm_temperature)
        text = raw.strip()
        if not text:
            await _emit(queue, "candidate", {
                "index": index, "stage": "error",
                "message": f"候选 {index}：生成结果为空",
            })
            return CandidateResult(index=index, error="模型未返回内容")

        await _emit(queue, "candidate", {
            "index": index, "stage": "humanize",
            "message": f"候选 {index}：正在去 AI 味并压啰嗦…",
        })
        text, meta = await _humanize_chapter_text(project_dict, title, text)

        await _emit(queue, "candidate", {
            "index": index, "stage": "score",
            "message": f"候选 {index}：正在评分…",
        })
        score, rationale, dimensions = await _score_chapter(
            project_dict, title, text, synopsis,
            memory["history_summary"], memory["recent_text"],
        )

        await _emit(queue, "candidate", {
            "index": index, "stage": "done",
            "message": f"候选 {index} 完成，评分 {score:.0f}",
            "score": score,
            "word_count": len(text),
        })
        return CandidateResult(
            index=index,
            text=text,
            word_count=len(text),
            score=score,
            rationale=rationale,
            scores=dimensions,
            humanize_skipped=meta.get("skipped", False),
        )
    except Exception as e:
        logger.exception("候选 %s 失败", index)
        await _emit(queue, "candidate", {
            "index": index, "stage": "error",
            "message": f"候选 {index} 失败：{e}",
        })
        return CandidateResult(index=index, error=str(e))


async def generate_best_parallel(
    messages: list[dict],
    project_dict: dict,
    title: str,
    synopsis: str,
    memory: dict,
    queue: asyncio.Queue | None = None,
    num_candidates: int | None = None,
) -> tuple[CandidateResult, list[CandidateResult]]:
    """并行生成 num_candidates 路，humanize 后评分，返回最优候选。"""
    n = num_candidates or client.settings.chapter_parallel_candidates
    await _emit(queue, "parallel", {
        "candidates": n,
        "message": f"已启动 {n} 路并行生成，完成后自动择优…",
    })

    tasks = [
        _run_single_candidate(
            i, messages, project_dict, title, synopsis, memory, queue,
        )
        for i in range(1, n + 1)
    ]
    results = list(await asyncio.gather(*tasks))

    valid = [r for r in results if r.text and r.score >= 0]
    if not valid:
        errors = [r.error for r in results if r.error]
        raise RuntimeError(errors[0] if errors else "所有候选均失败")

    word_min = _word_count_min()
    qualified = [r for r in valid if r.word_count >= word_min]
    pool = qualified if qualified else valid
    best = max(pool, key=lambda r: r.score)

    await _emit(queue, "select", {
        "winner": best.index,
        "score": best.score,
        "scores": [{"index": r.index, "score": r.score, "word_count": r.word_count}
                   for r in results if r.text],
        "rationale": best.rationale,
        "message": f"已选候选 {best.index}（评分 {best.score:.0f}）",
    })
    return best, results
