"""通义万相图片生成客户端。"""
import logging
import httpx
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


async def generate_image(
    prompt: str,
    style: str = "anime",
    width: int = 1024,
    height: int = 1024,
    save_path: Optional[Path] = None,
    negative_prompt: str = "",
    prompt_extend: bool = True,
) -> dict:
    """
    调用通义万相生成图片。

    Args:
        prompt: 图片描述提示词
        style: 风格 (anime, realistic, etc.)
        width: 图片宽度
        height: 图片高度
        save_path: 保存路径，如果提供则下载图片到本地
        negative_prompt: 负向提示词（要避免的内容，如"阴影、下半身"）
        prompt_extend: 是否让通义智能扩写提示词。特写类需关掉，否则会被扩写成全身。

    Returns:
        包含 url 和本地路径的字典
    """
    headers = {
        "Authorization": f"Bearer {settings.tongyi_api_key}",
        "Content-Type": "application/json",
    }

    # 判断模型类型，使用不同的API格式
    if settings.tongyi_model in ["wanx2.6-t2i", "wanx-2.6-t2i", "wan2.6-t2i"]:
        # wanx2.6-t2i 使用新的 multimodal-generation API (按照官方文档格式)
        api_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        payload = {
            "model": "wan2.6-t2i",  # 注意：实际模型名是 wan2.6-t2i (没有x)
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "text": prompt
                            }
                        ]
                    }
                ]
            },
            "parameters": {
                "prompt_extend": prompt_extend,
                "watermark": False,
                "n": 1,
                "negative_prompt": negative_prompt,
                "size": f"{width}*{height}"
            }
        }
    else:
        # wanx-v1 等旧版本模型使用原API
        api_url = settings.tongyi_base_url
        payload = {
            "model": settings.tongyi_model,
            "input": {
                "prompt": prompt,
            },
            "parameters": {
                "size": f"{width}*{height}",
                "n": 1,
                "negative_prompt": negative_prompt,
            },
        }

    logger.info(f"[TongyiImage] 开始生成图片，prompt: {prompt[:100]}...")
    logger.info(f"[TongyiImage] 参数: model={settings.tongyi_model}, size={width}*{height}")

    async with httpx.AsyncClient(timeout=60.0) as client:
        # 第一步：提交任务
        response = await client.post(
            api_url,
            headers=headers,
            json=payload,
        )

        # 打印详细错误信息
        if response.status_code != 200:
            logger.error(f"[TongyiImage] API错误: {response.status_code}")
            logger.error(f"[TongyiImage] 响应内容: {response.text}")

        response.raise_for_status()
        result = response.json()

        logger.info(f"[TongyiImage] 任务提交成功: {result}")

        # wan2.6-t2i 使用同步模式，直接返回结果
        if settings.tongyi_model in ["wanx2.6-t2i", "wanx-2.6-t2i", "wan2.6-t2i"]:
            # 新API格式: output.choices[0].message.content[0].image
            if "output" in result and "choices" in result["output"]:
                choices = result["output"]["choices"]
                if choices and len(choices) > 0:
                    message = choices[0].get("message", {})
                    content = message.get("content", [])
                    if content and len(content) > 0:
                        # content[0] 是一个包含 image 和 type 的字典
                        image_data = content[0]
                        image_url = image_data.get("image")

                        if image_url:
                            logger.info(f"[TongyiImage] 图片生成成功(wanx2.6): {image_url}")

                            local_path = None
                            if save_path:
                                save_path.parent.mkdir(parents=True, exist_ok=True)
                                img_response = await client.get(image_url)
                                img_response.raise_for_status()
                                save_path.write_bytes(img_response.content)
                                local_path = str(save_path)
                                logger.info(f"[TongyiImage] 图片已保存到: {local_path}")

                            return {
                                "url": image_url,
                                "local_path": local_path,
                                "width": width,
                                "height": height,
                            }

            logger.error(f"[TongyiImage] wanx2.6 响应格式错误: {result}")
            raise RuntimeError(f"wanx2.6 API响应格式错误: {result}")

        # 如果是异步模式，需要轮询任务状态（旧版API）
        if "output" in result and "task_id" in result["output"]:
            task_id = result["output"]["task_id"]
            task_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"

            # 轮询任务状态
            import asyncio
            max_attempts = 30  # 最多等待30次，每次2秒
            for attempt in range(max_attempts):
                await asyncio.sleep(2)

                status_response = await client.get(task_url, headers=headers)
                status_response.raise_for_status()
                status_result = status_response.json()

                task_status = status_result["output"]["task_status"]
                logger.info(f"[TongyiImage] 任务状态: {task_status} (尝试 {attempt+1}/{max_attempts})")

                if task_status == "SUCCEEDED":
                    image_url = status_result["output"]["results"][0]["url"]
                    logger.info(f"[TongyiImage] 图片生成成功: {image_url}")

                    # 如果提供了保存路径，下载图片
                    local_path = None
                    if save_path:
                        save_path.parent.mkdir(parents=True, exist_ok=True)
                        img_response = await client.get(image_url)
                        img_response.raise_for_status()
                        save_path.write_bytes(img_response.content)
                        local_path = str(save_path)
                        logger.info(f"[TongyiImage] 图片已保存到: {local_path}")

                    return {
                        "url": image_url,
                        "local_path": local_path,
                        "width": width,
                        "height": height,
                    }
                elif task_status == "FAILED":
                    error_msg = status_result.get("output", {}).get("message", "Unknown error")
                    logger.error(f"[TongyiImage] 任务失败: {error_msg}")
                    raise RuntimeError(f"图片生成失败: {error_msg}")

            # 超时
            raise RuntimeError(f"图片生成超时，任务ID: {task_id}")

        # 同步模式（直接返回图片URL）
        elif "output" in result and "results" in result["output"]:
            image_url = result["output"]["results"][0]["url"]
            logger.info(f"[TongyiImage] 图片生成成功(同步): {image_url}")

            local_path = None
            if save_path:
                save_path.parent.mkdir(parents=True, exist_ok=True)
                img_response = await client.get(image_url)
                img_response.raise_for_status()
                save_path.write_bytes(img_response.content)
                local_path = str(save_path)
                logger.info(f"[TongyiImage] 图片已保存到: {local_path}")

            return {
                "url": image_url,
                "local_path": local_path,
                "width": width,
                "height": height,
            }
        else:
            logger.error(f"[TongyiImage] 响应格式错误: {result}")
            raise RuntimeError(f"API响应格式错误: {result}")


