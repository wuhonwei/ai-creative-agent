"""中文 Prompt 模板。

集中管理所有发给模型的指令，便于调优。
分四类任务：生成大纲 / 生成正文 / 抽取摘要 / 抽取状态变化。
"""
import json
import re
from pathlib import Path

from app.config import settings

# ---------------------------------------------------------------------------
# 系统人设
# ---------------------------------------------------------------------------
SYSTEM_WRITER = (
    "你是一位资深中文小说家与故事架构师，擅长长篇连载创作。"
    "你严格遵守已设定的世界观、人物性格与已发生的剧情，保持前后一致，"
    "不擅自推翻既有设定，不出现与历史情节矛盾的内容。"
    "文笔凝练、叙事流畅，对话自然，节奏张弛有度；"
    "惜字如金，每句话都要推进情节、揭示人物或渲染氛围，禁止废话与重复阐释。"
)

SYSTEM_OUTLINE = (
    "你是故事架构师，只负责分层大纲。"
    "章概要是给作者看的情节提要，不是小说正文。"
    "不写对白、连续动作、神态和感官细节。"
    "严格遵守已有设定，不推翻既有剧情，只输出要求的 JSON。"
)

CHAPTER_SYNOPSIS_RULE = (
    "章概要是情节提要，不是正文，也不是分镜。\n"
    "用概括句写清：本章发生哪几件事、人物关系或信息因此怎么变化、章末留下什么悬念。\n"
    "不少于 100 字。字数用来把事件和因果写全，不要用来铺陈场景。\n"
    "禁止对话原句、动作分解和感官特写，例如不要写“她猛地缩回脚，嘶声说不用你，湿帕子贴着脚踝”。\n"
    "要写成“阮千媚停止让妹妹服侍，阮梦却不相信她真的变了；同时她确认家财将尽，还不知道如何证明自己”。"
)

# 方案2：生成时预防 AI 写作痕迹（精简版，完整规则留给生成后 humanize）
HUMANIZER_PREVENTION_RULES = """
7. **去 AI 味（写作时即遵守）**：
   - 不用「标志着」「至关重要」「展现出」「凸显了」等升华套话；不堆砌象征意义。
   - 少用「不仅……更……」「既……又……」式排比，以及三连形容词（「精准、高效、创新」）。
   - 避免「——」破折号后接「确保/象征/反映……」的浅层分析句。
   - 对话要口语化、有角色个性，不要人人像播音稿；叙述句式长短交替，忌句句同构。
   - 不用「业内人士指出」「专家认为」等模糊归因；心理活动写具体，不写空泛感慨。
   - 禁用 AI 高频词：此外、然而、综上所述、值得注意的是、毋庸置疑、无缝、赋能等。
   - 结尾留悬念即可，不要「未来可期」「意义重大」式空洞收束。
8. **精炼禁废话**：严格遵守 concise-fiction 技能中的「Prompt 注入规则」，尤其禁止「是 X，不是 Y」式对比句。"""

_HUMANIZER_SKILL_FALLBACK = """\
识别并改写 AI 写作痕迹： inflated symbolism、promotional language、superficial -ing analyses、
vague attributions、em dash overuse、rule of three、AI vocabulary、negative parallelisms。
保留剧情与人设，让文字读起来像资深作者手写。"""

_CONCISE_FICTION_PROMPT_FALLBACK = """\
**精炼写作（硬性要求）**：
- 惜字如金：能删则删，能一句说清不用两句；禁止为凑字数注水、叠床架屋。
- 禁止对比式废话：不用「是 X，不是 Y」「这不是 A，而是 B」「与其说……不如说……」等否定式阐释句；直接写事实与动作。
- 禁止解释性旁白：不写「这意味着……」「这让他意识到……」「仿佛在诉说着……」等把读者当傻子的解读。
- 禁止同义反复：同一信息不换说法重复两遍；形容词、副词能省则省，不用三连修饰。
- 心理与动作写具体，不写空泛感慨；对话简短有力，去掉寒暄、客套、说教和总结陈词。
- 叙述优先用动词推进，少用「感到」「似乎」「某种」「一种」等模糊填充词。"""

_CONCISE_FICTION_REVIEW_FALLBACK = """\
4. **精炼度**：是否存在废话、重复阐释、「是 X 不是 Y」式对比句、解释性旁白或凑字注水；若有明显啰嗦应判为不通过。"""


def _skills_dir() -> Path:
    return Path(__file__).resolve().parents[3] / ".cursor" / "skills"


def _load_skill_body(slug: str) -> str:
    """读取 skill 正文（去掉 YAML frontmatter）。"""
    skill_path = _skills_dir() / slug / "SKILL.md"
    if not skill_path.exists():
        return ""
    text = skill_path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2].strip()
    return text


def _extract_skill_section(body: str, heading: str) -> str:
    """截取 skill 中某个 ## 章节（不含子章节）。"""
    if not body:
        return ""
    marker = f"## {heading}"
    start = body.find(marker)
    if start == -1:
        return ""
    start += len(marker)
    # 下一个同级 ## 标题处截断
    rest = body[start:]
    end = len(rest)
    for line in rest.splitlines():
        if line.startswith("## ") and not line.startswith("### "):
            pos = rest.find(line)
            if pos > 0:
                end = pos
                break
    return rest[:end].strip()


def _humanizer_skill_path() -> Path:
    return _skills_dir() / "humanizer" / "SKILL.md"


def load_humanizer_skill_content() -> str:
    """读取项目内 humanizer 技能全文；缺失时用内置摘要。"""
    text = _load_skill_body("humanizer")
    return text or _HUMANIZER_SKILL_FALLBACK


def load_concise_fiction_prompt_rules() -> str:
    """读取 concise-fiction 技能的「Prompt 注入规则」；缺失时用内置摘要。"""
    body = _load_skill_body("concise-fiction")
    section = _extract_skill_section(body, "Prompt 注入规则")
    return section or _CONCISE_FICTION_PROMPT_FALLBACK


