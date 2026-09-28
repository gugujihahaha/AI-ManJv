"""ManJv Factory application entry point."""

import logging
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.projects import router as projects_router
from app.config import settings
from app.database import initialize_database


LOG_PATH = settings.database_path.parent / "app.log"


def _build_logger() -> logging.Logger:
    """Log to storage/app.log so a failed request can still be traced afterwards."""
    logger = logging.getLogger("manjv")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    for handler in (logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


logger = _build_logger()


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    logger.info("数据库就绪：%s", settings.database_path)
    yield
    logger.info("应用已停止")


app = FastAPI(
    title="漫剧工厂 ManJv Factory",
    description="从剧情文本到分镜、画面、配音与竖屏成片的 AI 创作流水线。",
    version="0.3.0",
    lifespan=lifespan,
)
app.include_router(projects_router)
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """One line per business request; static assets are skipped to keep the log readable."""
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("未处理异常 %s %s", request.method, request.url.path)
        raise
    if not request.url.path.startswith("/static"):
        cost = (perf_counter() - started) * 1000
        line = "%s %s -> %d  %.0fms" % (request.method, request.url.path, response.status_code, cost)
        if response.status_code >= 400:
            logger.warning(line)
        else:
            logger.info(line)
    return response


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(settings.static_dir / "index.html")


@app.get("/health", summary="健康检查")
def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}