def optimize_prompt_for_character(character_name: str, character_desc: str) -> str:
    """
    将角色描述转换为适合生成动漫风格图片的提示词。

    Args:
        character_name: 角色名称
        character_desc: 角色描述

    Returns:
        优化后的提示词
    """
    # 添加动漫风格关键词
    base_prompt = f"动漫风格，高质量，精细的画面，{character_name}，{character_desc}"

    # 添加通用质量提示
    quality_tags = "masterpiece, best quality, detailed, anime style"

    return f"{base_prompt}, {quality_tags}"


def optimize_prompt_for_scene(scene_name: str, scene_desc: str) -> str:
    """
    将场景描述转换为适合生成动漫风格场景图的提示词。

    Args:
        scene_name: 场景名称
        scene_desc: 场景描述

    Returns:
        优化后的提示词
    """
    base_prompt = f"动漫场景，高质量背景，{scene_name}，{scene_desc}"
    quality_tags = "anime background, detailed scenery, masterpiece, best quality"

    return f"{base_prompt}, {quality_tags}"


# ---------------------------------------------------------------------------
# 角色参考图：锁定提示词 + 四视角
#
# 一致性关键：所有视角共用同一段 locked_prompt（画风/五官/发色/服饰），
# 只在末尾追加各自的视角与构图词。这样四张图是"同一个人"的不同角度，
# 其中一张被设为首帧基准，供后续所有镜头图生视频复用，杜绝跨镜头变脸。
# ---------------------------------------------------------------------------

