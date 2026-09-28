"""ManJv Factory application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.projects import router as projects_router
from app.config import settings
from app.database import initialize_database


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="漫剧工厂 ManJv Factory",
    description="从剧情文本到分镜、画面、配音与竖屏成片的 AI 创作流水线。",
    version="0.3.0",
    lifespan=lifespan,
)
app.include_router(projects_router)
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(settings.static_dir / "index.html")


@app.get("/health", summary="健康检查")
def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}