def load_concise_fiction_review_rules() -> str:
    """读取 concise-fiction 技能的「审查清单」；缺失时用内置摘要。"""
    body = _load_skill_body("concise-fiction")
    section = _extract_skill_section(body, "审查清单")
    if not section:
        return _CONCISE_FICTION_REVIEW_FALLBACK
    return f"4. **精炼度**（依据 concise-fiction 技能）：\n{section}"


def load_concise_fiction_skill_content() -> str:
    """读取 concise-fiction 技能全文；缺失时用 prompt 规则摘要。"""
    text = _load_skill_body("concise-fiction")
    return text or _CONCISE_FICTION_PROMPT_FALLBACK


def _strip_model_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[\w]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    return t.strip()


def _bible_block(project: dict, characters: list[dict], include_state: bool = True,
                 include_overview: bool = False) -> str:
    """把故事圣经 + 角色设定拼成上下文块。

    include_state：正文写作要带上随剧情更新的当前状态。
    生成大纲时关掉，避免把尚未发生的状态写进卷章情节。
    include_overview：只有生成大纲时带上全文概述，正文写作不带。
    """
    lines = ["【故事圣经】"]
    lines.append(f"标题：{project.get('title', '')}")
    if project.get("premise"):
        lines.append(f"背景设定：{project['premise']}")
    if project.get("mood"):
        lines.append(f"整体氛围：{project['mood']}")
    if project.get("style"):
        lines.append(f"文风要求：{project['style']}")
    if project.get("world_rules"):
        lines.append(f"世界观规则：{project['world_rules']}")
    if include_overview and (project.get("full_synopsis") or "").strip():
        lines.append(f"全文概述：{project['full_synopsis'].strip()}")
        lines.append("大纲要沿着这篇全文概述推进。不到终卷，不要在章节梗概里写明结局。")

    if characters:
        lines.append("\n【角色档案】")
        for c in characters:
            seg = f"- {c['name']}"
            if c.get("role"):
                seg += f"（{c['role']}）"
            if c.get("description"):
                seg += f"：{c['description']}"
            if include_state and c.get("state"):
                seg += f"｜当前状态：{c['state']}"
            lines.append(seg)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 癫文宝经：生成大纲、展开卷章节、自动填充故事圣经时参考，不进入正文写作。
# ---------------------------------------------------------------------------
DEFAULT_DIANWEN = """1.读者阅读时未曾思考过这种情况，想知道下一步如何发展；
2.人物A传达了信息X给人物B，人物B恍然大悟，明白了未明确的道理，但这个道理人物A不知道。
3.在未达到小说尾声时，读者无法猜不到结局，或者根据传统思路猜想的结局是错的。
4.爽点和反差，让读者感到舒爽且惊诧。"""


def resolve_dianwen(provided: str | None, stored: str | None = None) -> str:
    """请求里显式传入的文本优先；未传时用项目已保存的文本，仍为空则用内置默认。"""
    if provided is not None:
        return provided.strip()
    stored = (stored or "").strip()
    return stored or DEFAULT_DIANWEN


