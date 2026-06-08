"""可灵（Kling）图生视频客户端。

国内站，JWT(HS256) 鉴权。请求/返回格式见 test 联调验证：
  创建：POST {base}/v1/videos/image2video → data.task_id
  轮询：GET  {base}/v1/videos/image2video/{task_id}
        data.task_status ∈ submitted|processing|succeed|failed
        成功：data.task_result.videos[0].url / .duration

图像生成：POST /v1/images/generations → 轮询 /v1/images/generations/{task_id}。
图生视频首帧图可用本地文件 base64 或公网 URL 作为 image 入参。
"""
import asyncio
import base64
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import jwt  # PyJWT

from app.config import resolve_video_duration, settings

logger = logging.getLogger(__name__)


class KlingError(RuntimeError):
    """可灵调用相关错误。"""


# 可灵主体标签（tag_list 用 key:value）
KLING_TAG_HOT = {"key": "o_101", "value": "热梗"}
KLING_TAG_CHARACTER = {"key": "o_102", "value": "人物"}
KLING_TAG_ANIMAL = {"key": "o_103", "value": "动物"}
KLING_TAG_PROP = {"key": "o_104", "value": "道具"}
KLING_TAG_COSTUME = {"key": "o_105", "value": "服饰"}
KLING_TAG_SCENE = {"key": "o_106", "value": "场景"}
KLING_TAG_EFFECT = {"key": "o_107", "value": "特效"}
KLING_TAG_OTHER = {"key": "o_108", "value": "其他"}


def character_element_tags() -> list[dict]:
    """角色主体默认标签：人物。"""
    return [KLING_TAG_CHARACTER]


def scene_element_tags() -> list[dict]:
    """场景主体默认标签：场景。"""
    return [KLING_TAG_SCENE]


# 生图/出片共用：压制画面中出现文字、字幕、招牌、水印等（易生成乱码）。
KLING_AVOID_TEXT_NEGATIVE = (
    "文字，字幕，标题，标牌，招牌，海报文字，书本文字，屏幕文字，水印，乱码，"
    "字母，数字，符号，logo，caption，subtitle，text overlay，watermark，"
    "written words，letters，typography，banner，signage，UI text"
)

KLING_AVOID_TEXT_POSITIVE = "纯视觉画面，无任何文字、对话框、字幕、水印、招牌或可读字符"

# 可灵 prompt 主体标记：名称<<<element_id>>>（三尖括号包裹主体 ID）


def entity_prompt_tag(name: str, element_id: str) -> str:
    """生成可灵主体标记，如 小明<<<308901>>>。"""
    n = (name or "").strip()
    eid = (element_id or "").strip()
    if not n or not eid:
        return n
    return f"{n}<<<{eid}>>>"


def _collapse_entity_tag(text: str, name: str, element_id: str) -> str:
    """合并重复名称与多余尖括号：乔妈乔妈<<<id>>>> → 乔妈<<<id>>>。"""
    tagged = entity_prompt_tag(name, element_id)
    return re.sub(
        r"(?:" + re.escape(name) + r")+<<<" + re.escape(element_id) + r">>+",
        tagged,
        text,
    )


def apply_element_tags(
    desc: str,
    element_ids: list[str],
    entities: list[tuple[str, str]],
) -> str:
    """把描述中的实体名 / 旧版双尖括号标记统一为 名称<<<element_id>>>。

    可重复调用（幂等），不会把 乔妈<<<id>>> 变成 乔妈乔妈<<<id>>>>。
    entities: [(实体名, element_id), ...]，如角色名、场景名。
    element_ids 顺序即提交可灵 element_list 的顺序。
    """
    text = (desc or "").strip()
    if not text or not entities:
        return text

    eid_to_name = {eid: name for name, eid in entities if name and eid}

    # 兼容误写的 <<<element_N>>>（按 element_list 顺序还原为 名称<<<id>>>）
    for i, eid in enumerate(element_ids, start=1):
        name = eid_to_name.get(eid)
        if not name:
            continue
        tagged = entity_prompt_tag(name, eid)
        text = re.sub(r"<<<element_" + str(i) + r">>>", tagged, text)
        text = re.sub(r"<<element_" + str(i) + r">>", tagged, text)

    for name, eid in entities:
        if not name or not eid:
            continue
        tagged = entity_prompt_tag(name, eid)
        text = _collapse_entity_tag(text, name, eid)

        if tagged in text:
            continue

        # 旧版双尖括号（仅匹配精确 ID）：小明<<308901>>
        text = re.sub(re.escape(name) + r"<<" + re.escape(eid) + r">>", tagged, text)
        # 裸 ID 三尖括号（名称尚未前缀时）：<<<308901>>> → 小明<<<308901>>>
        text = re.sub(
            r"(?<!" + re.escape(name) + r")<<<" + re.escape(eid) + r">>+",
            tagged,
            text,
        )
        # 裸名称（尚未接 <<< 时）
        text = re.sub(re.escape(name) + r"(?!<<<)", tagged, text)
        text = _collapse_entity_tag(text, name, eid)

    return text