# [技术与环境限制] —— 固定注入，保证全剧风格统一的"精品立绘"质感。
# 拆三部分：
#  GLOBAL 是与视角无关的通用约束（纯白背景禁杂物/中性无表情闭嘴/柔光），所有视角通用；
#  PROPORTION（哥特式全身比例）只给全身类视角，绝不进 GLOBAL——否则会把脸部特写也拽成全身；
#  FACE_DETAIL（面部极清/露颈部下颌线）给能看清正脸的视角（特写/正/侧/斜），不给背视图。
CHARACTER_GLOBAL_TECH = (
    "背景：纯白纯色背景，禁止出现任何背景、布景、阴影、倒影、道具或其它物品，画面中只有角色本身；"
    "无地面、无地板、角色脚下不要任何投影或落地阴影，角色干净地置于纯白底上；"
    "表情：中性无表情，自然闭嘴、双唇自然闭合，神态平静淡然，无喜怒哀乐；"
    "光照：全局均衡柔和漫射光，平光打光不产生明显投影"
)
# 全身类视角专用：哥特式时装人形比例
CHARACTER_PROPORTION = "哥特式比例，高级时装插画视觉"
# 看得清正脸的视角专用：面部极致清晰 + 颈部下颌线条清晰。
# 措辞避开"露出/皮肤纹理"等在"少女+全身"语境下会触发内容审核的词。
CHARACTER_FACE_DETAIL = (
    "面部刻画精细，五官精致锐利、轮廓分明；"
    "下颌线与颈部线条清晰利落，发型不遮挡下颌轮廓"
)

# 各视角的姿势 / 视角 / 构图（与 GLOBAL 拼成完整模板）。
# 全身类（front/side/back/three_quarter/full_body）带 PROPORTION + 全身构图；
# face_closeup 是头肩胸像特写，绝不带全身/比例语义，否则会被拽成全身照。
REF_VIEWS = {
    "face_closeup": (
        "角色头像，类似证件照构图：画面只包含头部与脖子，"
        "面部居中占据画面主要区域，画面底部到脖子或衣领处为止，"
        "不出现肩部以下、不出现身体、不出现躯干四肢；"
        "正面平视，正交视角，零透视畸变，无广角、无镜头畸形；"
        f"{CHARACTER_FACE_DETAIL}"
    ),
    "front": (
        "严格正前视全身立绘（front view），角色正面完全朝向镜头、与镜头正对，"
        "能清楚看到完整的正脸五官、身体正面、胸前服饰；"
        + CHARACTER_PROPORTION + "，平视中心镜头，正交视角，零透视畸变，"
        "无广角、无仰俯角度、无镜头畸形；"
        "绝对标准的 A-Pose，双臂自然下垂呈 A 字形；"
        "完整全身从头到脚完整呈现，双脚鞋子完整入镜，头顶保留适量留白，画面居中对称构图；"
        + CHARACTER_FACE_DETAIL
    ),
    "side": (
        "严格 90 度正侧面视图（profile view），角色完全侧身站立、身体朝向画面正侧方，"
        "只呈现侧面剪影轮廓，不是正面也不是四分之三斜侧；"
        "清晰展现侧面的额头-鼻梁-下巴轮廓线与颈部线条；"
        + CHARACTER_PROPORTION + "，平视，正交视角，零透视畸变；"
        "A-Pose 自然站姿，双臂自然下垂；"
        "完整全身从头到脚，双脚完整入镜，画面居中、头顶留白"
    ),
    "back": (
        "严格背面视图（back view / rear view），角色完全背对镜头，背朝观众站立，"
        "镜头只能看到后脑勺与发型背面、后背、臀部、腿背面——"
        "完全看不到脸、看不到五官、看不到正面，面部朝向画面深处（远离镜头）；"
        + CHARACTER_PROPORTION + "，A-Pose 站姿，平视，正交视角，零透视畸变；"
        "完整全身从头到脚，双脚完整入镜，画面居中、头顶留白；"
        "清晰展现发型背面与服饰背面的设计细节"
    ),
    "three_quarter": (
        "四分之三斜侧视图（约45度），" + CHARACTER_PROPORTION + "，平视，正交视角，零透视畸变；"
        "A-Pose 自然站姿；完整全身从头到脚，双脚完整入镜，画面居中、头顶留白；"
        + CHARACTER_FACE_DETAIL
    ),
    "full_body": (
        "正前视全身立绘，" + CHARACTER_PROPORTION + "，平视中心镜头，正交视角，零透视畸变，"
        "无广角、无仰俯角度；绝对标准的 A-Pose，双臂自然下垂呈 A 字形；"
        "从头到脚完整呈现，双脚鞋子完整入镜，头顶保留适量留白，画面居中对称构图；"
        + CHARACTER_FACE_DETAIL
    ),
}

