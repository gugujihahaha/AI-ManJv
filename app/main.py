"""AI-ManJv AI 漫剧创作系统 - 最小可运行入口。

阶段：实验1 环境搭建最小项目，仅提供项目信息与健康检查接口。
后续迭代将增加：剧情输入 -> AI 分镜拆分 -> 漫画图片生成 -> Web 浏览。
"""

from fastapi import FastAPI

app = FastAPI(
    title="AI-ManJv AI 漫剧创作系统",
    description="输入故事文本，AI 拆分漫画分镜并生成漫画图片，提供 Web 页面浏览漫剧作品。",
    version="0.1.0",
)


@app.get("/", summary="项目信息")
def root():
    """返回项目基本信息，用于验证服务已成功启动。"""
    return {
        "project": "AI-ManJv",
        "name": "AI 漫剧创作系统",
        "version": "0.1.0",
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health", summary="健康检查")
def health():
    """健康检查接口，用于环境验收（2.6 项目可启动项）。"""
    return {"status": "ok"}