def _make_token() -> str:
    """生成可灵 JWT（HS256，30 分钟有效）。"""
    headers = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": settings.kling_access_key,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5,
    }
    return jwt.encode(payload, settings.kling_secret_key, algorithm="HS256", headers=headers)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_make_token()}",
        "Content-Type": "application/json",
    }


def _payload_for_log(value: Any, depth: int = 0) -> Any:
    """复制 payload 供日志输出；超长 base64/字符串保留长度与前缀，避免刷屏。"""
    if depth > 24:
        return "<max depth>"
    if isinstance(value, dict):
        return {k: _payload_for_log(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_payload_for_log(v, depth + 1) for v in value]
    # if isinstance(value, str):
    #     if len(value) <= 300:
    #         return value
    #     if value.startswith("http://") or value.startswith("https://"):
    #         return f"{value[:240]}... <url len={len(value)}>"
    #     return f"<string len={len(value)} prefix={value[:120]!r}>"
    return value


def _log_kling_payload(method: str, url: str, payload: dict) -> None:
    """打印可灵 API 完整请求 payload（图片类字段做可读化缩写）。"""
    logger.info(
        "[Kling] %s %s\npayload=%s",
        method,
        url,
        json.dumps(_payload_for_log(payload), ensure_ascii=False, indent=2),
    )


def _resolve_image(image_url: Optional[str], image_path: Optional[Path]) -> str:
    """把首帧图解析成可灵 image 入参：本地文件转 base64，否则直接用 URL。

    优先用本地文件（首帧基准图），保证用的就是锁定的那张参考图、不依赖公网图床。
    """
    if image_path:
        p = Path(image_path)
        if not p.exists():
            raise KlingError(f"首帧图文件不存在：{p}")
        return base64.b64encode(p.read_bytes()).decode()
    if image_url:
        return image_url
    raise KlingError("必须提供首帧图（image_url 或 image_path 其一）")


async def image_to_video(
    image_url: Optional[str] = None,
    image_path: Optional[Path] = None,
    prompt: str = "",
    duration: Optional[int] = None,
    mode: Optional[str] = None,
    cfg_scale: float = 0.5,
    sound: Optional[str] = None,
    element_list: Optional[list[str]] = None,
    voice_list: Optional[list[str]] = None,
    negative_prompt: str = "",
    save_path: Optional[Path] = None,
    on_progress=None,
) -> dict:
    """以一张首帧图生成视频。

    首帧图二选一：
        image_path: 本地文件（优先，转 base64，用于锁定的参考图）。
        image_url:  公网 URL（如通义万相返回的 OSS URL）。

    Args:
        prompt: 运动/运镜描述。主体用 名称<<<element_id>>> 标记。
        duration: 时长秒（5~15），默认取配置。
        mode: std | pro，默认取配置。
        cfg_scale: 提示词遵循度 0~1。
        sound: 音效开关（on | off），默认取配置。指定 voice_list 时强制 on。
        element_list: 可灵 element_id 列表（角色/场景一致性 ID）。
        voice_list: 音色 ID 列表（最多 2 个），配合 prompt 中 <<voice_N>> 使用。
        negative_prompt: 负向提示词（如避免画面出现文字乱码）。
        save_path: 若提供则把成片下载到本地。
        on_progress: 可选回调 on_progress(status:str, attempt:int)。

    Returns:
        {"url", "local_path", "duration", "task_id"}
    """
    if not settings.kling_configured:
        raise KlingError("未配置可灵 AccessKey / SecretKey")

    image_field = _resolve_image(image_url, image_path)

    base = settings.kling_base_url.rstrip("/")
    create_url = f"{base}/v1/videos/image2video"
    # 可灵 API：element_list 与 voice_list 不能同时传
    if element_list and voice_list:
        logger.warning("[Kling] element_list 与 voice_list 互斥，已忽略 voice_list")
        voice_list = None

    effective_sound = sound or settings.video_sound
    if voice_list:
        effective_sound = "on"

    payload = {
        "model_name": settings.kling_model,
        "image": image_field,
        "prompt": prompt or "",
        "mode": mode or settings.video_mode,
        "duration": str(resolve_video_duration(duration)),
        "cfg_scale": cfg_scale,
        "sound": effective_sound,
    }

    if element_list:
        payload["element_list"] = [{"element_id": eid} for eid in element_list]

    if voice_list:
        payload["voice_list"] = [{"voice_id": vid} for vid in voice_list[:2]]

    if negative_prompt:
        payload["negative_prompt"] = negative_prompt

    _log_kling_payload("POST", create_url, payload)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(create_url, headers=_headers(), json=payload)
        if resp.status_code != 200:
            logger.error(f"[Kling] 创建任务失败 HTTP {resp.status_code}: {resp.text}")
            raise KlingError(f"创建任务失败({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        if data.get("code") != 0:
            raise KlingError(f"创建任务返回异常: {data.get('message')}")
        task_id = (data.get("data") or {}).get("task_id")
        if not task_id:
            raise KlingError(f"未拿到 task_id: {data}")

        logger.info(f"[Kling] 任务已提交：task_id={task_id}，开始轮询")
        query_url = f"{base}/v1/videos/image2video/{task_id}"

        # 轮询：每 5 秒一次，最多 ~6 分钟
        max_attempts = 72
        for attempt in range(max_attempts):
            await asyncio.sleep(5)
            r = await client.get(query_url, headers=_headers())
            r.raise_for_status()
            d = r.json()
            info = d.get("data") or {}
            status = info.get("task_status")
            if on_progress:
                on_progress(status, attempt + 1)
            logger.info(f"[Kling] 任务状态：{status} (尝试 {attempt+1}/{max_attempts})")

            if status == "succeed":
                videos = ((info.get("task_result") or {}).get("videos")) or []
                if not videos:
                    raise KlingError(f"任务成功但无视频结果: {d}")
                video_url = videos[0].get("url")
                vdur = float(videos[0].get("duration", 0) or 0)
                logger.info(f"[Kling] 出片成功：{video_url[:80]}... 时长={vdur}s")

                local_path = None
                if save_path and video_url:
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    vresp = await client.get(video_url)
                    vresp.raise_for_status()
                    save_path.write_bytes(vresp.content)
                    local_path = str(save_path)
                    logger.info(f"[Kling] 视频已保存：{local_path}")

                return {"url": video_url, "local_path": local_path,
                        "duration": vdur, "task_id": task_id}

            if status == "failed":
                msg = info.get("task_status_msg", "未知原因")
                logger.error(f"[Kling] 任务失败：{msg}")
                raise KlingError(f"图生视频失败：{msg}")

        raise KlingError(f"图生视频超时，task_id={task_id}")


async def generate_image(
    prompt: str,
    image_url: Optional[str] = None,
    image_path: Optional[Path] = None,
    negative_prompt: str = "",
    element_list: Optional[list[str]] = None,
    n: int = 1,
    model_name: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    save_path: Optional[Path] = None,
    on_progress=None,
) -> dict:
    """调用可灵图像生成接口（文生图 / 参考图生图）。

    POST /v1/images/generations → 轮询 GET /v1/images/generations/{task_id}

    Args:
        prompt: 画面描述，主体用 名称<<<element_id>>> 标记。
        image_url / image_path: 参考图（二选一，本地文件转 base64）。
        element_list: 主体 ID 列表，保持角色/场景一致性。
        n: 生成张数（默认 1）。
        aspect_ratio: 画面比例（如 16:9、1:1、3:4），默认取配置。
        save_path: 若提供则下载首张图到本地。

    Returns:
        {"url", "local_path", "task_id", "images"}
    """
    if not settings.kling_configured:
        raise KlingError("未配置可灵 AccessKey / SecretKey")

    base = settings.kling_base_url.rstrip("/")
    create_url = f"{base}/v1/images/generations"

    payload: dict = {
        "model_name": model_name or settings.kling_image_model,
        "prompt": prompt or "",
        "negative_prompt": negative_prompt or "",
        "n": max(1, min(int(n or 1), 4)),
        "aspect_ratio": aspect_ratio or settings.kling_image_aspect_ratio or "16:9",
    }

    if image_url or image_path:
        payload["image"] = _resolve_image(image_url, image_path)

    if element_list:
        payload["element_list"] = [{"element_id": eid} for eid in element_list]

    _log_kling_payload("POST", create_url, payload)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(create_url, headers=_headers(), json=payload)
        if resp.status_code != 200:
            logger.error(f"[Kling] 图像任务创建失败 HTTP {resp.status_code}: {resp.text}")
            raise KlingError(f"创建图像任务失败({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        if data.get("code") != 0:
            raise KlingError(f"创建图像任务返回异常: {data.get('message')}")
        task_id = (data.get("data") or {}).get("task_id")
        if not task_id:
            raise KlingError(f"未拿到 task_id: {data}")

        logger.info(f"[Kling] 图像任务已提交：task_id={task_id}，开始轮询")
        query_url = f"{base}/v1/images/generations/{task_id}"

        max_attempts = 60
        for attempt in range(max_attempts):
            await asyncio.sleep(5)
            r = await client.get(query_url, headers=_headers())
            r.raise_for_status()
            d = r.json()
            info = d.get("data") or {}
            status = info.get("task_status")
            if on_progress:
                on_progress(status, attempt + 1)
            logger.info(f"[Kling] 图像任务状态：{status} (尝试 {attempt+1}/{max_attempts})")

            if status == "succeed":
                images = ((info.get("task_result") or {}).get("images")) or []
                if not images:
                    raise KlingError(f"图像任务成功但无结果: {d}")
                image_url_out = images[0].get("url")
                if not image_url_out:
                    raise KlingError(f"图像结果无 URL: {images[0]}")
                logger.info(f"[Kling] 图像生成成功：{image_url_out[:80]}...")

                local_path = None
                if save_path and image_url_out:
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    img_resp = await client.get(image_url_out)
                    img_resp.raise_for_status()
                    save_path.write_bytes(img_resp.content)
                    local_path = str(save_path)
                    logger.info(f"[Kling] 图像已保存：{local_path}")

                return {
                    "url": image_url_out,
                    "local_path": local_path,
                    "task_id": task_id,
                    "images": images,
                }

            if status == "failed":
                msg = info.get("task_status_msg", "未知原因")
                logger.error(f"[Kling] 图像任务失败：{msg}")
                raise KlingError(f"图像生成失败：{msg}")

        raise KlingError(f"图像生成超时，task_id={task_id}")


async def create_custom_voice(
    voice_name: str,
    voice_url: str,
    video_id: str = "",
    callback_url: str = "",
) -> dict:
    """提交定制音色任务。

    POST /v1/general/custom-voices

    Returns:
        {"task_id", "task_status"}
    """
    if not settings.kling_configured:
        raise KlingError("未配置可灵 AccessKey / SecretKey")

    base = settings.kling_base_url.rstrip("/")
    create_url = f"{base}/v1/general/custom-voices"
    payload = {
        "video_id": video_id,
        "voice_url": voice_url,
        "voice_name": voice_name,
        "callback_url": callback_url,
    }

    _log_kling_payload("POST", create_url, payload)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(create_url, headers=_headers(), json=payload)
        if resp.status_code != 200:
            logger.error(f"[Kling] 创建音色任务失败 HTTP {resp.status_code}: {resp.text}")
            raise KlingError(f"创建音色任务失败({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        if data.get("code") != 0:
            raise KlingError(f"创建音色任务返回异常: {data.get('message')}")
        info = data.get("data") or {}
        task_id = info.get("task_id")
        if not task_id:
            raise KlingError(f"未拿到 task_id: {data}")

        logger.info(f"[Kling] 音色任务已提交：task_id={task_id}")
        return {
            "task_id": task_id,
            "task_status": info.get("task_status", "submitted"),
        }


async def query_custom_voice(task_id: str) -> dict:
    """查询定制音色任务状态。

    GET /v1/general/custom-voices/{task_id}

    Returns:
        {"task_id", "task_status", "voices": [{"voice_id", "voice_name", "trial_url"}]}
    """
    if not settings.kling_configured:
        raise KlingError("未配置可灵 AccessKey / SecretKey")

    base = settings.kling_base_url.rstrip("/")
    query_url = f"{base}/v1/general/custom-voices/{task_id}"

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(query_url, headers=_headers())
        if resp.status_code != 200:
            raise KlingError(f"查询音色任务失败({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        if data.get("code") != 0:
            raise KlingError(f"查询音色任务返回异常: {data.get('message')}")
        info = data.get("data") or {}
        result = info.get("task_result") or {}
        voices = result.get("voices") or []
        return {
            "task_id": info.get("task_id", task_id),
            "task_status": info.get("task_status", ""),
            "task_status_msg": info.get("task_status_msg", ""),
            "voices": voices,
        }


async def create_and_poll_custom_voice(
    voice_name: str,
    voice_url: str,
    on_progress=None,
) -> dict:
    """提交定制音色任务并轮询至完成。

    Returns:
        {"task_id", "voice_id", "voice_name", "trial_url"}
    """
    created = await create_custom_voice(voice_name=voice_name, voice_url=voice_url)
    task_id = created["task_id"]

    max_attempts = 72
    for attempt in range(max_attempts):
        await asyncio.sleep(5)
        info = await query_custom_voice(task_id)
        status = info.get("task_status")
        if on_progress:
            on_progress(status, attempt + 1)
        logger.info(f"[Kling] 音色任务状态：{status} (尝试 {attempt+1}/{max_attempts})")

        if status == "succeed":
            voices = info.get("voices") or []
            if not voices:
                raise KlingError(f"音色任务成功但无结果: {info}")
            v = voices[0]
            voice_id = v.get("voice_id", "")
            if not voice_id:
                raise KlingError(f"音色任务成功但未返回 voice_id: {info}")
            logger.info(f"[Kling] 音色生成成功：voice_id={voice_id}")
            return {
                "task_id": task_id,
                "voice_id": voice_id,
                "voice_name": v.get("voice_name", voice_name),
                "trial_url": v.get("trial_url", ""),
            }

        if status == "failed":
            msg = info.get("task_status_msg", "未知原因")
            logger.error(f"[Kling] 音色任务失败：{msg}")
            raise KlingError(f"定制音色失败：{msg}")

    raise KlingError(f"定制音色超时，task_id={task_id}")


def _normalize_preset_voice(v: dict) -> dict:
    return {
        "voice_id": str(v.get("voice_id", "") or ""),
        "voice_name": v.get("voice_name", "") or "",
        "trial_url": v.get("trial_url", "") or "",
        "owned_by": str(v.get("owned_by", "") or ""),
    }


def _extract_preset_voices_from_response(data: dict) -> tuple[list[dict], int, Optional[int]]:
    """解析可灵 presets-voices 响应。

    兼容三种结构：
    1. data 为扁平音色数组（同 presets-elements）
    2. data 为任务数组，音色在 task_result.voices
    3. data 为分页对象 {list/voices, total, pageNum, pageSize}

    Returns:
        (voices, raw_item_count, total_or_none)
    """
    payload = data.get("data")
    if payload is None:
        return [], 0, None

    total: Optional[int] = None
    if isinstance(payload, dict):
        total_val = payload.get("total")
        if total_val is not None:
            try:
                total = int(total_val)
            except (TypeError, ValueError):
                total = None
        items = (
            payload.get("list")
            or payload.get("voices")
            or payload.get("records")
            or payload.get("items")
            or []
        )
    elif isinstance(payload, list):
        items = payload
    else:
        return [], 0, None

    if not isinstance(items, list):
        return [], 0, total

    voices: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # 扁平音色（官方库常见格式）
        if item.get("voice_id"):
            voices.append(_normalize_preset_voice(item))
            continue
        # 任务包装格式
        for v in (item.get("task_result") or {}).get("voices") or []:
            if isinstance(v, dict) and v.get("voice_id"):
                voices.append(_normalize_preset_voice(v))

    return voices, len(items), total


async def _fetch_preset_voices_kling_page(
    client: httpx.AsyncClient,
    url: str,
    page_num: int,
    page_size: int,
) -> tuple[list[dict], int, Optional[int]]:
    """拉取可灵单页官方音色。Returns: (voices, raw_item_count, total)"""
    params = {"pageNum": page_num, "pageSize": page_size}
    resp = await client.get(url, headers=_headers(), params=params)
    if resp.status_code != 200:
        logger.error(f"[Kling] 查询官方音色失败 HTTP {resp.status_code}: {resp.text}")
        raise KlingError(f"查询官方音色失败({resp.status_code}): {resp.text[:200]}")
    data = resp.json()
    if data.get("code") != 0:
        raise KlingError(f"查询官方音色返回异常: {data.get('message')}")

    voices, raw_count, total = _extract_preset_voices_from_response(data)
    logger.info(
        f"[Kling] presets-voices pageNum={page_num} pageSize={page_size}: "
        f"raw_items={raw_count}, voices={len(voices)}, total={total}"
    )
    return voices, raw_count, total


async def list_preset_voices(page_num: int = 1, page_size: int = 30) -> dict:
    """查询可灵官方预置音色列表。

    可灵单次返回的 data 条数可能少于 pageSize，会连续请求多页直到
    凑满 page_size 条音色，或确认没有更多数据。

    Returns:
        {"voices", "page_num", "page_size", "has_more", "total_on_page", "total"}
    """
    if not settings.kling_configured:
        raise KlingError("未配置可灵 AccessKey / SecretKey")

    base = settings.kling_base_url.rstrip("/")
    url = f"{base}/v1/general/presets-voices"
    kling_page_size = min(max(page_size, 30), 500)
    skip = (page_num - 1) * page_size
    need = skip + page_size

    logger.info(f"[Kling] 查询官方音色列表：逻辑页={page_num}, page_size={page_size}")

    accumulated: list[dict] = []
    kling_page = 1
    api_total: Optional[int] = None
    last_raw_count = 0

    async with httpx.AsyncClient(timeout=60.0) as client:
        while len(accumulated) < need:
            batch, raw_count, total = await _fetch_preset_voices_kling_page(
                client, url, kling_page, kling_page_size,
            )
            last_raw_count = raw_count
            if total is not None:
                api_total = total
            if raw_count == 0:
                break
            accumulated.extend(batch)
            kling_page += 1

        page_voices = accumulated[skip:skip + page_size]

        if api_total is not None:
            has_more = page_num * page_size < api_total
        elif len(accumulated) > skip + page_size:
            has_more = True
        else:
            peek_voices, peek_raw, _ = await _fetch_preset_voices_kling_page(
                client, url, kling_page, kling_page_size,
            )
            has_more = peek_raw > 0 or len(peek_voices) > 0

    logger.info(
        f"[Kling] 官方音色列表：逻辑页 {page_num} 返回 {len(page_voices)} 条，"
        f"has_more={has_more}，累计拉取 {len(accumulated)} 条，"
        f"末次 raw_items={last_raw_count}"
    )
    return {
        "voices": page_voices,
        "page_num": page_num,
        "page_size": page_size,
        "has_more": has_more,
        "total_on_page": len(page_voices),
        "total": api_total,
    }


def _image_to_kling_field(image_path: Path) -> str:
    """本地参考图转可灵 image 入参（base64）。"""
    p = Path(image_path)
    if not p.exists():
        raise KlingError(f"参考图文件不存在：{p}")
    return base64.b64encode(p.read_bytes()).decode()


def _extract_element_id(info: dict) -> str:
    """从可灵主体创建响应/任务结果中解析 element_id。"""
    if info.get("element_id") is not None and str(info.get("element_id")).strip():
        return str(info["element_id"])
    result = info.get("task_result") or {}
    for el in result.get("elements") or []:
        if isinstance(el, dict) and el.get("element_id") is not None:
            return str(el["element_id"])
    return ""


async def _post_custom_element(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
) -> dict:
    _log_kling_payload("POST", url, payload)
    resp = await client.post(url, headers=_headers(), json=payload)
    if resp.status_code != 200:
        raise KlingError(f"创建主体失败({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    if data.get("code") != 0:
        raise KlingError(f"创建主体返回异常: {data.get('message')}")
    return data.get("data") or {}


async def create_custom_element(
    element_name: str,
    element_description: str,
    frontal_image_path: Path,
    refer_image_paths: Optional[list[Path]] = None,
    voice_id: str = "",
    tag_list: Optional[list[dict]] = None,
) -> dict:
    """创建可灵定制主体（多图 image_refer + element_image_list）。

    reference_type 固定为 image_refer；element_image_list 用正面图 + refer_images 定义外表。
    tag_list 格式：[{"key": "o_102", "value": "人物"}, ...]

    Returns:
        {"task_id", "element_id"}
    """
    if not settings.kling_configured:
        raise KlingError("未配置可灵 AccessKey / SecretKey")

    name = (element_name or "character")[:20]
    desc = (element_description or name)[:100]
    frontal = _image_to_kling_field(frontal_image_path)
    refer_paths = refer_image_paths or []
    refer_images = [
        {"image_url": _image_to_kling_field(p)}
        for p in refer_paths[:3]
        if p.exists()
    ]
    # image_refer 多图主体：正面图 + 参考图列表（至少 1 张参考图）
    if not refer_images:
        refer_images = [{"image_url": frontal}]

    payload: dict = {
        "element_name": name,
        "element_description": desc,
        "reference_type": "image_refer",
        "element_image_list": {
            "frontal_image": frontal,
            "refer_images": refer_images,
        },
    }
    if voice_id:
        payload["element_voice_id"] = voice_id
    if tag_list:
        payload["tag_list"] = tag_list

    base = settings.kling_base_url.rstrip("/")
    create_urls = [
        f"{base}/v1/general/advanced-custom-elements",
        f"{base}/v1/general/custom-elements",
    ]

    last_err: Optional[Exception] = None
    async with httpx.AsyncClient(timeout=120.0) as client:
        for create_url in create_urls:
            try:
                info = await _post_custom_element(client, create_url, payload)
                element_id = _extract_element_id(info)
                task_id = info.get("task_id", "")
                if element_id:
                    logger.info(f"[Kling] 主体创建成功：element_id={element_id}")
                    return {"task_id": task_id, "element_id": element_id}
                if task_id:
                    polled = await _poll_custom_element(client, base, task_id)
                    return polled
            except KlingError as e:
                last_err = e
                logger.warning(f"[Kling] 创建主体尝试失败 {create_url}: {e}")

    raise last_err or KlingError("创建主体失败：所有请求方式均未成功")


async def _poll_custom_element(
    client: httpx.AsyncClient,
    base: str,
    task_id: str,
    on_progress=None,
) -> dict:
    """轮询主体创建任务。"""
    query_urls = [
        f"{base}/v1/general/custom-elements/{task_id}",
        f"{base}/v1/general/advanced-custom-elements/{task_id}",
    ]

    max_attempts = 72
    for attempt in range(max_attempts):
        await asyncio.sleep(5)
        info = None
        for query_url in query_urls:
            try:
                resp = await client.get(query_url, headers=_headers())
                if resp.status_code != 200:
                    continue
                data = resp.json()
                if data.get("code") != 0:
                    continue
                info = data.get("data") or {}
                break
            except httpx.HTTPError:
                continue

        if not info:
            continue

        status = info.get("task_status", "")
        if on_progress:
            on_progress(status, attempt + 1)
        logger.info(f"[Kling] 主体任务状态：{status} (尝试 {attempt+1}/{max_attempts})")

        if status == "succeed":
            element_id = _extract_element_id(info)
            if not element_id:
                raise KlingError(f"主体任务成功但未返回 element_id: {info}")
            logger.info(f"[Kling] 主体合成成功：element_id={element_id}")
            return {"task_id": task_id, "element_id": element_id}

        if status == "failed":
            msg = info.get("task_status_msg", "未知原因")
            raise KlingError(f"主体合成失败：{msg}")

    raise KlingError(f"主体合成超时，task_id={task_id}")


async def create_and_poll_custom_element(
    element_name: str,
    element_description: str,
    frontal_image_path: Path,
    refer_image_paths: Optional[list[Path]] = None,
    voice_id: str = "",
    tag_list: Optional[list[dict]] = None,
    on_progress=None,
) -> dict:
    """创建主体并轮询至完成。"""
    return await create_custom_element(
        element_name=element_name,
        element_description=element_description,
        frontal_image_path=frontal_image_path,
        refer_image_paths=refer_image_paths,
        voice_id=voice_id,
        tag_list=tag_list,
    )