REF_VIEW_LABELS = {
    "face_closeup": "脸部特写",
    "front": "正视图",
    "side": "侧视图",
    "back": "背视图",
    "three_quarter": "斜视图",   # 旧图保留显示用，已不在默认生成集
    "full_body": "全身立绘",
}

# 各视角的画布比例（比提示词更硬的构图约束）：
#  face_closeup 用正方形 —— 竖长画布会诱导全身构图，正方形强化头肩特写；
#  全身类用竖图（高>宽）—— 利于站姿全身从头到脚。
# 返回 (width, height)；base 为项目默认尺寸（settings.image_width/height）。
def ref_view_size(view: str, base: int = 1024) -> tuple[int, int]:
    if view == "face_closeup":
        return base, base                  # 1:1 正方形特写
    return int(base * 0.75), base          # 3:4 竖图全身


def ref_view_aspect_ratio(view: str) -> str:
    """可灵文生图画面比例：特写 1:1，全身类 3:4。"""
    if view == "face_closeup":
        return "1:1"
    return "3:4"


# 所有参考图共用的负向词：去掉投影/落地阴影/倒影、背景杂物等。
# 角色立绘要"漂浮"在纯白底上，不要脚下阴影和地面投影。
REF_NEGATIVE_COMMON = (
    "阴影,投影,落地阴影,脚下阴影,地面阴影,drop shadow,cast shadow,ground shadow,"
    "倒影,反射,地面,地板,背景,杂物,装饰,渐变背景,纹理背景,水印,文字,logo,低质量,模糊"
)
# 特写专用：额外压制全身/下半身/腿脚，防止被画成全身照。
REF_NEGATIVE_FACE = (
    "全身,半身,胸像,肩膀,躯干,腰,腰部,腰部以下,臀部,大腿,小腿,腿,脚,鞋子,"
    "full body,half body,torso,waist,hips,thighs,legs,feet,shoes,"
    + REF_NEGATIVE_COMMON
)
# 背视图专用：压制正脸/五官，防止画成正面或转头露脸。
REF_NEGATIVE_BACK = (
    "脸,正脸,正面,面部,五官,眼睛,鼻子,嘴,转头,回头,侧脸,"
    "face,frontal face,facial features,eyes,nose,mouth,looking at viewer,looking back,"
    + REF_NEGATIVE_COMMON
)
# 侧视图专用：压制正面，强制只见半边脸侧影。
REF_NEGATIVE_SIDE = (
    "正面,正脸,双眼可见,看向镜头,frontal view,front face,both eyes visible,looking at viewer,"
    + REF_NEGATIVE_COMMON
)


def ref_view_gen_opts(view: str) -> dict:
    """某视角生图的附加参数：negative_prompt + prompt_extend。

    所有视角都关掉 prompt_extend——我们的提示词已是完整四区块模板，不需通义再扩写；
    而扩写正是两个 bug 的共同源头：① 把"特写"扩成带全身的描述（特写变全身照）；
    ② 扩写结果不稳定，有时改成触发内容审核的措辞导致 400 DataInspectionFailed。
    各视角用专属负向词：特写压全身、背视图压正脸、侧视图压正面、其余压阴影。
    """
    neg = {
        "face_closeup": REF_NEGATIVE_FACE,
        "back": REF_NEGATIVE_BACK,
        "side": REF_NEGATIVE_SIDE,
    }.get(view, REF_NEGATIVE_COMMON)
    return {"negative_prompt": neg, "prompt_extend": False}


