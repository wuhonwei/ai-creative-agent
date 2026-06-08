"""中文 Prompt 模板。

集中管理所有发给模型的指令，便于调优。
分四类任务：生成大纲 / 生成正文 / 抽取摘要 / 抽取状态变化。
"""
import json

# ---------------------------------------------------------------------------
# 系统人设
# ---------------------------------------------------------------------------
SYSTEM_WRITER = (
    "你是一位资深中文小说家与故事架构师，擅长长篇连载创作。"
    "你严格遵守已设定的世界观、人物性格与已发生的剧情，保持前后一致，"
    "不擅自推翻既有设定，不出现与历史情节矛盾的内容。"
    "文笔细腻、叙事流畅，对话自然，节奏张弛有度。"
)


def _bible_block(project: dict, characters: list[dict]) -> str:
    """把故事圣经 + 角色设定拼成上下文块。"""
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

    if characters:
        lines.append("\n【角色档案】")
        for c in characters:
            seg = f"- {c['name']}"
            if c.get("role"):
                seg += f"（{c['role']}）"
            if c.get("description"):
                seg += f"：{c['description']}"
            if c.get("state"):
                seg += f"｜当前状态：{c['state']}"
            lines.append(seg)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1. 生成分层大纲
# ---------------------------------------------------------------------------
def build_outline_prompt(project: dict, characters: list[dict],
                         instruction: str = "", num_volumes: int = 0,
                         chapters_per_volume: int = 0) -> list[dict]:
    bible = _bible_block(project, characters)

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

要求：
1. 先给出"卷"级结构，每卷一句主题。
2. 每卷下给出若干"章"，每章包含简短标题与 1-3 句梗概（说明本章发生的关键事件、推进的情节）。
3. 章节梗概要能串联成有张力的主线，并为后续埋下伏笔。

仅输出 JSON，不要任何额外文字，格式如下：
{{
  "volumes": [
    {{
      "title": "第一卷 卷名",
      "synopsis": "本卷主题",
      "chapters": [
        {{"title": "第1章 章名", "synopsis": "本章梗概"}}
      ]
    }}
  ]
}}"""
    return [
        {"role": "system", "content": SYSTEM_WRITER},
        {"role": "user", "content": user},
    ]


def build_volume_chapters_prompt(project: dict, characters: list[dict],
                                 all_volumes: list[dict], target_volume: dict,
                                 previous_chapters: list[dict],
                                 count: int = 0, instruction: str = "") -> list[dict]:
    """只为某一卷展开详细章节大纲。

    all_volumes：全书各卷的 {title, synopsis}，给模型把握整体节奏；
    target_volume：本次要展开的卷；
    previous_chapters：本卷之前各卷的章节 {volume_title, chapter_title, synopsis}，
                       避免剧情矛盾（人物状态、事件顺序、伏笔对不上）；
    count：本卷目标章数（0 = 由模型决定）。
    单卷单次生成，章数可控，避免一次性生成全书章节导致 JSON 截断。
    """
    bible = _bible_block(project, characters)

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

要求：
1. 参考后续各卷的主题,合理控制本卷节奏、埋设伏笔,但本卷章节只写本卷主题范围内的事件,不要把后续卷的核心冲突或高潮提前写进本卷。
2. 严格承接前面各卷已发生的剧情,人物状态、事件顺序、伏笔呼应要与前文一致,不能出现矛盾。
3. 每章包含简短标题与 1-3 句梗概（关键事件、推进的情节、埋设或回收的伏笔）。
4. 只输出本卷的章节,不要输出其它卷的内容。

仅输出 JSON，不要任何额外文字，格式如下：
{{
  "chapters": [
    {{"title": "第1章 章名", "synopsis": "本章梗概"}}
  ]
}}"""
    return [
        {"role": "system", "content": SYSTEM_WRITER},
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

    # 字数控制：给一个 ±15% 的浮动区间，便于模型在不破坏情节完整性的前提下收尾
    low = int(target_words * 0.85)
    high = int(target_words * 1.15)
    parts.append(
        f"\n请直接写出本章正文。要求：\n"
        f"1. 篇幅约 {target_words} 字(包括标点符号)（控制在 {low}-{high} 字之间），"
        f"围绕本章梗概合理安排节奏，写完整一个自然的章节段落，不要为凑字数注水，也不要草草收尾。\n"
        f"2. **严格衔接前文**：必须自然承接最近章节的内容，人物状态、场景位置、对话语境要连贯一致，"
        f"不能出现与前文矛盾的情节。开头要体现与上一章的连续性。\n"
        f"3. **只写本章内容，不要提前透露后续章节**：参考全书大纲了解整体进度，但本章只展开本章梗概的事件，"
        f"不要把后续章节的关键情节、高潮或结局提前写进本章。\n"
        f"4. 以叙事和对话推进剧情，避免大段空泛的总结。\n"
        f"5. **章节结尾必须留有悬念或引发期待**：通过未解之谜、意外转折、新的冲突苗头、"
        f"未完成的对话、突然出现的线索等方式，让读者产生\"接下来会怎样？\"的期待感，吸引继续阅读下一章。\n"
        f"6. 只输出正文本身，不要输出标题、解释、大纲或额外说明。"
    )

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
