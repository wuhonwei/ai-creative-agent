"""可灵（Kling）图生视频客户端。

国内站，JWT(HS256) 鉴权。请求/返回格式见 test 联调验证：
  创建：POST {base}/v1/videos/image2video → data.task_id
  轮询：GET  {base}/v1/videos/image2video/{task_id}
        data.task_status ∈ submitted|processing|succeed|failed
        成功：data.task_result.videos[0].url / .duration

通义万相 generate_image 返回的 OSS 公网 URL 可直接作为 image 入参，无需自建图床。
"""
import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Optional

import httpx
import jwt  # PyJWT

from app.config import settings

logger = logging.getLogger(__name__)


class KlingError(RuntimeError):
    """可灵调用相关错误。"""


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
    save_path: Optional[Path] = None,
    on_progress=None,
) -> dict:
    """以一张首帧图生成视频。

    首帧图二选一：
        image_path: 本地文件（优先，转 base64，用于锁定的参考图）。
        image_url:  公网 URL（如通义万相返回的 OSS URL）。

    Args:
        prompt: 运动/运镜描述。
        duration: 时长秒（5 或 10），默认取配置。
        mode: std | pro，默认取配置。
        cfg_scale: 提示词遵循度 0~1。
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
    payload = {
        "model_name": settings.kling_model,
        "image": image_field,
        "prompt": prompt or "",
        "mode": mode or settings.video_mode,
        "duration": str(duration or settings.video_duration),
        "cfg_scale": cfg_scale,
    }

    logger.info(f"[Kling] 提交图生视频任务：mode={payload['mode']}, "
                f"duration={payload['duration']}, "
                f"image={'base64' if image_path else 'url'}, prompt={prompt}")

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