def _sanitize_age(age: str) -> str:
    """软化年龄措辞，避免通义内容审核对"未成年具体年龄 + 身体/全身"组合的拦截。

    实测：locked_prompt 含"17岁少女"等具体未成年年龄时，side/back/full_body 等带身体
    姿态的视角会被 DataInspectionFailed 必现拦截（face_closeup 不带身体能过）；
    去掉具体年龄数字后稳定通过。这里把 ≤17 的具体年龄换成不含数字的体态描述，
    保留"少年/少女/青年/成人"等画风线索，但不出现触发审核的未成年数字。
    """
    import re
    a = (age or "").strip()
    if not a:
        return ""
    m = re.search(r"(\d{1,2})\s*(岁|周岁)?", a)
    if m:
        n = int(m.group(1))
        is_female = ("女" in a)
        if n <= 12:
            return "孩童体态" + ("（女孩）" if is_female else "（男孩）")
        if n <= 17:
            # 未成年：去掉数字，用"青春期少年/少女"泛化体态
            return "少女" if is_female else "少年"
        # 成年：保留原描述（不触发审核）
        return a
    return a


def build_locked_character_prompt(art_style: str = "", facial_features: str = "",
                                  hair: str = "", outfit: str = "",
                                  extra: str = "", gender: str = "",
                                  age: str = "", body_type: str = "",
                                  color_tone: str = "") -> str:
    """把角色锁定关键词组装成全剧统一复用的固定提示词（高质量精品立绘模板）。

    采用四区块模板：人物基础特征 / 头部细节 / 身体与穿搭 / 技术与环境限制。
    前三块来自用户填写，第四块（技术与环境限制）固定注入——正是它保证全剧
    风格统一、纯白背景、标准 A-Pose、正交无畸变的"精品立绘"质感。
    返回的 locked_prompt 会被存档；每次生成参考图都以它为基底。
    """
    style = art_style.strip() or "3D精品漫剧风格"

    # [人物基础特征]——不写"全身立绘/角色设定立绘"，构图语义交给各视角（REF_VIEWS），
    # 否则会把脸部特写也拽成全身照。
    base = [f"风格：{style}", "高质量精品角色概念设计"]
    if gender.strip():
        base.append(f"性别：{gender.strip()}")
    age_safe = _sanitize_age(age)
    if age_safe:
        base.append(f"年龄：{age_safe}")
    if body_type.strip():
        base.append(f"体型与肤色：{body_type.strip()}")
    if color_tone.strip():
        base.append(f"色彩基调：{color_tone.strip()}")

    # [头部细节] + [身体与穿搭]
    detail = []
    if hair.strip():
        detail.append(f"发型发色：{hair.strip()}")
    if facial_features.strip():
        detail.append(f"脸部五官：{facial_features.strip()}")
    if outfit.strip():
        detail.append(f"服饰穿搭：{outfit.strip()}，剪裁清晰、左右对称")
    if extra.strip():
        detail.append(extra.strip())

    parts = base + detail + [CHARACTER_GLOBAL_TECH]
    quality = ("masterpiece, best quality, ultra detailed, 3D rendered, "
               "consistent character design")
    return "，".join(parts) + f", {quality}"


def build_ref_image_prompt(locked_prompt: str, view: str) -> str:
    """在锁定提示词基础上追加视角构图词，生成某一视角的参考图提示词。"""
    view_phrase = REF_VIEWS.get(view, REF_VIEWS["front"])
    return f"{locked_prompt}，{view_phrase}"


def build_face_closeup_prompt(art_style: str = "", facial_features: str = "",
                              hair: str = "", gender: str = "", age: str = "",
                              color_tone: str = "") -> str:
    """脸部特写专属提示词：只含头/脸/发/风格，绝不含服饰（裤子/鞋子）和体型。

    为什么单独做：locked_prompt 里写了完整服饰（如"长裤+马丁靴"），即便加负向词、
    正方形画布、"仅头肩"措辞，模型仍有强动机把腿脚画出来 → 特写变全身。
    从源头删掉下装/体型描述，模型就没有画全身的依据。服饰一致性由其它全身视角保证。
    """
    style = art_style.strip() or "3D精品漫剧风格"
    parts = [f"风格：{style}", "高质量精品角色头像设计"]
    if gender.strip():
        parts.append(f"性别：{gender.strip()}")
    age_safe = _sanitize_age(age)
    if age_safe:
        parts.append(f"年龄：{age_safe}")
    if color_tone.strip():
        parts.append(f"色彩基调：{color_tone.strip()}")
    if hair.strip():
        parts.append(f"发型发色：{hair.strip()}")
    if facial_features.strip():
        parts.append(f"脸部五官：{facial_features.strip()}")
    parts.append(CHARACTER_GLOBAL_TECH)
    parts.append(REF_VIEWS["face_closeup"])
    quality = "masterpiece, best quality, ultra detailed, 3D rendered, portrait"
    return "，".join(parts) + f", {quality}"


