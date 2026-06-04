"""视频合成：用 ffmpeg 把一章的镜头片段按顺序拼接成章节成片。

要点（已联调验证）：
- ffmpeg/ffprobe 用 settings 里的完整路径调用（本机不在 PATH 且路径含中文）。
- concat demuxer 的 filelist 用正斜杠绝对路径，避免 Windows 反斜杠转义。
- 重编码为 H.264/yuv420p（不是 -c copy）：可灵各镜头片段编码可能不一致，
  统一重编码才能稳定拼接。
"""
import logging
import subprocess
import uuid
from pathlib import Path

from sqlmodel import Session, select

from app.config import settings
from app.models import Shot, Storyboard, Video

logger = logging.getLogger(__name__)

VIDEOS_DIR = Path(__file__).parent.parent.parent / "videos"


def _probe_duration(path: Path) -> float:
    """用 ffprobe 读视频时长（秒），失败返回 0。"""
    try:
        r = subprocess.run(
            [settings.ffprobe_path, "-v", "error", "-show_entries",
             "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True,
        )
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0.0
    except (ValueError, OSError):
        return 0.0


def collect_shot_clips(session: Session, storyboard_id: int) -> list[Path]:
    """按镜头顺序收集本章已出片的镜头片段本地路径。

    只纳入 status=done 且文件存在的镜头视频；顺序按 Shot.order_index。
    """
    shots = session.exec(
        select(Shot).where(Shot.storyboard_id == storyboard_id)
        .order_by(Shot.order_index)
    ).all()

    clips: list[Path] = []
    for shot in shots:
        v = session.exec(
            select(Video)
            .where(Video.video_type == "shot")
            .where(Video.ref_id == shot.id)
            .where(Video.status == "done")
            .order_by(Video.created_at.desc())
        ).first()
        if v and v.video_path:
            p = VIDEOS_DIR / v.video_path
            if p.exists():
                clips.append(p)
    return clips


def concat_clips(clips: list[Path], out_path: Path) -> float:
    """用 ffmpeg concat 把多片段拼接重编码到 out_path，返回成片时长。"""
    if not clips:
        raise ValueError("没有可合成的镜头片段")

    # 写 concat filelist（正斜杠绝对路径）
    list_path = out_path.parent / f"_concat_{uuid.uuid4().hex[:8]}.txt"
    lines = [f"file '{str(c.resolve()).replace(chr(92), '/')}'" for c in clips]
    list_path.write_text("\n".join(lines), encoding="utf-8")

    try:
        logger.info(f"[Compose] ffmpeg 拼接 {len(clips)} 个片段 → {out_path.name}")
        r = subprocess.run(
            [settings.ffmpeg_path, "-y", "-f", "concat", "-safe", "0",
             "-i", str(list_path),
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(out_path)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            logger.error(f"[Compose] ffmpeg 失败：{r.stderr[-500:]}")
            raise RuntimeError(f"ffmpeg 合成失败：{r.stderr[-200:]}")
    finally:
        list_path.unlink(missing_ok=True)

    return _probe_duration(out_path)
