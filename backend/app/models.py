"""数据模型（SQLModel / SQLite）。

设计要点（长篇一致性的关键）：
- Chapter.content 保存正文（线性增长，体量大）。
- Chapter.summary 保存该章压缩摘要（亚线性增长，喂给模型当"长期记忆"）。
- Character / PlotThread 保存随剧情演进的"状态"，作为结构化记忆。
这样即便正文上千万字，喂给模型的上下文仍可控。
"""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.utcnow()


class Project(SQLModel, table=True):
    """一部作品。圣经（背景/氛围/文风/世界观）直接挂在项目上。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    # 故事圣经字段
    premise: str = ""          # 故事背景 / 核心设定
    mood: str = ""             # 整体氛围
    style: str = ""            # 文风要求（人称、节奏、参考作品等）
    world_rules: str = ""      # 世界观规则 / 设定约束
    dianwen: str = ""          # 癫文宝经：生成大纲时参考的通用情节原则，空则用内置默认
    target_words: int = 0      # 目标总字数（0 表示不限）
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Character(SQLModel, table=True):
    """角色。description 是初始设定，state 随剧情滚动更新。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    name: str
    role: str = ""             # 主角/反派/配角等
    description: str = ""      # 性格、外貌、背景等初始设定
    state: str = ""            # 当前状态（位置/关系/目标/已知信息），随剧情更新
    created_at: datetime = Field(default_factory=_now)


class OutlineNode(SQLModel, table=True):
    """分层大纲树：卷 / 篇 / 章。通过 parent_id 自引用形成层级。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    parent_id: Optional[int] = Field(default=None, foreign_key="outlinenode.id", index=True)
    level: str = "chapter"     # volume | part | chapter
    order_index: int = 0       # 同级排序
    title: str = ""
    synopsis: str = ""         # 该节点的梗概
    created_at: datetime = Field(default_factory=_now)


class Chapter(SQLModel, table=True):
    """章节正文。一个 chapter 关联一个 chapter 级 OutlineNode。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    outline_node_id: Optional[int] = Field(
        default=None, foreign_key="outlinenode.id", index=True
    )
    order_index: int = 0       # 全书章节顺序
    title: str = ""
    content: str = ""          # 正文全文
    summary: str = ""          # 压缩摘要（长期记忆用）
    word_count: int = 0
    status: str = "draft"      # draft | confirmed
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class PlotThread(SQLModel, table=True):
    """伏笔 / 情节线。用于跟踪"埋了但还没回收"的线索。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    name: str
    detail: str = ""
    status: str = "open"       # open | resolved
    opened_at_chapter: int = 0
    created_at: datetime = Field(default_factory=_now)


class Scene(SQLModel, table=True):
    """场景定义。用于生成"无角色"的场景图（全剧风格统一）。

    视觉模板四区块（前三块由 LLM/用户填，[技术与约束] 固定注入）：
      [核心设定] style / time_weather / color_tone
      [空间与结构] space_desc / materials / vista
      [光影与镜头] light_source / light_tech / viewpoint / composition
    一个场景可被多个章节复用（多对多见 ChapterScene），靠 LLM 比对"是否同一地点"
    决定复用已有还是新建（场景有变化即新建）。
    kling_element_id 是可灵平台生成的场景一致性 ID，用于跨镜头保持场景统一。
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    name: str                  # 场景名称，如"雨夜街道"、"皇宫大殿"
    description: str = ""      # 场景详细描述（人类可读小结）
    # [核心设定]
    style: str = ""            # 风格，如"废土科幻/现代极简/中世纪哥特"
    time_weather: str = ""     # 时间与天气
    color_tone: str = ""       # 色彩基调（全剧统一）
    # [空间与结构]
    space_desc: str = ""       # 空间描述
    materials: str = ""        # 材质与细节
    vista: str = ""            # 远景 / 边界（视野尽头或窗外）
    # [光影与镜头]
    light_source: str = ""     # 光源设定（光从哪来）
    light_tech: str = ""       # 光影技术（丁达尔/全局光照/反射光）
    viewpoint: str = ""        # 视角（第一人称/无人机俯拍/广角仰视）
    composition: str = ""      # 构图（对称/三分法/引导线）
    locked_prompt: str = ""    # 组装后的固定提示词（生图用）
    image_path: str = ""       # 生成的场景图，相对 videos/ 目录的文件名
    kling_element_id: str = "" # 可灵 element_id（场景一致性 ID）
    status: str = "draft"      # draft | done | failed
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class SceneRefImage(SQLModel, table=True):
    """场景参考图。可灵文生图多次生成，多张图一并用于合成场景主体 ID。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    scene_id: int = Field(foreign_key="scene.id", index=True)
    prompt: str = ""
    image_path: str = ""
    status: str = "processing"  # processing | done | failed
    error: str = ""
    created_at: datetime = Field(default_factory=_now)


class ChapterScene(SQLModel, table=True):
    """章节 ↔ 场景 多对多关联。一个章节有一组场景，一个场景可跨多个章节复用。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    chapter_id: int = Field(foreign_key="chapter.id", index=True)
    scene_id: int = Field(foreign_key="scene.id", index=True)
    created_at: datetime = Field(default_factory=_now)