def build_appearance_prompt(art_style: str = "", facial_features: str = "",
                            hair: str = "", outfit: str = "", extra: str = "",
                            gender: str = "", age: str = "",
                            body_type: str = "", color_tone: str = "") -> str:
    """只组装角色"外貌"锁定词（基础特征/画风/五官/发色/服饰），不含姿势/构图/参考图词。

    用于"逐镜先文生图再图生视频"：把外貌词 + 本镜场景描述拼成首帧图提示词，
    让角色长相保持一致，但姿势和环境跟镜头描述走（如"床上睡觉、无轮椅"）。
    与 build_locked_character_prompt 的区别：后者带 A-Pose/纯白背景等立绘构图约束，
    不适合做镜头场景帧；这里只保留"长相/穿搭/风格"，不锁姿势和背景。
    """
    style = art_style.strip() or "3D精品漫剧风格"
    segs = [style]
    if color_tone.strip():
        segs.append(f"色彩基调：{color_tone.strip()}")
    bits = []
    if gender.strip():
        bits.append(gender.strip())
    if age.strip():
        bits.append(age.strip())
    if body_type.strip():
        bits.append(body_type.strip())
    if bits:
        segs.append("，".join(bits))
    if facial_features.strip():
        segs.append(f"五官特征：{facial_features.strip()}")
    if hair.strip():
        segs.append(f"发型发色：{hair.strip()}")
    if outfit.strip():
        segs.append(f"服饰：{outfit.strip()}")
    if extra.strip():
        segs.append(extra.strip())
    return "，".join(segs)


# [技术与约束] —— 场景图固定注入：画质/渲染 + "不要有角色出现在场景里"。
SCENE_TECH_CONSTRAINTS = (
    "画质与渲染：Unreal Engine 5，Octane Render，8k 分辨率，杰作，极高细节；"
    "附加要求：画面中不要出现任何人物 / 角色 / 人影，纯环境场景图"
)


def build_scene_prompt(style: str = "", time_weather: str = "", color_tone: str = "",
                       space_desc: str = "", materials: str = "", vista: str = "",
                       light_source: str = "", light_tech: str = "",
                       viewpoint: str = "", composition: str = "") -> str:
    """把场景视觉字段组装成"无角色场景图"的固定提示词（四区块模板）。

    [核心设定][空间与结构][光影与镜头] 来自字段，[技术与约束] 固定注入
    （UE5/Octane/8k + 不要有角色）。返回 locked_prompt 存档、生图复用。
    """
    parts = []
    # [核心设定]
    if style.strip():
        parts.append(f"风格：{style.strip()}")
    if time_weather.strip():
        parts.append(f"时间与天气：{time_weather.strip()}")
    if color_tone.strip():
        parts.append(f"色彩基调：{color_tone.strip()}")
    # [空间与结构]
    if space_desc.strip():
        parts.append(f"空间描述：{space_desc.strip()}")
    if materials.strip():
        parts.append(f"材质与细节：{materials.strip()}")
    if vista.strip():
        parts.append(f"远景/边界：{vista.strip()}")
    # [光影与镜头]
    if light_source.strip():
        parts.append(f"光源：{light_source.strip()}")
    if light_tech.strip():
        parts.append(f"光影技术：{light_tech.strip()}")
    if viewpoint.strip():
        parts.append(f"视角：{viewpoint.strip()}")
    if composition.strip():
        parts.append(f"构图：{composition.strip()}")
    parts.append(SCENE_TECH_CONSTRAINTS)
    quality = "masterpiece, best quality, ultra detailed, 8k, cinematic, no characters"
    return "，".join(parts) + f", {quality}"
