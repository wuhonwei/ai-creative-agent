"""DeepSeek 客户端（OpenAI 兼容 /chat/completions）。

提供两个能力：
- chat()        一次性返回完整文本（用于大纲、摘要、状态抽取等结构化任务）
- chat_stream() 异步逐字符流式产出（用于正文生成，前端实时显示）

故意用裸 httpx 而非 openai SDK：依赖更少、行为透明、便于切换到任意 OpenAI 兼容端点。
"""
import json
import logging
from collections.abc import AsyncGenerator

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def _check_configured() -> None:
    if not settings.llm_configured:
        raise LLMError(
            "未配置 DEEPSEEK_API_KEY。请复制 backend/.env.example 为 backend/.env 并填入你的 API Key。"
        )


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }


def output_token_budget(prompt_chars: int) -> int:
    """按模型输出上限要额度，并给输入留出上下文空间。"""
    room = settings.llm_context_tokens - max(prompt_chars, 1) - 1024
    return max(4096, min(settings.llm_max_output_tokens, room))


def _payload(messages: list[dict], stream: bool, temperature: float | None,
             max_tokens: int | None) -> dict:
    return {
        "model": settings.deepseek_model,
        "messages": messages,
        "stream": stream,
        "temperature": settings.llm_temperature if temperature is None else temperature,
        "max_tokens": settings.llm_max_tokens if max_tokens is None else max_tokens,
    }


async def chat(messages: list[dict], temperature: float | None = None,
               max_tokens: int | None = None) -> str:
    """非流式：返回完整 assistant 文本。"""
    _check_configured()
    url = f"{settings.deepseek_base_url}/chat/completions"
    payload = _payload(messages, False, temperature, max_tokens)

    # 详细日志：打印调用参数
    logger.info(
        f"LLM.chat 调用参数: model={payload['model']}, temp={payload['temperature']}, "
        f"max_tokens={payload['max_tokens']}, messages={len(messages)} 条"
    )
    for i, msg in enumerate(messages):
        content = msg.get('content', '')
        logger.info(f"  [{i}] role={msg.get('role')}, content={content}")

    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(url, headers=_headers(), json=payload)
        if resp.status_code != 200:
            err = f"DeepSeek 返回 {resp.status_code}: {resp.text[:500]}"
            logger.error(err)
            raise LLMError(err)
        data = resp.json()
        try:
            text = data["choices"][0]["message"]["content"]
            logger.info(f"LLM.chat 响应: {len(text)} 字符，前80字: {text[:80]!r}")
            return text
        except (KeyError, IndexError) as e:
            err = f"DeepSeek 响应格式异常: {data}"
            logger.error(err)
            raise LLMError(err) from e


async def chat_stream(messages: list[dict], temperature: float | None = None,
                      max_tokens: int | None = None) -> AsyncGenerator[str, None]:
    """流式：逐段产出增量文本（token delta）。"""
    _check_configured()
    url = f"{settings.deepseek_base_url}/chat/completions"
    payload = _payload(messages, True, temperature, max_tokens)

    # 详细日志：打印调用参数
    logger.info(
        f"LLM.chat_stream 调用参数: model={payload['model']}, temp={payload['temperature']}, "
        f"max_tokens={payload['max_tokens']}, messages={len(messages)} 条"
    )
    for i, msg in enumerate(messages):
        content = msg.get('content', '')
        logger.info(f"  [{i}] role={msg.get('role')}, content={content}")

    total_chars = 0
    async with httpx.AsyncClient(timeout=600.0) as client:
        async with client.stream("POST", url, headers=_headers(), json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                err = f"DeepSeek 返回 {resp.status_code}: {body[:500]!r}"
                logger.error(err)
                raise LLMError(err)
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[len("data:"):].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                    delta = obj["choices"][0]["delta"].get("content")
                    if delta:
                        total_chars += len(delta)
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    # 忽略心跳 / 非内容帧
                    continue
    logger.info(f"LLM.chat_stream 响应: 流式完成，总计 {total_chars} 字符")
