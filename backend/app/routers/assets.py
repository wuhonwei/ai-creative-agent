"""图片资产生成和管理路由。"""
import logging
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import get_session
from app.models import Asset, Character, Scene, Project
from app.llm import image_client

router = APIRouter(prefix="/api", tags=["assets"])
logger = logging.getLogger(__name__)

# 资产存储根目录
ASSETS_DIR = Path(__file__).parent.parent.parent / "assets"
ASSETS_DIR.mkdir(exist_ok=True)


class GenerateAssetRequest(BaseModel):
    """生成图片请求。"""
    asset_type: Literal["character", "scene"]
    ref_id: int                    # character.id 或 scene.id
    custom_prompt: str = ""        # 用户补充的提示词（可选）


class AssetResponse(BaseModel):
    """图片资产响应。"""
    id: int
    project_id: int
    asset_type: str
    ref_id: int
    ref_name: str
    prompt: str
    width: int
    height: int
    created_at: str
    image_url: str                 # 前端访问的URL路径


@router.post("/projects/{project_id}/assets/generate")
async def generate_asset(
    project_id: int,
    req: GenerateAssetRequest,
    session: Session = Depends(get_session),
) -> AssetResponse:
    """
    生成角色或场景图片。

    - 根据 asset_type 和 ref_id 查询对应的 Character 或 Scene
    - 自动生成优化的提示词（可用用户自定义提示补充）
    - 调用通义万相生成图片
    - 保存到本地并记录到数据库
    """
    # 验证项目是否存在
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 查询引用对象
    ref_name = ""
    base_prompt = ""

    if req.asset_type == "character":
        character = session.get(Character, req.ref_id)
        if not character or character.project_id != project_id:
            raise HTTPException(status_code=404, detail="角色不存在")
        ref_name = character.name
        base_prompt = image_client.optimize_prompt_for_character(
            character.name, character.description
        )
    elif req.asset_type == "scene":
        scene = session.get(Scene, req.ref_id)
        if not scene or scene.project_id != project_id:
            raise HTTPException(status_code=404, detail="场景不存在")
        ref_name = scene.name
        base_prompt = image_client.optimize_prompt_for_scene(
            scene.name, scene.description
        )
    else:
        raise HTTPException(status_code=400, detail="不支持的资产类型")

    # 合并用户自定义提示词
    final_prompt = base_prompt
    if req.custom_prompt:
        final_prompt = f"{base_prompt}, {req.custom_prompt}"

    logger.info(f"[Assets] 开始生成 {req.asset_type} 图片: {ref_name}")
    logger.info(f"[Assets] 提示词: {final_prompt}")

    # 生成唯一文件名
    unique_id = uuid.uuid4().hex[:12]
    filename = f"{req.asset_type}_{req.ref_id}_{unique_id}.png"
    file_path = ASSETS_DIR / filename

    try:
        # 调用通义万相生成图片
        from app.config import settings
        result = await image_client.generate_image(
            prompt=final_prompt,
            style=settings.image_style,
            width=settings.image_width,
            height=settings.image_height,
            save_path=file_path,
        )

        # 保存到数据库
        asset = Asset(
            project_id=project_id,
            asset_type=req.asset_type,
            ref_id=req.ref_id,
            ref_name=ref_name,
            prompt=final_prompt,
            image_path=filename,  # 只存储文件名，不存储完整路径
            width=result["width"],
            height=result["height"],
        )
        session.add(asset)
        session.commit()
        session.refresh(asset)

        logger.info(f"[Assets] 图片生成成功，ID: {asset.id}")

        return AssetResponse(
            id=asset.id,
            project_id=asset.project_id,
            asset_type=asset.asset_type,
            ref_id=asset.ref_id,
            ref_name=asset.ref_name,
            prompt=asset.prompt,
            width=asset.width,
            height=asset.height,
            created_at=asset.created_at.isoformat(),
            image_url=f"/api/assets/{asset.id}/image",
        )

    except Exception as e:
        logger.error(f"[Assets] 图片生成失败: {e}", exc_info=True)
        # 如果文件已生成但数据库保存失败，清理文件
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=500, detail=f"图片生成失败: {str(e)}")


@router.get("/projects/{project_id}/assets")
async def list_assets(
    project_id: int,
    asset_type: str = None,  # 可选过滤：character / scene
    session: Session = Depends(get_session),
) -> list[AssetResponse]:
    """获取项目的所有图片资产。"""
    query = select(Asset).where(Asset.project_id == project_id)
    if asset_type:
        query = query.where(Asset.asset_type == asset_type)

    assets = session.exec(query.order_by(Asset.created_at.desc())).all()

    return [
        AssetResponse(
            id=asset.id,
            project_id=asset.project_id,
            asset_type=asset.asset_type,
            ref_id=asset.ref_id,
            ref_name=asset.ref_name,
            prompt=asset.prompt,
            width=asset.width,
            height=asset.height,
            created_at=asset.created_at.isoformat(),
            image_url=f"/api/assets/{asset.id}/image",
        )
        for asset in assets
    ]


@router.get("/assets/{asset_id}/image")
async def get_asset_image(
    asset_id: int,
    session: Session = Depends(get_session),
) -> FileResponse:
    """获取图片文件。"""
    asset = session.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="图片不存在")

    file_path = ASSETS_DIR / asset.image_path
    if not file_path.exists():
        logger.error(f"[Assets] 图片文件不存在: {file_path}")
        raise HTTPException(status_code=404, detail="图片文件不存在")

    return FileResponse(
        file_path,
        media_type="image/png",
        filename=f"{asset.ref_name}_{asset.id}.png",
    )


@router.delete("/assets/{asset_id}")
async def delete_asset(
    asset_id: int,
    session: Session = Depends(get_session),
) -> dict:
    """删除图片资产（数据库记录+本地文件）。"""
    asset = session.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="图片不存在")

    # 删除本地文件
    file_path = ASSETS_DIR / asset.image_path
    if file_path.exists():
        file_path.unlink()
        logger.info(f"[Assets] 已删除文件: {file_path}")

    # 删除数据库记录
    session.delete(asset)
    session.commit()

    logger.info(f"[Assets] 已删除资产: {asset_id}")
    return {"message": "图片已删除"}
