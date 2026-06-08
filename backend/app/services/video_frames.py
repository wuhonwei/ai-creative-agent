"""从镜头视频中提取首帧与尾帧（ffmpeg）。"""
import logging
import subprocess
import uuid
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


def extract_first_and_last_frames(
    video_path: Path, out_dir: Path, shot_id: int,
) -> tuple[str, str]:
    """从 mp4 提取首帧、尾帧 PNG，返回 (first_name, last_name) 相对 out_dir 的文件名。"""
    if not video_path.exists():
        raise FileNotFoundError(f"视频文件不存在：{video_path}")

    uid = uuid.uuid4().hex[:10]
    first_name = f"frame_{shot_id}_{uid}_first.png"
    last_name = f"frame_{shot_id}_{uid}_last.png"
    first_path = out_dir / first_name
    last_path = out_dir / last_name

    r1 = subprocess.run(
        [settings.ffmpeg_path, "-y", "-i", str(video_path),
         "-vf", "select=eq(n\\,0)", "-vframes", "1", str(first_path)],
        capture_output=True, text=True,
    )
    if r1.returncode != 0 or not first_path.exists():
        logger.error(f"[VideoFrames] 首帧提取失败：{r1.stderr[-500:]}")
        raise RuntimeError(f"提取视频首帧失败：{r1.stderr[-200:]}")

    r2 = subprocess.run(
        [settings.ffmpeg_path, "-y", "-sseof", "-0.05", "-i", str(video_path),
         "-vframes", "1", str(last_path)],
        capture_output=True, text=True,
    )
    if r2.returncode != 0 or not last_path.exists():
        logger.error(f"[VideoFrames] 尾帧提取失败：{r2.stderr[-500:]}")
        first_path.unlink(missing_ok=True)
        raise RuntimeError(f"提取视频尾帧失败：{r2.stderr[-200:]}")

    logger.info(f"[VideoFrames] 镜头 {shot_id} 首尾帧已提取：{first_name} / {last_name}")
    return first_name, last_name
