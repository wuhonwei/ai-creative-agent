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
    # 图像编辑模型：保持参考图主体一致、按文字重绘场景（镜头首帧锁角色形象用）
    image_edit_model: str = "qwen-image-edit"

    database_url: str = "sqlite:///./creative_agent.db"

    llm_temperature: float = 0.85
    llm_max_tokens: int = 16384

    # 每章目标字数（引导模型，软约束）
    chapter_target_words: int = 2000

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
    kling_model: str = "kling-v2-6"     # 模型名称（v2-6 支持 element_id）
    video_duration: int = 5             # 单镜头时长（秒），可灵支持 5 / 10
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
