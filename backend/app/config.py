"""应用配置：从环境变量 / .env 读取。"""
import logging
import sys

from pydantic_settings import BaseSettings, SettingsConfigDict


# 配置日志：INFO 级别，带时间戳和模块名
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # DeepSeek（OpenAI 兼容接口）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"

    # 通义万相（阿里云图片生成）
    tongyi_api_key: str = ""
    tongyi_base_url: str = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
    tongyi_model: str = "wanx-v1"  # 通义万相最强模型

    database_url: str = "sqlite:///./creative_agent.db"

    llm_temperature: float = 0.85
    llm_max_tokens: int = 16384
    # deepseek-v4-pro：上下文 1M（含输入和输出），单次输出上限 384K。
    llm_context_tokens: int = 1_048_576
    llm_max_output_tokens: int = 393_216

    # 每章目标字数（引导模型，软约束，不设上限）
    chapter_target_words: int = 2000

    # 每章最少字数（硬性下限，审查与润色校验用）
    chapter_word_count_min: int = 1000

    # 每章并行生成候选数（多路生成 → humanize → 评分择优）
    chapter_parallel_candidates: int = 3

    # humanizer：生成时预防 AI 味（方案2）+ 生成后润色（方案3）
    enable_humanizer_prevention: bool = True
    enable_humanizer_post: bool = True

    # 喂给模型的"最近全文章节"字符预算，其余历史靠压缩摘要
    recent_chapters_char_budget: int = 12000

    # 图片生成默认参数
    image_width: int = 1024
    image_height: int = 1024
    image_style: str = "anime"  # 动漫风格

    # 可灵（Kling）图生视频。鉴权用 AccessKey + SecretKey 签 JWT（HS256）。
    kling_access_key: str = ""
    kling_secret_key: str = ""
    kling_base_url: str = "https://api-beijing.klingai.com"  # 国内站
    kling_model: str = "kling-v2-6"     # 图生视频模型（v2-6 支持 element_id）
    kling_image_model: str = "kling-v2-1"  # 图像生成模型（首帧图）
    kling_image_aspect_ratio: str = "16:9"  # 可灵生图画面比例
    video_duration: int = 5             # 单镜头默认时长（秒），可灵支持 5~15
    video_mode: str = "pro"             # std（标准）| pro（高表现）
    video_sound: str = "on"             # 音效：on | off

    # ffmpeg / ffprobe 可执行文件完整路径。
    # 本机装在 D:\下载\ffmpeg\bin\，不在 PATH 且路径含中文，必须用完整路径调用。
    ffmpeg_path: str = r"D:\下载\ffmpeg\bin\ffmpeg.exe"
    ffprobe_path: str = r"D:\下载\ffmpeg\bin\ffprobe.exe"

    frontend_origin: str = "http://localhost:5173"

    @property
    def llm_configured(self) -> bool:
        return bool(self.deepseek_api_key and self.deepseek_api_key != "sk-your-key-here")

    @property
    def kling_configured(self) -> bool:
        return bool(self.kling_access_key and self.kling_secret_key)


settings = Settings()

KLING_VIDEO_DURATION_MIN = 5
KLING_VIDEO_DURATION_MAX = 15


def resolve_video_duration(shot_duration: float | int | str | None = None) -> int:
    """解析可灵图生视频时长。合法范围为 5~15 秒（整数）。

    镜头时长在范围内时用镜头值，否则回退到 VIDEO_DURATION 配置。
    """
    try:
        d = int(round(float(shot_duration))) if shot_duration is not None else -1
    except (TypeError, ValueError):
        d = -1
    if KLING_VIDEO_DURATION_MIN <= d <= KLING_VIDEO_DURATION_MAX:
        return d
    fb = int(settings.video_duration)
    if KLING_VIDEO_DURATION_MIN <= fb <= KLING_VIDEO_DURATION_MAX:
        return fb
    return KLING_VIDEO_DURATION_MIN
