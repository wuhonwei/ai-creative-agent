"""FastAPI 入口：装配路由、CORS、启动建表。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import init_db
from app.routers import (
    assets, chapters, character_refs, export, generate, outline, projects,
    scenes, storyboard, videos,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AI 创作智能体", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(outline.router)
app.include_router(chapters.router)
app.include_router(generate.router)
app.include_router(export.router)
app.include_router(assets.router)
app.include_router(scenes.router)
app.include_router(character_refs.router)
app.include_router(storyboard.router)
app.include_router(videos.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "llm_configured": settings.llm_configured,
            "model": settings.deepseek_model}