class Asset(SQLModel, table=True):
    """图片资产(人物图/场景图)。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    asset_type: str            # "character" | "scene"
    ref_id: int                # character.id 或 scene.id
    ref_name: str = ""         # 冗余存储名称，便于查询显示
    prompt: str = ""           # 生成时使用的提示词
    image_path: str            # 本地文件路径(相对于 assets/ 目录)
    thumbnail_path: str = ""   # 缩略图路径(可选)
    width: int = 0
    height: int = 0
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# 视频生成流水线（全部新表，不影响既有数据）
#
# 一致性核心：每个角色有一份"锁定设定"(CharacterDesign)——固定的画风/五官/
# 发色/服饰关键词；据此生成多张参考图(CharacterRefImage)，其中一张被设为
# 首帧基准(is_primary)。后续所有镜头的图生视频都以该基准图为首帧，杜绝变脸。
# ---------------------------------------------------------------------------
class CharacterDesign(SQLModel, table=True):
    """角色视觉设定锁定。一个 Character 对应一份(character_id 唯一)。

    locked_prompt 是从下列关键词组装、全剧统一复用的固定提示词，
    保证同一角色在所有参考图/镜头中外观一致。
    kling_element_id 是可灵平台生成的角色一致性 ID，用于跨镜头保持角色形象统一。
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    character_id: int = Field(foreign_key="character.id", index=True)
    # 人物基础特征
    gender: str = ""           # 性别
    age: str = ""              # 年龄（如"18岁少年"）
    body_type: str = ""        # 体型与肤色
    color_tone: str = ""       # 色彩基调（全片统一）
    art_style: str = ""        # 风格，如"3D精品漫剧/2D/水墨/赛博朋克/暗黑奇幻"
    facial_features: str = ""  # 脸部五官（眉毛/鼻子/眼神等）
    hair: str = ""             # 发型 / 发色
    outfit: str = ""           # 服饰穿搭（衣服/裤子/鞋子/配饰）
    extra: str = ""            # 其它锁定关键词（配饰、体型等）
    locked_prompt: str = ""    # 组装后的固定提示词（全剧统一调用）
    kling_voice_id: str = ""   # 可灵音色 ID（用于合成主体）
    kling_element_id: str = "" # 可灵 element_id（主体 ID，由参考图+音色合成）
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class CharacterRefImage(SQLModel, table=True):
    """角色参考图：正视图 / 侧视图 / 斜视图 / 全身立绘。

    其中一张设为 is_primary=True，作为所有镜头图生视频的首帧基准。
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    character_id: int = Field(foreign_key="character.id", index=True)
    view: str = "front"        # front | side | three_quarter | full_body
    prompt: str = ""           # 实际使用的提示词（locked_prompt + 视角词）
    image_path: str = ""       # 相对 videos/ 目录的文件名
    is_primary: bool = False   # 是否为首帧基准图
    width: int = 0
    height: int = 0
    created_at: datetime = Field(default_factory=_now)


class Storyboard(SQLModel, table=True):
    """分镜脚本：一个章节(OutlineNode/Chapter)拆出的一组镜头的容器。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    chapter_id: Optional[int] = Field(
        default=None, foreign_key="chapter.id", index=True
    )
    title: str = ""            # 冗余章节标题，便于显示
    status: str = "draft"      # draft | confirmed
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Shot(SQLModel, table=True):
    """单个镜头。LLM 从章节正文拆出，可人工编辑。

    分镜模板 8 字段：景别(shot_size)、运镜(camera)、人物(character_ids)、
    画面描述(description)、台词(dialogue)、音效(sound)、光影(lighting)、场景(scene)。
    其中 景别/场景/光影 会参与首帧图提示词（影响画面）；音效留作后续音频阶段。
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    storyboard_id: int = Field(foreign_key="storyboard.id", index=True)
    order_index: int = 0       # 镜头顺序
    shot_size: str = ""        # 景别：远景/全景/中景/近景/特写等
    camera: str = ""           # 运镜，如"推近/摇/固定/跟随"
    description: str = ""      # 画面描述（用于图生视频提示词）
    dialogue: str = ""         # 台词 / 旁白（后续配音用）
    sound: str = ""            # 音效（环境音/拟音，后续音频阶段用）
    lighting: str = ""         # 光影：如"黄昏暖光/冷色调逆光/夜晚月光"
    scene: str = ""            # 场景：地点/环境，如"教室、雨夜街道"
    duration: float = 5.0      # 时长（秒）
    character_ids: str = ""    # 出场角色 id，逗号分隔（如"1,3"）
    created_at: datetime = Field(default_factory=_now)


class CustomVoice(SQLModel, table=True):
    """可灵定制音色。通过声音 URL 调用可灵 API 生成音色 ID，供后续创建主体 ID 使用。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    voice_name: str            # 声音名称
    voice_url: str             # 声音样本 URL
    voice_id: str = ""         # 可灵返回的音色 ID
    task_id: str = ""          # 可灵任务 ID（轮询用）
    trial_url: str = ""        # 试听 URL（可灵返回，可选）
    status: str = "pending"    # pending | processing | done | failed
    error: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Video(SQLModel, table=True):
    """视频产物：镜头片段 / 章节成片 / 全剧成片。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    video_type: str = "shot"   # shot | chapter | film
    ref_id: Optional[int] = Field(default=None, index=True)  # shot_id 或 chapter_id
    prompt: str = ""           # 生成时使用的提示词
    video_path: str = ""       # 相对 videos/ 目录的文件名
    poster_path: str = ""      # 首帧图（从成片提取，可选）
    last_frame_path: str = ""  # 尾帧图（从成片提取，可选）
    duration: float = 0.0
    width: int = 0
    height: int = 0
    status: str = "pending"    # pending | processing | done | failed
    task_id: str = ""          # 可灵任务 id（异步轮询用）
    error: str = ""            # 失败原因
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