def _dianwen_block(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    return (
        "\n【癫文宝经】\n"
        "下面是通用情节原则，由多条想法组成。设计卷章情节时要参考这些想法，"
        "把它们落实成具体事件。不要把原则原文写进标题或梗概。\n"
        f"{text}\n"
    )


def build_bible_fill_prompt(idea: str, title: str, dianwen: str,
                             core_character_count: int = 17) -> list[dict]:
    """根据开头思路填充故事圣经和角色档案，并参考癫文宝经。"""
    canon = (dianwen or "").strip() or DEFAULT_DIANWEN
    count = core_character_count if core_character_count and core_character_count > 0 else 17
    shown_title = (title or "").strip() or "未命名作品"
    user = f"""作品暂定标题：{shown_title}

【开头思路】
{idea.strip()}

【癫文宝经】
填充故事圣经、全文概述和角色档案时参考下面这些通用情节原则。
设定要能支撑这些手法：读者没想过的下一步、人物之间的信息差、不到尾声就猜不中结局、爽点与反差。
不要把原则原文抄进字段。结局只写在 full_synopsis 里，不要写进 premise。
{canon}

请把开头思路展开成可直接开写的故事圣经、全文概述和角色档案。要求：
1. premise 写清时代、世界、核心冲突，以及故事从哪个人、哪件事开始。留下后文才能揭开的缺口，不要写明结局。
2. mood 用简短的词写出整体氛围。
3. style 写人称、节奏和语言风格。
4. world_rules 写这个世界不能打破的规则。
5. full_synopsis 写 400 到 800 字的全书情节概述，供之后生成大纲使用。
   用概括句写开端、几次关键转折、读者按常理会猜到的结局、实际结局，以及主要的信息差和反差。
   不写对白，不写场景细节。结局要写清楚，因为这是给作者看的。
6. characters 正好写 {count} 个核心角色，包含主角和对手。每人有姓名、定位、性格与秘密。
   人数必须是 {count}，不要少写，也不要多写。description 每人两三句，避免输出被截断。
   至少有一对人物：一方知道的关键信息，另一方不知道。
   角色之间要有能形成反差的身份或关系。
   不要写角色的当前状态，也不要写开场这一刻正在做什么。状态要等正文确认后才更新。
7. title：暂定标题如果是「未命名作品」或过空，另起一个贴切书名；否则沿用暂定标题。

仅输出 JSON，不要任何额外文字：
{{
  "title": "书名",
  "premise": "背景与核心设定",
  "mood": "氛围",
  "style": "文风",
  "world_rules": "规则",
  "full_synopsis": "400到800字的全书情节概述",
  "characters": [
    {{"name": "姓名", "role": "定位", "description": "性格、欲望与秘密"}}
  ]
}}"""
    return [
        {"role": "system", "content": SYSTEM_WRITER},
        {"role": "user", "content": user},
    ]


def build_plot_options_prompt(idea: str, decisions: list[dict], dianwen: str) -> list[dict]:
    """基于开头思路、已选决策和癫文宝经，给出三个不同方向。"""
    canon = (dianwen or "").strip() or DEFAULT_DIANWEN
    if decisions:
        history = "\n".join(
            f"{i}. {item.get('title') or '未命名'}\n"
            f"剧情：{item.get('plot') or ''}\n"
            f"新人物：{item.get('new_characters') or '无'}\n"
            f"新势力：{item.get('new_factions') or '无'}"
            for i, item in enumerate(decisions, 1)
        )
    else:
        history = "（还没有已选决策，这是从开头往下的第一步）"
    user = f"""【用户的开头或思路】
{idea.strip()}

【已经选定的剧情决策，必须全部承接，不能推翻】
{history}

【癫文宝经】
下面是通用情节原则。三个方向都要能用上其中的手法：读者没想过的下一步、人物之间的信息差、不到尾声猜不中结局、爽点与反差。
不要把原则原文抄进选项。
{canon}

请给出三个后续剧情方向。要求：
1. 三个方向必须分叉，不能是同一件事的三种说法。
2. 每个方向都要有新人物或新势力登场，写出名字和他们为何此时出现。三份登场名单不要相同。
3. plot 用 150 到 260 字的情节提要，写清这一步发生什么、局面怎么变、留下什么悬念。不写对白，不写场景细节。
4. merits 写这个方向的优点，drawbacks 写缺点。都要具体，对着信息差、悬念、反差和后续空间说，不要写空话。
5. 只续写下一步，不要把结局写完。

仅输出 JSON，不要任何额外文字：
{{
  "options": [
    {{
      "title": "方向名称",
      "plot": "150到260字的情节提要",
      "new_characters": "新人物及为何登场",
      "new_factions": "新势力及为何登场",
      "merits": "优点",
      "drawbacks": "缺点"
    }}
  ]
}}"""
    return [
        {"role": "system", "content": SYSTEM_OUTLINE},
        {"role": "user", "content": user},
    ]


def build_lengthen_synopses_prompt(chapters: list[dict]) -> list[dict]:
    """把过短的章节梗概补到不少于 100 字，事件保持原样。"""
    lines = []
    for ch in chapters:
        lines.append(
            f"{ch['index']}. {ch.get('title') or '未命名'}\n现有梗概：{ch.get('synopsis') or '（空）'}"
        )
    body = "\n\n".join(lines)
    user = f"""下面这些章节梗概不足 100 字。请逐章改写成情节提要，每章不少于 100 字。
保持原有事件、人物和顺序，不要另起情节，不要改标题。
补的是还没写明的因果和章末悬念，不要扩成场景。
{CHAPTER_SYNOPSIS_RULE}

{body}

仅输出 JSON，不要任何额外文字：
{{
  "chapters": [
    {{"index": 0, "synopsis": "不少于100字的情节提要，不写对白和场景细节"}}
  ]
}}"""
    return [
        {"role": "system", "content": SYSTEM_OUTLINE},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# 1. 生成分层大纲
# ---------------------------------------------------------------------------
def build_outline_prompt(project: dict, characters: list[dict],
                         instruction: str = "", num_volumes: int = 0,
                         chapters_per_volume: int = 0,
                         dianwen: str = "") -> list[dict]:
    bible = _bible_block(project, characters, include_state=False, include_overview=True)

    scale = ""
    if num_volumes:
        scale += f"请规划约 {num_volumes} 卷。"
    if chapters_per_volume:
        scale += f"每卷约 {chapters_per_volume} 章。"
    if not scale:
        scale = "卷数与章节数由你根据故事体量合理决定（首次可先规划第一卷的章节）。"

    extra = f"\n额外要求：{instruction}" if instruction else ""

    user = f"""{bible}

请基于以上设定，设计这部小说的分层大纲。{scale}{extra}
{_dianwen_block(dianwen)}
要求：
1. 先给出"卷"级结构，每卷一句主题。
2. 每章有简短标题。章概要遵守下面的写法，每章都不得少于 100 字。
{CHAPTER_SYNOPSIS_RULE}
3. 各章提要要能串联成有张力的主线，并为后续埋下伏笔。若有全文概述，卷章走向要和它一致，不到终卷不要把结局写进章节梗概。

仅输出 JSON，不要任何额外文字，格式如下：
{{
  "volumes": [
    {{
      "title": "第一卷 卷名",
      "synopsis": "本卷主题",
      "chapters": [
        {{"title": "第1章 章名", "synopsis": "不少于100字的情节提要，只写事件、变化和悬念"}}
      ]
    }}
  ]
}}"""
    return [
        {"role": "system", "content": SYSTEM_OUTLINE},
        {"role": "user", "content": user},
    ]


def build_volume_chapters_prompt(project: dict, characters: list[dict],
                                 all_volumes: list[dict], target_volume: dict,
                                 previous_chapters: list[dict],
                                 count: int = 0, instruction: str = "",
                                 dianwen: str = "") -> list[dict]:
    """只为某一卷展开详细章节大纲。

    all_volumes：全书各卷的 {title, synopsis}，给模型把握整体节奏；
    target_volume：本次要展开的卷；
    previous_chapters：本卷之前各卷的章节 {volume_title, chapter_title, synopsis}，
                       避免剧情矛盾（人物状态、事件顺序、伏笔对不上）；
    count：本卷目标章数（0 = 由模型决定）。
    单卷单次生成，章数可控，避免一次性生成全书章节导致 JSON 截断。
    """
    bible = _bible_block(project, characters, include_state=False, include_overview=True)

    arc_lines = ["【全书卷结构（把握整体走向、控制节奏、合理埋伏笔）】"]
    for i, v in enumerate(all_volumes, 1):
        line = f"第{i}卷：{v.get('title', '')}"
        if v.get("synopsis"):
            line += f" —— {v['synopsis']}"
        if v.get("_is_target"):
            line += "   ← 本次展开的就是这一卷"
        arc_lines.append(line)
    arc = "\n".join(arc_lines)

    # 前面各卷的章节梗概（展开第 1 卷时为空；第 2 卷时带第 1 卷章节...）
    history = ""
    if previous_chapters:
        history_lines = ["【前面各卷已规划的章节（避免剧情矛盾，需要承接）】"]
        current_vol = None
        for ch in previous_chapters:
            if ch["volume_title"] != current_vol:
                current_vol = ch["volume_title"]
                history_lines.append(f"\n{current_vol}:")
            history_lines.append(f"  {ch['chapter_title']}：{ch['synopsis']}")
        history = "\n" + "\n".join(history_lines)

    count_req = (f"请为本卷规划约 {count} 章。" if count
                 else "本卷章数由你根据内容合理决定。")
    extra = f"\n额外要求：{instruction}" if instruction else ""

    user = f"""{bible}

{arc}{history}

现在请只为【{target_volume.get('title', '')}】（本卷主题：{target_volume.get('synopsis', '')}）设计详细的章节大纲。{count_req}{extra}
{_dianwen_block(dianwen)}
要求：
1. 参考后续各卷的主题,合理控制本卷节奏、埋设伏笔,但本卷章节只写本卷主题范围内的事件,不要把后续卷的核心冲突或高潮提前写进本卷。
2. 严格承接前面各卷已发生的剧情,人物状态、事件顺序、伏笔呼应要与前文一致,不能出现矛盾。
3. 每章有简短标题。章概要遵守下面的写法，每章都不得少于 100 字。
{CHAPTER_SYNOPSIS_RULE}
4. 只输出本卷的章节,不要输出其它卷的内容。

仅输出 JSON，不要任何额外文字，格式如下：
{{
  "chapters": [
    {{"title": "第1章 章名", "synopsis": "不少于100字的情节提要，只写事件、变化和悬念"}}
  ]
}}"""
    return [
        {"role": "system", "content": SYSTEM_OUTLINE},
        {"role": "user", "content": user},
    ]
def build_chapter_prompt(project: dict, characters: list[dict],
                         history_summary: str, recent_text: str,
                         open_threads: list[dict], chapter_title: str,
                         chapter_synopsis: str, action: str = "new",
                         instruction: str = "", target_words: int = 2000,
                         all_chapter_outlines: str = "") -> list[dict]:
    """组装写正文的上下文。

    history_summary：早期章节的压缩摘要（长期记忆）
    recent_text：最近几章的正文节选（短期记忆，保证衔接自然）
    open_threads：尚未回收的伏笔
    target_words：本章目标字数（软约束，给模型一个允许的浮动区间）
    all_chapter_outlines：全书所有章节的大纲结构（防止提前输出后续内容）
    """
    bible = _bible_block(project, characters)

    parts = [bible]

    # 添加全书大纲结构（让模型了解整体进度，防止跨章提前透露）
    if all_chapter_outlines:
        parts.append(f"\n【全书章节大纲（了解整体进度，控制节奏，本章只写本章内容）】\n{all_chapter_outlines}")

    if history_summary:
        parts.append(f"\n【前情提要（历史摘要）】\n{history_summary}")

    if recent_text:
        parts.append(f"\n【最近章节正文节选（必须自然衔接，承接上文）】\n{recent_text}")

    if open_threads:
        thread_lines = "\n".join(
            f"- {t['name']}：{t.get('detail', '')}" for t in open_threads
        )
        parts.append(f"\n【尚未回收的伏笔/线索】\n{thread_lines}")

    task = f"\n【本章任务】\n标题：{chapter_title}\n梗概：{chapter_synopsis}"
    parts.append(task)

    if action == "redirect" and instruction:
        parts.append(
            f"\n【重要：调整发展方向】\n请不要按原梗概的默认走向，"
            f"而是依据以下意图改写本章的情节发展：{instruction}"
        )
    elif action == "regenerate":
        hint = f"（参考要求：{instruction}）" if instruction else ""
        parts.append(f"\n【重写】上一版不够理想，请重新创作本章，提升质量{hint}。")
    elif instruction:
        parts.append(f"\n【补充要求】{instruction}")

    # 字数：硬性下限来自配置，目标字数为软约束，不设上限
    word_min = settings.chapter_word_count_min
    parts.append(
        f"\n请直接写出本章正文。要求：\n"
        f"1. 篇幅不少于 {word_min} 字（含标点），建议约 {target_words} 字，不设上限；"
        f"围绕本章梗概合理安排节奏，写完整一个自然的章节段落，不要为凑字数注水，也不要草草收尾。\n"
        f"2. **严格衔接前文**：必须自然承接最近章节的内容，人物状态、场景位置、对话语境要连贯一致，"
        f"不能出现与前文矛盾的情节。开头要体现与上一章的连续性。\n"
        f"3. **只写本章内容，不要提前透露后续章节**：参考全书大纲了解整体进度，但本章只展开本章梗概的事件，"
        f"不要把后续章节的关键情节、高潮或结局提前写进本章。\n"
        f"4. 以叙事和对话推进剧情，避免大段空泛的总结。\n"
        f"5. **章节结尾必须留有悬念或引发期待**：通过未解之谜、意外转折、新的冲突苗头、"
        f"未完成的对话、突然出现的线索等方式，让读者产生\"接下来会怎样？\"的期待感，吸引继续阅读下一章。\n"
        f"6. 只输出正文本身，不要输出标题、解释、大纲或额外说明。\n"
        f"{load_concise_fiction_prompt_rules()}"
    )
    if settings.enable_humanizer_prevention:
        parts.append(HUMANIZER_PREVENTION_RULES)

    return [
        {"role": "system", "content": SYSTEM_WRITER},
        {"role": "user", "content": "\n".join(parts)},
    ]


# ---------------------------------------------------------------------------
# 3. 抽取章节摘要（长期记忆）
# ---------------------------------------------------------------------------
def build_summary_prompt(chapter_title: str, chapter_content: str) -> list[dict]:
    user = f"""请为以下章节撰写一段精炼的剧情摘要，用于后续创作时的"前情记忆"。

要求：
- 150 字以内。
- 只保留对后续剧情有影响的关键事件、人物动向、新信息、新埋下的伏笔。
- 用客观第三人称陈述，不做评价。

章节标题：{chapter_title}
章节正文：
{chapter_content}

仅输出摘要文字本身。"""
    return [
        {"role": "system", "content": "你是严谨的剧情记录员。"},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# 4. 抽取角色状态变化 + 伏笔变动
# ---------------------------------------------------------------------------
def build_state_extraction_prompt(chapter_content: str,
                                  characters: list[dict],
                                  open_threads: list[dict]) -> list[dict]:
    char_names = [c["name"] for c in characters]
    thread_names = [t["name"] for t in open_threads]

    user = f"""阅读以下章节正文，判断已知角色的状态变化、以及伏笔的开启/回收。

已知角色：{json.dumps(char_names, ensure_ascii=False)}
当前未回收伏笔：{json.dumps(thread_names, ensure_ascii=False)}

章节正文：
{chapter_content}

仅输出 JSON，不要额外文字：
{{
  "character_updates": [
    {{"name": "角色名", "state": "本章之后该角色的最新状态（位置/关系/目标/掌握的信息）"}}
  ],
  "new_threads": [
    {{"name": "新伏笔简称", "detail": "伏笔内容"}}
  ],
  "resolved_threads": ["本章回收/解决的伏笔简称"]
}}

只为状态确有变化的角色输出 character_updates；没有变化就不要列出。"""
    return [
        {"role": "system", "content": "你是严谨的剧情连续性分析员，只输出 JSON。"},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# 5. 审查章节质量（自动重试机制）
# ---------------------------------------------------------------------------
def build_review_prompt(project: dict, chapter_title: str, chapter_content: str,
                        chapter_synopsis: str, history_summary: str,
                        recent_text: str) -> list[dict]:
    """审查刚生成的章节：逻辑是否合理、是否与上下文衔接。"""
    conciseness_check = load_concise_fiction_review_rules()
    user = f"""你是专业的小说审稿编辑，现在需要审查刚生成的章节质量。

【作品背景】
标题：{project.get('title', '')}
背景设定：{project.get('premise', '')}
整体氛围：{project.get('mood', '')}

【历史剧情摘要】
{history_summary if history_summary else '（本章是开篇，无前文）'}

【最近章节正文（用于检查衔接）】
{recent_text if recent_text else '（无最近章节）'}

【本章梗概（预期写什么）】
{chapter_synopsis}

【刚生成的本章正文】
{chapter_title}
{chapter_content}

请审查以下方面：
1. **逻辑合理性**：情节推进是否合理，人物行为动机是否自洽，有无明显逻辑漏洞。
2. **上下文衔接**：与前文是否自然衔接，人物状态、场景、时间线是否连贯，有无矛盾。
3. **梗概符合度**：是否按照梗概展开，有无偏离或遗漏关键情节。
{conciseness_check}

仅输出 JSON，格式如下：
{{
  "pass": true,  // true=通过审查，false=不通过需要重新生成
  "issues": "审查意见：如果 pass=false，说明具体问题（逻辑漏洞/衔接矛盾/偏离梗概等）；如果 pass=true，可留空或简单评价"
}}"""
    return [
        {"role": "system", "content": "你是严谨的小说审稿编辑，只输出 JSON。"},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# 5c. 章节质量评分（并行候选择优）
# ---------------------------------------------------------------------------
def build_chapter_score_prompt(project: dict, chapter_title: str, chapter_content: str,
                               chapter_synopsis: str, history_summary: str,
                               recent_text: str) -> list[dict]:
    """对 humanize 后的章节打分，供多路并行生成择优。"""
    word_min = settings.chapter_word_count_min
    conciseness_check = load_concise_fiction_review_rules()
    user = f"""你是专业的小说审稿编辑，请对以下章节综合打分（0-100，整数或一位小数）。

【作品背景】
标题：{project.get('title', '')}
背景设定：{project.get('premise', '')}
整体氛围：{project.get('mood', '')}
文风：{project.get('style', '') or '（未指定）'}

【历史剧情摘要】
{history_summary if history_summary else '（本章是开篇，无前文）'}

【最近章节正文（用于检查衔接）】
{recent_text if recent_text else '（无最近章节）'}

【本章梗概（预期写什么）】
{chapter_synopsis}

【待评章节正文】
{chapter_title}
{chapter_content}

评分维度（各 0-100，综合 score 为加权印象分，不必严格平均）：
1. coherence：逻辑与叙事是否自洽
2. continuity：与前文衔接、人物状态是否连贯
3. synopsis_fit：是否符合本章梗概
4. human_quality：是否像人写、AI 味是否低（套话、升华句、机械排比）
5. conciseness：是否精炼、废话与「是 X 不是 Y」式啰嗦是否少

硬性扣分：字数少于 {word_min} 字时 score 不得超过 40。

{conciseness_check}

仅输出 JSON：
{{
  "score": 85,
  "rationale": "一句话说明得分理由",
  "dimensions": {{
    "coherence": 90,
    "continuity": 85,
    "synopsis_fit": 80,
    "human_quality": 88,
    "conciseness": 82
  }}
}}"""
    return [
        {"role": "system", "content": "你是严谨的小说审稿编辑，只输出 JSON。"},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# 5b. 生成后 humanize 润色（方案3）
# ---------------------------------------------------------------------------
def build_humanizer_prompt(project: dict, chapter_title: str,
                           chapter_content: str,
                           word_min: int | None = None) -> list[dict]:
    """对已定稿章节做去 AI 味润色，规则来自 humanizer + concise-fiction 技能。"""
    if word_min is None:
        word_min = settings.chapter_word_count_min
    skill_guide = load_humanizer_skill_content()
    concise_guide = load_concise_fiction_skill_content()
    style_hint = ""
    if project.get("style"):
        style_hint += f"文风：{project['style']}。"
    if project.get("mood"):
        style_hint += f"氛围：{project['mood']}。"

    system = (
        "你是一位资深中文小说编辑，专门去除 AI 生成文本的痕迹，让文字读起来像人类作者手写。"
        "你必须严格遵守 humanizer 技能指南中的模式识别与改写规则。"
        "润色时不得改变剧情走向、人物关系、关键事件与伏笔；只改表达方式，删废话、压啰嗦，让文字更精炼。"
        "仅输出润色后的正文，不要输出标题、解释、变更摘要或 Markdown 围栏。"
    )
    user = f"""【humanizer 技能指南】
{skill_guide}

【concise-fiction 精炼写作技能】
{concise_guide}

【作品风格参考】
{style_hint or '（按原文风格保持即可）'}

【本章标题】{chapter_title}

【待润色正文】
{chapter_content}

请 humanize 以上正文：
1. 识别并改写 AI 写作模式（套话、排比、破折号分析句、模糊归因、AI 高频词等）。
2. 严格执行精炼要求：删除废话与同义反复，把「是 X，不是 Y」等对比阐释句改为直接叙述。
3. 保持剧情、人物、时间线、伏笔完全不变；对话可更口语化但须符合角色性格。
4. 字数不少于 {word_min} 字（含标点），不设上限；删废话后字数可少于原文，但不低于下限。
5. 只输出润色后的正文本身。"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def strip_humanizer_output(raw: str) -> str:
    return _strip_model_fences(raw)


# ---------------------------------------------------------------------------
# 6. 拆剧本分镜（章节正文 → 镜头列表）
# ---------------------------------------------------------------------------
def build_storyboard_prompt(chapter_title: str, chapter_content: str,
                            characters: list[dict],
                            scenes: list[dict] | None = None) -> list[dict]:
    """把一章正文拆成可逐镜生成视频的分镜脚本。

    characters：本作已知角色 {id, name, kling_element_id?}，让模型用准确的角色名标注出场角色，
    有主体 ID 时在画面描述里用 名称<<<element_id>>> 标记（三尖括号）；台词也写在画面描述中。
    scenes：本作已登记场景 {name, description, kling_element_id?}，供参考；scene 字段可自由填写，
    画面描述中场景也用 场景名<<<element_id>>> 标记（与角色写法一致）。
    """
    def _fmt_char(c: dict) -> str:
        base = f"{c['name']}(id={c['id']}"
        eid = (c.get("kling_element_id") or "").strip()
        return f"{base}, element_id={eid})" if eid else f"{base})"

    def _fmt_scene(s: dict) -> str:
        base = f"{s['name']}"
        eid = (s.get("kling_element_id") or "").strip()
        desc = (s.get("description") or "").strip()
        parts = [base]
        if eid:
            parts.append(f"element_id={eid}")
        if desc:
            parts.append(desc)
        return "：".join(parts) if len(parts) > 1 else base

    char_list = "、".join(_fmt_char(c) for c in characters) if characters else "（无已登记角色）"

    scenes = scenes or []
    if scenes:
        scene_lines = "\n".join(f"  - {_fmt_scene(s)}" for s in scenes)
        scene_block = f"""已登记场景（参考用；若镜头发生在这些场景，描述中写 场景名<<<element_id>>>）：
{scene_lines}"""
    else:
        scene_block = "（暂无已登记场景）"

    scene_rule = (
        "   - scene（场景）：地点 / 环境名称，**可自由填写**"
        "（如\"高中教室、雨夜街道、咖啡馆窗边\"），不必局限于已登记场景。\n"
        "     画面描述中描写场景环境时，若该场景有 element_id，写作：场景名<<<element_id>>"
        "（三尖括号，写法与角色一致）。"
    )

    user = f"""你是一个编剧兼导演，擅长把小说文案改编成 AI 生成视频的分镜脚本。请把下面这一章小说正文，拆解成一组连续的镜头(分镜脚本)，用于逐镜生成 3D 动漫风格的动画视频。

已登记角色：{char_list}

{scene_block}

【章节标题】{chapter_title}
【章节正文】
{chapter_content}

总体风格要求：
- 全片统一为 3D 动漫风格，所有镜头保持统一的画风和统一的色调。
- 严格忠于原文情节顺序，不要新增或删改剧情。

拆分要求：
1. 按叙事节奏切分镜头，每个镜头是一个连续的画面单元（通常 3-8 秒）。一章一般拆 6-15 个镜头。
2. 每个镜头按下面的分镜模板给出 8 个字段：
   - shot_size（景别）：远景 / 全景 / 中景 / 近景 / 特写 等。
   - camera（运镜）：运镜方式，如"固定 / 推近 / 拉远 / 横摇 / 跟随 / 俯拍 / 仰拍"等。
   - character_ids（人物）：本镜头出场角色的 id 数组（从已登记角色里选，没有出场角色给空数组）。
   - description（画面描述）：画面内容的客观、可视化描述（人物动作、表情、构图），用于图生视频。
     **台词也写在本字段**，格式为：角色名<<<element_id>>>说："台词原文"（三尖括号包裹主体 ID）。
     描写场景环境时，若场景有 element_id，写作：场景名<<<element_id>>>（如 教室<<<123456>>>内）。
     也可先写角色名/场景名，系统会自动补上 <<<element_id>>>。
     旁白可写：旁白说："……"。没有台词则只写画面动作。
   - dialogue（台词）：**固定留空字符串 ""**，不要单独写台词。
   - sound（音效）：环境音 / 拟音 / 音效提示（如"雨声、脚步声、远处人声"，没有可留空）。
   - lighting（光影）：光线与影调（如"黄昏暖光、冷色调逆光、夜晚月光、室内顶光"）。
{scene_rule}
3. 画面描述要具体、可视化，避免抽象心理描写；心理活动转化为可见的表情/动作。
4. 同一场景下相邻镜头的 scene、lighting 尽量保持一致，维持统一色调。

仅输出 JSON，不要任何额外文字，格式如下：
{{
  "shots": [
    {{
      "shot_size": "景别",
      "camera": "运镜",
      "character_ids": [1, 3],
      "description": "中景，教室<<<123456>>>内，小明<<<308901>>>站在讲台前，小明<<<308901>>>说：\"你好\"",
      "dialogue": "",
      "sound": "音效，可为空",
      "lighting": "光影",
      "scene": "场景"
    }}
  ]
}}"""
    return [
        {"role": "system", "content": "你是专业的编剧兼分镜导演，只输出 JSON。"},
        {"role": "user", "content": user},
    ]


def build_character_design_prompt(project: dict, characters: list[dict]) -> list[dict]:
    """为一批角色批量生成"视觉锁定关键词"（高质量 3D 精品漫剧立绘用）。

    project：{title, premise, mood, style, world_rules} 提供全局画风/色彩基调参考，
    保证所有角色风格统一。characters：[{id, name, role, description, state}]。
    要求模型为每个角色输出 9 个锁定字段（与 CharacterDesign 表对应）。
    """
    proj_lines = []
    for k, label in [("title", "标题"), ("premise", "故事背景"), ("mood", "氛围"),
                     ("style", "文风"), ("world_rules", "世界观规则")]:
        v = (project.get(k) or "").strip()
        if v:
            proj_lines.append(f"【{label}】{v}")
    proj_block = "\n".join(proj_lines) or "（无项目设定）"

    char_lines = []
    for c in characters:
        desc = (c.get("description") or "").strip()
        state = (c.get("state") or "").strip()
        line = f"- id={c['id']}，姓名：{c.get('name', '')}，定位：{c.get('role', '')}\n  档案：{desc}"
        if state:
            line += f"\n  当前状态：{state}"
        char_lines.append(line)
    char_block = "\n".join(char_lines)

    user = f"""你是一名美术指导。下面是一部作品的整体设定和角色档案，请为每个角色提炼"视觉锁定关键词"，用于生成全剧统一风格的高质量 3D 精品漫剧角色立绘。

作品整体设定（决定全局风格与色彩基调，所有角色必须统一）：
{proj_block}

角色档案：
{char_block}

为每个角色输出以下 9 个字段（从角色档案里抽取，档案没写的合理补全，保持同一作品内风格统一）：
- gender：性别（男/女）
- age：年龄（如"17岁少女""40岁中年男"）
- body_type：体型与肤色（如"娇小160cm、肤色白皙""高挑170cm、健康肤色"）
- color_tone：色彩基调（全剧统一，建议所有角色填同一个，呼应作品氛围/文风，如"冷色低饱和、水墨质感"）
- art_style：风格（全剧统一，建议所有角色填同一个，如"3D精品漫剧"）
- facial_features：脸部五官（眉毛/鼻子/眼神等，如"杏眼、淡色瞳、清秀"）
- hair：发型发色（如"黑色长直发、齐刘海"）
- outfit：服饰穿搭（衣服/裤子/鞋子/配饰，如"白色连衣裙""校服JK"）
- extra：其它锁定关键词（配饰、标志性特征等，可留空）

注意：
1. 不要写姿势、动作、表情、背景、镜头（这些由系统统一控制，写了会污染立绘）。
2. art_style 和 color_tone 全剧保持统一，让所有角色看起来是同一部作品。
3. 忠于角色档案，不要臆造与档案冲突的特征。

仅输出 JSON，不要任何额外文字，格式如下：
{{
  "designs": [
    {{
      "id": 1,
      "gender": "女",
      "age": "17岁少女",
      "body_type": "娇小160cm、肤色白皙",
      "color_tone": "冷色低饱和",
      "art_style": "3D精品漫剧",
      "facial_features": "杏眼、淡色瞳、清秀",
      "hair": "黑色长直发、齐刘海",
      "outfit": "白色连衣裙",
      "extra": ""
    }}
  ]
}}"""
    return [
        {"role": "system", "content": "你是专业的角色美术指导，只输出 JSON。"},
        {"role": "user", "content": user},
    ]


def build_chapter_scene_prompt(project: dict, chapter_title: str,
                               chapter_content: str,
                               existing_scenes: list[dict]) -> list[dict]:
    """从一章正文抽取场景，并与已有场景比对：复用同一地点 / 新建有变化的场景。

    project：提供全局风格/色彩基调，保证所有场景风格统一。
    existing_scenes：[{id, name, description}] 全项目已有场景，供模型判断"是否同一地点"。
    模型对本章每个场景：能对应到已有场景就给 match_id（复用，跨章共用），
    否则 match_id=null 并给出完整视觉字段（新场景）。
    """
    proj_lines = []
    for k, label in [("title", "标题"), ("premise", "故事背景"), ("mood", "氛围"),
                     ("style", "文风"), ("world_rules", "世界观规则")]:
        v = (project.get(k) or "").strip()
        if v:
            proj_lines.append(f"【{label}】{v}")
    proj_block = "\n".join(proj_lines) or "（无项目设定）"

    if existing_scenes:
        ex_lines = [f"- id={s['id']}，名称：{s['name']}，描述：{(s.get('description') or '')[:120]}"
                    for s in existing_scenes]
        ex_block = "\n".join(ex_lines)
    else:
        ex_block = "（暂无已有场景）"

    user = f"""你是一名场景概念美术。下面是作品整体设定、已有场景清单、以及本章正文。请抽取本章出现的所有"地点/场景"，并判断每个场景是不是已有清单里的同一地点。

作品整体设定（决定全局风格与色彩基调，所有场景必须统一）：
{proj_block}

已有场景清单（用于判断是否复用，同一地点要复用、不要重复新建）：
{ex_block}

【本章标题】{chapter_title}
【本章正文】
{chapter_content}

判断规则：
1. 本章每个场景，如果就是已有清单里的某个地点（同一物理空间，哪怕天气/时间略变），给出它的 match_id（复用）。
2. 如果是清单里没有的新地点，或同一地点发生了显著的、需要重新出图的环境变化（如被炸毁、季节巨变、白天变废墟），match_id 设为 null，并给出完整视觉字段（新建）。
3. 不要把同一地点因为镜头不同就拆成多个场景。一章通常 1-4 个场景。

对每个场景输出（match_id 为 null 时下列视觉字段必填；复用时只需 name + match_id）：
- name：场景名称（简洁，如"雨夜废墟街道"）
- match_id：复用的已有场景 id，新场景为 null
- description：一句话场景小结
- style：风格（全剧统一，呼应作品设定，如"废土科幻/中世纪哥特/现代极简"）
- time_weather：时间与天气
- color_tone：色彩基调（全剧统一，建议所有场景一致）
- space_desc：空间描述
- materials：材质与细节（如"生锈的管道、光滑微水泥地面、破碎玻璃"）
- vista：远景/边界（视野尽头或窗外景象）
- light_source：光源设定（光从哪来）
- light_tech：光影技术（如"丁达尔效应、全局光照、反射光"）
- viewpoint：视角（如"广角仰视、无人机俯拍、第一人称"）
- composition：构图（如"绝对对称、三分法则、引导线"）

注意：场景图里不要有任何人物（这是环境图）。不要写人物动作。

仅输出 JSON，不要任何额外文字，格式如下：
{{
  "scenes": [
    {{
      "name": "场景名",
      "match_id": null,
      "description": "一句话小结",
      "style": "风格",
      "time_weather": "时间与天气",
      "color_tone": "色彩基调",
      "space_desc": "空间描述",
      "materials": "材质与细节",
      "vista": "远景/边界",
      "light_source": "光源",
      "light_tech": "光影技术",
      "viewpoint": "视角",
      "composition": "构图"
    }}
  ]
}}"""
    return [
        {"role": "system", "content": "你是专业的场景概念美术，只输出 JSON。"},
        {"role": "user", "content": user},
    ]


def build_supplement_characters_prompt(project: dict, existing: list[dict],
                                       outline_text: str, dianwen: str = "") -> list[dict]:
    """从已生成的大纲里找出还没有档案的人物，只返回需要新建的角色。"""
    proj_lines = []
    for k, label in [("title", "标题"), ("premise", "故事背景"), ("mood", "氛围"),
                     ("style", "文风"), ("world_rules", "世界观规则")]:
        v = (project.get(k) or "").strip()
        if v:
            proj_lines.append(f"【{label}】{v}")
    proj_block = "\n".join(proj_lines) or "（无项目设定）"

    if existing:
        exist_lines = []
        for c in existing:
            line = f"- {c.get('name', '')}"
            if c.get("role"):
                line += f"（{c['role']}）"
            if c.get("description"):
                line += f"：{c['description']}"
            exist_lines.append(line)
        exist_block = "\n".join(exist_lines)
    else:
        exist_block = "（暂无）"

    canon = (dianwen or "").strip()
    canon_block = (
        f"\n【癫文宝经】\n档案里的秘密和信息差要能支撑这些情节手法，不要把原文抄进档案：\n{canon}\n"
        if canon else ""
    )

    user = f"""下面是一部作品的故事圣经、已有角色档案和刚生成的大纲。
请只补充大纲里真实出场、但还没有档案的人物。

{proj_block}

已有角色档案（这些人不要返回，别名也算同一个人）：
{exist_block}
{canon_block}
完整大纲：
{outline_text}

要求：
1. 只写大纲里有名字或明确称谓、并且不在已有档案中的人物。
2. 不写路人、群体、地名、组织和物品。同一人的不同称呼合并成一个最常用的名字。
3. 不要改写已有角色，也不要编造大纲里没出现的人。没有新人就返回空数组。
4. description 写身份、性格、和已有人物的关系，以及这个人知道而别人不知道的事。
5. 不要写当前状态，也不要写此人此刻正在做的事。状态留空。

仅输出 JSON，不要任何额外文字：
{{
  "characters": [
    {{
      "name": "姓名",
      "role": "定位",
      "description": "身份、性格、关系与秘密"
    }}
  ]
}}"""
    return [
        {"role": "system", "content": "你是故事分析师，只根据大纲补充缺失的角色档案，只输出 JSON。"},
        {"role": "user", "content": user},
    ]


def build_scan_characters_prompt(project: dict, outline_text: str,
                                 existing_names: list[str]) -> list[dict]:
    """从大纲文本扫描所有出现过的人物，提取姓名 + 档案 + 视觉锁定关键词。

    project：全局设定，决定统一画风/色彩基调。
    outline_text：拼接的大纲（卷/篇/章梗概 + 圣经背景）。
    existing_names：已登记角色名，让模型知道哪些已有（仍要返回，便于前端去重）。
    每个人物输出：name/role/description（档案）+ 9 个视觉锁定字段。
    """
    proj_lines = []
    for k, label in [("title", "标题"), ("premise", "故事背景"), ("mood", "氛围"),
                     ("style", "文风"), ("world_rules", "世界观规则")]:
        v = (project.get(k) or "").strip()
        if v:
            proj_lines.append(f"【{label}】{v}")
    proj_block = "\n".join(proj_lines) or "（无项目设定）"
    existing_block = "、".join(existing_names) if existing_names else "（暂无）"

    user = f"""你是一名故事分析师 + 角色美术指导。下面是一部作品的整体设定和完整大纲。请扫描大纲，找出所有**有名字的、真实出场的人物角色**，为每个人物提取档案并提炼视觉锁定关键词。

作品整体设定（决定全局风格与色彩基调，所有角色必须统一）：
{proj_block}

已登记角色（这些也要返回，便于核对）：{existing_block}

完整大纲：
{outline_text}

要求：
1. 只提取**具名的人物角色**（有姓名或明确称谓）。忽略：泛指的群体（"路人""学生们"）、地名、组织名、物品名。
2. 同一人物的不同称呼/别名合并为一个，用最常用的姓名作 name。
3. 为每个人物输出：
   - name：人物姓名
   - role：定位（主角/女主/反派/配角等）
   - description：从大纲提炼的人物档案（性别年龄、性格、外貌、背景，尽量具体）
   - 视觉锁定 9 字段：gender / age / body_type / color_tone / art_style / facial_features / hair / outfit / extra
4. art_style 和 color_tone 全剧统一（所有人物填同一个，呼应作品氛围/文风）。
5. 视觉字段不要写姿势/动作/表情/背景/镜头（会污染立绘）；忠于大纲，不臆造冲突特征。

仅输出 JSON，不要任何额外文字，格式如下：
{{
  "characters": [
    {{
      "name": "聂晓晓",
      "role": "女主角",
      "description": "17岁高中少女，性格内向……",
      "gender": "女",
      "age": "17岁少女",
      "body_type": "娇小160cm、肤色白皙",
      "color_tone": "冷色低饱和",
      "art_style": "3D精品漫剧",
      "facial_features": "杏眼、淡色瞳、清秀",
      "hair": "黑色长直发、齐刘海",
      "outfit": "校服JK",
      "extra": ""
    }}
  ]
}}"""
    return [
        {"role": "system", "content": "你是专业的故事分析师兼角色美术指导，只输出 JSON。"},
        {"role": "user", "content": user},
    ]
